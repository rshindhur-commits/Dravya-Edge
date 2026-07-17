from app.indicators.technical_indicators import (
    get_live_price
)

from app.state.state_trade_manager import (
    get_trade,
    open_trade,
    close_trade,
    update_trade
)

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import time
import json
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from rich.console import Console
from rich.table import Table

from app.config.watchlist import (
    get_scanner_watchlist,
    MARKET_REFERENCE_SYMBOLS,
    REFERENCE_FETCH_SYMBOLS
)
from app.config.settings import (
    print_runtime_banner,
    settings,
    validate_runtime_settings
)
from app.gates import (
    EntryGateConfig,
    evaluate_entry_gate
)
from app.indicators.technical_indicators import compute_indicators
from app.strategies.momentum_strategy import analyze_setup
from app.ai.trade_analyzer import generate_trade_summary
#from app.options.options_recommender import recommend_option
from app.state.state_manager import should_call_ai
from app.strategies.entry_engine import detect_entry
from app.risk.risk_manager import calculate_risk
from app.risk.event_blocker import evaluate_event_blocker
from app.exit.exit_engine import evaluate_exit

from app.utils.timeframe_resampler import (
    resample_timeframe
)

# from app.indicators.indicator_engine import (
#     apply_indicators
# )

from app.indicators.technical_indicators import (
    get_polygon_data
)

from app.options.options_filter import (
    evaluate_option_liquidity
)

from app.options.options_recommender import (
    recommend_live_option_bundle,
    recommend_live_option
)

from app.options.live_options_chain import (
    fetch_option_snapshot,
    refresh_contract_quote
)

from app.options.affordability_config import (
    get_affordability_config
)

from app.options.option_affordability import (
    add_affordability_metrics
)

from app.options.option_metrics import (
    calculate_option_pl
)

from app.options.option_direction import (
    contract_matches_direction,
    resolve_option_direction
)

from app.projections.trade_projection import (
    project_trade
)

from app.risk.position_sizing import (
    calculate_position_size
)

from app.analytics.trade_telemetry import (
    save_trade_telemetry
)

from app.analytics.engine_health import (
    append_engine_health_history,
    calculate_health_score,
    EngineHealth
)
from app.analytics.scanner_profiler import (
    StageTimer,
    append_scanner_stage_profile
)

from app.analytics.candidate_snapshot_writer import (
    append_candidate_snapshots
)
from app.background import run_background
from app.storage.signal_lifecycle_store import (
    record_signal_lifecycle_events_for_scan
)

from app.analytics.performance_summary import (
    summarize_telemetry
)

from app.analytics.replay_engine import (
    replay_trade_projection
)
from app.alerts.telegram_alerts import (
    calculate_entry_alert_score,
    maybe_send_scanner_entry_alert,
    maybe_send_trade_exit_alert
)
from app.db.persistence import (
    print_db_status,
    record_gate_decisions,
    record_scanner_run_finish,
    record_scanner_run_start
)
from app.utils.runtime_logging import debug_print
from app.storage.daily_paths import (
    daily_path,
    live_path
)
from app.storage.session_manager import (
    get_scan_id,
    get_trading_day,
    now_et,
    register_scan
)

console = Console(width=120)


SCANNER_ENTRY_GATE_CONFIG = EntryGateConfig(
    min_rr=2.0,
    min_setup_percent=70.0,
    min_option_quality=65.0,
    max_spread_pct=10.0
)


def _env_bool(name, default=False):

    value = os.getenv(name)

    if value is None:

        return default

    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _paper_option_validation_mode():

    return _env_bool(
        "PAPER_IGNORE_AFFORDABILITY",
        True
    )


def _env_int(name, default):

    value = os.getenv(name)

    if value is None:

        return default

    try:

        return int(str(value).strip())

    except Exception:

        return default


SCANNER_MAX_WORKERS = max(
    1,
    _env_int("SCANNER_MAX_WORKERS", 5)
)


def process_symbol(symbol):

    start = time.perf_counter()

    try:

        df_5m_raw = get_polygon_data(
            symbol,
            5,
            "minute",
            1
        )

        return {
            "symbol": symbol,
            "data": df_5m_raw,
            "runtime": time.perf_counter() - start,
            "success": True,
            "error": None,
        }

    except Exception as exc:

        return {
            "symbol": symbol,
            "data": pd.DataFrame(),
            "runtime": time.perf_counter() - start,
            "success": False,
            "error": str(exc),
        }


def _prefetch_watchlist_market_data(watchlist):

    if SCANNER_MAX_WORKERS <= 1:

        return {
            symbol: process_symbol(symbol)
            for symbol in watchlist
        }

    results = {}

    with ThreadPoolExecutor(max_workers=SCANNER_MAX_WORKERS) as executor:

        future_map = {
            executor.submit(process_symbol, symbol): symbol
            for symbol in watchlist
        }

        for future in as_completed(future_map):

            symbol = future_map[future]
            results[symbol] = future.result()

    return results


def _candidate_persistence_key(row):

    return "|".join(
        str(row.get(field) or "").strip().upper()
        for field in [
            "Symbol",
            "Candidate Direction",
            "Entry"
        ]
    )


def _load_candidate_persistence_state(trading_day):

    path = daily_path(trading_day, "candidate_persistence_state.json")

    try:

        if not path.exists() or path.stat().st_size == 0:

            return {}

        return json.loads(path.read_text(encoding="utf-8"))

    except Exception:

        return {}


def _save_candidate_persistence_state(trading_day, state):

    path = daily_path(trading_day, "candidate_persistence_state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8"
    )


def _add_candidate_persistence(df_results, trading_day, scan_id):

    if df_results.empty:

        return df_results

    state = _load_candidate_persistence_state(trading_day)
    observed_at = now_et().isoformat()
    output = df_results.copy()

    for column in [
        "Candidate Persistence Key",
        "Candidate First Seen",
        "Candidate Last Seen",
        "Candidate Scan Count",
        "Candidate Best Score",
        "Candidate Current Score",
        "Candidate Score Delta",
        "Candidate Strengthening"
    ]:

        if column not in output.columns:

            output[column] = None

    for index, row in output.iterrows():

        entry_type = str(row.get("Entry") or "").strip().upper()
        direction = str(row.get("Candidate Direction") or "").strip().upper()

        if entry_type in {"", "NAN", "NONE", "NO_ENTRY", "NO_SETUP"} or direction not in {"CALL", "PUT"}:

            continue

        key = _candidate_persistence_key(row)
        current_score = _safe_metric(
            pd.DataFrame([{"score": row.get("15m Score")}]),
            "score"
        )

        existing = state.get(key, {})
        first_seen = existing.get("first_seen_at", observed_at)
        scan_count = int(existing.get("scan_count", 0) or 0) + 1
        previous_score = existing.get("current_score")
        best_score = existing.get("best_score")

        try:

            current_score_float = float(current_score)

        except Exception:

            current_score_float = None

        try:

            previous_score_float = float(previous_score)

        except Exception:

            previous_score_float = None

        try:

            best_score_float = float(best_score)

        except Exception:

            best_score_float = current_score_float

        if current_score_float is not None:

            best_score_float = max(
                current_score_float,
                best_score_float if best_score_float is not None else current_score_float
            )

        score_delta = (
            round(current_score_float - previous_score_float, 2)
            if current_score_float is not None and previous_score_float is not None
            else None
        )
        strengthening = score_delta is not None and score_delta > 0

        state[key] = {
            "symbol": row.get("Symbol"),
            "direction": direction,
            "entry": entry_type,
            "first_seen_at": first_seen,
            "last_seen_at": observed_at,
            "last_scan_id": scan_id,
            "scan_count": scan_count,
            "current_score": current_score_float,
            "previous_score": previous_score_float,
            "best_score": best_score_float,
            "score_delta": score_delta,
            "strengthening": strengthening,
            "action_status": row.get("Action Status"),
            "top_candidate": row.get("Top Candidate")
        }
        output.at[index, "Candidate Persistence Key"] = key
        output.at[index, "Candidate First Seen"] = first_seen
        output.at[index, "Candidate Last Seen"] = observed_at
        output.at[index, "Candidate Scan Count"] = scan_count
        output.at[index, "Candidate Best Score"] = best_score_float
        output.at[index, "Candidate Current Score"] = current_score_float
        output.at[index, "Candidate Score Delta"] = score_delta
        output.at[index, "Candidate Strengthening"] = strengthening

    _save_candidate_persistence_state(trading_day, state)

    return output


def _append_market_opportunity_audit(df_results, trading_day, scan_id):

    try:

        if df_results.empty:

            return None

        def audit_column(name):

            if name in df_results.columns:

                return df_results[name]

            return pd.Series([None] * len(df_results), index=df_results.index)

        blocked_reason = (
            audit_column("Blocked By")
            .combine_first(audit_column("Action Reason"))
            .combine_first(audit_column("Option Rejection Reason"))
        )

        audit = pd.DataFrame({
            "trading_day": trading_day,
            "scan_id": scan_id,
            "observed_at": now_et().isoformat(),
            "symbol": audit_column("Symbol"),
            "score": audit_column("15m Score"),
            "category_score": audit_column("Category Score"),
            "setup": audit_column("Entry"),
            "action": audit_column("Action Status"),
            "blocked_reason": blocked_reason,
            "top_candidate": audit_column("Top Candidate"),
            "candidate_scan_count": audit_column("Candidate Scan Count"),
            "candidate_best_score": audit_column("Candidate Best Score"),
            "candidate_score_delta": audit_column("Candidate Score Delta"),
            "candidate_strengthening": audit_column("Candidate Strengthening"),
            "market_move_pct": audit_column("Symbol Move %"),
            "final_outcome": audit_column("Replay Outcome")
        })
        path = daily_path(trading_day, "market_opportunity_audit.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        audit.to_csv(
            path,
            mode="a",
            header=write_header,
            index=False
        )

        return {
            "path": str(path),
            "rows": len(audit)
        }

    except Exception as exc:

        print(f"[MARKET OPPORTUNITY AUDIT WARNING] {exc}")
        return None


def _json_list(value):

    if value is None:

        return []

    if isinstance(value, list):

        return value

    try:

        if pd.isna(value):

            return []

    except Exception:

        pass

    try:

        parsed = json.loads(str(value))

        if isinstance(parsed, list):

            return parsed

    except Exception:

        return []

    return []


def _append_option_liquidity_audit(df_results, trading_day, scan_id):

    try:

        if df_results.empty or "Option Liquidity Attempts" not in df_results.columns:

            return None

        rows = []

        for _, row in df_results.iterrows():

            attempts = _json_list(
                row.get("Option Liquidity Attempts")
            )

            for attempt_index, attempt in enumerate(attempts, start=1):

                rows.append({
                    "trading_day": trading_day,
                    "scan_id": scan_id,
                    "observed_at": now_et().isoformat(),
                    "symbol": row.get("Symbol"),
                    "selected_option_ticker": row.get("Option Ticker"),
                    "attempt_index": attempt_index,
                    "attempt_source": attempt.get("source"),
                    "attempt_ticker": attempt.get("ticker"),
                    "attempt_code": attempt.get("code"),
                    "attempt_reason": attempt.get("reason"),
                    "attempt_spread_pct": attempt.get("spread_pct"),
                    "attempt_liquid": attempt.get("liquid"),
                    "attempt_accepted": attempt.get("accepted", False),
                    "action_status": row.get("Action Status"),
                    "option_quote_status": row.get("Option Quote Status"),
                    "option_rejection_reason": row.get("Option Rejection Reason")
                })

        if not rows:

            return None

        audit = pd.DataFrame(rows)
        path = daily_path(trading_day, "option_liquidity_attempts.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        audit.to_csv(
            path,
            mode="a",
            header=write_header,
            index=False
        )

        return {
            "path": str(path),
            "rows": len(audit)
        }

    except Exception as exc:

        print(f"[OPTION LIQUIDITY AUDIT WARNING] {exc}")
        return None


def _bool_series(series):

    return series.astype(str).str.strip().str.lower().isin([
        "true",
        "1",
        "yes"
    ])


def _build_candidate_funnel(df_results, telegram_summary=None):

    telegram_summary = telegram_summary or {}

    if df_results.empty:

        return {
            "scanned": 0,
            "directional": 0,
            "entry_ready": 0,
            "risk_passed": 0,
            "option_selected": 0,
            "liquidity_passed": 0,
            "affordability_passed": 0,
            "ema_rejection_short": 0,
            "enter_paper": 0,
            "telegram": 0
        }

    final_signal = df_results.get("Final Signal", pd.Series(dtype=object)).astype(str)
    entry = df_results.get("Entry", pd.Series(dtype=object)).astype(str).str.upper()
    candidate_direction = df_results.get("Candidate Direction", pd.Series(dtype=object)).astype(str).str.upper()
    option_ticker = df_results.get("Option Ticker", pd.Series(dtype=object))
    action_status = df_results.get("Action Status", pd.Series(dtype=object)).astype(str).str.upper()
    trade_allowed = _bool_series(
        df_results.get("Trade Allowed", pd.Series(dtype=object))
    )
    setup_valid = _bool_series(
        df_results.get("Setup Valid", pd.Series(dtype=object))
    )
    liquidity_passed = _bool_series(
        df_results.get("Option Liquidity Passed", pd.Series(dtype=object))
    )
    affordable = _bool_series(
        df_results.get("Affordable", pd.Series(dtype=object))
    )

    directional = final_signal.str.contains(
        "BULLISH|BEARISH",
        case=False,
        regex=True,
        na=False
    )
    entry_ready = (
        ~entry.isin(["", "NAN", "NONE", "NO_ENTRY", "NO_SETUP"])
        & candidate_direction.isin(["CALL", "PUT"])
    )

    return {
        "scanned": int(len(df_results)),
        "directional": int(directional.sum()),
        "entry_ready": int(entry_ready.sum()),
        "risk_passed": int(setup_valid.sum()),
        "option_selected": int(option_ticker.notna().sum()),
        "liquidity_passed": int(liquidity_passed.sum()),
        "affordability_passed": int(affordable.sum()),
        "ema_rejection_short": int(entry.eq("EMA_REJECTION_SHORT").sum()),
        "enter_paper": int(action_status.eq("ENTER_PAPER").sum()),
        "telegram": int(telegram_summary.get("sent_count", 0))
    }


