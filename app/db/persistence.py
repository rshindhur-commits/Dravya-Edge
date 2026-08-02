from __future__ import annotations

import math
import os
import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.connection import get_engine


logger = logging.getLogger(__name__)


def db_writes_enabled() -> bool:
    value = os.getenv("DB_WRITE_ENABLED", "false")

    return str(value).strip().lower() in [
        "1",
        "true",
        "yes",
        "y",
        "on"
    ] and bool(os.getenv("DATABASE_URL", "").strip())


def database_reachable():
    """Can a query actually run, as opposed to: is a URL configured.

    `db_writes_enabled()` answers the second question, and the dashboard printed
    its answer as "DB writes ACTIVE". A container holding a URL it cannot reach
    therefore rendered green while every read returned empty and every write was
    silently dropped -- which is the state that let a worker publish "No
    positions were closed this week" over a week with seven.
    """

    try:

        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))

        return True

    except Exception as exc:

        logger.warning("Database unreachable: %s", exc)

        return False


def database_status():
    """`ON`, `OFF` (deliberately switched off), or `UNREACHABLE` (broken)."""

    if not db_writes_enabled():

        return "OFF"

    return "ON" if database_reachable() else "UNREACHABLE"


def print_db_status(prefix="[DB STATUS]"):
    print(
        prefix,
        "DB_WRITE_ENABLED=",
        os.getenv("DB_WRITE_ENABLED"),
        "DATABASE_URL_PRESENT=",
        bool(os.getenv("DATABASE_URL", "").strip()),
        "DB_WRITES_ACTIVE=",
        db_writes_enabled()
    )


def _json_safe(value):
    if value is None:
        return None

    if isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    try:
        import pandas as pd

        if pd.isna(value):
            return None

    except Exception:
        pass

    return str(value)


def _safe_execute(statement, params):
    if not db_writes_enabled():
        return False

    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(statement, params)
        return True

    except Exception as exc:
        logger.warning(
            "DB write failed; continuing without blocking operational flow",
            exc_info=exc
        )
        return False


def _payload_param(statement):
    return statement.bindparams(
        bindparam("payload", type_=JSONB)
    )


def record_alert_event(
    alert_type,
    symbol=None,
    direction=None,
    option_ticker=None,
    status="SKIPPED",
    reason=None,
    dedupe_key=None,
    payload=None,
    telegram_message_id=None
):
    statement = _payload_param(text("""
        INSERT INTO alert_events (
            dedupe_key,
            alert_type,
            symbol,
            direction,
            option_ticker,
            status,
            reason,
            payload,
            sent_at,
            telegram_message_id
        ) VALUES (
            :dedupe_key,
            :alert_type,
            :symbol,
            :direction,
            :option_ticker,
            :status,
            :reason,
            :payload,
            CASE WHEN :status = 'SENT' THEN now() ELSE NULL END,
            :telegram_message_id
        )
        ON CONFLICT (dedupe_key) DO UPDATE SET
            status = EXCLUDED.status,
            reason = EXCLUDED.reason,
            payload = EXCLUDED.payload,
            sent_at = COALESCE(alert_events.sent_at, EXCLUDED.sent_at),
            telegram_message_id = COALESCE(
                EXCLUDED.telegram_message_id,
                alert_events.telegram_message_id
            )
    """))
    params = {
        "dedupe_key": dedupe_key or f"{alert_type}|attempt|{uuid4()}",
        "alert_type": alert_type,
        "symbol": symbol,
        "direction": direction,
        "option_ticker": option_ticker,
        "status": status,
        "reason": reason,
        "payload": _json_safe(payload or {}),
        "telegram_message_id": telegram_message_id
    }

    return _safe_execute(statement, params)


