from app.analytics.candidate_snapshot_writer import normalize_candidate_row
from app.indicators.enrich_indicators import enrich_indicators
from app.projections.trade_projection import project_trade
from app.risk.risk_manager import calculate_risk
from app.strategies.entry_engine import detect_entry
from app.strategies.momentum_strategy import analyze_setup


def _direction_from_signal(signal):

    signal = str(signal or "").upper()

    if "BEARISH" in signal:

        return "PUT"
    if "BULLISH" in signal:

        return "CALL"
    return None


def _ensure_structure_columns(df):

    structured_df = df.copy()

    if "ROLLING_SUPPORT" not in structured_df.columns:

        structured_df["ROLLING_SUPPORT"] = structured_df["Low"].rolling(10).min()

    if "ROLLING_RESISTANCE" not in structured_df.columns:

        structured_df["ROLLING_RESISTANCE"] = structured_df["High"].rolling(10).max()

    if "PREV_LOW" not in structured_df.columns:

        structured_df["PREV_LOW"] = structured_df["Low"].shift(1)

    if "PREV_HIGH" not in structured_df.columns:

        structured_df["PREV_HIGH"] = structured_df["High"].shift(1)

    return structured_df


def evaluate_symbol_at_time(symbol, candles_5m, timestamp=None):

    if candles_5m is None or len(candles_5m) < 45:

        return None

    historical_df = enrich_indicators(candles_5m.copy())
    historical_df = _ensure_structure_columns(historical_df)
    analysis = analyze_setup(historical_df)
    final_signal = analysis.get("signal", "NEUTRAL")
    entry_setup = detect_entry(
        historical_df,
        analysis,
        symbol=symbol
    )
    risk_setup = calculate_risk(historical_df, analysis, entry_setup)
    projection = None

    if risk_setup.get("stop_loss") is not None and entry_setup.get("entry_type") != "NO_ENTRY":

        projection = project_trade(
            symbol=symbol,
            latest_price=historical_df.iloc[-1]["Close"],
            analysis={
                "signal": final_signal,
                "score": analysis.get("score", 0),
                "ATR": historical_df.iloc[-1].get("ATR"),
                "market_regime": analysis.get("market_regime", "UNKNOWN"),
            },
            entry_setup=entry_setup,
            risk_setup=risk_setup,
        )

    row = {
        "timestamp": timestamp,
        "Symbol": symbol,
        "Price": historical_df.iloc[-1]["Close"],
        "Final Signal": final_signal,
        "Setup %": min(abs(analysis.get("score", 0)) * 10, 100),
        "Entry": entry_setup.get("entry_type"),
        "Entry Quality": entry_setup.get("entry_quality"),
        "Risk Reward": risk_setup.get("risk_reward"),
        "Candidate RR": risk_setup.get("risk_reward"),
        "Trade Allowed": risk_setup.get("trade_allowed"),
        "Action Status": "ENTER_PAPER" if risk_setup.get("trade_allowed") else "REVIEW_TV_CHART",
        "Blocked By": None if risk_setup.get("trade_allowed") else "; ".join(risk_setup.get("reasons", [])),
        "Candidate Direction": _direction_from_signal(final_signal),
        "Candidate Entry Price": risk_setup.get("entry_price"),
        "Candidate Stop Price": risk_setup.get("stop_loss"),
        "Candidate Target Price": risk_setup.get("take_profit"),
        "Market Regime": analysis.get("market_regime", "UNKNOWN"),
        "ATR %": historical_df.iloc[-1].get("ATR_PCT"),
        "Relative Volume": historical_df.iloc[-1].get("REL_VOLUME"),
        "Option Quality Score": 100,
        "Option Spread %": 0,
        "Option Quote Freshness": "LIVE_QUOTE",
        "Affordable": True,
        "Target Price": projection.get("target_price") if projection else None,
        "Stop Price": projection.get("stop_price") if projection else None,
    }

    return normalize_candidate_row(row, timestamp=timestamp)