def _write_candidate_funnel(funnel, trading_day, scan_id, telegram_summary=None):

    telegram_summary = telegram_summary or {}
    payload = {
        "trading_day": trading_day,
        "scan_id": scan_id,
        "observed_at": now_et().isoformat(),
        **funnel,
        "telegram_attempted": telegram_summary.get("attempted_count", 0),
        "telegram_blocked": telegram_summary.get("blocked_count", 0),
        "telegram_reasons": telegram_summary.get("reasons", {})
    }
    path = daily_path(trading_day, "candidate_funnel.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:

        handle.write(json.dumps(payload, default=str) + "\n")

    return {
        "path": str(path),
        "rows": 1
    }


def _print_candidate_funnel(funnel, telegram_summary=None):

    telegram_summary = telegram_summary or {}
    print("\n[CANDIDATE FUNNEL]")

    for label, key in [
        ("scanned", "scanned"),
        ("directional", "directional"),
        ("entry ready", "entry_ready"),
        ("risk passed", "risk_passed"),
        ("option selected", "option_selected"),
        ("liquidity passed", "liquidity_passed"),
        ("affordability passed", "affordability_passed"),
        ("EMA_REJECTION_SHORT", "ema_rejection_short"),
        ("ENTER_PAPER", "enter_paper"),
        ("Telegram", "telegram")
    ]:

        print(f"{funnel.get(key, 0)} {label}")

    ema_rejection_short_count = funnel.get("ema_rejection_short", 0)
    ema_rejection_warning_threshold = _env_int(
        "EMA_REJECTION_SHORT_WARNING_THRESHOLD",
        10
    )

    if ema_rejection_short_count > ema_rejection_warning_threshold:

        print(
            "[CANDIDATE FUNNEL WARNING] "
            f"EMA_REJECTION_SHORT count={ema_rejection_short_count} "
            f"exceeds threshold={ema_rejection_warning_threshold}; "
            "recent rejection window may be too wide"
        )

    print(
        "[TELEGRAM SUMMARY] "
        f"ENTER_PAPER={funnel.get('enter_paper', 0)} "
        f"attempted={telegram_summary.get('attempted_count', 0)} "
        f"sent={telegram_summary.get('sent_count', 0)} "
        f"blocked={telegram_summary.get('blocked_count', 0)} "
        f"reasons={telegram_summary.get('reasons', {})}"
    )


def _align_action_status_with_entry_gate(row):

    if row.get("Action Status") not in [
        "ENTER",
        "ENTER_PAPER",
        "REVIEW_TV_CHART"
    ]:

        return row

    gate_allowed, gate_reason = evaluate_entry_gate(
        row,
        SCANNER_ENTRY_GATE_CONFIG,
        mode="paper"
    )

    if gate_allowed:

        return row

    row = dict(row)
    row["Action Status"] = "REVIEW_TV_CHART"
    row["Action Reason"] = gate_reason
    row["Blocked By"] = gate_reason
    row["Execution Ready"] = False
    row["Realtime Ready"] = False
    row["Rejected Trade Reason"] = gate_reason
    row["Do Not Enter Reason"] = gate_reason

    return row


def _safe_metric(df, column):

    try:

        if df is None or df.empty or column not in df.columns:

            return None

        value = df[column].iloc[-1]

        if pd.isna(value):

            return None

        return round(
            float(value),
            2
        )

    except Exception:

        return None


def _append_daily_candles(symbol, candles_df, trading_day, scan_id, interval="5m"):

    try:

        if candles_df is None or candles_df.empty:

            return 0

        output = candles_df.copy()
        output = output.reset_index()
        rename_map = {}

        for column in output.columns:

            normalized = str(column).strip().lower()

            if normalized in {"index", "datetime", "date", "time", "timestamp"}:

                rename_map[column] = "timestamp"
            elif normalized == "open":

                rename_map[column] = "open"
            elif normalized == "high":

                rename_map[column] = "high"
            elif normalized == "low":

                rename_map[column] = "low"
            elif normalized == "close":

                rename_map[column] = "close"
            elif normalized == "volume":

                rename_map[column] = "volume"

        output = output.rename(columns=rename_map)

        required = ["timestamp", "open", "high", "low", "close"]

        if any(column not in output.columns for column in required):

            return 0

        output = output[required + (["volume"] if "volume" in output.columns else [])].copy()
        output.insert(0, "symbol", symbol)
        output.insert(1, "interval", interval)
        output["trading_day"] = trading_day
        output["scan_id"] = scan_id
        output["timestamp"] = output["timestamp"].astype(str)

        path = daily_path(trading_day, "candles_5m.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        output.to_csv(
            path,
            mode="a",
            header=write_header,
            index=False
        )

        return len(output)

    except Exception as exc:

        print(f"[DAILY CANDLE WARNING] {symbol}: {exc}")
        return 0


def _calculate_premarket_gap_pct(df):

    try:

        if df is None or df.empty:

            return None

        market_df = df.copy()

        if market_df.index.tz is None:

            market_df.index = market_df.index.tz_localize(
                "UTC"
            )

        market_df.index = market_df.index.tz_convert(
            "America/New_York"
        )

        latest_date = market_df.index[-1].date()

        current_day = market_df[
            market_df.index.date == latest_date
        ]

        previous_days = market_df[
            market_df.index.date < latest_date
        ]

        if current_day.empty or previous_days.empty:

            return None

        current_open = float(
            current_day["Open"].iloc[0]
        )
        previous_close = float(
            previous_days["Close"].iloc[-1]
        )

        if previous_close <= 0:

            return None

        return round(
            (
                (current_open - previous_close)
                / previous_close
            ) * 100,
            2
        )

    except Exception:

        return None


def get_market_session(current_et=None):

    current_et = current_et or datetime.now(
        ZoneInfo("America/New_York")
    )

    market_minutes = (
        current_et.hour * 60
    ) + current_et.minute

    if market_minutes < 4 * 60:

        return "CLOSED"

    if market_minutes < 9 * 60 + 30:

        return "PREMARKET"

    if market_minutes < 9 * 60 + 45:

        return "OPENING_RANGE"

    if market_minutes < 16 * 60:

        return "REGULAR"

    if market_minutes < 20 * 60:

        return "AFTERHOURS"

    return "CLOSED"


def infer_aggregate_interval_minutes(df):

    if df is None or df.empty or len(df.index) < 2:

        return 0

    try:

        deltas = pd.Series(df.index).diff().dropna()

        if deltas.empty:

            return 0

        interval_minutes = deltas.median().total_seconds() / 60

        return max(
            0,
            round(interval_minutes, 2)
        )

    except Exception:

        return 0


def _classify_explicit_regime(df):

    try:

        if df is None or df.empty:

            return "UNKNOWN"

        latest = df.iloc[-1]
        atr_pct = float(latest.get("ATR_PCT", 0) or 0)
        close = float(latest.get("Close", 0) or 0)
        vwap = float(latest.get("VWAP", 0) or 0)
        ema9 = float(latest.get("EMA9", 0) or 0)
        ema20 = float(latest.get("EMA20", 0) or 0)
        rsi = float(latest.get("RSI", 50) or 50)
        macd = latest.get("MACD")
        macd_signal = latest.get("MACD_SIGNAL")

        if atr_pct > 0.45:

            return "HIGH_VOLATILITY"

        if atr_pct < 0.18:

            return "LOW_VOLATILITY"

        macd_bullish = (
            pd.isna(macd_signal)
            or macd > macd_signal
        )
        macd_bearish = (
            pd.isna(macd_signal)
            or macd < macd_signal
        )

        if (
            ema9 > ema20
            and close > vwap
            and rsi >= 55
            and macd_bullish
        ):

            return "TRENDING_BULL"

        if (
            ema9 < ema20
            and close < vwap
            and rsi <= 45
            and macd_bearish
        ):

            return "TRENDING_BEAR"

        return "RANGE_BOUND"

    except Exception:

        return "UNKNOWN"


def _sector_group(symbol):

    sector_map = {
        "NVDA": "SEMIS",
        "AMD": "SEMIS",
        "AVGO": "SEMIS",
        "MU": "SEMIS",
        "SMCI": "SEMIS",
        "AAPL": "MEGA_TECH",
        "MSFT": "MEGA_TECH",
        "AMZN": "MEGA_TECH",
        "META": "MEGA_TECH",
        "GOOGL": "MEGA_TECH",
        "CRWD": "SOFTWARE",
        "PLTR": "SOFTWARE",
        "NFLX": "CONSUMER_TECH",
        "TSLA": "CONSUMER_TECH",
        "QQQ": "MARKET",
        "SPY": "MARKET",
        "SPCX": "CONSUMER_TECH",
        "SMH": "SEMIS",
        "ARM": "SEMIS",
        "TSM": "SEMIS",
        "INTC": "SEMIS",
        "AMAT": "SEMIS",
        "LRCX": "SEMIS",
        "MRVL": "SEMIS",
        "ORCL": "SOFTWARE",
        "PANW": "SOFTWARE",
        "SOXL": "SEMIS"
    }

    return sector_map.get(
        symbol,
        "OTHER"
    )


def _sector_reference_symbol(sector_group):

    if sector_group == "SEMIS":

        return "SMH"

    if sector_group in [
        "MEGA_TECH",
        "SOFTWARE"
    ]:

        return "XLK"

    return "QQQ"


def _fetch_reference_context():

    references = {}

    for display_symbol in MARKET_REFERENCE_SYMBOLS:

        fetch_symbol = REFERENCE_FETCH_SYMBOLS.get(
            display_symbol,
            display_symbol
        )

        try:

            raw_df = get_polygon_data(
                fetch_symbol,
                5,
                "minute",
                1
            )

            if raw_df.empty:

                references[display_symbol] = {
                    "status": "NO_DATA",
                    "move_pct": None,
                    "regime": "UNKNOWN"
                }
                continue

            df_15m = compute_indicators(
                resample_timeframe(raw_df, "15m"),
                interval="15m",
                symbol=display_symbol
            )

            references[display_symbol] = {
                "status": "OK",
                "move_pct": _safe_metric(
                    df_15m,
                    "SYMBOL_MOVE_PCT"
                ),
                "relative_volume": _safe_metric(
                    df_15m,
                    "REL_VOLUME"
                ),
                "atr_pct": _safe_metric(
                    df_15m,
                    "ATR_PCT"
                ),
                "above_vwap": _latest_bool(
                    df_15m,
                    "Close",
                    "VWAP"
                ),
                "above_ema20": _latest_bool(
                    df_15m,
                    "Close",
                    "EMA20"
                ),
                "regime": _classify_explicit_regime(
                    df_15m
                )
            }

        except Exception as e:

            references[display_symbol] = {
                "status": f"ERROR: {e}",
                "move_pct": None,
                "regime": "UNKNOWN"
            }

    reference_regime = "UNKNOWN"

    qqq_like = [
        references.get("XLK", {}).get("regime"),
        references.get("SMH", {}).get("regime"),
        references.get("SOXX", {}).get("regime")
    ]

    if qqq_like.count("TRENDING_BULL") >= 2:

        reference_regime = "TRENDING_BULL"

    elif qqq_like.count("TRENDING_BEAR") >= 2:

        reference_regime = "TRENDING_BEAR"

    elif "HIGH_VOLATILITY" in qqq_like:

        reference_regime = "HIGH_VOLATILITY"

    elif "LOW_VOLATILITY" in qqq_like:

        reference_regime = "LOW_VOLATILITY"
    elif any(regime == "RANGE_BOUND" for regime in qqq_like):

        reference_regime = "RANGE_BOUND"

    return {
        "references": references,
        "reference_regime": reference_regime
    }


def _latest_bool(df, left_column, right_column):

    try:

        if df is None or df.empty:

            return None

        left = float(df[left_column].iloc[-1])
        right = float(df[right_column].iloc[-1])

        return left > right

    except Exception:

        return None


def _sector_strength(symbol, symbol_move_pct, reference_context):

    sector = _sector_group(symbol)
    reference_symbol = _sector_reference_symbol(sector)
    reference = reference_context.get(
        "references",
        {}
    ).get(
        reference_symbol,
        {}
    )
    reference_move = reference.get("move_pct")

    sector_rs = None

    try:

        if symbol_move_pct is not None and reference_move is not None:

            sector_rs = round(
                float(symbol_move_pct) - float(reference_move),
                2
            )

    except Exception:

        sector_rs = None

    if sector_rs is None:

        label = "UNKNOWN"

    elif sector_rs >= 0.75:

        label = "LEADING"

    elif sector_rs <= -0.75:

        label = "LAGGING"

    else:

        label = "NEUTRAL"

    return {
        "sector_group": sector,
        "sector_reference": reference_symbol,
        "sector_reference_move_pct": reference_move,
        "sector_rs": sector_rs,
        "sector_strength": label
    }


def _evaluate_regime_setup_block(entry_type, final_signal, market_regime):

    entry_type = str(entry_type or "NO_ENTRY")
    final_signal = str(final_signal or "NEUTRAL")

    if market_regime in [
        "HIGH_VOLATILITY",
        "UNKNOWN"
    ]:

        return {
            "blocked": False,
            "reason": None
        }

    bullish_setups = [
        "BREAKOUT",
        "BREAKOUT_LONG",
        "EMA_PULLBACK",
        "VWAP_RECLAIM",
        "COILED_BREAKOUT"
    ]
    bearish_setups = [
        "BREAKDOWN_SHORT",
        "EMA_REJECTION_SHORT",
        "VWAP_REJECTION",
        "COILED_BREAKDOWN"
    ]

    if entry_type in bullish_setups and market_regime == "TRENDING_BEAR":

        return {
            "blocked": True,
            "reason": "Bullish setup blocked in TRENDING_BEAR regime"
        }

    if entry_type in bearish_setups and market_regime == "TRENDING_BULL":

        return {
            "blocked": True,
            "reason": "Bearish setup blocked in TRENDING_BULL regime"
        }

    if entry_type in [
        "VWAP_RECLAIM",
        "BREAKOUT",
        "BREAKOUT_LONG"
    ] and market_regime not in [
        "TRENDING_BULL",
        "HIGH_VOLATILITY"
    ]:

        return {
            "blocked": True,
            "reason": f"{entry_type} blocked in {market_regime} regime"
        }

    if entry_type in [
        "BREAKDOWN_SHORT",
        "VWAP_REJECTION"
    ] and market_regime not in [
        "TRENDING_BEAR",
        "HIGH_VOLATILITY"
    ]:

        return {
            "blocked": True,
            "reason": f"{entry_type} blocked in {market_regime} regime"
        }

    if "BULLISH" in final_signal and market_regime == "LOW_VOLATILITY":

        return {
            "blocked": True,
            "reason": "Bullish momentum setup blocked in LOW_VOLATILITY regime"
        }

    if "BEARISH" in final_signal and market_regime == "LOW_VOLATILITY":

        return {
            "blocked": True,
            "reason": "Bearish momentum setup blocked in LOW_VOLATILITY regime"
        }

    return {
        "blocked": False,
        "reason": None
    }


def _apply_breadth_and_leaderboards(df_results):

    if df_results.empty:

        return df_results

    df_results = df_results.copy()
    moves = pd.to_numeric(
        df_results.get("Symbol Move %"),
        errors="coerce"
    )
    advancers = int((moves > 0).sum())
    decliners = int((moves < 0).sum())
    total_with_moves = int(moves.notna().sum())

    above_vwap = pd.Series(
        df_results.get("Above VWAP"),
        dtype="object"
    )
    above_ema20 = pd.Series(
        df_results.get("Above EMA20"),
        dtype="object"
    )

    if total_with_moves:

        breadth_score = round(
            ((advancers - decliners) / total_with_moves) * 100,
            2
        )

    else:

        breadth_score = None

    if above_vwap.notna().sum():

        above_vwap_pct = round(
            (above_vwap == True).sum()
            / above_vwap.notna().sum()
            * 100,
            2
        )
    else:

        above_vwap_pct = None

    if above_ema20.notna().sum():

        above_ema20_pct = round(
            (above_ema20 == True).sum()
            / above_ema20.notna().sum()
            * 100,
            2
        )
    else:

        above_ema20_pct = None

    rankable = df_results[
        moves.notna()
        &
        pd.to_numeric(
            df_results.get("RS Rank Score"),
            errors="coerce"
        ).notna()
    ]

    strongest = rankable.sort_values(
        by="RS Rank Score",
        ascending=False,
        na_position="last"
    ).head(5)

    weakest = rankable.sort_values(
        by="RS Rank Score",
        ascending=True,
        na_position="last"
    ).head(5)

    df_results["Strength Rank"] = None
    df_results["Weakness Rank"] = None

    for rank, index in enumerate(strongest.index, start=1):

        df_results.at[index, "Strength Rank"] = rank

    for rank, index in enumerate(weakest.index, start=1):

        df_results.at[index, "Weakness Rank"] = rank

    df_results["Top 5 Strongest"] = ", ".join(
        strongest["Symbol"].dropna().astype(str).tolist()
    ) if not strongest.empty else None
    df_results["Top 5 Weakest"] = ", ".join(
        weakest["Symbol"].dropna().astype(str).tolist()
    ) if not weakest.empty else None
    df_results["Watchlist Advancers"] = advancers
    df_results["Watchlist Decliners"] = decliners
    df_results["Watchlist Breadth Score"] = breadth_score
    df_results["Above VWAP %"] = above_vwap_pct
    df_results["Above EMA20 %"] = above_ema20_pct

    return df_results


def _add_relative_strength_rankings(df_results):

    if df_results.empty:

        return df_results

    df_results = df_results.copy()

    qqq_move = 0
    spy_move = 0

    try:

        qqq_move = float(
            df_results.loc[
                df_results["Symbol"] == "QQQ",
                "Symbol Move %"
            ].iloc[0]
        )

    except Exception:

        qqq_move = 0

    try:

        spy_move = float(
            df_results.loc[
                df_results["Symbol"] == "SPY",
                "Symbol Move %"
            ].iloc[0]
        )

    except Exception:

        spy_move = 0

    symbol_moves = pd.to_numeric(
        df_results.get("Symbol Move %"),
        errors="coerce"
    )

    df_results["RS vs QQQ"] = (
        symbol_moves - qqq_move
    ).round(2)

    df_results["RS vs SPY"] = (
        symbol_moves - spy_move
    ).round(2)

    rs_rank_score = (
        pd.to_numeric(
            df_results["RS vs QQQ"],
            errors="coerce"
        ).fillna(0)
        +
        pd.to_numeric(
            df_results["RS vs SPY"],
            errors="coerce"
        ).fillna(0)
    ) / 2

    df_results["RS Rank Score"] = rs_rank_score.where(
        symbol_moves.notna()
    ).round(2)
    df_results["Bullish Rank"] = None
    df_results["Bearish Rank"] = None
    df_results["Top Candidate"] = None

    tradable = df_results[
        df_results["Symbol"].notna()
    ].copy()

    bullish = tradable[
        tradable["Final Signal"].astype(str).str.contains(
            "BULLISH",
            case=False,
            na=False
        )
    ].sort_values(
        by=[
            "RS Rank Score",
            "Setup Valid",
            "Risk Reward",
            "15m Score"
        ],
        ascending=[False, False, False, False]
    ).head(3)

    bearish = tradable[
        tradable["Final Signal"].astype(str).str.contains(
            "BEARISH",
            case=False,
            na=False
        )
    ].sort_values(
        by=[
            "RS Rank Score",
            "Setup Valid",
            "Risk Reward",
            "15m Score"
        ],
        ascending=[True, False, False, True]
    ).head(3)

    for rank, index in enumerate(bullish.index, start=1):

        df_results.at[index, "Bullish Rank"] = rank
        df_results.at[index, "Top Candidate"] = f"BULLISH_TOP_{rank}"

    for rank, index in enumerate(bearish.index, start=1):

        df_results.at[index, "Bearish Rank"] = rank
        df_results.at[index, "Top Candidate"] = f"BEARISH_TOP_{rank}"

    return _apply_breadth_and_leaderboards(
        df_results
    )

def timeframe_bias(result):

    signal = result["signal"]

    if signal == "HIGH CONVICTION BULLISH":
        return 2

    elif signal == "BULLISH":
        return 1

    elif signal == "NEUTRAL":
        return 0

    elif signal == "HIGH CONVICTION BEARISH":
        return -2

    elif signal in [
        "WEAK/BEARISH",
        "BEARISH"
    ]:
        return -1

    return 0


def get_market_data_status(df):

    current_et = datetime.now(
        ZoneInfo("America/New_York")
    )

    market_session = get_market_session(
        current_et
    )

    if df is None or df.empty:

        return {
            "data_timestamp_et": None,
            "current_et": current_et,
            "delay_minutes": None,
            "raw_delay_minutes": None,
            "aggregate_interval_minutes": None,
            "stock_data_freshness": "NO_DATA",
            "market_session": market_session,
            "market_closed": (
                market_session == "CLOSED"
            )
        }

    latest_timestamp = df.index[-1]

    if latest_timestamp.tzinfo is None:

        latest_timestamp = latest_timestamp.tz_localize(
            "UTC"
        )

    data_timestamp_et = latest_timestamp.tz_convert(
        "America/New_York"
    )

    raw_delay_minutes = (
        current_et - data_timestamp_et
    ).total_seconds() / 60

    aggregate_interval_minutes = infer_aggregate_interval_minutes(
        df
    )

    delay_minutes = max(
        0,
        raw_delay_minutes - aggregate_interval_minutes
    )

    return {
        "data_timestamp_et": data_timestamp_et,
        "current_et": current_et,
        "delay_minutes": round(delay_minutes, 2),
        "raw_delay_minutes": round(raw_delay_minutes, 2),
        "aggregate_interval_minutes": aggregate_interval_minutes,
        "stock_data_freshness": (
            "LIVE"
            if delay_minutes <= settings.max_stock_data_delay_minutes
            else "STALE"
        ),
        "market_session": market_session,
        "market_closed": (
            market_session == "CLOSED"
        )
    }


def format_timestamp(value):

    if value is None:

        return None

    return value.strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )


