from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import csv
from zoneinfo import ZoneInfo

from app.utils.json_store import (
    load_json_file,
    save_json_file
)
from app.gates import (
    active_symbol_trade,
    build_trade_key
)
from app.exit.exit_engine import resolve_risk_per_share
from app.state.holding_policy import derive_holding_profile, holding_policy
from app.storage.daily_paths import daily_path, state_path
from app.storage.session_manager import (
    get_or_create_session_manifest,
    get_scan_id,
    get_session_id,
    get_trading_day
)
from app.config.settings import get_int_env


ROOT_DIR = Path(__file__).resolve().parents[2]
PAPER_TRADE_STATE_FILE = str(state_path("paper_trade_state.json"))
LEGACY_TRADE_STATE_FILE = str(state_path("trade_state.json"))
ET_TZ = ZoneInfo("America/New_York")
PROFILE_OVERRIDE_SOURCES = {"MANUAL_OVERRIDE", "BROKER_SYNC"}

PAPER_TELEMETRY_REQUIRED_FIELDS = [
    "paper_trade",
    "symbol",
    "setup_grade",
    "setup_percent",
    "planned_rr",
    "rs_vs_qqq",
    "market_regime",
    "option_quality_score",
    "pnl_pct",
    "r_multiple",
    "exit_reason",
    "opened_at",
    "closed_at"
]

PAPER_TRADE_EVENT_COLUMNS = [
    "trading_day",
    "session_id",
    "scan_id",
    "event_time",
    "event_time_et",
    "event_time_utc",
    "event_type",
    "trade_key",
    "symbol",
    "direction",
    "option_ticker",
    "entry_price",
    "exit_price",
    "status",
    "r_multiple",
    "exit_reason"
]


def load_paper_trades():

    return load_json_file(
        PAPER_TRADE_STATE_FILE,
        {}
    )


def save_paper_trades(state):

    save_json_file(
        PAPER_TRADE_STATE_FILE,
        state
    )


def restore_open_trades_from_db():
    """Re-adopt open positions the local state file has lost.

    `paper_trade_state.json` is the live source of truth, but on Streamlit Cloud it
    sits on an ephemeral filesystem that is wiped whenever the container restarts.
    Postgres survives, and nothing ever read it back. Two failures on 2026-07-30
    came from that gap:

    * an NVDA put opened at 14:23 vanished from state. A *second* NVDA position
      opened at 14:42 -- impossible while the first was visible, because
      open_paper_trade() returns the existing trade instead. The first was left
      open in the database forever, with no exit and no subscriber alert.
    * `_auto_paper_trade_count_today()` counts trades in the state file, so the
      daily cap reset with it. A limit of 3 produced 6 trades.

    The local file always wins for keys it already has: the database copy is a
    best-effort mirror written through a background queue and can lag. Only keys
    absent locally are re-adopted, so a restore can never overwrite fresher state
    or resurrect a position that was closed while the mirror was behind.
    """

    try:
        from app.db.paper_trade_repository import PaperTradeRepository

        open_rows = PaperTradeRepository().fetch_open()
    except Exception as exc:
        print(f"[PAPER STATE RESTORE WARNING] {exc}")
        return []

    if not open_rows:
        return []

    state = load_paper_trades()
    restored = []

    for row in open_rows:
        trade_key = row.get("trade_key")
        payload = row.get("payload") or {}

        if not trade_key or trade_key in state or not payload.get("symbol"):
            continue

        # A symbol already held locally under a different key means local state is
        # ahead; re-adopting would double the position.
        if active_symbol_trade(state, payload.get("symbol"))[1] is not None:
            continue

        payload.setdefault("trade_key", trade_key)
        state[trade_key] = payload
        restored.append(payload)

    if restored:
        save_paper_trades(state)
        print(
            "[PAPER STATE RESTORE] re-adopted "
            f"{len(restored)} open position(s) the state file had lost: "
            + ", ".join(str(trade.get("symbol")) for trade in restored)
        )

    return restored


def _initial_risk_per_share(entry_price, stop_loss):

    try:
        risk = abs(float(entry_price) - float(stop_loss))
    except (TypeError, ValueError):
        return None

    return risk if risk > 0 else None


def _backfill_initial_stop(trade):
    """Adopt the current stop as the initial stop for pre-existing trades.

    Only safe while the stop has not yet been moved. Once `initial_stop_loss`
    exists it is never rewritten, so a later breakeven or trailing move cannot
    corrupt the R denominator.
    """

    if trade.get("initial_stop_loss") is not None:
        return trade

    trade["initial_stop_loss"] = trade.get("stop_loss")
    trade["initial_risk_per_share"] = _initial_risk_per_share(
        trade.get("entry_price"),
        trade.get("stop_loss")
    )
    return trade


