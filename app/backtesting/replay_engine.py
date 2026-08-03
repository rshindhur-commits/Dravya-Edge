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
from app.gates.setup_quality import compute_setup_percent
from app.indicators.technical_indicators import compute_indicators
from app.options.option_metrics import classify_expiration_bucket
from app.risk.risk_manager import calculate_risk
from app.state.holding_policy import derive_holding_profile, holding_policy
from app.strategies.entry_engine import detect_entry
from app.strategies.momentum_strategy import analyze_setup
from app.utils.timeframe_resampler import resample_timeframe

# The live auto-paper entry window, ET. Outside it the scanner watches but does
# not open, so a replay that entered outside it would be reporting trades the
# system would never have taken.
ENTRY_WINDOW_START = "09:45"
ENTRY_WINDOW_END = "15:30"

# When an INTRADAY position is force-closed. Live closes near the bell rather
# than at it, and the replay needs a bar to fill against, so this is the last
# moment a 5m close is taken as the exit mark.
EOD_EXIT_TIME = "15:55"

# ``timeframe_bias`` from app/main.py, which is the Streamlit entrypoint and
# cannot be imported here. Reproduced rather than approximated because it feeds
# the alignment term, and alignment is 25 of the 100 points in Setup % --
# without it the ceiling is 75 against a MULTIDAY threshold of 76, so a wrong
# copy here does not misclassify a few trades, it silently disables MULTIDAY
# altogether. tests/test_backtest_multiday.py asserts this still matches.
_BIAS_BY_SIGNAL = {
    "HIGH CONVICTION BULLISH": 2,
    "BULLISH": 1,
    "NEUTRAL": 0,
    "HIGH CONVICTION BEARISH": -2,
    "WEAK/BEARISH": -1,
    "BEARISH": -1,
}


def _timeframe_bias(analysis):

    return _BIAS_BY_SIGNAL.get(str((analysis or {}).get("signal") or ""), 0)


@dataclass
class ReplayConfig:

    decision_lag_minutes: float = DEFAULT_DECISION_LAG_MINUTES
    lookback_days: int = 5
    entry_window_start: str = ENTRY_WINDOW_START
    entry_window_end: str = ENTRY_WINDOW_END
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT
    max_open_positions: int = 1
    # When INTRADAY positions are force-closed. See EOD_EXIT_TIME.
    eod_exit_time: str = EOD_EXIT_TIME
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
    holding_profile: str = "INTRADAY"
    # False when an input to the profile decision was unavailable. Every missing
    # input forces INTRADAY, so without this a dead MULTIDAY path is invisible.
    holding_profile_inputs_complete: bool = False
    # Sessions this position has been carried into beyond the one it opened in.
    sessions_held: int = 0
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


def _normalise_selection(selected):
    """Accept a bare ticker or ``(ticker, contract)`` from the selector.

    The contract is wanted for the holding profile -- option quality and the
    expiration bucket are two of the four MULTIDAY conditions -- but a selector
    that only knows a ticker stays valid.
    """

    if not selected:

        return None, None

    if isinstance(selected, (tuple, list)):

        return (selected[0] or None), (selected[1] if len(selected) > 1 else None)

    return selected, None


