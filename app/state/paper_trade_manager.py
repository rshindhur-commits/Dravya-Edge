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
from app.storage.daily_paths import daily_path
from app.storage.session_manager import (
    get_or_create_session_manifest,
    get_scan_id,
    get_session_id,
    get_trading_day
)
from app.config.settings import get_int_env


ROOT_DIR = Path(__file__).resolve().parents[2]
PAPER_TRADE_STATE_FILE = str(
    ROOT_DIR / "app" / "state" / "paper_trade_state.json"
)
ET_TZ = ZoneInfo("America/New_York")

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
        from app.db.artifact_persistence import persist_exit_snapshot
        from app.runtime import RuntimeJob, get_runtime_scheduler
        from app.trades.exit_snapshot import create_exit_snapshot
        from app.trades.timeline import append_trade_timeline_event
        exit_snapshot = create_exit_snapshot(trade, row)
        timeline_event = append_trade_timeline_event(
            trade.get("trading_day", get_trading_day()),
            exit_snapshot.trade_id,
            "EXIT",
            exit_snapshot.exit_time,
            exit_snapshot.to_record(),
        )
        get_runtime_scheduler().submit_normal(RuntimeJob(
            name="persist_exit_snapshot_db",
            priority=3,
            func=persist_exit_snapshot,
            args=(exit_snapshot.to_record(), timeline_event),
            cancelable=True,
            scan_id=trade.get("scan_id"),
        ))
        return output

    except Exception as exc:

        print(f"[TREND CAPTURE WARNING] {exc}")
        return None


def _paper_trade_result(trade, close_price):

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

    if is_short:

        pnl = entry_price - close_price
        risk = (
            stop_loss - entry_price
            if stop_loss is not None
            else None
        )

    else:

        pnl = close_price - entry_price
        risk = (
            entry_price - stop_loss
            if stop_loss is not None
            else None
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
        "direction": direction,
        "entry_type": entry_type,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "option_ticker": option_ticker,
        "option_bid": option_bid,
        "option_ask": option_ask,
        "option_mid": option_mid,
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

    try:

        from app.db.artifact_persistence import persist_entry_snapshot
        from app.runtime import RuntimeJob, get_runtime_scheduler
        from app.trades.entry_snapshot import create_entry_snapshot
        from app.trades.timeline import append_trade_timeline_event

        entry_snapshot = create_entry_snapshot(trade)
        timeline_event = append_trade_timeline_event(
            trading_day,
            entry_snapshot.trade_id,
            "ENTRY",
            entry_snapshot.entered_at,
            entry_snapshot.to_record(),
        )
        get_runtime_scheduler().submit_normal(RuntimeJob(
            name="persist_entry_snapshot_db",
            priority=3,
            func=persist_entry_snapshot,
            args=(entry_snapshot.to_record(), timeline_event),
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
    scanner_context=None
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

    if trade.get("status") != "OPEN":

        return trade

    result = _paper_trade_result(
        trade,
        close_price
    )

    if scanner_context and not trade.get("scanner_context"):

        trade["close_scanner_context"] = scanner_context

    closed_dt = _now_et()

    trade["status"] = "CLOSED"
    trade["closed_at"] = _timestamp_for_key(closed_dt)
    trade["closed_at_et"] = closed_dt.isoformat()
    trade["closed_at_utc"] = closed_dt.astimezone(timezone.utc).isoformat()
    trade["close_price"] = close_price
    trade["exit_reason"] = exit_reason
    trade["pnl_pct"] = result["pnl_pct"]
    trade["r_multiple"] = result["r_multiple"]
    trade["outcome"] = result["outcome"]

    if not trade_key:

        trade_key = _state_key_for_trade(trade)

    state[trade_key] = trade
    save_paper_trades(state)

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
    _append_trend_capture_for_closed_trade(trade)
    _save_paper_trade_telemetry(trade)

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
            ).get("Symbol")
        )

        if telegram_result.get("sent"):

            state[trade_key] = trade
            save_paper_trades(state)

    except Exception as e:

        print(
            f"[PAPER TELEGRAM EXIT ALERT ERROR] {symbol}: {e}"
        )

    return trade