def build_status_result_row(
    symbol,
    final_signal,
    action_status,
    explanation,
    next_condition,
    market_data_status=None,
    blocked_by=None
):

    market_data_status = market_data_status or {}

    return {
        "Symbol": symbol,
        "Price": "-",
        "Final Signal": final_signal,
        "15m Score": None,
        "Alignment Score": None,
        "Symbol Move %": None,
        "RS vs QQQ": None,
        "RS vs SPY": None,
        "RS Rank Score": None,
        "Bullish Rank": None,
        "Bearish Rank": None,
        "Top Candidate": None,
        "Premarket Gap %": None,
        "Relative Volume": None,
        "ATR %": None,
        "Market Regime": "UNKNOWN",
        "Reference Regime": "UNKNOWN",
        "Regime Blocked": False,
        "Regime Block Reason": None,
        "Sector Group": None,
        "Sector Reference": None,
        "Sector Reference Move %": None,
        "Sector RS": None,
        "Sector Strength": "UNKNOWN",
        "Above VWAP": None,
        "Above EMA20": None,
        "Reasons": explanation,
        "Entry": "NO_ENTRY",
        "Entry Quality": "NONE",
        "Entry Trigger": None,
        "Risk Reward": None,
        "Stop Loss": None,
        "Take Profit": None,
        "Trade Allowed": False,
        "Setup Valid": False,
        "Execution Ready": False,
        "Blocked By": blocked_by or action_status,
        "Candidate Direction": "NONE",
        "Candidate Entry Price": None,
        "Candidate Stop Price": None,
        "Candidate Target Price": None,
        "Candidate RR": None,
        "Candidate Trigger": None,
        "Live Chart Checklist": "Need fresh market data",
        "Planned Invalidation": "No planned trade",
        "Profit Taking Rule": "No planned trade",
        "Do Not Enter Reason": blocked_by or action_status,
        "Action Status": action_status,
        "Action Reason": explanation,
        "Explanation": explanation,
        "Next Condition": next_condition,
        "Market Data Delay Minutes": market_data_status.get(
            "delay_minutes"
        ),
        "Stock Data Freshness": market_data_status.get(
            "stock_data_freshness"
        ),
        "Stock Data Age Minutes": market_data_status.get(
            "delay_minutes"
        ),
        "Data Timestamp ET": format_timestamp(
            market_data_status.get("data_timestamp_et")
        ),
        "Current ET": format_timestamp(
            market_data_status.get("current_et")
        ),
        "Realtime Confirmation Needed": False,
        "TradingView Check Status": "NOT_REQUIRED",
        "Rejected Trade Reason": explanation,
        "Data Delay Warning": None,
        "Live Exit Signal": False,
        "Live Exit Reason": "No active trade",
        "Replay Ran": False,
        "Replay Outcome": "NO_REPLAY",
        "Option Quote Freshness": None,
        "Option Quote Age Minutes": None,
        "Option Quote Status": None,
        "Option Rejection Reason": None,
        "Event Blocked": False,
        "Event Block Reason": None,
        "Realtime Ready": False,
        "Realtime Block Reason": blocked_by or action_status,
        "Trade Action": "NO_ACTIVE_TRADE",
        "RR Progress": 0,
        "Updated Stop": "-",
        "Trailing Stop": None,
        "Bars In Trade": 0,
        "Partial Profit Taken": False,
        "Adjustment Reason": explanation
    }


def build_action_decision(
    final_signal,
    entry_setup,
    risk_setup,
    risk_passed_before_options,
    projection,
    option_quote_status,
    option_rejection_reason,
    market_data_status
):

    quote_blocking_statuses = [
        "NO_BID_ASK",
        "OPTION_MARKET_CLOSED",
        "RATE_LIMITED",
        "PROVIDER_ERROR",
        "NO_QUOTE_SNAPSHOT",
        "DELAYED_QUOTE",
        "STALE_QUOTE",
        "QUOTE_UNAVAILABLE"
    ]

    affordability_blocking_statuses = [
        "OPTION_TOO_EXPENSIVE",
        "TOO_CHEAP_LOW_QUALITY_RISK",
        "DELTA_TOO_LOW_FOR_AFFORDABLE_TRADE",
        "NO_OPTION_PRICE"
    ]

    delay_minutes = market_data_status.get(
        "delay_minutes"
    )

    market_session = market_data_status.get(
        "market_session"
    )

    realtime_confirmation_needed = (
        delay_minutes is not None
        and delay_minutes >= 10
        and final_signal != "NEUTRAL"
    )

    if (
        settings.realtime_market_data_required
        and delay_minutes is not None
        and delay_minutes > settings.max_stock_data_delay_minutes
        and final_signal != "NEUTRAL"
    ):

        return {
            "action_status": "STALE_STOCK_DATA",
            "action_reason": "Stock aggregate data is not real-time",
            "realtime_confirmation_needed": True,
            "tradingview_check_status": "REQUIRED"
        }

    tradingview_check_status = (
        "REQUIRED"
        if realtime_confirmation_needed
        else "NOT_REQUIRED"
    )

    if market_data_status.get("market_closed"):

        return {
            "action_status": "NO_TRADE_MARKET_CLOSED",
            "action_reason": "Market closed; no live intraday trade",
            "realtime_confirmation_needed": realtime_confirmation_needed,
            "tradingview_check_status": tradingview_check_status
        }

    if final_signal == "NEUTRAL":

        return {
            "action_status": "WAIT",
            "action_reason": "No directional edge",
            "realtime_confirmation_needed": False,
            "tradingview_check_status": "NOT_REQUIRED"
        }

    if not entry_setup or entry_setup.get("entry_type") == "NO_ENTRY":

        return {
            "action_status": "WAIT",
            "action_reason": "No actionable entry trigger",
            "realtime_confirmation_needed": realtime_confirmation_needed,
            "tradingview_check_status": tradingview_check_status
        }

    if not risk_passed_before_options:

        if risk_setup.get("risk_reward", 0) < 1.5:

            reason = "Risk failed: RR below threshold"

        else:

            reason = "; ".join(
                risk_setup.get("reasons", [])
            ) or "Risk failed"

        return {
            "action_status": "AVOID",
            "action_reason": reason,
            "realtime_confirmation_needed": realtime_confirmation_needed,
            "tradingview_check_status": tradingview_check_status
        }

    if market_session == "PREMARKET":

        return {
            "action_status": "PREMARKET_WATCH",
            "action_reason": (
                "Premarket candidate; wait for regular-market confirmation"
            ),
            "realtime_confirmation_needed": False,
            "tradingview_check_status": "NOT_REQUIRED"
        }

    if market_session == "OPENING_RANGE":

        return {
            "action_status": "OPENING_RANGE_CONFIRMATION",
            "action_reason": (
                "Opening range forming; wait until after 9:45 ET"
            ),
            "realtime_confirmation_needed": True,
            "tradingview_check_status": "REQUIRED"
        }

    if realtime_confirmation_needed:

        return {
            "action_status": "REVIEW_TV_CHART",
            "action_reason": (
                "Polygon data delayed; confirm live chart before execution"
            ),
            "realtime_confirmation_needed": True,
            "tradingview_check_status": "REQUIRED"
        }

    if option_quote_status in quote_blocking_statuses:

        return {
            "action_status": option_quote_status,
            "action_reason": (
                option_rejection_reason
                or "Risk passed, option quote unavailable"
            ),
            "realtime_confirmation_needed": realtime_confirmation_needed,
            "tradingview_check_status": tradingview_check_status
        }

    if option_quote_status in affordability_blocking_statuses:

        if option_quote_status == "OPTION_TOO_EXPENSIVE":

            return {
                "action_status": "QUALITY_BUT_TOO_EXPENSIVE",
                "action_reason": (
                    option_rejection_reason
                    or "High-quality option exceeds capital profile"
                ),
                "realtime_confirmation_needed": realtime_confirmation_needed,
                "tradingview_check_status": tradingview_check_status
            }

        return {
            "action_status": "NO_TRADE_LOW_OPTION_QUALITY",
            "action_reason": (
                option_rejection_reason
                or "No affordable option passed quality and delta gates"
            ),
            "realtime_confirmation_needed": realtime_confirmation_needed,
            "tradingview_check_status": tradingview_check_status
        }

    if option_rejection_reason:

        return {
            "action_status": "AVOID",
            "action_reason": option_rejection_reason,
            "realtime_confirmation_needed": realtime_confirmation_needed,
            "tradingview_check_status": tradingview_check_status
        }

    if risk_setup.get("trade_allowed") and projection:

        action_status = (
            "ENTER_PAPER"
            if (
                settings.realtime_market_data_required
                or settings.realtime_options_required
            )
            else "ENTER"
        )

        return {
            "action_status": action_status,
            "action_reason": "Risk, option, and timing checks passed",
            "realtime_confirmation_needed": False,
            "tradingview_check_status": "NOT_REQUIRED"
        }

    return {
        "action_status": "WAIT",
        "action_reason": "Setup not actionable yet",
        "realtime_confirmation_needed": realtime_confirmation_needed,
        "tradingview_check_status": tradingview_check_status
    }


def _format_level(value):

    try:

        return f"{float(value):.2f}"

    except Exception:

        return "-"


def _compact_entry_type(entry_type):

    compact_map = {
        "NO_ENTRY": "NONE",
        "NO_SETUP": "NONE",
        "EMA_PULLBACK": "EMA_PB",
        "EMA_REJECTION_SHORT": "EMA_REJ",
        "BREAKOUT": "BRKOUT",
        "BREAKOUT_LONG": "BRKOUT",
        "BREAKDOWN_SHORT": "BRKDN",
        "VWAP_REJECTION": "VWAP_REJ",
        "ACTIVE_TRADE": "ACTIVE"
    }

    return compact_map.get(
        entry_type,
        entry_type or "NONE"
    )