def _holding_profile(entry_setup, risk_setup, contract, analyses):
    """INTRADAY or MULTIDAY, by live's rule, on live's inputs.

    Returns ``(profile, inputs_complete)``. The flag matters more than it looks:
    every unavailable input pushes Setup % *down*, so a missing one does not
    misclassify at random, it forces INTRADAY -- which is indistinguishable from
    a correct answer and would make the whole MULTIDAY path look implemented
    while being dead. Callers record it rather than assume.
    """

    analysis_5m, analysis_15m, analysis_1h = analyses
    inputs_complete = all(a is not None for a in analyses) and contract is not None

    # main.py: bias_5m * 1 + bias_15m * 3 + bias_1h * 2, plus conviction/4.
    alignment = (
        _timeframe_bias(analysis_5m)
        + _timeframe_bias(analysis_15m) * 3
        + _timeframe_bias(analysis_1h) * 2
        + float((analysis_15m or {}).get("score") or 0) / 4
    )

    # main.py's setup_valid, minus the terms _open_trade has already enforced to
    # get this far: a valid entry_type, trade_allowed, and all three prices
    # present. RR is the one it does not gate on, so it is checked here.
    risk_reward = float(risk_setup.get("risk_reward") or 0)
    setup_valid = (
        risk_reward >= 1.5
        and str((analysis_15m or {}).get("signal") or "NEUTRAL") != "NEUTRAL"
    )

    setup_percent = compute_setup_percent(
        (analysis_15m or {}).get("score"),
        alignment=alignment,
        entry=entry_setup.get("entry_type"),
        setup_valid=setup_valid,
        action_status="ENTER" if setup_valid else "WAIT",
    )

    contract = contract or {}

    profile = derive_holding_profile(
        {
            "Setup %": setup_percent,
            "Candidate RR": risk_reward,
            "Expiration Bucket": contract.get("expiration_bucket")
            or classify_expiration_bucket(contract.get("dte")),
            "Option Quality Score": contract.get("option_quality_score"),
        }
    )

    return str(getattr(profile, "value", profile)), inputs_complete


def _open_trade(
    symbol,
    moment,
    scan_id,
    df_5m,
    df_15m,
    analysis_15m,
    config,
    analysis_5m=None,
    analysis_1h=None,
):
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
    contract = None

    if config.contract_selector:

        ticker, contract = _normalise_selection(
            config.contract_selector(symbol, direction, moment)
        )

        # A configured selector that resolves nothing means there was no
        # contract to buy, so there is no trade. Falling through would open a
        # position on the underlying and manage it to an exit, which produces
        # an R for something that could never have been placed -- and, worse,
        # spends the max_open_positions slot, suppressing tradeable signals
        # behind an untradeable one. On the first forward day, 12 of 16 signals
        # resolved to no contract, so this is most of the run rather than an
        # edge case.
        #
        # Only when a selector is configured. Parity runs leave it None and
        # pin the contract live bought, and must keep opening on that basis.
        if not ticker:

            return None

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
        "mfe_r": 0.0,
        "mae_r": 0.0,
    }

    profile, inputs_complete = _holding_profile(
        entry_setup,
        risk_setup,
        contract,
        (analysis_5m, analysis_15m, analysis_1h),
    )

    trade.holding_profile = profile
    trade.state["holding_profile"] = profile
    trade.holding_profile_inputs_complete = inputs_complete

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


def replay_day(
    symbols,
    trading_day,
    scan_times,
    config=None,
    raw_frames=None,
    carried_trades=None,
):
    """Replay ``trading_day`` scan by scan.

    ``scan_times`` should be the real ET scan clock -- ``scanner_runs`` or a
    trade's ``scan_id``. The live cadence is not uniform, so a synthetic grid
    changes which bar every decision lands on.

    ``raw_frames`` optionally supplies pre-loaded 5m frames keyed by symbol,
    which is what makes a multi-day run affordable: the frames are otherwise
    re-read from cache once per scan per symbol.

    ``carried_trades`` are MULTIDAY positions still open from the previous
    session, keyed by symbol. They are managed from this day's first scan and
    block a new entry in that symbol exactly as a same-day position does.
    """

    config = config or ReplayConfig()

    frames = raw_frames or {
        symbol: load_replay_frames(
            symbol, trading_day, lookback_days=config.lookback_days
        )
        for symbol in symbols
    }

    open_trades = dict(carried_trades or {})

    for trade in open_trades.values():

        trade.sessions_held += 1

    closed = []

    for moment in scan_times:

        scan_id = _et(moment).strftime("%Y-%m-%d_%H%M%S")

        for symbol in symbols:

            raw = frames.get(symbol)

            if raw is None or raw.empty:

                continue

            active = open_trades.get(symbol)

            # The two gates below cost nothing and used to be checked *after*
            # build_frames, which is three full indicator computations. With
            # max_open_positions at 1, that meant every symbol but the one
            # holding the position computed a full frame set at every scan and
            # threw it away -- the single largest cost in a forward run, spent
            # on decisions that could not be taken. Order is otherwise
            # unchanged: a symbol with an open position still builds frames,
            # because it has to be managed.
            if not active:

                if len(open_trades) >= config.max_open_positions:

                    continue

                if not _within_entry_window(moment, config):

                    continue

            df_5m, df_15m, _, analysis_5m, analysis_15m, analysis_1h = build_frames(
                raw, moment, symbol, config
            )

            if df_5m is None:

                continue

            if active:

                _manage_trade(active, moment, df_5m, df_15m, analysis_15m, config)

                if not active.is_open:

                    closed.append(active)
                    open_trades.pop(symbol)

                # Live evaluates exits first and never re-enters the same
                # symbol on the scan that closed it.
                continue

            trade = _open_trade(
                symbol,
                moment,
                scan_id,
                df_5m,
                df_15m,
                analysis_15m,
                config,
                analysis_5m=analysis_5m,
                analysis_1h=analysis_1h,
            )

            if trade:

                open_trades[symbol] = trade

    forced = _force_eod_exits(open_trades, trading_day, frames, config)
    closed.extend(forced)

    return {
        "closed": closed,
        "open": list(open_trades.values()),
        "carried": list(open_trades.values()),
        "forced_eod": forced,
        "trading_day": str(trading_day),
        "scans": len(scan_times),
    }