def _queue_paper_trade_upsert(trade):

    try:
        from app.db.persistence import upsert_paper_trade
        from app.runtime import RuntimeJob, get_runtime_scheduler

        get_runtime_scheduler().submit_normal(RuntimeJob(
            name="upsert_paper_trade_db",
            priority=3,
            func=upsert_paper_trade,
            args=(trade.copy(),),
            cancelable=False,
            scan_id=trade.get("scan_id"),
        ))
    except Exception as exc:
        print(f"[PAPER TRADE DB UPSERT WARNING] {exc}")


def get_open_paper_trade(symbol):

    state = load_paper_trades()
    _, trade = active_symbol_trade(state, symbol)
    if trade is None:
        trade = _migrate_legacy_scanner_trade(symbol, state)
    return trade


def _migrate_legacy_scanner_trade(symbol, paper_state):

    legacy_state = load_json_file(LEGACY_TRADE_STATE_FILE, {})
    legacy_trade = legacy_state.get(symbol)
    if not isinstance(legacy_trade, dict) or str(legacy_trade.get("status") or "").upper() != "OPEN":
        return None

    opened_at = legacy_trade.get("opened_at") or _timestamp_for_key(_now_et())
    scanner_context = {
        "Symbol": symbol,
        "Candidate Direction": legacy_trade.get("direction"),
        "Entry": legacy_trade.get("entry_type"),
        "Candidate RR": legacy_trade.get("rr_progress"),
        "Option Quality Score": legacy_trade.get("option_quality_score"),
        "Option Expiration": legacy_trade.get("option_expiration"),
        "Expiration Bucket": legacy_trade.get("option_expiration_bucket"),
    }
    trade = {
        **legacy_trade,
        "trade_id": str(uuid4()),
        "trade_state": "OPEN",
        "holding_profile": derive_holding_profile(scanner_context).value,
        "holding_profile_locked_at": opened_at,
        "holding_profile_override_source": None,
        "days_held": legacy_trade.get("days_held", 1),
        "overnight_count": legacy_trade.get("overnight_count", 0),
        "forced_eod_exit": False,
        "option_mid": legacy_trade.get("option_entry_mid"),
        "scanner_context": scanner_context,
        "trade_mode": "PAPER",
        "entry_source": "LEGACY_SCANNER_STATE_MIGRATION",
        "opened_at": opened_at,
        "opened_at_et": legacy_trade.get("opened_at_et") or opened_at,
        "closed_at": None,
        "close_price": None,
        "exit_reason": None,
    }
    trade_key = _state_key_for_trade(trade)
    trade["trade_key"] = trade_key
    paper_state[trade_key] = trade
    save_paper_trades(paper_state)
    _append_paper_trade_event(trade, "OPEN")

    del legacy_state[symbol]
    save_json_file(LEGACY_TRADE_STATE_FILE, legacy_state)
    return trade


def update_paper_trade(
    symbol,
    highest_price,
    rr_progress,
    updated_stop,
    current_price=None,
    lowest_price=None,
    bars_in_trade=None,
    partial_profit_taken=None,
    option_data=None,
    option_pl=None,
    execution_metrics=None,
    exit_state=None,
):

    state = load_paper_trades()
    trade_key, trade = active_symbol_trade(state, symbol)
    if trade is None:
        return None

    trade["highest_price"] = highest_price
    trade["lowest_price"] = lowest_price if lowest_price is not None else trade.get("lowest_price")
    trade["current_price"] = current_price if current_price is not None else trade.get("current_price")
    trade["current_price_updated_at"] = _now_et().isoformat()
    trade["rr_progress"] = rr_progress
    # Capture the entry risk before the protective stop is allowed to move.
    _backfill_initial_stop(trade)
    trade["stop_loss"] = updated_stop
    if bars_in_trade is not None:
        trade["bars_in_trade"] = bars_in_trade
    if partial_profit_taken is not None:
        trade["partial_profit_taken"] = partial_profit_taken
    if option_data:
        for field, option_field in {
            "option_current_mid": "mid_price",
            "option_bid": "bid",
            "option_ask": "ask",
            "option_spread_pct": "spread_pct",
            "option_volume": "volume",
            "option_open_interest": "open_interest",
            "option_delta": "delta",
            "option_theta": "theta",
            "option_iv": "iv",
            "option_gamma": "gamma",
            "option_expiration_bucket": "expiration_bucket",
            "option_expiration_risk": "expiration_risk",
            "option_quality_score": "option_quality_score",
            "option_liquidity_grade": "option_liquidity_grade",
            "option_quality_reasons": "option_quality_reasons",
            "option_quote_freshness": "quote_freshness",
            "option_quote_age_minutes": "quote_age_minutes",
        }.items():
            trade[field] = option_data.get(option_field)
    if option_pl:
        trade["option_pl_pct"] = option_pl.get("option_pl_pct")
        trade["option_pl_dollars"] = option_pl.get("option_pl_dollars")
    if execution_metrics:
        # Excursions are the extreme over the life of the trade, not the latest
        # scan's reading. This overwrote, so a trade that ran to +1.66R and
        # retraced recorded mfe_r 0.0 -- which is exactly what NVDA did on
        # 2026-07-31 while three "Partial profit threshold reached" signals
        # fired and it closed at +0.60R.
        #
        # That is not only a reporting loss. MFE gates grace-zone eligibility
        # ("in profit or MFE >= 1R") and profit protection, so both were
        # reasoning about a number that reset every scan and could never see
        # the peak they exist to defend. `state_trade_manager` already does
        # this correctly; the two are now consistent.
        for field in ("mfe_r", "mae_r"):
            value = _safe_float(execution_metrics.get(field))
            if value is not None:
                trade[field] = max(_safe_float(trade.get(field)) or 0.0, value)

        if execution_metrics.get("trend_health_score") is not None:
            trade["trend_health_score"] = execution_metrics.get("trend_health_score")
        if execution_metrics.get("trend_health_status") is not None:
            trade["last_trend_health_status"] = execution_metrics.get("trend_health_status")
        if execution_metrics.get("exit_confidence_score") is not None:
            trade["last_exit_confidence_score"] = execution_metrics.get("exit_confidence_score")
    if exit_state is not None:
        trade["v1_ema_grace_pending"] = bool(exit_state.get("v1_ema_grace_pending"))
        for field in ("profit_protection_active", "profit_lock_stop", "profit_giveback_r"):
            if exit_state.get(field) is not None:
                trade[field] = exit_state.get(field)

    state[trade_key] = trade
    save_paper_trades(state)
    _queue_paper_trade_upsert(trade)
    return trade