def upsert_paper_trade(trade):
    trade = trade or {}
    statement = _payload_param(text("""
        INSERT INTO paper_trades (
            trade_key,
            symbol,
            direction,
            option_ticker,
            status,
            entry_source,
            entry_price,
            option_entry_mid,
            close_price,
            option_close_mid,
            pnl_pct,
            r_multiple,
            payload,
            opened_at,
            closed_at,
            holding_profile,
            overnight_count,
            days_held,
            forced_eod_exit,
            session_id_open,
            session_id_close,
            updated_at
        ) VALUES (
            :trade_key,
            :symbol,
            :direction,
            :option_ticker,
            :status,
            :entry_source,
            :entry_price,
            :option_entry_mid,
            :close_price,
            :option_close_mid,
            :pnl_pct,
            :r_multiple,
            :payload,
            :opened_at,
            :closed_at,
            :holding_profile,
            :overnight_count,
            :days_held,
            :forced_eod_exit,
            :session_id_open,
            :session_id_close,
            now()
        )
        ON CONFLICT (trade_key) DO UPDATE SET
            symbol = EXCLUDED.symbol,
            direction = EXCLUDED.direction,
            option_ticker = EXCLUDED.option_ticker,
            status = EXCLUDED.status,
            entry_source = EXCLUDED.entry_source,
            entry_price = EXCLUDED.entry_price,
            option_entry_mid = EXCLUDED.option_entry_mid,
            close_price = EXCLUDED.close_price,
            option_close_mid = EXCLUDED.option_close_mid,
            pnl_pct = EXCLUDED.pnl_pct,
            r_multiple = EXCLUDED.r_multiple,
            payload = EXCLUDED.payload,
            opened_at = EXCLUDED.opened_at,
            closed_at = EXCLUDED.closed_at,
            holding_profile = EXCLUDED.holding_profile,
            overnight_count = EXCLUDED.overnight_count,
            days_held = EXCLUDED.days_held,
            forced_eod_exit = EXCLUDED.forced_eod_exit,
            session_id_open = EXCLUDED.session_id_open,
            session_id_close = EXCLUDED.session_id_close,
            updated_at = now()
        -- A close is terminal. Upserts are queued jobs carrying a snapshot of
        -- the trade taken when they were queued, and nothing orders them, so an
        -- OPEN snapshot queued by an earlier scan can run after the close and
        -- revert the row. On 2026-07-31 CRWD sat OPEN for 39 minutes after
        -- exiting and NVDA was still OPEN 71 minutes after its exit alert, with
        -- the realised R existing only inside an alert payload.
        --
        -- A CLOSED write always applies, so genuine corrections to a closed
        -- trade still land; only the regression to OPEN is refused.
        WHERE paper_trades.status IS DISTINCT FROM 'CLOSED'
           OR EXCLUDED.status = 'CLOSED'
    """))
    params = {
        "trade_key": trade.get("trade_key"),
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "option_ticker": trade.get("option_ticker"),
        "status": trade.get("status"),
        "entry_source": trade.get("entry_source"),
        "entry_price": trade.get("entry_price"),
        "option_entry_mid": trade.get("option_mid"),
        "close_price": trade.get("close_price"),
        "option_close_mid": trade.get("option_close_mid"),
        "pnl_pct": trade.get("pnl_pct"),
        "r_multiple": trade.get("r_multiple"),
        "payload": _json_safe(trade),
        # The *_utc variants, which carry an offset. `opened_at`/`closed_at` are
        # naive ET wall-clock strings from `_timestamp_for_key`, and Postgres
        # read them as UTC when writing a timestamptz column -- so every row was
        # four hours early. NVDA on 2026-07-31 stored `10:58:46+00:00` for a
        # trade opened at 14:58:46 UTC, while `created_at` on the same row was
        # correct, which is the tell.
        #
        # Deliberately not fixed in `_timestamp_for_key`: that string is part of
        # `trade_key` ("NVDA|O:NVDA...|2026-07-31 12:57:59"), so changing its
        # format would break identity for every position open across the change.
        # Migration 024 corrects the rows already written.
        "opened_at": trade.get("opened_at_utc") or trade.get("opened_at"),
        "closed_at": trade.get("closed_at_utc") or trade.get("closed_at"),
        # Migration 012 lifecycle columns. These were never written, so the
        # holding_profile column kept its default while the payload carried the
        # real value -- on 2026-07-29 the column read INTRADAY for a MULTIDAY
        # trade. Reporting queries read the column, so it must be authoritative.
        "holding_profile": trade.get("holding_profile"),
        "overnight_count": trade.get("overnight_count"),
        "days_held": trade.get("days_held"),
        "forced_eod_exit": bool(trade.get("forced_eod_exit")),
        "session_id_open": trade.get("session_id_open"),
        "session_id_close": trade.get("session_id_close"),
    }

    if not params["trade_key"] or not params["symbol"]:
        return False

    return _safe_execute(statement, params)


def record_scanner_run_start(run_id, payload=None, started_at=None):
    """Record a scan start. `started_at` is when the scan began, not when this ran.

    See record_scanner_run_finish for why the distinction matters.
    """

    statement = _payload_param(text("""
        INSERT INTO scanner_runs (
            run_id,
            status,
            payload,
            started_at
        ) VALUES (
            :run_id,
            'STARTED',
            :payload,
            COALESCE(CAST(:started_at AS timestamptz), now())
        )
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            started_at = COALESCE(scanner_runs.started_at, EXCLUDED.started_at)
    """))

    return _safe_execute(
        statement,
        {
            "run_id": run_id,
            "payload": _json_safe(payload or {}),
            "started_at": _timestamp_text(started_at),
        }
    )