def _force_eod_exits(open_trades, trading_day, frames, config):
    """Close INTRADAY positions at the bell; leave MULTIDAY ones to carry.

    Live's INTRADAY policy sets ``force_eod_exit=True``, and until now the
    replay simply handed back whatever was still open at the last scan. That
    understates cost in the one direction that matters: an intraday loser was
    left unresolved rather than closed, so it contributed no R and no premium.
    """

    if not open_trades:

        return []

    moment = pd.Timestamp(
        f"{pd.Timestamp(trading_day).date()} {config.eod_exit_time}"
    ).tz_localize("America/New_York")

    forced = []

    for symbol, trade in list(open_trades.items()):

        if not holding_policy(trade.holding_profile).force_eod_exit:

            continue

        raw = frames.get(symbol)
        df_5m = build_frames(raw, moment, symbol, config)[0] if raw is not None else None

        if df_5m is None or df_5m.empty:

            # No bar to fill against. Left open and reported rather than closed
            # at an invented price -- a made-up EOD fill is indistinguishable
            # from a real one once it is in the totals.
            continue

        _close_trade(
            trade,
            moment,
            float(df_5m["Close"].iloc[-1]),
            "EOD_CLOSE",
            "FORCE_EOD_EXIT",
            config,
        )

        forced.append(trade)
        open_trades.pop(symbol)

    return forced


def replay_days(symbols, trading_days, scan_times_for, config=None, on_day=None):
    """Replay consecutive sessions, carrying MULTIDAY positions across them.

    ``scan_times_for(trading_day)`` supplies that day's scan clock.
    ``on_day(day, result)`` is called after each session, for progress.

    Frames are loaded per day rather than once, because each session needs its
    own lookback window; a carried position is re-managed against the new day's
    frames from its first scan, which is what live's
    ``restore_open_multiday_positions`` does at session start.
    """

    config = config or ReplayConfig()

    carried = {}
    results = []

    for trading_day in trading_days:

        frames = {
            symbol: load_replay_frames(
                symbol, trading_day, lookback_days=config.lookback_days
            )
            for symbol in symbols
        }

        result = replay_day(
            symbols,
            trading_day,
            scan_times_for(trading_day),
            config=config,
            raw_frames=frames,
            carried_trades=carried,
        )

        carried = {trade.symbol: trade for trade in result["carried"]}
        results.append(result)

        if on_day:

            on_day(trading_day, result)

    return {
        "days": results,
        "closed": [trade for result in results for trade in result["closed"]],
        # Still open after the final session. Reported separately because they
        # have no exit and belong in no P&L total.
        "open": list(carried.values()),
    }