def build_explanation_and_hint(
    final_signal,
    entry_setup,
    risk_setup,
    analysis_15m,
    df_15m,
    action_decision
):

    action_status = action_decision.get(
        "action_status",
        "WAIT"
    )

    action_reason = action_decision.get(
        "action_reason",
        "Setup not actionable yet"
    )

    quote_blocking_statuses = [
        "NO_BID_ASK",
        "OPTION_MARKET_CLOSED",
        "RATE_LIMITED",
        "PROVIDER_ERROR",
        "NO_QUOTE_SNAPSHOT",
        "DELAYED_QUOTE",
        "STALE_QUOTE",
        "QUOTE_UNAVAILABLE"
    ]

    if action_status in [
        "ENTER",
        "REVIEW_TV_CHART",
        "QUOTE_UNAVAILABLE"
    ] or action_status in quote_blocking_statuses:

        if action_status == "REVIEW_TV_CHART":

            return (
                "Delayed feed",
                "Confirm live chart"
            )

        if action_status in quote_blocking_statuses:

            return (
                "Quote blocked",
                "Check live chain"
            )

        return (
            action_reason,
            "Confirm candle/spread"
        )

    if final_signal == "NEUTRAL":

        return (
            "No edge",
            "Need MTF"
        )

    entry_type = (
        entry_setup.get("entry_type")
        if entry_setup
        else "NO_ENTRY"
    )

    entry_reasons = (
        entry_setup.get("reasons", [])
        if entry_setup
        else []
    )

    if (
        not entry_setup
        or entry_type in ["NO_ENTRY", "NO_SETUP"]
    ):

        explanation = (
            "; ".join(entry_reasons[:2])
            if entry_reasons
            else "No trigger"
        )

        if len(explanation) > 18:

            explanation = "No trigger"

        if df_15m is None or df_15m.empty:

            return (
                explanation,
                "Need 15m candles"
            )

        latest = df_15m.iloc[-1]

        recent_high = (
            df_15m["High"]
            .shift(1)
            .tail(5)
            .max()
        )

        recent_low = (
            df_15m["Low"]
            .shift(1)
            .tail(5)
            .min()
        )

        if "BULLISH" in final_signal:

            if latest["EMA9"] <= latest["EMA20"]:

                hint = (
                    "Need EMA9 > EMA20"
                )

            elif latest["Close"] <= latest["EMA9"]:

                hint = (
                    f"Reclaim EMA9 "
                    f"{_format_level(latest['EMA9'])}"
                )

            elif latest["Low"] > latest["EMA9"]:

                hint = (
                    f"EMA9/> {_format_level(recent_high)}"
                )

            else:

                hint = (
                    f"> {_format_level(recent_high)} + vol"
                )

        elif "BEARISH" in final_signal:

            if latest["EMA9"] >= latest["EMA20"]:

                hint = (
                    "Need EMA9 < EMA20"
                )

            elif latest["Close"] >= latest["EMA9"]:

                hint = (
                    f"Reject EMA9 "
                    f"{_format_level(latest['EMA9'])}"
                )

            else:

                hint = (
                    f"< {_format_level(recent_low)}"
                )

        else:

            hint = "Need clean direction"

        return explanation, hint

    if not risk_setup.get("trade_allowed", False):

        risk_reasons = risk_setup.get("reasons", [])

        explanation = (
            "; ".join(risk_reasons[:2])
            if risk_reasons
            else action_reason
        )

        return (
            explanation,
            "Need RR >= 1.5"
        )

    return (
        action_reason,
        "Wait for confirm"
    )


def build_candidate_trade_plan(
    final_signal,
    entry_setup,
    risk_setup,
    action_decision,
    risk_passed_before_options,
    option_quote_status,
    option_rejection_reason
):

    entry_type = (
        entry_setup.get("entry_type")
        if entry_setup
        else "NO_ENTRY"
    )

    setup_valid = (
        final_signal != "NEUTRAL"
        and entry_type not in ["NO_ENTRY", "NO_SETUP"]
        and risk_passed_before_options
        and risk_setup.get("risk_reward", 0) >= 1.5
        and risk_setup.get("entry_price") is not None
        and risk_setup.get("stop_loss") is not None
        and risk_setup.get("take_profit") is not None
    )

    raw_direction = resolve_option_direction(
        final_signal,
        entry_type
    )

    direction = (
        raw_direction
        if setup_valid
        else "NONE"
    )

    execution_ready = (
        action_decision.get("action_status") == "ENTER"
    )

    blocked_by = None

    if not setup_valid:

        if final_signal == "NEUTRAL":

            blocked_by = "NO_DIRECTIONAL_EDGE"

        elif entry_type in ["NO_ENTRY", "NO_SETUP"]:

            blocked_by = "NO_ENTRY_TRIGGER"

        elif risk_setup.get("risk_reward", 0) < 1.5:

            blocked_by = "LOW_RR"

        elif not risk_passed_before_options:

            risk_reasons = " ".join(
                risk_setup.get("reasons", [])
            ).lower()

            if "timing" in risk_reasons:

                blocked_by = "TIMING_NOT_CONFIRMED"

            else:

                blocked_by = "RISK_REJECTED"

        else:

            blocked_by = "INCOMPLETE_RISK_PLAN"

    elif action_decision.get("realtime_confirmation_needed"):

        blocked_by = "DELAYED_DATA_CONFIRM_LIVE_CHART"

    elif option_quote_status == "QUOTE_UNAVAILABLE":

        blocked_by = "OPTION_QUOTE_UNAVAILABLE"

    elif option_quote_status in [
        "DELAYED_QUOTE",
        "STALE_QUOTE"
    ]:

        blocked_by = option_quote_status

    elif option_rejection_reason:

        blocked_by = "OPTION_REJECTED"

    elif not execution_ready:

        blocked_by = action_decision.get(
            "action_status",
            "WAIT"
        )

    if direction == "CALL":

        checklist = (
            "Price above VWAP; EMA9 above EMA20; "
            "close/reclaim EMA9; avoid red rejection"
        )

        invalidation = (
            "Exit if price loses EMA9/VWAP or stop is hit"
        )

    elif direction == "PUT":

        checklist = (
            "Price below VWAP; reject EMA9/VWAP; "
            "lower high intact; avoid shorting into support"
        )

        invalidation = (
            "Exit if price reclaims EMA9/VWAP or stop is hit"
        )

    else:

        checklist = "Wait for clean direction and entry trigger"

        invalidation = "No planned trade"

    do_not_enter_reason = None

    if not execution_ready:

        do_not_enter_reason = blocked_by

    return {
        "setup_valid": setup_valid,
        "execution_ready": execution_ready,
        "blocked_by": blocked_by,
        "candidate_direction": direction,
        "candidate_entry_price": (
            risk_setup.get("entry_price")
            if setup_valid
            else None
        ),
        "candidate_stop_price": (
            risk_setup.get("stop_loss")
            if setup_valid
            else None
        ),
        "candidate_target_price": (
            risk_setup.get("take_profit")
            if setup_valid
            else None
        ),
        "candidate_rr": (
            risk_setup.get("risk_reward")
            if setup_valid
            else None
        ),
        "candidate_trigger": (
            entry_setup.get("entry_trigger")
            if entry_setup and setup_valid
            else None
        ),
        "live_chart_checklist": checklist,
        "planned_invalidation": invalidation,
        "profit_taking_rule": (
            "Take partial near 1R; trail remainder toward target"
            if setup_valid
            else "No planned trade"
        ),
        "do_not_enter_reason": do_not_enter_reason
    }


def _iter_option_bundle_candidates(option_bundle):

    if not option_bundle:

        return

    for label in [
        "active",
        "primary",
        "affordable",
        "short_dte",
        "longer_dte"
    ]:

        yield label, option_bundle.get(label)

    for index, contract in enumerate(
        option_bundle.get("ranked") or [],
        start=1
    ):

        yield f"ranked #{index}", contract


def _select_liquid_option_from_bundle(option_bundle, intended_option_direction):

    attempts = []

    if not option_bundle:

        return None, None, attempts

    affordability_config = get_affordability_config()
    seen_tickers = set()

    for source, contract in _iter_option_bundle_candidates(option_bundle):

        if not contract:

            continue

        ticker = contract.get("ticker")
        dedupe_key = ticker or id(contract)

        if dedupe_key in seen_tickers:

            continue

        seen_tickers.add(dedupe_key)
        print(
            f"[LIQUIDITY FALLBACK] Try {source} contract "
            f"{ticker or 'UNKNOWN'}"
        )

        candidate = add_affordability_metrics(
            refresh_contract_quote(dict(contract)),
            config=affordability_config
        )
        direction_match = contract_matches_direction(
            candidate,
            intended_option_direction
        )

        if not direction_match:

            attempt = {
                "source": source,
                "ticker": ticker,
                "liquid": False,
                "code": "DIRECTION_MISMATCH",
                "reason": "Option contract type does not match setup direction",
                "spread_pct": candidate.get("spread_pct"),
                "accepted": False
            }
            attempts.append(attempt)
            print(
                f"[LIQUIDITY FALLBACK] {source} failed: "
                f"{attempt['reason']}"
            )
            continue

        liquidity = evaluate_option_liquidity(candidate)
        attempt = {
            "source": source,
            "ticker": candidate.get("ticker"),
            "liquid": liquidity.get("liquid"),
            "code": liquidity.get("code"),
            "reason": liquidity.get("reason"),
            "spread_pct": liquidity.get("spread_pct"),
            "accepted": False
        }
        attempts.append(attempt)

        if liquidity.get("liquid"):

            attempt["accepted"] = True
            candidate["liquidity_attempts"] = list(attempts)
            print(
                f"[LIQUIDITY FALLBACK] Accepted {source} contract "
                f"{candidate.get('ticker') or 'UNKNOWN'}"
            )
            return candidate, liquidity, attempts

        print(
            f"[LIQUIDITY FALLBACK] {source} liquidity failed: "
            f"{liquidity.get('reason')}"
        )

    return None, None, attempts


def _row_bool(value):

    if value is None:

        return False

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in [
        "true",
        "1",
        "yes"
    ]


def _row_float(row, column, default=0):

    try:

        value = row.get(column)

        if pd.isna(value):

            return default

        return float(value)

    except Exception:

        return default


def _dispatch_telegram_entry_alerts(df_results):

    summary = {
        "enter_paper_count": 0,
        "attempted_count": 0,
        "sent_count": 0,
        "blocked_count": 0,
        "error_count": 0,
        "reasons": {},
        "alerts": []
    }

    if df_results.empty:

        return summary

    if "Action Status" in df_results.columns:

        summary["enter_paper_count"] = int(
            df_results["Action Status"].astype(str).str.upper().eq("ENTER_PAPER").sum()
        )

    rows = []

    for _, row in df_results.iterrows():

        option_quality_score = _row_float(
            row,
            "Option Quality Score"
        )
        option_spread_pct = _row_float(
            row,
            "Option Spread %",
            None
        )
        risk_reward = _row_float(
            row,
            "Risk Reward"
        )
        alert_score = calculate_entry_alert_score(
            setup_score=_row_float(row, "15m Score"),
            alignment_score=_row_float(row, "Alignment Score"),
            rs_rank_score=_row_float(row, "RS Rank Score"),
            option_quality_score=option_quality_score,
            risk_reward=risk_reward,
            relative_volume=_row_float(row, "Relative Volume"),
            option_spread_pct=option_spread_pct
        )
        rows.append(
            (
                alert_score,
                row
            )
        )

    for alert_score, row in sorted(
        rows,
        key=lambda item: item[0],
        reverse=True
    ):

        option_contract = {
            "ticker": row.get("Option Ticker"),
            "type": row.get("Candidate Direction"),
            "contract_cost": row.get("Option Contract Cost"),
            "risk_at_stop": row.get("Option Risk At Stop"),
            "affordability_status": row.get("Affordability Status"),
            "affordable": row.get("Affordable"),
            "spread_pct": row.get("Option Spread %"),
            "option_quality_score": row.get("Option Quality Score"),
            "quote_freshness": row.get("Option Quote Freshness")
        }

        try:

            telegram_result = maybe_send_scanner_entry_alert(
                symbol=row.get("Symbol"),
                final_signal=row.get("Final Signal"),
                action_decision={
                    "action_status": row.get("Action Status")
                },
                entry_setup={
                    "entry_type": row.get("Entry")
                },
                risk_setup={
                    "risk_reward": row.get("Risk Reward")
                },
                option_contract=option_contract,
                latest_price=row.get("Price"),
                bar_timestamp=(
                    row.get("Data Timestamp ET")
                    or row.get("Current ET")
                ),
                next_condition=row.get("Next Condition"),
                top_candidate=row.get("Top Candidate"),
                option_quote_freshness=row.get("Option Quote Freshness"),
                option_quality_score=row.get("Option Quality Score"),
                option_spread_pct=row.get("Option Spread %"),
                event_blocked=_row_bool(row.get("Event Blocked")),
                regime_blocked=_row_bool(row.get("Regime Blocked")),
                setup_score=row.get("15m Score"),
                alignment_score=row.get("Alignment Score"),
                rs_rank_score=row.get("RS Rank Score"),
                relative_volume=row.get("Relative Volume")
            )
            debug_print(
                f"[TELEGRAM ENTRY ALERT] {row.get('Symbol')} "
                f"score={alert_score} "
                f"sent={telegram_result.get('sent')} "
                f"reason={telegram_result.get('reason')}"
            )
            reason = telegram_result.get("reason") or "UNKNOWN"
            sent = bool(telegram_result.get("sent"))
            summary["attempted_count"] += 1
            summary["sent_count"] += int(sent)
            summary["blocked_count"] += int(not sent)
            summary["reasons"][reason] = summary["reasons"].get(reason, 0) + 1
            summary["alerts"].append({
                "symbol": row.get("Symbol"),
                "action_status": row.get("Action Status"),
                "sent": sent,
                "reason": reason,
                "alert_score": alert_score,
                "option_ticker": row.get("Option Ticker")
            })

        except Exception as e:

            print(
                f"[TELEGRAM ENTRY ALERT ERROR] "
                f"{row.get('Symbol')}: {e}"
            )
            summary["attempted_count"] += 1
            summary["blocked_count"] += 1
            summary["error_count"] += 1
            summary["reasons"]["ERROR"] = summary["reasons"].get("ERROR", 0) + 1
            summary["alerts"].append({
                "symbol": row.get("Symbol"),
                "action_status": row.get("Action Status"),
                "sent": False,
                "reason": "ERROR",
                "alert_score": alert_score,
                "option_ticker": row.get("Option Ticker"),
                "error": str(e)
            })

    return summary


def _write_scanner_output_files(df_results, trading_day, output_file):

    live_csv_path = live_path("scanner_output_latest.csv")
    daily_csv_path = daily_path(trading_day, "scanner_output_close.csv")
    live_csv_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )
    daily_csv_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )
    df_results.to_csv(
        live_csv_path,
        index=False
    )
    df_results.to_csv(
        daily_csv_path,
        index=False
    )

    try:

        with pd.ExcelWriter(

            output_file,
            engine="openpyxl"

        ) as writer:

            df_results.to_excel(

                writer,
                sheet_name="Scanner",
                index=False

            )

        for mirror_output_file in [
            live_path("scanner_output_latest.xlsx"),
            daily_path(trading_day, "scanner_output_close.xlsx")
        ]:

            with pd.ExcelWriter(
                mirror_output_file,
                engine="openpyxl"
            ) as mirror_writer:

                df_results.to_excel(
                    mirror_writer,
                    sheet_name="Scanner",
                    index=False
                )

    except PermissionError:

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        fallback_output_file = (
            f"scanner_output_{timestamp}.xlsx"
        )

        print(
            f"[REPORT WARNING] {output_file} is locked; "
            f"writing {fallback_output_file} instead"
        )

        output_file = fallback_output_file

        with pd.ExcelWriter(

            output_file,
            engine="openpyxl"

        ) as writer:

            df_results.to_excel(

                writer,
                sheet_name="Scanner",
                index=False

            )

    return output_file