def _state_key_for_trade(trade):

    return build_trade_key(
        trade.get("symbol"),
        trade.get("option_ticker"),
        trade.get("opened_at")
    )


def _now_et():

    return datetime.now(ET_TZ)


def _timestamp_for_key(dt):

    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_datetime(value):

    if not value:

        return None

    if isinstance(value, datetime):

        return value

    try:

        return datetime.fromisoformat(str(value))

    except Exception:

        pass

    try:

        return datetime.strptime(
            str(value),
            "%Y-%m-%d %H:%M:%S"
        )

    except Exception:

        return None


def _safe_float(value):

    try:

        if value is None:

            return None

        return float(value)

    except Exception:

        return None


def _append_paper_trade_event(trade, event_type, exit_price=None):

    try:

        scanner_context = trade.get("scanner_context") or {}
        trading_day = trade.get(
            "trading_day",
            get_trading_day()
        )
        session_id = trade.get(
            "session_id",
            get_session_id(trading_day)
        )
        scan_id = trade.get(
            "scan_id",
            scanner_context.get("scan_id") or get_scan_id(trading_day)
        )
        get_or_create_session_manifest(trading_day)
        event_path = daily_path(
            trading_day,
            "paper_trade_events.csv"
        )
        event_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )
        event_dt = _now_et()
        event = {
            "trading_day": trading_day,
            "session_id": session_id,
            "scan_id": scan_id,
            "event_time": _timestamp_for_key(event_dt),
            "event_time_et": event_dt.isoformat(),
            "event_time_utc": event_dt.astimezone(timezone.utc).isoformat(),
            "event_type": event_type,
            "trade_key": trade.get("trade_key") or _state_key_for_trade(trade),
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
            "option_ticker": trade.get("option_ticker"),
            "entry_price": trade.get("entry_price"),
            "exit_price": exit_price if exit_price is not None else trade.get("close_price"),
            "status": trade.get("status"),
            "r_multiple": trade.get("r_multiple"),
            "exit_reason": trade.get("exit_reason")
        }
        write_header = (
            not event_path.exists()
            or event_path.stat().st_size == 0
        )

        with event_path.open("a", newline="", encoding="utf-8") as file:

            writer = csv.DictWriter(
                file,
                fieldnames=PAPER_TRADE_EVENT_COLUMNS
            )

            if write_header:

                writer.writeheader()

            writer.writerow(event)

    except Exception as exc:

        print(f"[PAPER EVENT LOG ERROR] {exc}")


