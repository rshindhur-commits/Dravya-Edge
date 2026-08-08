from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from app.gates.rule_evaluation import build_rule_evaluations
from app.storage.daily_paths import daily_path, live_path


TRACE_COLUMNS = [
    "event_id", "trading_day", "time", "symbol", "category", "event",
    "context", "origin", "stage", "rule", "passed", "actual", "required",
    "previous_state", "state_changed", "setup_score", "rr", "option_quality",
    "candle_time", "candle_open", "candle_high", "candle_low", "candle_close",
    "candle_volume", "scanner_recommendation", "execution_eligibility",
    "execution_outcome", "execution_reason", "trade_status", "telegram_status",
    "telegram_reason", "scan_id", "candidate_key", "trade_id",
]


def _read_csv(path: Path):
    try:
        return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _category(event, origin):
    event = str(event or "").upper()
    if origin == "Telegram dispatcher":
        return "Telegram"
    if origin == "Auto-paper gate":
        return "Paper"
    if origin == "Trade lifecycle":
        return "Trades"
    if event in {"FAILED", "ERROR"}:
        return "Errors"
    return "Scanner"


def _record(
    trading_day, time, symbol, event, context, origin, scan_id=None,
    candidate_key=None, trade_id=None, stage=None, rule=None, passed=None,
    actual=None, required=None, previous_state=None, state_changed=None,
    setup_score=None, rr=None, option_quality=None, candle_time=None,
    candle_open=None, candle_high=None, candle_low=None, candle_close=None,
    candle_volume=None, scanner_recommendation=None, execution_eligibility=None,
    execution_outcome=None, execution_reason=None,
    trade_status=None, telegram_status=None, telegram_reason=None,
):
    source = "|".join([
        str(trading_day), str(time), str(symbol), str(event), str(context),
        str(origin), str(scan_id), str(candidate_key), str(trade_id),
    ])
    return {
        "event_id": hashlib.sha256(source.encode()).hexdigest()[:24],
        "trading_day": trading_day,
        "time": time,
        "symbol": symbol,
        "category": _category(event, origin),
        "event": str(event or "").replace("_", " "),
        "context": context,
        "origin": origin,
        "stage": stage,
        "rule": rule,
        "passed": passed,
        "actual": actual,
        "required": required,
        "previous_state": previous_state,
        "state_changed": state_changed,
        "setup_score": setup_score,
        "rr": rr,
        "option_quality": option_quality,
        "candle_time": candle_time,
        "candle_open": candle_open,
        "candle_high": candle_high,
        "candle_low": candle_low,
        "candle_close": candle_close,
        "candle_volume": candle_volume,
        "scanner_recommendation": scanner_recommendation,
        "execution_eligibility": execution_eligibility,
        "execution_outcome": execution_outcome,
        "execution_reason": execution_reason,
        "trade_status": trade_status,
        "telegram_status": telegram_status,
        "telegram_reason": telegram_reason,
        "scan_id": scan_id,
        "candidate_key": candidate_key,
        "trade_id": trade_id,
    }


def _value(row, *names):
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        if str(value).strip().lower() not in {"", "nan", "none"}:
            return value
    return None


def _annotate_state_changes(current, previous_by_symbol):
    previous_by_symbol = dict(previous_by_symbol or {})

    for record in current:
        if record.get("origin") != "Scanner decision" or not record.get("symbol"):
            continue
        symbol = str(record["symbol"])
        previous_state = previous_by_symbol.get(symbol)
        record["previous_state"] = previous_state
        record["state_changed"] = previous_state is not None and previous_state != record["event"]
        previous_by_symbol[symbol] = record["event"]