def _persist_scan_outputs(
    df_results,
    trading_day,
    scan_id,
    health_payload,
    output_file,
    foreground_timings,
    observed_at
):

    profile_timer = StageTimer()

    for stage_name, seconds in (foreground_timings or {}).items():

        profile_timer.record(stage_name, seconds)

    snapshot_result = None
    records = df_results.to_dict("records")

    with profile_timer.stage("Engine Health"):

        append_engine_health_history(
            trading_day,
            health_payload
        )

    print(
        "[ENGINE HEALTH] "
        f"score={health_payload.get('health_score')} "
        f"runtime={health_payload.get('scan_runtime_sec')}s "
        f"workers={health_payload.get('worker_count')} "
        f"symbols={health_payload.get('symbols_completed')}/"
        f"{health_payload.get('polygon_calls')}"
    )

    with profile_timer.stage("Database gate_decisions"):

        record_gate_decisions(
            records,
            run_id=scan_id
        )

    with profile_timer.stage("Candidate snapshot"):

        snapshot_result = append_candidate_snapshots(
            df_results,
            trading_day=trading_day,
            scan_id=scan_id
        )

    try:

        with profile_timer.stage("Signal lifecycle"):

            lifecycle_count = record_signal_lifecycle_events_for_scan(
                records,
                trading_day=trading_day,
                scan_id=scan_id,
                observed_at=observed_at
            )

        print(
            f"[SIGNAL LIFECYCLE] recorded {lifecycle_count} observations"
        )

    except Exception as exc:

        print(
            "[SIGNAL LIFECYCLE WARNING] "
            f"failed to record lifecycle observations: {exc}"
        )

    if snapshot_result:

        print(
            "[CANDIDATE SNAPSHOTS] "
            f"saved {snapshot_result['rows']} rows to "
            f"{snapshot_result['path']}"
        )

    with profile_timer.stage("Excel scanner_output"):

        output_file = _write_scanner_output_files(
            df_results,
            trading_day,
            output_file
        )

    print(
        f"\nExcel report saved:"
        f" {output_file}"
    )

    with profile_timer.stage("Database scanner_run"):

        record_scanner_run_finish(
            scan_id,
            status="FINISHED",
            rows_count=len(df_results),
            payload={
                "trading_day": trading_day,
                "output_file": str(output_file),
                "snapshot_rows": (
                    snapshot_result or {}
                ).get("rows")
            }
        )

    profile_result = append_scanner_stage_profile(
        trading_day,
        scan_id,
        profile_timer,
        observed_at=observed_at.isoformat()
    )

    if profile_result:

        print(
            "[SCANNER PROFILE] "
            f"saved {profile_result['rows']} stages to "
            f"{profile_result['path']}"
        )


