from __future__ import annotations

import math
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.connection import get_engine


def db_writes_enabled() -> bool:
    value = os.getenv("DB_WRITE_ENABLED", "false")

    return str(value).strip().lower() in [
        "1",
        "true",
        "yes",
        "y",
        "on"
    ] and bool(os.getenv("DATABASE_URL", "").strip())


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
        print(f"[DB WRITE WARNING] {exc}")
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
            updated_at = now()
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
        "opened_at": trade.get("opened_at"),
        "closed_at": trade.get("closed_at")
    }

    if not params["trade_key"] or not params["symbol"]:
        return False

    return _safe_execute(statement, params)


def record_scanner_run_start(run_id, payload=None):
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
            now()
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
            "payload": _json_safe(payload or {})
        }
    )


def record_scanner_run_finish(run_id, status="FINISHED", rows_count=None, payload=None):
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
            now()
        )
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            rows_count = EXCLUDED.rows_count,
            payload = EXCLUDED.payload,
            finished_at = now()
    """))

    return _safe_execute(
        statement,
        {
            "run_id": run_id,
            "status": status,
            "rows_count": rows_count,
            "payload": _json_safe(payload or {})
        }
    )


def record_gate_decision(row, run_id=None):
    if row is None:
        row = {}
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

    return _safe_execute(
        statement,
        {
            "run_id": run_id,
            "symbol": row.get("Symbol"),
            "decision": action_status or "UNKNOWN",
            "reason": reason,
            "action_status": action_status,
            "blocked_by": row.get("Blocked By"),
            "payload": _json_safe(payload)
        }
    )


def record_gate_decisions(rows, run_id=None):
    count = 0

    for row in rows:
        if record_gate_decision(row, run_id=run_id):
            count += 1

    return count