def _append_trend_capture_for_closed_trade(trade):

    try:

        from app.analytics.trend_capture import (
            analyze_trend_capture,
            append_trend_capture_row,
            build_trend_capture_row
        )
        from app.analytics.trade_snapshot import (
            append_trade_exit_snapshot,
            build_trade_snapshot
        )
        from app.analytics.trend_health import evaluate_trend_health
        from app.indicators.technical_indicators import compute_indicators
        from app.indicators.technical_indicators import get_polygon_data

        symbol = trade.get("symbol")

        if not symbol:

            return None

        df_5m = get_polygon_data(
            symbol,
            5,
            "minute",
            1
        )
        df_5m = compute_indicators(
            df_5m,
            interval="5m",
            symbol=symbol
        )
        trend_capture = analyze_trend_capture(
            trade,
            df_5m
        )
        latest_bar = {}

        if df_5m is not None and not df_5m.empty:

            close_time = _parse_datetime(
                trade.get("closed_at_et")
                or trade.get("closed_at")
            )

            try:

                index = df_5m.index

                if close_time is not None:

                    index = index.tz_localize(None) if getattr(index, "tz", None) is not None else index
                    close_time = close_time.replace(tzinfo=None)
                    exit_bars = df_5m.loc[index <= close_time]
                    latest_bar = (
                        exit_bars.iloc[-1].to_dict()
                        if not exit_bars.empty
                        else df_5m.iloc[-1].to_dict()
                    )

                else:

                    latest_bar = df_5m.iloc[-1].to_dict()

            except Exception:

                latest_bar = df_5m.iloc[-1].to_dict()
        base_snapshot = build_trade_snapshot(
            trade,
            latest_bar,
            latest_bar,
            {}
        )
        trend_health = evaluate_trend_health(
            base_snapshot
        )
        snapshot = build_trade_snapshot(
            {
                **trade,
                "bars_held": trend_capture.get("bars_held")
            },
            latest_bar,
            latest_bar,
            trend_health
        )
        append_trade_exit_snapshot(
            trade.get("trading_day", get_trading_day()),
            snapshot
        )
        row = build_trend_capture_row(
            trade,
            trend_capture,
            snapshot
        )
        output = append_trend_capture_row(
            trade.get("trading_day", get_trading_day()),
            row
        )
        from app.db.artifact_persistence import persist_completed_trade
        from app.runtime import RuntimeJob, get_runtime_scheduler
        from app.trades.exit_snapshot import create_exit_snapshot
        from app.trades.timeline import append_trade_timeline_event
        exit_snapshot = create_exit_snapshot(trade, row)
        timeline_event = append_trade_timeline_event(
            trade.get("trading_day", get_trading_day()),
            exit_snapshot.trade_id,
            "ExitTriggered",
            exit_snapshot.exit_time,
            exit_snapshot.to_record(),
        )
        get_runtime_scheduler().submit_normal(RuntimeJob(
            name="persist_completed_trade_db",
            priority=3,
            func=persist_completed_trade,
            args=(trade.copy(), exit_snapshot.to_record(), timeline_event),
            cancelable=True,
            scan_id=trade.get("scan_id"),
        ))
        return row

    except Exception as exc:

        print(f"[TREND CAPTURE WARNING] {exc}")
        return None


def _option_trade_result(trade):
    """Close-side option pricing and the P&L that actually reaches the account.

    `option_close_mid` is mapped by upsert_paper_trade but nothing ever set it,
    so every closed trade carried a null exit premium and P&L was computed purely
    on the underlying. That is the wrong instrument: on 2026-07-30 six trades
    moved 0.13%-0.36% in the stock while their options carried 2.1%-8.0%
    round-trip spreads, so trades booked at +1.35R and +0.88R were losses once the
    spread was paid, and nothing in the numbers showed it.

    Two figures are returned deliberately:

    * `option_pnl_pct`      mid to mid - what the position was theoretically worth
    * `option_pnl_pct_net`  ask to bid - what a real round trip returns

    The gap between them is the spread cost, which is the number that decides
    whether a setup is tradeable at all. `update_paper_trade` refreshes the option
    quote each scan, so the latest bid/ask is already on the trade at close.
    """

    entry_mid = _safe_float(trade.get("option_mid"))
    # `option_entry_ask` is frozen at open; `option_ask` is the live quote and
    # holds the exit price by the time this runs. Trades opened before that
    # field existed fall back to the entry mid, which understates the spread
    # rather than cancelling it out entirely.
    entry_ask = _safe_float(trade.get("option_entry_ask")) or entry_mid
    close_bid = _safe_float(trade.get("option_bid"))
    close_ask = _safe_float(trade.get("option_ask"))
    close_mid = _safe_float(trade.get("option_current_mid"))

    if close_mid is None and close_bid is not None and close_ask is not None:
        close_mid = (close_bid + close_ask) / 2

    result = {
        "option_close_bid": close_bid,
        "option_close_ask": close_ask,
        "option_close_mid": _round_or_none(close_mid),
        "option_pnl_pct": None,
        "option_pnl_pct_net": None,
        "option_spread_cost_pct": None,
    }

    if not entry_mid or entry_mid <= 0:
        return result

    if close_mid is not None:
        result["option_pnl_pct"] = round(
            ((close_mid - entry_mid) / entry_mid) * 100,
            2
        )

    # A long option is bought at the ask and sold at the bid. Both legs of the
    # spread are paid by the trade, so this is the honest figure.
    if close_bid is not None and entry_ask and entry_ask > 0:
        result["option_pnl_pct_net"] = round(
            ((close_bid - entry_ask) / entry_ask) * 100,
            2
        )

    if (
        result["option_pnl_pct"] is not None
        and result["option_pnl_pct_net"] is not None
    ):
        result["option_spread_cost_pct"] = round(
            result["option_pnl_pct"] - result["option_pnl_pct_net"],
            2
        )

    return result


def _round_or_none(value, digits=4):
    return None if value is None else round(value, digits)


