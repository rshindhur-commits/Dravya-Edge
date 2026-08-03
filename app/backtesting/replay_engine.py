"""Replay the live scanner over historical data.

The design rule is that this module owns no trading logic. Every decision is
delegated to the function the live scanner calls -- ``compute_indicators``,
``analyze_setup``, ``detect_entry``, ``calculate_risk``, ``evaluate_exit`` --
so a replay can only disagree with production if the *inputs* differ, never
because a second implementation drifted. The previous backtester's
``no_lookahead_scanner`` had its own indicator set (``enrich_indicators``, ATR
as a rolling High-Low mean) and resolved trades with a stop/target barrier, so
it could report an edge the live engine had no way of earning.

Two contracts recovered by measuring the nine live trades of 2026-07-30/31 and
reproduced here:

* **Exits are decided on 15m, fills are priced on 5m.** ``evaluate_exit``
  receives ``df_15m``/``analysis_15m``; the fill uses the latest 5m close. See
  ``app/main.py`` around the ``evaluate_exit`` call.
* **Entries fill at the decision candle's close** -- exact on all nine.

Position state is evolved the way ``paper_trade_manager.update_paper_trade``
does it, which is not the obvious way: ``stop_loss`` is overwritten by the
engine's ``updated_stop`` each scan while ``initial_stop_loss`` stays frozen
(R is always measured against entry risk), and excursions ratchet with ``max``
rather than being overwritten -- the bug fixed in ``eb56f75``, where MFE reset
every scan and profit protection could never see the peak it defends.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.backtesting.historical_market_data import (
    DEFAULT_DECISION_LAG_MINUTES,
    frame_as_of,
    load_replay_frames,
)
from app.backtesting.historical_options import (
    DEFAULT_MAX_SPREAD_PCT,
    fill_price,
    is_tradeable,
    option_bars,
    price_at,
    quote_at,
)
from app.exit.exit_engine import evaluate_exit
from app.indicators.technical_indicators import compute_indicators
from app.risk.risk_manager import calculate_risk
from app.strategies.entry_engine import detect_entry
from app.strategies.momentum_strategy import analyze_setup
from app.utils.timeframe_resampler import resample_timeframe

# The live auto-paper entry window, ET. Outside it the scanner watches but does
# not open, so a replay that entered outside it would be reporting trades the
# system would never have taken.
ENTRY_WINDOW_START = "09:45"
ENTRY_WINDOW_END = "15:30"


@dataclass
class ReplayConfig:

    decision_lag_minutes: float = DEFAULT_DECISION_LAG_MINUTES
    lookback_days: int = 5
    entry_window_start: str = ENTRY_WINDOW_START
    entry_window_end: str = ENTRY_WINDOW_END
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT
    max_open_positions: int = 1
    # Price fills at the quoted bid/ask rather than the mid. Mid-pricing is the
    # single most flattering assumption an options backtest can make; it is
    # available only so a run can quantify how much of the result it is worth.
    use_spread_fills: bool = True
    # Resolve the contract to trade. Signature: (symbol, direction, moment) ->
    # option ticker or None. Parity runs pin this to what live chose; a
    # forward run supplies a selector built on the live ranker.
    contract_selector: object = None


@dataclass
class ReplayTrade:

    symbol: str
    direction: str
    entry_type: str
    scan_id: str
    entry_time: datetime
    entry_price: float
    stop_loss: float
    initial_stop_loss: float
    take_profit: float
    option_ticker: str = None
    option_entry_quote: dict = None
    option_entry_fill: float = None
    exit_time: datetime = None
    exit_price: float = None
    option_exit_quote: dict = None
    option_exit_fill: float = None
    exit_reason: str = None
    exit_rule: str = None
    r_multiple: float = None
    option_pnl_pct_gross: float = None
    option_pnl_pct_net: float = None
    bars_in_trade: int = 0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    state: dict = field(default_factory=dict)

    @property
    def is_open(self):

        return self.exit_time is None


def _et(moment):

    stamp = pd.Timestamp(moment)

    return stamp.tz_localize("America/New_York") if stamp.tzinfo is None else stamp


def _within_entry_window(moment, config):

    clock = _et(moment).strftime("%H:%M")

    return config.entry_window_start <= clock <= config.entry_window_end


def _is_short(direction, entry_type):
    """Mirror the live inference, which reads entry_type not the current stop.

    ``app/main.py`` is explicit about why: a short whose stop has moved to
    breakeven would infer LONG from stop-vs-entry and flip every exit
    comparison.
    """

    if entry_type and "SHORT" in str(entry_type).upper():

        return True

    return str(direction or "").upper() == "PUT"


def build_frames(raw_5m, moment, symbol, config):
    """The three timeframes a scan sees, from one truncated 5m frame.

    Returns ``(df_5m, df_15m, df_1h, analysis_5m, analysis_15m, analysis_1h)``
    with ``None`` entries where there is too little history -- which is a real
    state live handles, not an error: ``compute_indicators`` returns an EMPTY
    frame below its per-interval minimum rather than raising, and the exit
    engine simply cannot run that scan.
    """

    visible = frame_as_of(
        raw_5m,
        moment,
        bar_minutes=5,
        decision_lag_minutes=config.decision_lag_minutes,
    )

    if visible.empty:

        return (None,) * 6

    df_5m = compute_indicators(visible.copy(), interval="5m", symbol=symbol)
    df_15m = compute_indicators(
        resample_timeframe(visible, "15m"), interval="15m", symbol=symbol
    )
    df_1h = compute_indicators(
        resample_timeframe(visible, "1h"), interval="1h", symbol=symbol
    )

    def _analyse(frame, label):

        if frame is None or frame.empty:

            return {
                "signal": "NEUTRAL",
                "score": 0,
                "reasons": [f"{label} unavailable"],
                "valid": False,
            }

        return analyze_setup(frame)

    return (
        df_5m,
        df_15m,
        df_1h,
        _analyse(df_5m, "5m"),
        _analyse(df_15m, "15m"),
        _analyse(df_1h, "1h"),
    )


def _open_trade(symbol, moment, scan_id, df_5m, df_15m, analysis_15m, config):
    """Run the live entry path; return a ReplayTrade or None."""

    if df_15m is None or df_15m.empty or df_5m is None or df_5m.empty:

        return None

    entry_setup = detect_entry(df_15m, analysis_15m, symbol=symbol)

    if not entry_setup or entry_setup.get("entry_type") in (None, "NO_ENTRY"):

        return None

    risk_setup = calculate_risk(df_15m, analysis_15m, entry_setup)

    if not risk_setup.get("trade_allowed"):

        return None

    stop_loss = risk_setup.get("stop_loss")
    take_profit = risk_setup.get("take_profit")

    # Entries fill at the decision candle close -- exact on all nine fixtures.
    entry_price = float(df_5m["Close"].iloc[-1])

    if stop_loss is None or take_profit is None:

        return None

    direction = "PUT" if _is_short(None, entry_setup.get("entry_type")) else "CALL"

    # Price geometry is a hard gate live applies before anything else.
    if direction == "CALL" and not (stop_loss < entry_price < take_profit):

        return None

    if direction == "PUT" and not (take_profit < entry_price < stop_loss):

        return None

    trade = ReplayTrade(
        symbol=symbol,
        direction=direction,
        entry_type=entry_setup.get("entry_type"),
        scan_id=scan_id,
        entry_time=_et(moment),
        entry_price=entry_price,
        stop_loss=float(stop_loss),
        initial_stop_loss=float(stop_loss),
        take_profit=float(take_profit),
    )

    ticker = None

    if config.contract_selector:

        ticker = config.contract_selector(symbol, direction, moment)

    if ticker:

        quote = quote_at(ticker, moment)
        tradeable, reason = is_tradeable(quote, config.max_spread_pct)

        if not tradeable:

            return None

        trade.option_ticker = ticker
        trade.option_entry_quote = quote
        trade.option_entry_fill = (
            fill_price(quote, "BUY") if config.use_spread_fills else quote["mid"]
        )

    trade.state = {
        "symbol": symbol,
        "status": "OPEN",
        "entry_type": trade.entry_type,
        "entry_price": entry_price,
        "stop_loss": trade.stop_loss,
        "initial_stop_loss": trade.initial_stop_loss,
        "take_profit": trade.take_profit,
        "highest_price": entry_price,
        "lowest_price": entry_price,
        "bars_in_trade": 0,
        "partial_profit_taken": False,
        "holding_profile": "INTRADAY",
        "mfe_r": 0.0,
        "mae_r": 0.0,
    }

    return trade


def _manage_trade(trade, moment, df_5m, df_15m, analysis_15m, config):
    """One scan's exit evaluation, mirroring app/main.py's call and writeback."""

    if df_15m is None or df_15m.empty:

        return

    verdict = evaluate_exit(
        df_15m,
        analysis_15m,
        {
            "stop_loss": trade.state["stop_loss"],
            "initial_stop_loss": trade.state["initial_stop_loss"],
            "take_profit": trade.state["take_profit"],
            "entry_price": trade.state["entry_price"],
        },
        {"entry_type": trade.state["entry_type"]},
        trade_state=trade.state,
    )

    # Writeback, matching update_paper_trade: the protective stop moves, entry
    # risk does not, and excursions ratchet rather than overwrite.
    trade.state["highest_price"] = verdict["highest_price"]
    trade.state["lowest_price"] = verdict["lowest_price"]
    trade.state["stop_loss"] = verdict["updated_stop"]
    trade.state["bars_in_trade"] = verdict["bars_in_trade"]
    trade.state["partial_profit_taken"] = verdict["partial_profit_taken"]
    trade.state["v1_ema_grace_pending"] = verdict.get("v1_ema_grace_pending")

    for key in ("profit_protection_active", "profit_lock_stop", "profit_giveback_r"):

        if verdict.get(key) is not None:

            trade.state[key] = verdict[key]

    if verdict.get("mfe_r") is not None:

        trade.state["mfe_r"] = max(trade.state.get("mfe_r") or 0.0, verdict["mfe_r"])
        trade.mfe_r = trade.state["mfe_r"]

    trade.bars_in_trade = verdict["bars_in_trade"]

    if not verdict["exit_signal"]:

        return

    # Live decides on 15m but fills against the fresher 5m mark.
    exit_price = (
        float(df_5m["Close"].iloc[-1])
        if df_5m is not None and not df_5m.empty
        else verdict["current_price"]
    )

    _close_trade(
        trade,
        moment,
        exit_price,
        verdict.get("exit_reason"),
        verdict.get("exit_rule"),
        config,
    )


def _close_trade(trade, moment, exit_price, reason, rule, config):

    trade.exit_time = _et(moment)
    trade.exit_price = float(exit_price)
    trade.exit_reason = reason
    trade.exit_rule = rule

    risk = abs(trade.entry_price - trade.initial_stop_loss)

    if risk > 0:

        move = (
            trade.entry_price - trade.exit_price
            if trade.direction == "PUT"
            else trade.exit_price - trade.entry_price
        )
        trade.r_multiple = round(move / risk, 4)

    if not trade.option_ticker:

        return

    quote = quote_at(trade.option_ticker, moment)

    if not quote:

        return

    trade.option_exit_quote = quote
    trade.option_exit_fill = (
        fill_price(quote, "SELL") if config.use_spread_fills else quote["mid"]
    )

    entry_mid = (trade.option_entry_quote or {}).get("mid")

    if entry_mid:

        trade.option_pnl_pct_gross = round(
            (quote["mid"] - entry_mid) / entry_mid * 100, 4
        )

    if trade.option_entry_fill and trade.option_exit_fill:

        trade.option_pnl_pct_net = round(
            (trade.option_exit_fill - trade.option_entry_fill)
            / trade.option_entry_fill
            * 100,
            4,
        )


def replay_day(symbols, trading_day, scan_times, config=None, raw_frames=None):
    """Replay ``trading_day`` scan by scan.

    ``scan_times`` should be the real ET scan clock -- ``scanner_runs`` or a
    trade's ``scan_id``. The live cadence is not uniform, so a synthetic grid
    changes which bar every decision lands on.

    ``raw_frames`` optionally supplies pre-loaded 5m frames keyed by symbol,
    which is what makes a multi-day run affordable: the frames are otherwise
    re-read from cache once per scan per symbol.
    """

    config = config or ReplayConfig()

    frames = raw_frames or {
        symbol: load_replay_frames(
            symbol, trading_day, lookback_days=config.lookback_days
        )
        for symbol in symbols
    }

    open_trades = {}
    closed = []

    for moment in scan_times:

        scan_id = _et(moment).strftime("%Y-%m-%d_%H%M%S")

        for symbol in symbols:

            raw = frames.get(symbol)

            if raw is None or raw.empty:

                continue

            df_5m, df_15m, _, _, analysis_15m, _ = build_frames(
                raw, moment, symbol, config
            )

            if df_5m is None:

                continue

            active = open_trades.get(symbol)

            if active:

                _manage_trade(active, moment, df_5m, df_15m, analysis_15m, config)

                if not active.is_open:

                    closed.append(active)
                    open_trades.pop(symbol)

                # Live evaluates exits first and never re-enters the same
                # symbol on the scan that closed it.
                continue

            if len(open_trades) >= config.max_open_positions:

                continue

            if not _within_entry_window(moment, config):

                continue

            trade = _open_trade(
                symbol, moment, scan_id, df_5m, df_15m, analysis_15m, config
            )

            if trade:

                open_trades[symbol] = trade

    return {
        "closed": closed,
        "open": list(open_trades.values()),
        "trading_day": str(trading_day),
        "scans": len(scan_times),
    }