def run_scanner():   

    scan_perf_start = time.perf_counter()
    stage_timer = StageTimer()
    validate_runtime_settings()
    print_runtime_banner()
    print_db_status()
    scan_timestamp = now_et()
    trading_day = get_trading_day(scan_timestamp)
    scan_id = get_scan_id(trading_day, scan_timestamp)
    register_scan(
        trading_day=trading_day,
        scan_id=scan_id,
        scan_timestamp=scan_timestamp
    )
    run_background(
        record_scanner_run_start,
        scan_id,
        {
            "trading_day": trading_day,
            "scan_timestamp": scan_timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
    )

    table = Table(
        title="AI Trading Copilot Scanner",
        expand=False
    )
    results = []

    table.add_column("Symbol")
    table.add_column("Price")
    table.add_column("Signal")
    table.add_column("15m Score")
    table.add_column("RR")
    table.add_column("Action")
    table.add_column("Entry")
    table.add_column("Why")
    table.add_column("Next")
    scanner_watchlist = get_scanner_watchlist()
    print(
        "[WATCHLIST] "
        f"scanning {len(scanner_watchlist)} symbols: "
        f"{', '.join(scanner_watchlist)}"
    )
    
    market_reference = {}
    reference_context = _fetch_reference_context()
    reference_regime = reference_context.get(
        "reference_regime",
        "UNKNOWN"
    )
    with stage_timer.stage("Market Data"):

        market_data_prefetch = _prefetch_watchlist_market_data(scanner_watchlist)

    symbol_runtimes = {}
    symbol_failures = {}
    
    for symbol in scanner_watchlist:

        symbol_start = time.perf_counter()

        try:

            # =====================================
            # Fetch ONLY fresh 5m Polygon data
            # =====================================

            prefetch_result = market_data_prefetch.get(symbol) or process_symbol(symbol)
            symbol_runtimes[symbol] = prefetch_result.get("runtime")

            if not prefetch_result.get("success"):

                raise RuntimeError(prefetch_result.get("error") or "market data fetch failed")

            df_5m_raw = prefetch_result.get("data")


            if df_5m_raw.empty:

                market_data_status = get_market_data_status(
                    df_5m_raw
                )

                results.append(
                    build_status_result_row(
                        symbol=symbol,
                        final_signal="STALE DATA",
                        action_status="AVOID",
                        explanation="Polygon delayed/stale feed",
                        next_condition="Need fresh 5m market data",
                        market_data_status=market_data_status,
                        blocked_by="STALE_MARKET_DATA"
                    )
                )

                table.add_row(
                    symbol,
                    "-",
                    "STALE DATA",
                    "-",
                    "-",
                    "AVOID",
                    "-",
                    "Polygon delayed/stale feed",
                    "Need fresh 5m market data"
                )

                continue

            _append_daily_candles(
                symbol,
                df_5m_raw,
                trading_day,
                scan_id,
                interval="5m"
            )

            market_data_status = get_market_data_status(
                df_5m_raw
            )

            time.sleep(1)

            # =====================================
            # Build higher timeframes internally
            # =====================================

            df_15m_raw = resample_timeframe(
                df_5m_raw,
                "15m"
            )

            df_1h_raw = resample_timeframe(
                df_5m_raw,
                "1h"
            )

            # =====================================
            # Apply indicators
            # =====================================

            with stage_timer.stage("Indicators"):

                df_5m = compute_indicators(df_5m_raw,interval="5m",symbol=symbol)

                df_15m = compute_indicators(df_15m_raw,interval="15m",symbol=symbol)

                df_1h = compute_indicators(df_1h_raw,interval="1h",symbol=symbol)

            try:

                market_reference[symbol] = (
                    df_15m["SYMBOL_MOVE_PCT"].iloc[-1]
                )

            except Exception:

                market_reference[symbol] = 0            

            symbol_move_pct = _safe_metric(
                df_15m,
                "SYMBOL_MOVE_PCT"
            )
            relative_volume = _safe_metric(
                df_15m,
                "REL_VOLUME"
            )
            atr_pct = _safe_metric(
                df_15m,
                "ATR_PCT"
            )
            premarket_gap_pct = _calculate_premarket_gap_pct(
                df_5m
            )
            market_regime = _classify_explicit_regime(
                df_15m
            )
            above_vwap = _latest_bool(
                df_15m,
                "Close",
                "VWAP"
            )
            above_ema20 = _latest_bool(
                df_15m,
                "Close",
                "EMA20"
            )
            sector_context = _sector_strength(
                symbol,
                symbol_move_pct,
                reference_context
            )

            # =====================================
            # Dataframe validation
            # =====================================

            if df_5m.empty:

                results.append(
                    build_status_result_row(
                        symbol=symbol,
                        final_signal="NO DATA",
                        action_status="AVOID",
                        explanation="5m dataframe empty",
                        next_condition="Need valid 5m candles",
                        market_data_status=market_data_status,
                        blocked_by="NO_5M_DATA"
                    )
                )

                table.add_row(
                    symbol,
                    "-",
                    "NO DATA",
                    "-",
                    "-",
                    "AVOID",
                    "-",
                    "5m dataframe empty",
                    "Need valid 5m candles"
                )

                continue

            if df_15m.empty:

                print(
                    f"[WARNING] {symbol} "
                    f"15m dataframe unavailable"
                )

            if df_1h.empty:

                print(
                    f"[WARNING] {symbol} "
                    f"1h dataframe unavailable"
                )

            # =====================================
            # Analyze each timeframe
            # =====================================

            with stage_timer.stage("Strategy"):

                analysis_5m = analyze_setup(df_5m)

                analysis_15m = (
                    analyze_setup(df_15m)
                    if not df_15m.empty
                    else {
                        "signal": "NEUTRAL",
                        "score": 0,
                        "reasons": ["15m unavailable"],
                        "valid": False
                    }
                )

                analysis_1h = (
                    analyze_setup(df_1h)
                    if not df_1h.empty
                    else {
                        "signal": "NEUTRAL",
                        "score": 0,
                        "reasons": ["1h unavailable"],
                        "valid": False
                    }
                )

            # =====================================
            # Analysis validation protection
            # =====================================

            if not analysis_5m.get("valid", True):

                print(
                    f"[SKIP] {symbol} "
                    f"5m -> "
                    f"{analysis_5m.get('reason')}"
                )

                results.append(
                    build_status_result_row(
                        symbol=symbol,
                        final_signal="INVALID DATA",
                        action_status="AVOID",
                        explanation=analysis_5m.get(
                            "reason",
                            "5m analysis invalid"
                        ),
                        next_condition="Need valid 5m analysis",
                        market_data_status=market_data_status,
                        blocked_by="INVALID_5M_ANALYSIS"
                    )
                )

                continue

            # if not analysis_15m.get("valid", True):

            #     print(
            #         f"[SKIP] {symbol} "
            #         f"15m -> "
            #         f"{analysis_15m.get('reason')}"
            #     )

            #     continue

            # if not analysis_1h.get("valid", True):

            #     print(
            #         f"[SKIP] {symbol} "
            #         f"1h -> "
            #         f"{analysis_1h.get('reason')}"
            #     )

            #     continue

            active_trade = get_trade(symbol)
            opened_trade_this_scan = False

            if (
                not active_trade
                or active_trade["status"] != "OPEN"
            ):

                if not df_15m.empty:

                    with stage_timer.stage("Entries"):

                        entry_setup = detect_entry(
                            df_15m,
                            analysis_15m
                        )

                else:

                    entry_setup = {

                        "entry_type": "NO_SETUP",

                        "entry_quality": "LOW",

                        "entry_trigger": None,

                        "avoid_chasing": True

                    }

            else:

                entry_setup = {

                    "entry_type": "ACTIVE_TRADE",

                    "entry_quality": "ACTIVE",

                    "entry_trigger": None,

                    "avoid_chasing": False

                }

            if not df_15m.empty:

                with stage_timer.stage("Risk"):

                    risk_setup = calculate_risk(
                        df_15m,
                        analysis_15m,
                        entry_setup
                    )

                print(
                    f"[RISK ENGINE] "
                    f"{symbol} "
                    f"allowed={risk_setup['trade_allowed']} "
                    f"rr={risk_setup['risk_reward']}"
                )                

            else:

                risk_setup = {

                    "risk_reward": 0,

                    "trade_allowed": False,

                    "entry_price": None,

                    "stop_loss": None,

                    "take_profit": None

                }

            if entry_setup["entry_type"] == "NO_ENTRY":

                risk_setup["trade_allowed"] = False

                entry_setup["entry_quality"] = "NONE"

                print(
                    f"[ENTRY FILTER BLOCKED] "
                    f"{symbol} "
                    f"No entry trigger"
                )

            debug_print(
                f"[PRE TIMING] "
                f"{symbol} "
                f"allowed={risk_setup['trade_allowed']}"
            )

            # =====================================
            # Entry Timing Filter
            # =====================================

            if (
                entry_setup["entry_type"] in 
                ["BREAKOUT_LONG", "BREAKDOWN_SHORT"]
                and 
                not analysis_15m["entry_timing_ok"]
            ):

                risk_setup["trade_allowed"] = False

                risk_setup.setdefault(
                    "reasons",
                    []
                )

                risk_setup["reasons"].append(
                    "Entry timing not confirmed"
                )

                print(
                    f"[ENTRY TIMING BLOCKED] "
                    f"{symbol}"
                )

            event_risk = evaluate_event_blocker(symbol)

            if event_risk.get("blocked"):

                risk_setup["trade_allowed"] = False

                risk_setup.setdefault(
                    "reasons",
                    []
                )

                risk_setup["reasons"].append(
                    event_risk["reason"]
                )

                print(
                    f"[EVENT BLOCKED] "
                    f"{symbol} "
                    f"{event_risk['reason']}"
                )

            risk_passed_before_options = (
                risk_setup.get("trade_allowed", False)
            )

            active_option_snapshot = None
            active_option_pl = {
                "option_pl_pct": None,
                "option_pl_dollars": None
            }

            if (
                active_trade
                and active_trade.get("status") == "OPEN"
                and active_trade.get("option_ticker")
            ):

                active_option_snapshot = fetch_option_snapshot(
                    symbol,
                    active_trade.get("option_ticker")
                )

                if active_option_snapshot:

                    active_option_pl = calculate_option_pl(
                        active_trade.get("option_entry_mid"),
                        active_option_snapshot.get("mid_price"),
                        active_trade.get("option_contracts") or 1
                    )



            # =====================================
            # Trade Management
            # =====================================

            if (
                active_trade
                and active_trade["status"] == "OPEN"
                and not opened_trade_this_scan
                and not df_15m.empty
            ):

                trade_management = {
                    "trade_action": "HOLD",
                    "updated_stop": active_trade["stop_loss"],
                    "rr_progress": active_trade.get(
                        "rr_progress",
                        0
                    ),
                    "highest_price": active_trade.get(
                        "highest_price",
                        active_trade["entry_price"]
                    ),
                    "lowest_price": active_trade.get(
                        "lowest_price",
                        active_trade["entry_price"]
                    ),
                    "bars_in_trade": active_trade.get(
                        "bars_in_trade",
                        0
                    ),
                    "partial_profit_taken": active_trade.get(
                        "partial_profit_taken",
                        False
                    ),
                    "adjustment_reason": "Active trade monitored"
                }

            else:

                trade_management = {

                    "trade_action": (
                        "OPENED"
                        if opened_trade_this_scan
                        else "NO_ACTIVE_TRADE"
                    ),

                    "updated_stop": "-",

                    "rr_progress": 0,

                    "highest_price": "-",

                    "adjustment_reason": (
                        "Entry opened this scan"
                        if opened_trade_this_scan
                        else "-"
                    )

                }

            # Default exit state

            exit_setup = {

                "exit_signal": False,

                "exit_reason": (
                    "Entry opened this scan; exit evaluation starts next scan"
                    if opened_trade_this_scan
                    else "No active trade"
                )

            }

            current_symbol_close = None
            current_symbol_price_source = "df_15m_latest_close"

            try:

                if df_5m is not None and not df_5m.empty:

                    current_symbol_close = float(
                        df_5m["Close"].iloc[-1]
                    )
                    current_symbol_price_source = "df_5m_latest_close"

                elif df_15m is not None and not df_15m.empty:

                    current_symbol_close = float(
                        df_15m["Close"].iloc[-1]
                    )

            except Exception:

                current_symbol_close = None

            # Active trade exit evaluation


            if (
                active_trade 
                and active_trade["status"] == "OPEN" 
                and not opened_trade_this_scan
                and not df_15m.empty
            ):

                # =====================================
                # Evaluate exits FIRST
                # =====================================

                with stage_timer.stage("Paper Trades"):

                    exit_setup = evaluate_exit(

                        df_15m,

                        analysis_15m,

                        {

                            "stop_loss": (
                                active_trade["stop_loss"]
                            ),

                            "take_profit": (
                                active_trade["take_profit"]
                            ),

                            "entry_price": (
                                active_trade.get(
                                    "entry_price",
                                    None
                                )
                            )

                        },
                    
                        # For active trades, infer direction
                        # from stop_loss vs entry_price
                        {
                            "entry_type": (
                                active_trade.get("entry_type")
                                or (
                                    "BREAKDOWN_SHORT"
                                    if (
                                        active_trade.get("stop_loss", 0)
                                        > active_trade.get("entry_price", 0)
                                    )
                                    else "BREAKOUT_LONG"
                                )
                            )
                        },

                        trade_state=active_trade

                    )

                trade_management.update({
                    "trade_action": exit_setup["trade_action"],
                    "updated_stop": exit_setup["updated_stop"],
                    "rr_progress": exit_setup["rr_progress"],
                    "highest_price": exit_setup["highest_price"],
                    "lowest_price": exit_setup["lowest_price"],
                    "bars_in_trade": exit_setup["bars_in_trade"],
                    "partial_profit_taken": exit_setup[
                        "partial_profit_taken"
                    ],
                    "adjustment_reason": exit_setup[
                        "adjustment_reason"
                    ]
                })
                
                # DEBUG: Show direction inference
                debug_print(
                    f"[DIRECTION INFERENCE] "
                    f"entry_price={active_trade.get('entry_price', 0)} "
                    f"stop_loss={active_trade.get('stop_loss', 0)} "
                    f"inferred={'SHORT' if active_trade.get('stop_loss', 0) > active_trade.get('entry_price', 0) else 'LONG'}"
                )

                # =====================================
                # Close trade if needed
                # =====================================

                if (
                    active_trade
                    and exit_setup["exit_signal"]
                ):

                    try:

                        telegram_exit_result = maybe_send_trade_exit_alert(
                            symbol=symbol,
                            trade=active_trade,
                            exit_reason=exit_setup.get("exit_reason"),
                            current_price=current_symbol_close,
                            option_current_mid=(
                                active_option_snapshot.get("mid_price")
                                if active_option_snapshot
                                else active_trade.get("option_current_mid")
                            ),
                            pnl_pct=active_option_pl.get("option_pl_pct"),
                            r_multiple=exit_setup.get("rr_progress"),
                            outcome="EXIT_SIGNAL",
                            event_type="EXIT",
                            event_timestamp=(
                                df_15m.index[-1].isoformat()
                                if df_15m is not None and not df_15m.empty
                                else None
                            ),
                            expected_underlying_price=current_symbol_close,
                            price_source=current_symbol_price_source,
                            scanner_row_symbol=symbol,
                            candidate_prices={
                                "df_5m_latest_close": (
                                    float(df_5m["Close"].iloc[-1])
                                    if df_5m is not None and not df_5m.empty
                                    else None
                                ),
                                "df_15m_latest_close": (
                                    float(df_15m["Close"].iloc[-1])
                                    if df_15m is not None and not df_15m.empty
                                    else None
                                )
                            }
                        )
                        debug_print(
                            f"[TELEGRAM EXIT ALERT] {symbol} "
                            f"sent={telegram_exit_result.get('sent')} "
                            f"reason={telegram_exit_result.get('reason')}"
                        )

                    except Exception as e:

                        print(
                            f"[TELEGRAM EXIT ALERT ERROR] {symbol}: {e}"
                        )

                    close_trade(symbol)

                    trade_management["trade_action"] = "EXIT"

                # =====================================
                # ONLY update surviving trades
                # =====================================

                else:

                    if exit_setup.get("trade_action") == "PARTIAL_PROFIT":

                        try:

                            telegram_partial_result = maybe_send_trade_exit_alert(
                                symbol=symbol,
                                trade=active_trade,
                                exit_reason=exit_setup.get(
                                    "adjustment_reason",
                                    "Partial profit threshold reached"
                                ),
                                current_price=current_symbol_close,
                                option_current_mid=(
                                    active_option_snapshot.get("mid_price")
                                    if active_option_snapshot
                                    else active_trade.get("option_current_mid")
                                ),
                                pnl_pct=active_option_pl.get("option_pl_pct"),
                                r_multiple=exit_setup.get("rr_progress"),
                                outcome="PARTIAL_PROFIT",
                                event_type="PARTIAL_EXIT",
                                expected_underlying_price=current_symbol_close,
                                price_source=current_symbol_price_source,
                                scanner_row_symbol=symbol,
                                candidate_prices={
                                    "df_5m_latest_close": (
                                        float(df_5m["Close"].iloc[-1])
                                        if df_5m is not None and not df_5m.empty
                                        else None
                                    ),
                                    "df_15m_latest_close": (
                                        float(df_15m["Close"].iloc[-1])
                                        if df_15m is not None and not df_15m.empty
                                        else None
                                    )
                                }
                            )
                            debug_print(
                                f"[TELEGRAM PARTIAL ALERT] {symbol} "
                                f"sent={telegram_partial_result.get('sent')} "
                                f"reason={telegram_partial_result.get('reason')}"
                            )

                        except Exception as e:

                            print(
                                f"[TELEGRAM PARTIAL ALERT ERROR] {symbol}: {e}"
                            )

                    update_trade(

                        symbol,

                        exit_setup[
                            "highest_price"
                        ],

                        exit_setup[
                            "rr_progress"
                        ],

                        exit_setup[
                            "updated_stop"
                        ],

                        lowest_price=exit_setup[
                            "lowest_price"
                        ],

                        bars_in_trade=exit_setup[
                            "bars_in_trade"
                        ],

                        partial_profit_taken=exit_setup[
                            "partial_profit_taken"
                        ],

                        option_data=active_option_snapshot,

                        option_pl=active_option_pl

                    )          

            bias_5m = timeframe_bias(analysis_5m)
            bias_15m = timeframe_bias(analysis_15m)
            bias_1h = timeframe_bias(analysis_1h)

            # Weighted alignment score
            alignment_score = (
                bias_5m * 1
                +
                bias_15m * 3
                +
                bias_1h * 2
            )

            # Add actual conviction strength
            alignment_score += (
                analysis_15m["score"] / 4
            )

            # Count bullish confirmations
            bullish_count = sum([
                bias_5m > 0,
                bias_15m > 0,
                bias_1h > 0
            ])

            bearish_count = sum([
                bias_5m < 0,
                bias_15m < 0,
                bias_1h < 0
            ])

            # Final signal based on alignment
            if (
                alignment_score >= 4
                and bullish_count >= 2
                and analysis_15m["score"] >= 8
                and risk_setup["trade_allowed"]
                and entry_setup["entry_quality"] == "HIGH"
            ):
                final_signal = "HIGH CONVICTION BULLISH"

            elif alignment_score >= 2:
                final_signal = "BULLISH"

            elif (
                alignment_score <= -4
                and bearish_count >= 2
                and analysis_15m["score"] <= -8
                and risk_setup["trade_allowed"]
                and entry_setup["entry_quality"] == "HIGH"
            ):
                final_signal = "HIGH CONVICTION BEARISH"

            elif alignment_score <= -2:
                final_signal = "BEARISH"

            else:
                final_signal = "NEUTRAL"

            regime_block = _evaluate_regime_setup_block(
                entry_setup.get("entry_type"),
                final_signal,
                market_regime
            )

            if regime_block.get("blocked"):

                risk_setup["trade_allowed"] = False
                risk_setup.setdefault(
                    "reasons",
                    []
                )
                risk_setup["reasons"].append(
                    regime_block["reason"]
                )
                print(
                    f"[REGIME BLOCKED] "
                    f"{symbol} "
                    f"{regime_block['reason']}"
                )

            risk_passed_before_options = (
                risk_setup.get("trade_allowed", False)
            )


            # =====================================
            # Multi-timeframe narrative aggregation
            # =====================================

            final_reasons = []

            # Strongest timeframe first
            valid_15m_reasons = [

                r for r in analysis_15m.get(
                    "reasons",
                    []
                )

                if "NaN" not in r

                and "warmup" not in r.lower()

            ]

            final_reasons.extend(
                valid_15m_reasons[:3]
            )

            # =====================================
            # Clean 1h reasons
            # =====================================

            valid_1h_reasons = [

                r for r in analysis_1h.get(
                    "reasons",
                    []
                )

                if "NaN" not in r

                and "warmup" not in r.lower()

            ]

            # Add 1h confirmation
            final_reasons.extend(

                [

                    r for r in valid_1h_reasons[:2]

                    if r not in final_reasons

                ]

            )

            # =====================================
            # Clean 5m reasons
            # =====================================

            valid_5m_reasons = [

                r for r in analysis_5m.get(
                    "reasons",
                    []
                )

                if "NaN" not in r

                and "warmup" not in r.lower()

            ]

            # Add 5m trigger context
            final_reasons.extend(

                [

                    r for r in valid_5m_reasons[:2]

                    if r not in final_reasons

                ]

            )

            # =====================================
            # Latest Price Logic
            # =====================================

            latest_price = None

            try:

                latest_price = get_live_price(
                    df_15m
                )

            except Exception as e:

                print(
                    f"[LIVE PRICE FETCH ERROR] "
                    f"{symbol}: {e}"
                )

                latest_price = None

            # Fallback to latest candle close
            if latest_price is None:

                try:

                    if not df_5m.empty:

                        latest_price = float(
                            df_5m["Close"].iloc[-1]
                        )

                    elif not df_15m.empty:

                        latest_price = float(
                            df_15m["Close"].iloc[-1]
                        )

                except Exception as e:

                    print(
                        f"[FALLBACK PRICE ERROR] "
                        f"{symbol}: {e}"
                    )

                    latest_price = None

            # Final formatting
            if latest_price is not None:

                latest_price = round(
                    float(latest_price),
                    2
                )

            else:

                latest_price = "-"

            option_recommendation = None
            best_quality_option = None
            affordable_option = None
            option_liquidity = None
            option_rejection_reason = None
            option_quote_status = None
            option_spread_pct = None
            option_mid_price = None
            option_bundle = None
            option_liquidity_attempts = []
            short_dte_option = None
            longer_dte_option = None
            realtime_block_reason = None
            realtime_ready = False
            projection = None
            replay_result = None
            position_data = None
            intended_option_direction = resolve_option_direction(
                final_signal,
                (
                    entry_setup.get("entry_type")
                    if entry_setup
                    else None
                )
            )

            if (
                not entry_setup
                or entry_setup.get("entry_type") in [
                    "NO_ENTRY",
                    "NO_SETUP"
                ]
            ):

                intended_option_direction = "NONE"

            option_direction_match = None

            if (
                settings.realtime_market_data_required
                and market_data_status.get("delay_minutes") is not None
                and market_data_status.get("delay_minutes")
                > settings.max_stock_data_delay_minutes
                and final_signal != "NEUTRAL"
            ):

                realtime_block_reason = "REALTIME_STOCK_DATA_REQUIRED"
                risk_setup["trade_allowed"] = False
                risk_setup.setdefault("reasons", [])
                risk_setup["reasons"].append(
                    "Stock aggregate data is not real-time"
                )

            if (
                final_signal != "NEUTRAL" 
                and entry_setup["entry_type"] != "NO_ENTRY"                
                and isinstance(
                    latest_price,
                    (int, float)
                )
                and risk_setup["trade_allowed"]
            ):

                with stage_timer.stage("Options"):

                    option_bundle = (
                        recommend_live_option_bundle(
                            symbol,
                            latest_price,
                            final_signal,
                            entry_setup.get("entry_type"),
                            paper_mode=_paper_option_validation_mode()
                        )
                    )

                best_quality_option = option_bundle.get(
                    "primary"
                ) if option_bundle else None
                affordable_option = option_bundle.get(
                    "affordable"
                ) if option_bundle else None
                option_recommendation = (
                    option_bundle.get("active")
                    if option_bundle
                    else None
                ) or best_quality_option
                short_dte_option = option_bundle.get(
                    "short_dte"
                ) if option_bundle else None
                longer_dte_option = option_bundle.get(
                    "longer_dte"
                ) if option_bundle else None

                option_recommendation, option_liquidity, option_liquidity_attempts = (
                    _select_liquid_option_from_bundle(
                        option_bundle,
                        intended_option_direction
                    )
                )

                if option_recommendation:

                    option_direction_match = True
                    option_quote_status = option_recommendation.get(
                        "quote_status"
                    ) or option_liquidity.get("code")
                    option_mid_price = option_recommendation.get(
                        "mid_price"
                    ) or option_recommendation.get("quote_midpoint")
                    option_spread_pct = option_liquidity.get(
                        "spread_pct"
                    )
                    print(
                        f"[LIQUIDITY] {symbol} "
                        f"liquid={option_liquidity['liquid']} "
                        f"code={option_liquidity.get('code')} "
                        f"reason={option_liquidity['reason']}"
                    )

                else:

                    last_attempt = (
                        option_liquidity_attempts[-1]
                        if option_liquidity_attempts
                        else {}
                    )

                    risk_setup["trade_allowed"] = False

                    risk_setup.setdefault(
                        "reasons",
                        []
                    )

                    option_rejection_reason = (
                        last_attempt.get("reason")
                        or "No option contract passed ranking"
                    )
                    option_quote_status = (
                        last_attempt.get("code")
                        or "NO_CONTRACT"
                    )
                    option_spread_pct = last_attempt.get("spread_pct")
                    option_direction_match = (
                        False
                        if option_quote_status == "DIRECTION_MISMATCH"
                        else None
                    )

                    if option_quote_status in [
                        "MISSING_BID_ASK",
                        "UNKNOWN_QUOTE_TIME",
                        "STALE_QUOTE",
                        "DELAYED_QUOTE"
                    ]:

                        realtime_block_reason = option_quote_status

                    risk_setup["reasons"].append(
                        f"Liquidity failed: "
                        f"{option_rejection_reason}"
                    )

                    print(
                        f"[LIQUIDITY] {symbol} "
                        f"liquid=False "
                        f"code={option_quote_status} "
                        f"reason={option_rejection_reason}"
                    )

            if (
                final_signal != "NEUTRAL" 
                and entry_setup["entry_type"] != "NO_ENTRY"                
                and isinstance(
                    latest_price,
                    (int, float)
                )
                and risk_setup["trade_allowed"]
                and risk_setup["stop_loss"] is not None
                and risk_setup["take_profit"] is not None
            ):
                projection = project_trade(
                    symbol,
                    latest_price,
                    {
                        "signal": final_signal,
                        "score": alignment_score,
                        "reasons": final_reasons,
                        "ATR": df_15m.iloc[-1]["ATR"],
                        "market_regime":
                            analysis_15m.get(
                                "market_regime",
                                "CHOPPY"
                            )                        
                    },
                    entry_setup,
                    risk_setup,
                    alignment_score=alignment_score,
                    option_data=option_recommendation
                )

                if option_recommendation:

                    bid = option_recommendation.get(
                        "bid",
                        0
                    ) or 0

                    ask = option_recommendation.get(
                        "ask",
                        0
                    ) or 0

                    option_price = option_recommendation.get(
                        "mid_price"
                    )

                    if not option_price and bid > 0 and ask > 0:

                        option_price = (
                            bid + ask
                        ) / 2

                    if not option_price:

                        option_price = option_recommendation.get(
                            "close",
                            5
                        ) or 5

                    position_data = calculate_position_size(

                        account_size=settings.account_size,

                        risk_percent=settings.risk_percent,

                        entry_price=latest_price,

                        stop_loss=risk_setup["stop_loss"],

                        option_price=option_price,

                        projection=projection,

                        max_contracts=settings.max_contracts_per_trade,

                        option_stop_loss_pct=settings.option_stop_loss_pct

                    )

                if (
                    entry_setup
                    and entry_setup["entry_quality"] == "HIGH"
                    and option_recommendation
                    and (
                        not active_trade
                        or active_trade["status"] != "OPEN"
                    )
                ):

                    open_trade(
                        symbol,
                        risk_setup["entry_price"],
                        risk_setup["stop_loss"],
                        risk_setup["take_profit"],
                        entry_type=entry_setup["entry_type"],
                        option_data=option_recommendation,
                        contracts=(
                            position_data.get("contracts")
                            if position_data
                            else None
                        )
                    )

                    active_trade = get_trade(symbol)
                    opened_trade_this_scan = True

                    trade_management = {
                        "trade_action": "OPENED",
                        "updated_stop": "-",
                        "rr_progress": 0,
                        "highest_price": "-",
                        "adjustment_reason": "Entry opened this scan"
                    }

                    exit_setup = {
                        "exit_signal": False,
                        "exit_reason": (
                            "Entry opened this scan; "
                            "exit evaluation starts next scan"
                        )
                    }

            # =====================================
            # Replay projection outcome
            # =====================================
            if risk_setup["trade_allowed"] and projection:

                replay_result = replay_trade_projection(
                    symbol=symbol,
                    df=df_15m,
                    projection=projection,
                    final_signal=final_signal
                )

                if replay_result:
                    print(
                        f"[REPLAY] "
                        f"{symbol} → "
                        f"{replay_result['outcome']} "
                        f"after "
                        f"{replay_result['bars_processed']} "
                        f"bars"
                    )

            # AI summary only for strong setups
            summary = None

            strong_bullish = (
                alignment_score >= 2
                and analysis_15m["score"] >= 7
            )

            strong_bearish = (
                alignment_score <= -2
                and analysis_15m["score"] <= -7
            )

            if (
                settings.scanner_ai_summary_enabled
                and
                (
                    strong_bullish
                    or strong_bearish
                )
                and should_call_ai(
                    symbol,
                    final_signal,
                    analysis_15m["score"]
                )
            ):
                
                if (
                    final_signal != "NEUTRAL"
                    and entry_setup["entry_type"] != "NO_ENTRY"
                    and abs(alignment_score) >= 4
                ):
                    summary = generate_trade_summary(
                        symbol,
                        latest_price,
                        {
                            "signal": final_signal,
                            "score": alignment_score,
                            "reasons": final_reasons
                        }
                    )
                else:
                    summary = "AI skipped"

            debug_print(
                f"[ALLOW DEBUG] "
                f"{symbol} "
                f"allowed={risk_setup['trade_allowed']} "
                f"rr={risk_setup['risk_reward']} "
                f"entry={entry_setup['entry_quality']}"
            )    

            debug_print(
                f"[TIMING DEBUG] "
                f"{symbol} "
                f"entry_timing_ok="
                f"{analysis_15m.get('entry_timing_ok', True)}"
            ) 

            replay_outcome = (
                replay_result["outcome"]
                if replay_result
                else "NO_REPLAY"
            )

            action_decision = build_action_decision(
                final_signal=final_signal,
                entry_setup=entry_setup,
                risk_setup=risk_setup,
                risk_passed_before_options=(
                    risk_passed_before_options
                ),
                projection=projection,
                option_quote_status=option_quote_status,
                option_rejection_reason=(
                    option_rejection_reason
                ),
                market_data_status=market_data_status
            )

            realtime_ready = (
                action_decision["action_status"] in ["ENTER", "ENTER_PAPER"]
                and not realtime_block_reason
                and not action_decision.get("realtime_confirmation_needed")
            )

            explanation, next_condition = build_explanation_and_hint(
                final_signal=final_signal,
                entry_setup=entry_setup,
                risk_setup=risk_setup,
                analysis_15m=analysis_15m,
                df_15m=df_15m,
                action_decision=action_decision
            )

            candidate_plan = build_candidate_trade_plan(
                final_signal=final_signal,
                entry_setup=entry_setup,
                risk_setup=risk_setup,
                action_decision=action_decision,
                risk_passed_before_options=(
                    risk_passed_before_options
                ),
                option_quote_status=option_quote_status,
                option_rejection_reason=(
                    option_rejection_reason
                )
            )

            # Add table row
            table.add_row(
                symbol,
                str(latest_price),
                final_signal,
                str(analysis_15m["score"]),
                str(risk_setup["risk_reward"]),
                action_decision["action_status"],
                _compact_entry_type(
                    entry_setup["entry_type"]
                    if entry_setup
                    else "NO_ENTRY"
                ),
                explanation,
                next_condition
            )
            
            # DEBUG: Show exit_setup state in table row
            debug_print(
                f"[TABLE ROW DEBUG] "
                f"symbol={symbol} "
                f"exit_signal={exit_setup['exit_signal']} "
                f"replay_result={replay_result is not None} "
                f"replay_outcome={replay_outcome} "
                f"active_trade={active_trade is not None}"
            )



            result_row = {

                "Symbol": symbol,

                "Suggestion Status": None,

                "Suggestion First Seen": None,

                "Suggestion Last Seen": None,

                "Suggestion Age Minutes": None,

                "Still Valid": None,

                "Invalidation Reason": None,

                "Exit Status": None,

                "Exit Reason Live": (
                    exit_setup.get("exit_reason")
                    if exit_setup
                    else None
                ),

                "Price": latest_price,

                "Final Signal": final_signal,

                "15m Score": analysis_15m["score"],

                "Category Score": analysis_15m.get("category_score"),

                "Alignment Score": round(
                    alignment_score,
                    2
                ),                

                "Symbol Move %": symbol_move_pct,

                "RS vs QQQ": None,

                "RS vs SPY": None,

                "RS Rank Score": None,

                "Bullish Rank": None,

                "Bearish Rank": None,

                "Top Candidate": None,

                "Premarket Gap %": premarket_gap_pct,

                "Relative Volume": relative_volume,

                "ATR %": atr_pct,

                "Market Regime": market_regime,

                "Reference Regime": reference_regime,

                "Regime Blocked": regime_block.get("blocked"),

                "Regime Block Reason": regime_block.get("reason"),

                "Sector Group": sector_context["sector_group"],

                "Sector Reference": sector_context["sector_reference"],

                "Sector Reference Move %": sector_context[
                    "sector_reference_move_pct"
                ],

                "Sector RS": sector_context["sector_rs"],

                "Sector Strength": sector_context["sector_strength"],

                "SMH Move %": reference_context.get(
                    "references",
                    {}
                ).get("SMH", {}).get("move_pct"),

                "SOXX Move %": reference_context.get(
                    "references",
                    {}
                ).get("SOXX", {}).get("move_pct"),

                "XLK Move %": reference_context.get(
                    "references",
                    {}
                ).get("XLK", {}).get("move_pct"),

                "VIX Move %": reference_context.get(
                    "references",
                    {}
                ).get("VIX", {}).get("move_pct"),

                "Above VWAP": above_vwap,

                "Above EMA20": above_ema20,

                "Reasons": ", ".join(
                    final_reasons[:5]
                ),

                "Entry": (
                    entry_setup["entry_type"]
                    if entry_setup
                    else "NO_ENTRY"
                ),

                "Entry Quality": (
                    entry_setup["entry_quality"]
                    if entry_setup
                    else "LOW"
                ),

                "Entry Trigger": (
                    entry_setup["entry_trigger"]
                    if entry_setup
                    else None
                ),

                "Risk Reward": (
                    risk_setup["risk_reward"]
                ),

                "Stop Loss": (
                    risk_setup["stop_loss"]
                ),

                "Take Profit": (
                    risk_setup["take_profit"]
                ),

                "Trade Allowed": (
                    risk_setup["trade_allowed"]
                ),

                "Setup Valid": (
                    candidate_plan["setup_valid"]
                ),

                "Execution Ready": (
                    candidate_plan["execution_ready"]
                ),

                "Blocked By": (
                    candidate_plan["blocked_by"]
                ),

                "Candidate Direction": (
                    candidate_plan["candidate_direction"]
                ),

                "Candidate Entry Price": (
                    candidate_plan["candidate_entry_price"]
                ),

                "Candidate Stop Price": (
                    candidate_plan["candidate_stop_price"]
                ),

                "Candidate Target Price": (
                    candidate_plan["candidate_target_price"]
                ),

                "Candidate RR": (
                    candidate_plan["candidate_rr"]
                ),

                "Candidate Trigger": (
                    candidate_plan["candidate_trigger"]
                ),

                "Live Chart Checklist": (
                    candidate_plan["live_chart_checklist"]
                ),

                "Planned Invalidation": (
                    candidate_plan["planned_invalidation"]
                ),

                "Profit Taking Rule": (
                    candidate_plan["profit_taking_rule"]
                ),

                "Do Not Enter Reason": (
                    candidate_plan["do_not_enter_reason"]
                ),

                "Action Status": (
                    action_decision["action_status"]
                ),

                "Action Reason": (
                    action_decision["action_reason"]
                ),

                "Explanation": explanation,

                "Next Condition": next_condition,

                "Market Data Delay Minutes": (
                    market_data_status.get("delay_minutes")
                ),

                "Stock Data Freshness": (
                    market_data_status.get("stock_data_freshness")
                ),

                "Stock Data Age Minutes": (
                    market_data_status.get("delay_minutes")
                ),

                "Data Timestamp ET": format_timestamp(
                    market_data_status.get(
                        "data_timestamp_et"
                    )
                ),

                "Current ET": format_timestamp(
                    market_data_status.get("current_et")
                ),

                "Realtime Confirmation Needed": (
                    action_decision[
                        "realtime_confirmation_needed"
                    ]
                ),

                "TradingView Check Status": (
                    action_decision[
                        "tradingview_check_status"
                    ]
                ),

                "Rejected Trade Reason": (
                    action_decision["action_reason"]
                    if action_decision["action_status"]
                    != "ENTER"
                    else None
                ),

                "Data Delay Warning": (
                    "Signal delayed by 15 min; validate against TradingView"
                    if action_decision[
                        "realtime_confirmation_needed"
                    ]
                    else None
                ),

                "Live Exit Signal": (
                    exit_setup["exit_signal"]
                ),

                "Live Exit Reason": (
                    exit_setup["exit_reason"]
                ),

                "Replay Ran": (
                    replay_result is not None
                ),

                "Replay Outcome": (
                    replay_outcome
                ),

                "Trade Action": (
                    trade_management["trade_action"]
                ),

                "RR Progress": (
                    trade_management["rr_progress"]
                ),

                "Updated Stop": (
                    trade_management["updated_stop"]
                ),

                "Trailing Stop": (
                    exit_setup.get("trailing_stop")
                ),

                "Bars In Trade": (
                    trade_management.get(
                        "bars_in_trade",
                        0
                    )
                ),

                "Partial Profit Taken": (
                    trade_management.get(
                        "partial_profit_taken",
                        False
                    )
                ),

                "Adjustment Reason": (
                    exit_setup["exit_reason"]
                    if exit_setup["exit_signal"]
                    else trade_management[
                        "adjustment_reason"
                    ]
                ),

                "Probability": (
                    projection["probability"]
                    if projection
                    else None
                ),

                "Trade Grade": (
                    projection["trade_grade"]
                    if projection
                    else None
                ),

                "Target Price": (
                    projection["target_price"]
                    if projection
                    else None
                ),

                "Stop Price": (
                    projection["stop_price"]
                    if projection
                    else None
                ),

                "Option Ticker": (
                    option_recommendation.get("ticker")
                    if option_recommendation
                    else None
                ),

                "Intended Option Direction": (
                    intended_option_direction
                ),

                "Option Direction Match": (
                    option_direction_match
                ),

                "Option Strike": (
                    option_recommendation.get("strike")
                    if option_recommendation
                    else None
                ),

                "Option Expiration": (
                    option_recommendation.get("expiration")
                    if option_recommendation
                    else None
                ),

                "Option Bid": (
                    option_recommendation.get("bid")
                    if option_recommendation
                    else None
                ),

                "Option Ask": (
                    option_recommendation.get("ask")
                    if option_recommendation
                    else None
                ),

                "Option Mid Price": option_mid_price,

                "Option Midpoint": (
                    option_recommendation.get("quote_midpoint")
                    if option_recommendation
                    else option_mid_price
                ),

                "Option Quote Timestamp": (
                    option_recommendation.get("quote_time_utc")
                    if option_recommendation
                    else None
                ),

                "Option Quote Timeframe": (
                    option_recommendation.get("quote_timeframe")
                    if option_recommendation
                    else None
                ),

                "Option Quote Source": (
                    option_recommendation.get("quote_source")
                    if option_recommendation
                    else None
                ),

                "Option Spread %": option_spread_pct,

                "Option Volume": (
                    option_recommendation.get("volume")
                    if option_recommendation
                    else None
                ),

                "Option Open Interest": (
                    option_recommendation.get("open_interest")
                    if option_recommendation
                    else None
                ),

                "Option Delta": (
                    option_recommendation.get("delta")
                    if option_recommendation
                    else None
                ),

                "Option Theta": (
                    option_recommendation.get("theta")
                    if option_recommendation
                    else None
                ),

                "Option IV": (
                    option_recommendation.get("iv")
                    if option_recommendation
                    else None
                ),

                "Option Gamma": (
                    option_recommendation.get("gamma")
                    if option_recommendation
                    else None
                ),

                "Expiration Risk": (
                    option_recommendation.get("expiration_risk")
                    if option_recommendation
                    else None
                ),

                "Expiration Bucket": (
                    option_recommendation.get("expiration_bucket")
                    if option_recommendation
                    else None
                ),

                "Option Quality Score": (
                    option_recommendation.get("option_quality_score")
                    if option_recommendation
                    else None
                ),

                "Option Liquidity Grade": (
                    option_recommendation.get("option_liquidity_grade")
                    if option_recommendation
                    else None
                ),

                "Option Quality Reasons": (
                    option_recommendation.get("option_quality_reasons")
                    if option_recommendation
                    else None
                ),

                "Option Quote Freshness": (
                    option_recommendation.get("quote_freshness")
                    if option_recommendation
                    else None
                ),

                "Option Quote Age Minutes": (
                    option_recommendation.get("quote_age_minutes")
                    if option_recommendation
                    else None
                ),

                "Option Contract Cost": (
                    option_recommendation.get("contract_cost")
                    if option_recommendation
                    else None
                ),

                "Option Risk At Stop": (
                    option_recommendation.get("risk_at_stop")
                    if option_recommendation
                    else None
                ),

                "Current Capital": (
                    option_recommendation.get("current_capital")
                    if option_recommendation
                    else None
                ),

                "Max Allowed Contract Cost": (
                    option_recommendation.get("max_allowed_contract_cost")
                    if option_recommendation
                    else None
                ),

                "Preferred Max Contract Cost": (
                    option_recommendation.get("preferred_max_contract_cost")
                    if option_recommendation
                    else None
                ),

                "Affordability Status": (
                    option_recommendation.get("affordability_status")
                    if option_recommendation
                    else None
                ),

                "Affordable": (
                    option_recommendation.get("affordable")
                    if option_recommendation
                    else None
                ),

                "Preferred Affordable": (
                    option_recommendation.get("preferred_affordable")
                    if option_recommendation
                    else None
                ),

                "Affordability Mode": (
                    option_recommendation.get("affordability_mode")
                    if option_recommendation
                    else None
                ),

                "Capital Profile": (
                    option_recommendation.get("capital_profile")
                    if option_recommendation
                    else None
                ),

                "Best Quality Option Ticker": (
                    best_quality_option.get("ticker")
                    if best_quality_option
                    else None
                ),

                "Best Quality Contract Cost": (
                    best_quality_option.get("contract_cost")
                    if best_quality_option
                    else None
                ),

                "Best Quality Affordability Status": (
                    best_quality_option.get("affordability_status")
                    if best_quality_option
                    else None
                ),

                "Affordable Option Ticker": (
                    affordable_option.get("ticker")
                    if affordable_option
                    else None
                ),

                "Affordable Option Contract Cost": (
                    affordable_option.get("contract_cost")
                    if affordable_option
                    else None
                ),

                "Active Option Mid": (
                    active_option_snapshot.get("mid_price")
                    if active_option_snapshot
                    else None
                ),

                "Active Option P/L %": active_option_pl.get(
                    "option_pl_pct"
                ),

                "Active Option P/L $": active_option_pl.get(
                    "option_pl_dollars"
                ),

                "Event Blocked": event_risk.get("blocked"),

                "Event Block Reason": event_risk.get("reason"),

                "Option Quote Status": option_quote_status,

                "Option Rejection Reason": option_rejection_reason,

                "Option Liquidity Passed": (
                    option_liquidity.get("liquid")
                    if option_liquidity
                    else False
                ),

                "Option Liquidity Selected Source": (
                    option_liquidity_attempts[-1].get("source")
                    if option_liquidity_attempts
                    and option_liquidity_attempts[-1].get("accepted")
                    else None
                ),

                "Option Liquidity Attempts": (
                    json.dumps(option_liquidity_attempts)
                    if option_liquidity_attempts
                    else None
                ),

                "Realtime Ready": realtime_ready,

                "Realtime Block Reason": realtime_block_reason,

                "Expected Option Profit %": (
                    projection["projected_option_gain"]
                    if projection
                    else None
                ),

                "Short DTE Option Ticker": (
                    short_dte_option.get("ticker")
                    if short_dte_option
                    else None
                ),

                "Short DTE Expiration": (
                    short_dte_option.get("expiration")
                    if short_dte_option
                    else None
                ),

                "Short DTE Strike": (
                    short_dte_option.get("strike")
                    if short_dte_option
                    else None
                ),

                "Short DTE Bucket": (
                    short_dte_option.get("expiration_bucket")
                    if short_dte_option
                    else None
                ),

                "Short DTE Mid Price": (
                    short_dte_option.get("mid_price")
                    if short_dte_option
                    else None
                ),

                "Short DTE Spread %": (
                    short_dte_option.get("spread_pct")
                    if short_dte_option
                    else None
                ),

                "Short DTE Quality Score": (
                    short_dte_option.get("option_quality_score")
                    if short_dte_option
                    else None
                ),

                "Short DTE Quote Freshness": (
                    short_dte_option.get("quote_freshness")
                    if short_dte_option
                    else None
                ),

                "Longer DTE Option Ticker": (
                    longer_dte_option.get("ticker")
                    if longer_dte_option
                    else None
                ),

                "Longer DTE Expiration": (
                    longer_dte_option.get("expiration")
                    if longer_dte_option
                    else None
                ),

                "Longer DTE Strike": (
                    longer_dte_option.get("strike")
                    if longer_dte_option
                    else None
                ),

                "Longer DTE Bucket": (
                    longer_dte_option.get("expiration_bucket")
                    if longer_dte_option
                    else None
                ),

                "Longer DTE Mid Price": (
                    longer_dte_option.get("mid_price")
                    if longer_dte_option
                    else None
                ),

                "Longer DTE Spread %": (
                    longer_dte_option.get("spread_pct")
                    if longer_dte_option
                    else None
                ),

                "Longer DTE Quality Score": (
                    longer_dte_option.get("option_quality_score")
                    if longer_dte_option
                    else None
                ),

                "Longer DTE Quote Freshness": (
                    longer_dte_option.get("quote_freshness")
                    if longer_dte_option
                    else None
                )

            }

            result_row = _align_action_status_with_entry_gate(
                result_row
            )
            results.append(result_row)

            # =====================================
            # Save telemetry
            # =====================================

            # =====================================
            # Save telemetry only for
            # actionable setups
            # =====================================

            if (
                final_signal != "NEUTRAL"
                and projection
                and replay_result is not None
                and replay_result.get("outcome") is not None
                and risk_setup["trade_allowed"]
            ):
                save_trade_telemetry(
                    {
                        "run_type": "live_scan",
                        "symbol": symbol,
                        "price": latest_price,
                        "final_signal": final_signal,
                        "alignment_score":
                            alignment_score,
                        "score":
                            analysis_15m["score"],
                        "entry":
                            entry_setup["entry_type"],
                        "setup_category":
                            entry_setup["entry_type"],                            
                        "entry_quality":
                            entry_setup["entry_quality"],
                        "risk_reward":
                            risk_setup["risk_reward"],
                        "allowed":
                            risk_setup["trade_allowed"],
                        "probability":
                            (
                                projection["probability"]
                                if projection
                                else None
                            ),

                        "trade_grade":
                            (
                                projection["trade_grade"]
                                if projection
                                else None
                            ),

                        "target_price":
                            (
                                projection["target_price"]
                                if projection
                                else None
                            ),

                        "stop_price":
                            (
                                projection["stop_price"]
                                if projection
                                else None
                            ),

                        "projected_option_gain":
                            (
                                projection[
                                    "projected_option_gain"
                                ]
                                if projection
                                else None
                            ),

                        "stock_data_freshness":
                            market_data_status.get(
                                "stock_data_freshness"
                            ),

                        "stock_data_age_minutes":
                            market_data_status.get(
                                "delay_minutes"
                            ),

                        "market_data_delay_minutes":
                            market_data_status.get(
                                "delay_minutes"
                            ),

                        "realtime_ready": realtime_ready,

                        "realtime_block_reason": realtime_block_reason,

                        "action_status": action_decision.get(
                            "action_status"
                        ),

                        "blocked_by": candidate_plan.get(
                            "blocked_by"
                        ),

                        "option_delta":
                            (
                                option_recommendation["delta"]
                                if option_recommendation
                                else None
                            ),

                        "option_gamma":
                            (
                                option_recommendation["gamma"]
                                if option_recommendation
                                else None
                            ),

                        "option_iv":
                            (
                                option_recommendation["iv"]
                                if option_recommendation
                                else None
                            ),

                        "option_theta":
                            (
                                option_recommendation["theta"]
                                if option_recommendation
                                else None
                            ),

                        "option_mid_price": option_mid_price,

                        "option_bid":
                            (
                                option_recommendation["bid"]
                                if option_recommendation
                                else None
                            ),

                        "option_ask":
                            (
                                option_recommendation["ask"]
                                if option_recommendation
                                else None
                            ),

                        "option_quote_timestamp":
                            (
                                option_recommendation[
                                    "quote_time_utc"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "option_quote_timeframe":
                            (
                                option_recommendation[
                                    "quote_timeframe"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "option_quote_source":
                            (
                                option_recommendation[
                                    "quote_source"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "option_spread_pct": option_spread_pct,

                        "option_volume":
                            (
                                option_recommendation["volume"]
                                if option_recommendation
                                else None
                            ),

                        "option_open_interest":
                            (
                                option_recommendation[
                                    "open_interest"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "expiration_risk":
                            (
                                option_recommendation[
                                    "expiration_risk"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "expiration_bucket":
                            (
                                option_recommendation[
                                    "expiration_bucket"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "option_quality_score":
                            (
                                option_recommendation[
                                    "option_quality_score"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "option_liquidity_grade":
                            (
                                option_recommendation[
                                    "option_liquidity_grade"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "option_quote_freshness":
                            (
                                option_recommendation[
                                    "quote_freshness"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "option_quote_age_minutes":
                            (
                                option_recommendation[
                                    "quote_age_minutes"
                                ]
                                if option_recommendation
                                else None
                            ),

                        "event_blocked": event_risk.get("blocked"),

                        "event_block_reason": event_risk.get("reason"),

                        "option_strike":
                            (
                                option_recommendation["strike"]
                                if option_recommendation
                                else None
                            ),

                        "market_regime":
                            analysis_15m.get(
                                "market_regime",
                                "UNKNOWN"
                            ),

                        "replay_outcome":
                            (
                                replay_result["outcome"]
                                if replay_result
                                else None
                            ),

                        "bars_to_outcome":
                            (
                                replay_result[
                                    "bars_processed"
                                ]
                                if replay_result
                                else None
                            ), 

                        "r_multiple":
                            (
                                replay_result.get(
                                    "r_multiple"
                                )
                                if replay_result
                                else None
                            ),

                        "mae":
                            (
                                replay_result.get(
                                    "mae"
                                )
                                if replay_result
                                else None
                            ),

                        "mfe":
                            (
                                replay_result.get(
                                    "mfe"
                                )
                                if replay_result
                                else None
                            ),                                                       

                        "reasons":
                            json.dumps(
                                final_reasons
                            ),

                        "trading_day": trading_day,

                        "scan_id": scan_id,

                        "scan_timestamp": scan_timestamp.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    }
                )            


            if option_recommendation and projection:

                debug_print(
                    f"\n[OPTIONS] {symbol}"
                )

                debug_print(
                    f"Best Contract: "
                    f"{option_recommendation['ticker']}"
                )

                debug_print(
                    f"Strike: "
                    f"{option_recommendation['strike']}"
                )

                debug_print(
                    f"Delta: "
                    f"{round(option_recommendation['delta'], 2)}"
                )

                debug_print(
                    f"IV: "
                    f"{round(option_recommendation['iv'], 2)}"
                )

                debug_print(
                    f"Gamma: "
                    f"{round(option_recommendation['gamma'], 4)}"
                )

                debug_print(
                    f"Volume: "
                    f"{option_recommendation['volume']}"
                )

                debug_print(
                    f"OI: "
                    f"{option_recommendation['open_interest']}"
                )

                debug_print(
                    f"\n[PROJECTION] {symbol}"
                )

                debug_print(
                    f"Expected Move: "
                    f"{projection['expected_move_pct']}%"
                )

                debug_print(
                    f"Projected Option Gain: "
                    f"{projection['projected_option_gain']}%"
                )

                debug_print(
                    f"Probability: "
                    f"{projection['probability']}%"
                )

                debug_print(
                    f"Best Expiry: "
                    f"{projection['best_expiry']}"
                )

                debug_print(
                    f"Trade Grade: "
                    f"{projection['trade_grade']}"
                )

                debug_print(
                    f"Target Price: "
                    f"{projection['target_price']}"
                )

                debug_print(
                    f"Stop Price: "
                    f"{projection['stop_price']}"
                )


            if position_data:

                debug_print(
                    f"\n[POSITION SIZE] {symbol}"
                )

                debug_print(
                    f"Contracts: "
                    f"{position_data['contracts']}"
                )

                debug_print(
                    f"Max Risk: "
                    f"${position_data['max_risk']}"
                )

                debug_print(
                    f"Estimated Loss: "
                    f"${position_data['estimated_loss']}"
                )

                debug_print(
                    f"Estimated Profit: "
                    f"${position_data['estimated_profit']}"
                )

                debug_print(
                    f"Aggressiveness: "
                    f"{position_data['aggressiveness']}"
                )


            # Print AI analysis
            if summary and isinstance(summary, dict):

                print(f"\n[bold cyan]{symbol} AI ANALYSIS[/bold cyan]")

                print(f"Bias: {summary.get('bias', 'N/A')}")
                print(f"Confidence: {summary.get('confidence', 'N/A')}/10")
                print(f"Strategy: {summary.get('strategy', 'N/A')}")
                print(f"Entry Type: {summary.get('entry_type', 'N/A')}")
                print(f"Expiration: {summary.get('expiration_style', 'N/A')}")
                print(f"Risk: {summary.get('risk_level', 'N/A')}")


                print(
                    f"Entry Setup: "
                    f"{entry_setup['entry_type'] if entry_setup else 'NO_ENTRY'}"
                )

                print(
                    f"Entry Quality: "
                    f"{entry_setup['entry_quality'] if entry_setup else 'LOW'}"
                )

                if entry_setup and entry_setup["entry_trigger"]:

                    print(
                        f"Entry Trigger: "
                        f"{entry_setup['entry_trigger']}"
                    )

                if entry_setup and entry_setup["avoid_chasing"]:

                    print(
                        "[yellow]Avoid chasing extended move[/yellow]"
                    )

                print(f"\nSummary:")
                print(summary.get('summary', 'N/A'))

                print("-" * 80)

        except Exception as e:

            import traceback

            traceback.print_exc()

            results.append(
                build_status_result_row(
                    symbol=symbol,
                    final_signal="ERROR",
                    action_status="AVOID",
                    explanation=str(e),
                    next_condition="Fix scanner exception",
                    blocked_by="SCANNER_ERROR"
                )
            )

            table.add_row(
                symbol,
                "-",
                "ERROR",
                "-",
                "-",
                "False",
                "-",
                str(e),
                "Fix scanner exception"
            )

            symbol_failures[symbol] = str(e)

        time.sleep(0.5)
        symbol_runtimes[symbol] = time.perf_counter() - symbol_start

    # =========================
    # Export Excel Report
    # =========================

    df_results = pd.DataFrame(results)
    scan_runtime_sec = round(time.perf_counter() - scan_perf_start, 2)
    with stage_timer.stage("Dashboard"):

        df_results = _add_candidate_persistence(
            df_results,
            trading_day,
            scan_id
        )
        df_results = _add_relative_strength_rankings(
            df_results
        )
        audit_result = _append_market_opportunity_audit(
            df_results,
            trading_day,
            scan_id
        )
        liquidity_audit_result = _append_option_liquidity_audit(
            df_results,
            trading_day,
            scan_id
        )

    if audit_result:

        print(
            "[MARKET OPPORTUNITY AUDIT] "
            f"saved {audit_result['rows']} rows to "
            f"{audit_result['path']}"
        )

    if liquidity_audit_result:

        print(
            "[OPTION LIQUIDITY AUDIT] "
            f"saved {liquidity_audit_result['rows']} attempts to "
            f"{liquidity_audit_result['path']}"
        )

    option_freshness = df_results.get("Option Quote Freshness", pd.Series(dtype=object)).astype(str).str.upper()
    successful_runtimes = [
        runtime for symbol, runtime in symbol_runtimes.items()
        if symbol not in symbol_failures and runtime is not None
    ]
    average_symbol_runtime = (
        round(sum(successful_runtimes) / len(successful_runtimes), 2)
        if successful_runtimes
        else None
    )
    health = EngineHealth(
        scan_runtime_sec=scan_runtime_sec,
        scanner_runtime=scan_runtime_sec,
        worker_count=SCANNER_MAX_WORKERS,
        polygon_calls=len(scanner_watchlist),
        polygon_failures=len(symbol_failures),
        exceptions=len(symbol_failures),
        average_symbol_runtime=average_symbol_runtime,
        average_symbol_time=average_symbol_runtime,
        symbols_completed=len(scanner_watchlist) - len(symbol_failures),
        symbols_failed=len(symbol_failures),
        fresh_quotes=int(option_freshness.eq("LIVE_QUOTE").sum()),
        stale_quotes=int(option_freshness.eq("STALE_QUOTE").sum()),
        delayed_quotes=int(option_freshness.eq("DELAYED_QUOTE").sum()),
    )
    health.health_score = calculate_health_score(health)

    with stage_timer.stage("Telegram"):

        telegram_summary = _dispatch_telegram_entry_alerts(
            df_results
        )

    candidate_funnel = _build_candidate_funnel(
        df_results,
        telegram_summary
    )
    _print_candidate_funnel(
        candidate_funnel,
        telegram_summary
    )
    funnel_result = _write_candidate_funnel(
        candidate_funnel,
        trading_day,
        scan_id,
        telegram_summary
    )

    if funnel_result:

        print(
            "[CANDIDATE FUNNEL] "
            f"saved to {funnel_result['path']}"
        )

    output_file = (
        settings.scanner_output_file
    )

    run_background(
        _persist_scan_outputs,
        df_results.copy(),
        trading_day,
        scan_id,
        {
            "timestamp": now_et().isoformat(),
            "scan_runtime_sec": health.scan_runtime_sec,
            "health_score": health.health_score,
            "cache_hit_rate": health.cache_hit_rate,
            "worker_count": health.worker_count,
            "polygon_calls": health.polygon_calls,
            "exceptions": health.exceptions,
            "symbols_completed": health.symbols_completed,
            "symbols_failed": health.symbols_failed,
            "average_symbol_runtime": health.average_symbol_runtime,
            "candidate_funnel": candidate_funnel,
            "telegram_summary": telegram_summary,
        },
        output_file,
        dict(stage_timer.timings),
        now_et()
    )

    print(
        "[BACKGROUND] queued scanner persistence "
        "(DB, snapshots, lifecycle, health, Excel, profile)"
    )

    console.print(table)

    summarize_telemetry()


if __name__ == "__main__":
    run_scanner()