def _paper_trade_result(trade, close_price):
    """Realised P&L and R for a trade being closed.

    R is measured against the risk frozen at entry, never the stop as it stands
    at exit. `update_paper_trade` writes the moved stop back to `stop_loss`, so
    by the time a trade closes that field may be at breakeven or trailed into
    profit. Dividing by it is not a smaller denominator, it is the wrong one:

    * stop moved to breakeven -> `entry - stop` is 0 -> `r_multiple` is None
    * stop trailed past entry -> the difference goes negative -> also None

    Either way the trade reports no R. That selects precisely against the trades
    that worked, because reaching +1R is what moves the stop in the first place,
    so the surviving R series described only the losers. NVDA on 2026-07-29
    closed with entry and stop both at 193.32 and booked `r_multiple = NaN`.

    `resolve_risk_per_share` is the exit engine's rule for the same question and
    stays the single definition of the R denominator; this reuses it rather than
    restating it. `initial_stop_loss` is frozen at entry by `open_paper_trade`
    and backfilled by `_backfill_initial_stop` for trades opened before it
    existed, with the current stop as the last fallback.
    """

    entry_price = _safe_float(
        trade.get("entry_price")
    )
    stop_loss = _safe_float(
        trade.get("stop_loss")
    )
    take_profit = _safe_float(
        trade.get("take_profit")
    )
    close_price = _safe_float(close_price)

    if entry_price is None or close_price is None:

        return {
            "pnl_pct": None,
            "r_multiple": None,
            "outcome": "UNKNOWN"
        }

    direction = str(
        trade.get("direction")
        or ""
    ).upper()

    is_short = direction == "PUT"

    pnl = (
        entry_price - close_price
        if is_short
        else close_price - entry_price
    )

    # Magnitude, not a signed distance: `pnl` already carries the direction.
    risk = resolve_risk_per_share(
        entry_price,
        _safe_float(trade.get("initial_stop_loss")),
        stop_loss
    )

    pnl_pct = round(
        (pnl / entry_price) * 100,
        2
    )

    r_multiple = None

    if risk and risk > 0:

        r_multiple = round(
            pnl / risk,
            2
        )

    if take_profit is not None:

        if (
            is_short
            and close_price <= take_profit
        ) or (
            not is_short
            and close_price >= take_profit
        ):

            outcome = "TARGET_HIT"

        elif r_multiple is not None and r_multiple > 0:

            outcome = "WIN"

        elif r_multiple is not None and r_multiple < 0:

            outcome = "LOSS"

        else:

            outcome = "FLAT"

    else:

        if pnl > 0:

            outcome = "WIN"

        elif pnl < 0:

            outcome = "LOSS"

        else:

            outcome = "FLAT"

    return {
        "pnl_pct": pnl_pct,
        "r_multiple": r_multiple,
        "outcome": outcome
    }


