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
    "candle_volume", "scan_id", "candidate_key", "trade_id",
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
    candle_volume=None,
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


def _annotate_state_changes(current, existing):
    previous_by_symbol = {}
    for row in existing.to_dict("records") if not existing.empty else []:
        if row.get("origin") == "Scanner decision" and row.get("symbol"):
            previous_by_symbol[str(row["symbol"])] = row.get("event")

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


def write_daily_activity_trace(trading_day, scanner_rows=None, scan_id=None, observed_at=None):
    path = daily_path(trading_day, "activity_trace.csv")
    current = build_activity_trace(trading_day, scanner_rows, scan_id, observed_at)
    existing = _read_csv(path)
    current_records = current.to_dict("records")
    _annotate_state_changes(current_records, existing)
    current = pd.DataFrame(current_records, columns=TRACE_COLUMNS)
    trace = pd.concat([existing, current], ignore_index=True, sort=False)
    trace = trace.drop_duplicates("event_id", keep="last")
    trace.to_csv(path, index=False)
    return {"path": str(path), "rows": len(trace), "events": trace.to_dict("records")}


def persist_activity_trace(events):
    from app.db.activity_trace_repository import ActivityTraceRepository

    return ActivityTraceRepository().batch_upsert(events)