def _timestamp_text(value):
    """Coerce a timestamp to a string Postgres can cast, or None."""

    if value is None or value == "":
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    return str(value)


def record_scanner_run_finish(run_id, status="FINISHED", rows_count=None, payload=None,
                              finished_at=None):
    """Record a scan completion at the time it actually completed.

    `finished_at` was `now()`, evaluated when the write executed. These writes go
    through a single-threaded background queue behind hundreds of gate, rule and
    snapshot rows, so the recorded time was when the queue drained, not when the
    scan ended. On 2026-07-30 a scan the engine measured at 88.4 seconds was
    recorded as 312, and rows sat at STARTED for minutes after finishing.

    The table then reads as though scans overlap and runs are orphaned. That cost
    real diagnostic time: it produced a confident and wrong conclusion that the
    container was restarting every fifteen minutes and running concurrent scanners,
    when the scanner was healthy and only its bookkeeping was late.

    Falls back to `now()` when the caller has no timestamp, so a missing value
    degrades to the old behaviour rather than a null.
    """

    statement = _payload_param(text("""
        INSERT INTO scanner_runs (
            run_id,
            status,
            rows_count,
            payload,
            finished_at
        ) VALUES (
            :run_id,
            :status,
            :rows_count,
            :payload,
            COALESCE(CAST(:finished_at AS timestamptz), now())
        )
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            rows_count = EXCLUDED.rows_count,
            payload = EXCLUDED.payload,
            finished_at = COALESCE(CAST(:finished_at AS timestamptz), now())
    """))

    return _safe_execute(
        statement,
        {
            "run_id": run_id,
            "status": status,
            "rows_count": rows_count,
            "payload": _json_safe(payload or {}),
            "finished_at": _timestamp_text(finished_at),
        }
    )


def _gate_decision_params(row, run_id=None):

    if row is None:
        row = {}
    action_status = row.get("Action Status")
    reason = (
        row.get("Action Reason")
        or row.get("Do Not Enter Reason")
        or row.get("Rejected Trade Reason")
        or row.get("Reasons")
    )
    payload = {
        "final_signal": row.get("Final Signal"),
        "top_candidate": row.get("Top Candidate"),
        "setup_percent": row.get("Setup %"),
        "candidate_rr": row.get("Candidate RR") or row.get("RR"),
        "realtime_ready": row.get("Realtime Ready"),
        "realtime_block_reason": row.get("Realtime Block Reason"),
        "option_quality_score": row.get("Option Quality Score"),
        "option_spread_pct": row.get("Option Spread %"),
        "event_blocked": row.get("Event Blocked"),
        "regime_blocked": row.get("Regime Blocked")
    }

    return {
        "run_id": run_id,
        "symbol": row.get("Symbol"),
        "decision": action_status or "UNKNOWN",
        "reason": reason,
        "action_status": action_status,
        "blocked_by": row.get("Blocked By"),
        "payload": _json_safe(payload)
    }


def record_gate_decision(row, run_id=None):
    statement = _payload_param(text("""
        INSERT INTO gate_decisions (
            run_id,
            symbol,
            decision,
            reason,
            action_status,
            blocked_by,
            payload
        ) VALUES (
            :run_id,
            :symbol,
            :decision,
            :reason,
            :action_status,
            :blocked_by,
            :payload
        )
    """))

    return _safe_execute(
        statement,
        _gate_decision_params(row, run_id=run_id)
    )


def record_gate_decisions(rows, run_id=None):

    rows = list(rows or [])

    if not rows or not db_writes_enabled():

        return 0

    statement = _payload_param(text("""
        INSERT INTO gate_decisions (
            run_id,
            symbol,
            decision,
            reason,
            action_status,
            blocked_by,
            payload
        ) VALUES (
            :run_id,
            :symbol,
            :decision,
            :reason,
            :action_status,
            :blocked_by,
            :payload
        )
    """))
    params = [
        _gate_decision_params(row, run_id=run_id)
        for row in rows
    ]

    try:

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(statement, params)

        return len(params)

    except Exception as exc:

        logger.warning(
            "Gate decision DB batch failed; continuing scanner output",
            exc_info=exc
        )

    return 0