def build_activity_trace(trading_day, scanner_rows=None, scan_id=None, observed_at=None, directory=None):
    directory = Path(directory) if directory is not None else daily_path(trading_day, "activity_trace.csv").parent
    records = []
    for row in scanner_rows or []:
        event = row.get("Action Status")
        if not event:
            continue
        event_time = row.get("Current ET") or row.get("Data Timestamp ET") or observed_at
        resolved_scan_id = scan_id or row.get("Scan ID")
        records.append(_record(
            trading_day,
            event_time,
            row.get("Symbol"), event,
            row.get("Action Reason") or row.get("Blocked By"), "Scanner decision",
            scan_id=resolved_scan_id,
            candidate_key=row.get("Candidate Persistence Key"),
            stage=row.get("ENTRY_GATE_FAILURE_STAGE") or "Decision",
            rule=row.get("Blocked By") or "Action Status",
            passed=str(event).upper() in {"ENTER", "ENTER_PAPER"},
            actual=event,
            required="ENTER or ENTER_PAPER",
            setup_score=_value(row, "Setup %", "15m Score"),
            rr=_value(row, "Candidate RR", "Risk Reward", "RR"),
            option_quality=_value(row, "Option Quality Score"),
            candle_time=_value(row, "Decision Candle Time ET"),
            candle_open=_value(row, "Decision Candle Open"),
            candle_high=_value(row, "Decision Candle High"),
            candle_low=_value(row, "Decision Candle Low"),
            candle_close=_value(row, "Decision Candle Close"),
            candle_volume=_value(row, "Decision Candle Volume"),
            scanner_recommendation=_value(row, "Scanner Recommendation", "Action Status"),
            execution_eligibility=_value(row, "Execution Eligibility"),
            execution_outcome=_value(row, "Execution Outcome"),
            execution_reason=_value(row, "Execution Reason"),
            trade_status=_value(row, "Trade Status"),
            telegram_status=_value(row, "Telegram Status"),
            telegram_reason=_value(row, "Telegram Reason"),
        ))
        for evaluation in build_rule_evaluations(row, str(resolved_scan_id or "")):
            records.append(_record(
                trading_day,
                event_time,
                evaluation.symbol,
                "RULE_PASSED" if evaluation.passed else "RULE_FAILED",
                f"{evaluation.actual_value} / required {evaluation.required_value}",
                "Rule evaluation",
                scan_id=evaluation.scan_id,
                candidate_key=row.get("Candidate Persistence Key"),
                stage=evaluation.rule_group,
                rule=evaluation.rule_name,
                passed=evaluation.passed,
                actual=evaluation.actual_value,
                required=evaluation.required_value,
            ))
    for _, row in _read_csv(directory / "auto_paper_decisions.csv").iterrows():
        records.append(_record(
            trading_day, row.get("timestamp"), row.get("symbol"), row.get("decision"),
            row.get("reason"), "Auto-paper gate", scan_id=row.get("scan_id"),
            candidate_key=row.get("candidate_key"), trade_id=row.get("trade_key"),
            stage="Auto-paper",
            rule=row.get("blocked_by") or "Execution eligibility",
            passed=str(row.get("decision") or "").upper() == "OPENED",
            scanner_recommendation=row.get("scanner_recommendation") or row.get("action_status"),
            execution_eligibility=row.get("execution_eligibility"),
            execution_outcome=row.get("execution_outcome") or row.get("decision"),
            execution_reason=row.get("execution_reason") or row.get("reason"),
            trade_status=row.get("trade_status"),
            telegram_status=row.get("telegram_status"),
            telegram_reason=row.get("telegram_reason"),
        ))
    for row in _read_jsonl(directory / "trade_timeline.jsonl"):
        payload = row.get("payload") or {}
        records.append(_record(
            trading_day, row.get("occurred_at"), payload.get("symbol"), row.get("event_type"),
            payload.get("exit_phase") or payload.get("entry_reason"), "Trade lifecycle",
            scan_id=payload.get("scan_id"), trade_id=row.get("trade_id"),
            stage="Trade lifecycle",
        ))
    for row in _read_jsonl(live_path("telegram_dispatch_audit.jsonl")):
        if not str(row.get("observed_at_utc") or "").startswith(str(trading_day)):
            continue
        if str(row.get("message_type") or "").startswith("TEST_"):
            continue
        records.append(_record(
            trading_day, row.get("observed_at_utc"), row.get("symbol"),
            row.get("message_type") or row.get("event"),
            row.get("event") if row.get("event") != "FAILED" else row.get("error"),
            "Telegram dispatcher", scan_id=row.get("scan_id"),
            candidate_key=row.get("candidate_key"), trade_id=row.get("trade_id"),
            stage="Telegram",
            rule=row.get("message_type"),
            passed=row.get("event") == "SENT",
        ))
    return pd.DataFrame(records, columns=TRACE_COLUMNS)