def _save_paper_trade_telemetry(trade):

    try:

        from app.analytics.trade_telemetry import save_trade_telemetry

        scanner_context = (
            trade.get("scanner_context")
            or trade.get("close_scanner_context")
            or {}
        )
        scanner_context_source = (
            "entry"
            if trade.get("scanner_context")
            else "close"
            if trade.get("close_scanner_context")
            else None
        )

        telemetry_payload = {
            "run_type": "paper_trade",
            "symbol": trade.get("symbol"),
            "final_signal": trade.get("direction"),
            "entry": trade.get("entry_type"),
            "setup_category": trade.get("entry_type"),
            "setup_grade": scanner_context.get("Setup Grade"),
            "setup_percent": scanner_context.get("Setup %"),
            "scanner_final_signal": scanner_context.get("Final Signal"),
            "scanner_context_source": scanner_context_source,
            "scanner_score_15m": scanner_context.get("15m Score"),
            "alignment_score": scanner_context.get("Alignment Score"),
            "entry_price": trade.get("entry_price"),
            "stop_price": trade.get("stop_loss"),
            "target_price": trade.get("take_profit"),
            "close_price": trade.get("close_price"),
            "planned_rr": trade.get("planned_rr"),
            "rs_rank_score": scanner_context.get("RS Rank Score"),
            "rs_vs_qqq": scanner_context.get("RS vs QQQ"),
            "rs_vs_spy": scanner_context.get("RS vs SPY"),
            "relative_volume": scanner_context.get("Relative Volume"),
            "atr_pct": scanner_context.get("ATR %"),
            "market_regime": scanner_context.get("Market Regime"),
            "reference_regime": scanner_context.get("Reference Regime"),
            "regime_blocked": scanner_context.get("Regime Blocked"),
            "regime_block_reason": scanner_context.get("Regime Block Reason"),
            "sector_group": scanner_context.get("Sector Group"),
            "sector_reference": scanner_context.get("Sector Reference"),
            "sector_rs": scanner_context.get("Sector RS"),
            "sector_strength": scanner_context.get("Sector Strength"),
            "strength_rank": scanner_context.get("Strength Rank"),
            "weakness_rank": scanner_context.get("Weakness Rank"),
            "top_5_strongest": scanner_context.get("Top 5 Strongest"),
            "top_5_weakest": scanner_context.get("Top 5 Weakest"),
            "watchlist_advancers": scanner_context.get("Watchlist Advancers"),
            "watchlist_decliners": scanner_context.get("Watchlist Decliners"),
            "watchlist_breadth_score": scanner_context.get("Watchlist Breadth Score"),
            "above_vwap_pct": scanner_context.get("Above VWAP %"),
            "above_ema20_pct": scanner_context.get("Above EMA20 %"),
            "market_data_delay_minutes": scanner_context.get("Market Data Delay Minutes"),
            "realtime_confirmation_needed": scanner_context.get("Realtime Confirmation Needed"),
            "tradingview_check_status": scanner_context.get("TradingView Check Status"),
            "option_ticker": trade.get("option_ticker"),
            "option_bid": trade.get("option_bid"),
            "option_ask": trade.get("option_ask"),
            "option_mid": trade.get("option_mid"),
            "option_strike": scanner_context.get("Option Strike"),
            "option_expiration": scanner_context.get("Option Expiration"),
            "option_spread_pct": scanner_context.get("Option Spread %"),
            "option_volume": scanner_context.get("Option Volume"),
            "option_open_interest": scanner_context.get("Option Open Interest"),
            "option_delta": scanner_context.get("Option Delta"),
            "option_theta": scanner_context.get("Option Theta"),
            "option_iv": scanner_context.get("Option IV"),
            "option_gamma": scanner_context.get("Option Gamma"),
            "expiration_bucket": scanner_context.get("Expiration Bucket"),
            "expiration_risk": scanner_context.get("Expiration Risk"),
            "option_quality_score": scanner_context.get("Option Quality Score"),
            "option_liquidity_grade": scanner_context.get("Option Liquidity Grade"),
            "option_quality_reasons": scanner_context.get("Option Quality Reasons"),
            "option_quote_freshness": scanner_context.get("Option Quote Freshness"),
            "option_quote_age_minutes": scanner_context.get("Option Quote Age Minutes"),
            "event_blocked": scanner_context.get("Event Blocked"),
            "event_block_reason": scanner_context.get("Event Block Reason"),
            "action_status": scanner_context.get("Action Status"),
            "blocked_by": scanner_context.get("Blocked By"),
            "action_reason": scanner_context.get("Action Reason"),
            "next_condition": scanner_context.get("Next Condition"),
            "paper_trade": True,
            "entry_source": trade.get("entry_source"),
            "trade_mode": trade.get("trade_mode"),
            "include_in_strategy_stats": trade.get(
                "include_in_strategy_stats"
            ),
            "live_confirmed": trade.get("live_confirmed"),
            "opened_at": trade.get("opened_at"),
            "closed_at": trade.get("closed_at"),
            "exit_reason": trade.get("exit_reason"),
            "replay_outcome": trade.get("outcome"),
            "pnl_pct": trade.get("pnl_pct"),
            "r_multiple": trade.get("r_multiple"),
            "reasons": trade.get("notes")
        }

        missing_fields = [
            field for field in PAPER_TELEMETRY_REQUIRED_FIELDS
            if field not in telemetry_payload
        ]

        empty_context_fields = [
            field for field in [
                "setup_grade",
                "setup_percent",
                "planned_rr",
                "rs_vs_qqq",
                "market_regime",
                "option_quality_score"
            ]
            if telemetry_payload.get(field) in [None, ""]
        ]

        if missing_fields or empty_context_fields:

            print(
                "[PAPER TELEMETRY WARNING] "
                f"missing={missing_fields} "
                f"empty_context={empty_context_fields}"
            )

        save_trade_telemetry(telemetry_payload)

    except Exception as e:

        print(
            f"[PAPER TELEMETRY ERROR] {e}"
        )


def open_paper_trade(
    symbol,
    direction,
    entry_price,
    stop_loss,
    take_profit,
    entry_type,
    option_ticker=None,
    option_bid=None,
    option_ask=None,
    notes=None,
    scanner_context=None,
    entry_source="MANUAL_PAPER",
    trade_mode="PAPER",
    include_in_strategy_stats=False,
    option_contracts=None
):

    state = load_paper_trades()

    _, existing = active_symbol_trade(
        state,
        symbol
    )

    if existing:

        return existing

    option_mid = None
    max_contracts = get_int_env(
        "MAX_CONTRACTS_PER_TRADE",
        1
    )

    try:

        option_contracts = int(option_contracts or 1)

    except Exception:

        option_contracts = 1

    option_contracts = max(
        1,
        min(option_contracts, max_contracts)
    )

    try:

        if option_bid and option_ask:

            option_mid = (
                float(option_bid)
                + float(option_ask)
            ) / 2

    except Exception:

        option_mid = None

    opened_dt = _now_et()
    opened_at = _timestamp_for_key(opened_dt)

    trade = {
        "trade_id": str(uuid4()),
        "symbol": symbol,
        "status": "OPEN",
        "trade_state": "OPEN",
        "direction": direction,
        "holding_profile": derive_holding_profile(scanner_context or {}).value,
        "holding_profile_locked_at": opened_at,
        "holding_profile_override_source": None,
        "overnight_count": 0,
        "days_held": 1,
        "forced_eod_exit": False,
        "entry_type": entry_type,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        # Frozen at entry. `stop_loss` moves (breakeven, trailing, profit lock);
        # this stays put because it is the denominator for every R measurement.
        "initial_stop_loss": stop_loss,
        "initial_risk_per_share": _initial_risk_per_share(entry_price, stop_loss),
        "take_profit": take_profit,
        "option_ticker": option_ticker,
        "option_bid": option_bid,
        "option_ask": option_ask,
        "option_mid": option_mid,
        # Frozen at entry, like `initial_stop_loss` and for the same reason.
        # `option_bid`/`option_ask` are refreshed on every scan, so by close
        # they hold the exit quote. `_option_trade_result` read the entry ask
        # from `option_ask` and so compared the close bid against the close
        # ask, making `option_pnl_pct_net` come out as minus the current spread
        # on every trade regardless of outcome -- NVDA recorded -2.2% on a 2.2%
        # spread, CRWD -9.21% on a 9.21% spread.
        "option_entry_bid": option_bid,
        "option_entry_ask": option_ask,
        "option_contracts": option_contracts,
        "scanner_context": scanner_context or {},
        "planned_rr": (
            scanner_context or {}
        ).get("Candidate RR"),
        "opened_at": opened_at,
        "opened_at_et": opened_dt.isoformat(),
        "opened_at_utc": opened_dt.astimezone(timezone.utc).isoformat(),
        "closed_at": None,
        "close_price": None,
        "exit_reason": None,
        "rr_progress": 0,
        "bars_in_trade": 0,
        "entry_source": entry_source,
        "trade_mode": trade_mode,
        "include_in_strategy_stats": bool(include_in_strategy_stats),
        "live_confirmed": True,
        "notes": notes or "Paper trade from live-confirmed dashboard candidate"
    }

    trading_day = get_trading_day()
    trade["trading_day"] = trading_day
    trade["session_id"] = get_session_id(trading_day)
    trade["session_id_open"] = trade["session_id"]
    trade["session_id_close"] = None
    trade["session_id_current"] = trade["session_id"]
    trade["scan_id"] = (
        (scanner_context or {}).get("scan_id")
        or get_scan_id(trading_day)
    )

    trade_key = _state_key_for_trade(trade)
    trade["trade_key"] = trade_key

    state[trade_key] = trade
    save_paper_trades(state)

    _append_paper_trade_event(
        trade,
        "OPEN"
    )
    _queue_paper_trade_upsert(trade)

    try:

        from app.db.artifact_persistence import persist_timeline_event
        from app.runtime import RuntimeJob, get_runtime_scheduler
        from app.trades.entry_snapshot import create_entry_snapshot
        from app.trades.timeline import append_trade_timeline_event

        entry_snapshot = create_entry_snapshot(trade)
        timeline_event = append_trade_timeline_event(
            trading_day,
            entry_snapshot.trade_id,
            "EntryOpened",
            entry_snapshot.entered_at,
            entry_snapshot.to_record(),
        )
        get_runtime_scheduler().submit_normal(RuntimeJob(
            name="persist_trade_event_db",
            priority=3,
            func=persist_timeline_event,
            args=(timeline_event,),
            cancelable=True,
            scan_id=trade.get("scan_id"),
        ))

    except Exception as exc:

        print(f"[ENTRY SNAPSHOT WARNING] {exc}")

    return trade