_STATE_COLUMNS = ("event_id", "origin", "symbol", "event")


def _existing_trace_state(path):
    """Event ids already on disk, and the last scanner state per symbol.

    Reading the whole trace back was costing more than writing it. The file is
    thirty-four mostly-text columns and grows all session, so by the close a
    scan was loading tens of megabytes of Python objects to answer two small
    questions: which events have we already written, and what did each symbol
    last do. Four columns answer both.

    Returns (seen_event_ids, previous_state_by_symbol, row_count, columns).
    `columns` is None when the file is absent, and is compared against the
    current schema so a trace written by an older build gets rewritten once
    rather than appended to with misaligned columns.
    """
    if not path.exists() or not path.stat().st_size:
        return set(), {}, 0, None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            header = handle.readline().strip()
        columns = header.split(",") if header else []
        wanted = [column for column in _STATE_COLUMNS if column in columns]
        frame = pd.read_csv(path, usecols=wanted) if wanted else pd.DataFrame()
    except Exception:
        return set(), {}, 0, None

    seen = (
        set(frame["event_id"].astype(str))
        if "event_id" in frame.columns else set()
    )

    previous_by_symbol = {}
    if {"origin", "symbol", "event"}.issubset(frame.columns):
        decisions = frame[
            (frame["origin"] == "Scanner decision") & frame["symbol"].notna()
        ]
        # Later rows win, which is what walking the file in order used to do.
        for symbol, event in zip(decisions["symbol"], decisions["event"]):
            previous_by_symbol[str(symbol)] = event

    return seen, previous_by_symbol, len(frame), columns


def write_daily_activity_trace(trading_day, scanner_rows=None, scan_id=None, observed_at=None):
    path = daily_path(trading_day, "activity_trace.csv")
    current = build_activity_trace(trading_day, scanner_rows, scan_id, observed_at)
    seen, previous_by_symbol, existing_rows, columns = _existing_trace_state(path)

    current_records = current.to_dict("records")
    _annotate_state_changes(current_records, previous_by_symbol)

    # build_activity_trace re-derives the whole day from the jsonl logs on every
    # scan, so most of what it returns is already on disk. event_id is a hash of
    # the fields that identify an event, which makes "already written" an exact
    # test rather than a guess -- and the rows it leaves are exactly the rows
    # the old drop_duplicates would have kept.
    fresh = []
    for record in current_records:
        event_id = str(record.get("event_id"))
        if event_id in seen:
            continue
        seen.add(event_id)
        fresh.append(record)

    stale_schema = columns is not None and columns != list(TRACE_COLUMNS)

    if stale_schema:
        # One rewrite to bring an older file up to the current columns; every
        # scan after this one appends.
        trace = pd.concat(
            [_read_csv(path), pd.DataFrame(fresh, columns=TRACE_COLUMNS)],
            ignore_index=True,
            sort=False,
        ).drop_duplicates("event_id", keep="last")
        path.parent.mkdir(parents=True, exist_ok=True)
        trace.to_csv(path, index=False)
        existing_rows = len(trace)
        fresh = trace.to_dict("records")

    elif fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(fresh, columns=TRACE_COLUMNS).to_csv(
            path,
            mode="a",
            header=columns is None,
            index=False,
        )

    # Only the new events go to the database. The upsert keys on event_id, so
    # replaying rows already stored changed nothing except the bill.
    return {
        "path": str(path),
        "rows": existing_rows + (0 if stale_schema else len(fresh)),
        "events": fresh,
    }


def persist_activity_trace(events):
    from app.db.activity_trace_repository import ActivityTraceRepository

    return ActivityTraceRepository().batch_upsert(events)