def close_paper_trade(
    symbol,
    close_price=None,
    exit_reason="Manual paper exit",
    scanner_context=None,
    notify_exit=True,
):

    state = load_paper_trades()

    trade_key, trade = active_symbol_trade(
        state,
        symbol
    )

    if trade is None:

        legacy_trade = state.get(symbol)

        if legacy_trade:

            trade_key = symbol
            trade = legacy_trade

    if not trade:

        return None

    if trade.get("status") not in {"OPEN", "PAUSED"}:

        return trade

    result = _paper_trade_result(
        trade,
        close_price
    )

    if scanner_context and not trade.get("scanner_context"):

        trade["close_scanner_context"] = scanner_context

    closed_dt = _now_et()

    trade["status"] = "CLOSED"
    trade["trade_state"] = "CLOSED"
    trade["closed_at"] = _timestamp_for_key(closed_dt)
    trade["closed_at_et"] = closed_dt.isoformat()
    trade["closed_at_utc"] = closed_dt.astimezone(timezone.utc).isoformat()
    trade["session_id_close"] = get_session_id(get_trading_day(closed_dt))
    trade["forced_eod_exit"] = "end-of-day close" in str(exit_reason or "").lower()
    opened_dt = _parse_datetime(trade.get("opened_at_et") or trade.get("opened_at"))
    if opened_dt is not None:
        if opened_dt.tzinfo is None:
            opened_dt = opened_dt.replace(tzinfo=ET_TZ)
        trade["days_held"] = max(1, (closed_dt.date() - opened_dt.date()).days + 1)
        trade["overnight_count"] = max(0, trade["days_held"] - 1)
    trade["close_price"] = close_price
    trade["exit_reason"] = exit_reason
    trade["pnl_pct"] = result["pnl_pct"]
    trade["r_multiple"] = result["r_multiple"]
    trade["outcome"] = result["outcome"]

    # Underlying P&L above is not what the account earns: the position is an
    # option. Record the exit premium and both the mid-to-mid and the realistic
    # ask-to-bid return, so the spread cost is visible instead of silent.
    trade.update(_option_trade_result(trade))

    if not trade_key:

        trade_key = _state_key_for_trade(trade)

    state[trade_key] = trade
    save_paper_trades(state)
    _queue_paper_trade_upsert(trade)

    event_type = (
        "MANUAL_CLOSE"
        if "manual" in str(exit_reason or "").lower()
        else "AUTO_EXIT"
    )
    _append_paper_trade_event(
        trade,
        event_type,
        exit_price=close_price
    )
    trend_capture_row = _append_trend_capture_for_closed_trade(trade)
    _save_paper_trade_telemetry(trade)

    if notify_exit:
        try:

            from app.alerts.telegram_alerts import maybe_send_trade_exit_alert

            telegram_result = maybe_send_trade_exit_alert(
                symbol=symbol,
                trade=trade,
                exit_reason=exit_reason,
                current_price=close_price,
                option_current_mid=trade.get("option_mid"),
                pnl_pct=trade.get("pnl_pct"),
                r_multiple=trade.get("r_multiple"),
                outcome=trade.get("outcome"),
                event_type="EXIT",
                event_timestamp=trade.get("closed_at"),
                expected_underlying_price=(
                    scanner_context or {}
                ).get("Price"),
                price_source="paper_trade_close_price",
                scanner_row_symbol=(
                    scanner_context or {}
                ).get("Symbol"),
                trend_capture_pct=(
                    (trend_capture_row or {}).get("Trend Capture %")
                    if isinstance(trend_capture_row, dict)
                    else None
                )
            )

            if telegram_result.get("sent"):

                state[trade_key] = trade
                save_paper_trades(state)

        except Exception as e:

            print(
                f"[PAPER TELEGRAM EXIT ALERT ERROR] {symbol}: {e}"
            )

    return trade


def _active_paper_trade(state, symbol):

    trade_key, trade = active_symbol_trade(state, symbol)
    if trade is not None:

        return trade_key, trade

    legacy_trade = state.get(symbol)
    if legacy_trade and legacy_trade.get("status") in {"OPEN", "PAUSED"}:

        return symbol, legacy_trade

    return None, None


def pause_paper_trade(symbol, reason="Operational pause"):

    state = load_paper_trades()
    trade_key, trade = _active_paper_trade(state, symbol)
    if trade is None or trade.get("status") == "PAUSED":

        return trade

    paused_at = _now_et()
    trade["status"] = "PAUSED"
    trade["trade_state"] = "PAUSED"
    trade["paused_at"] = _timestamp_for_key(paused_at)
    trade["pause_reason"] = reason
    state[trade_key] = trade
    save_paper_trades(state)
    _append_paper_trade_event(trade, "PAUSED")

    return trade


def resume_paper_trade(symbol):

    state = load_paper_trades()
    trade_key, trade = _active_paper_trade(state, symbol)
    if trade is None or trade.get("status") != "PAUSED":

        return trade

    resumed_at = _now_et()
    trade["status"] = "OPEN"
    trade["trade_state"] = "OPEN"
    trade["resumed_at"] = _timestamp_for_key(resumed_at)
    trade["pause_reason"] = None
    state[trade_key] = trade
    save_paper_trades(state)
    _append_paper_trade_event(trade, "RESUMED")

    return trade


def override_paper_trade_holding_profile(symbol, holding_profile, source="MANUAL_OVERRIDE"):

    source = str(source or "").upper()
    if source not in PROFILE_OVERRIDE_SOURCES:

        raise ValueError("Holding profile changes require MANUAL_OVERRIDE or BROKER_SYNC")

    state = load_paper_trades()
    trade_key, trade = _active_paper_trade(state, symbol)
    if trade is None:

        return None

    profile = holding_policy(holding_profile).holding_profile.value
    trade["holding_profile"] = profile
    trade["holding_profile_override_source"] = source
    trade["holding_profile_overridden_at"] = _timestamp_for_key(_now_et())
    state[trade_key] = trade
    save_paper_trades(state)
    _append_paper_trade_event(trade, "HOLDING_PROFILE_OVERRIDE")

    return trade
