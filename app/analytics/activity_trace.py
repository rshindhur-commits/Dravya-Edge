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


# Per-process, per-day: the event ids already written, the last scanner state
# per symbol, and how far into each source file we have read.
#
# Every scan used to re-derive the whole day -- three growing source files read
# end to end, a DataFrame built of every event since the open, an event_id
# hashed for each, and then all but the newest discarded as already seen. The
# work grew with the day rather than with what happened in the scan, which is
# what a 2026-08-10 session looks like from the outside: 1.2s scans at 04:06
# against 199s at 12:31 on identical 26-symbol work, and resident memory from
# 231MB to 752MB.
#
# Every source here is opened "a" by its writer, so what is new is what is past
# the offset we stopped at. Seeded by a single full read on the first scan of a
# process, which is also what makes a restart mid-session correct rather than
# merely fast: an empty cache re-reads everything exactly as before.
_TRACE_STATE: dict[str, dict] = {}


def _trace_state(trading_day, path):
    """Cached state for one day's trace file.

    Keyed on the resolved path as well as the day, because the day alone does
    not identify what is being cached: point the writer at a different file for
    the same date -- which every test that redirects `daily_path` does -- and a
    day-keyed cache reports the previous file's events as already written.
    """

    day = f"{trading_day}|{path}"
    state = _TRACE_STATE.get(day)

    if state is not None:
        return state

    # One day at a time. The worker runs for weeks; keeping yesterday's event
    # ids is a leak of the kind this function exists to remove.
    _TRACE_STATE.clear()
    state = {
        "seen": None,
        "previous_by_symbol": {},
        "rows": 0,
        "columns": None,
        "offsets": {},
        "headers": {},
    }
    _TRACE_STATE[day] = state

    return state


def _tail_bytes(path: Path, offsets: dict):
    """Bytes appended since we last looked, and only whole lines of them.

    Stopping at the last newline means a record caught half-written by another
    process is read on the next scan instead of being parsed as garbage now.
    """

    key = str(path)
    start = offsets.get(key, 0)

    if not path.exists():
        return b""

    size = path.stat().st_size

    # Shorter than where we stopped means the file was replaced, not appended
    # to -- the only safe reading of which is to start again.
    if size < start:
        start = 0

    if size == start:
        return b""

    with path.open("rb") as handle:
        handle.seek(start)
        blob = handle.read()

    consumed = blob.rfind(b"\n") + 1
    offsets[key] = start + consumed

    return blob[:consumed]


def _read_jsonl_since(path: Path, offsets: dict):
    rows = []

    for line in _tail_bytes(path, offsets).decode("utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue

    return rows


def _read_csv_since(path: Path, offsets: dict, headers: dict):
    """Rows appended since the last read, as dicts.

    The header is written once by `append_daily_auto_paper_decisions` and then
    never again, so it is captured on the seeding read and reused to parse every
    chunk after it.
    """

    import csv
    import io

    key = str(path)
    first = key not in offsets
    chunk = _tail_bytes(path, offsets).decode("utf-8", errors="replace")

    if not chunk:
        return []

    if first:
        reader = csv.DictReader(io.StringIO(chunk))
        rows = list(reader)
        headers[key] = reader.fieldnames

        return [_blanks_to_none(row) for row in rows]

    fieldnames = headers.get(key)

    if not fieldnames:
        return []

    return [
        _blanks_to_none(row)
        for row in csv.DictReader(io.StringIO(chunk), fieldnames=fieldnames)
    ]


def _blanks_to_none(row):
    """One empty-value spelling, because event_id is a hash of the strings.

    This file is now parsed by `csv` on both the seeding read and every read
    after it, where it used to be parsed by pandas throughout. The two disagree
    on an empty cell -- "" against nan -- and that difference reaches the hash,
    so a trace file written by the previous build has its auto-paper rows
    derived under new ids once. The worker's disk is wiped on every deploy, so
    there is no such file there; a local file picks up one duplicate set and
    dedupes normally from then on.
    """

    return {
        key: (None if value is None or str(value).strip() == "" else value)
        for key, value in row.items()
    }


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
    """Stamp each scanner decision with the one before it, and return the new map.

    Returns rather than mutates so the caller can carry the map to the next
    scan. It used to be discarded because the next scan rebuilt it by reading
    the whole trace file back; nothing rebuilds it now.
    """

    previous_by_symbol = dict(previous_by_symbol or {})

    for record in current:
        if record.get("origin") != "Scanner decision" or not record.get("symbol"):
            continue
        symbol = str(record["symbol"])
        previous_state = previous_by_symbol.get(symbol)
        record["previous_state"] = previous_state
        record["state_changed"] = previous_state is not None and previous_state != record["event"]
        previous_by_symbol[symbol] = record["event"]

    return previous_by_symbol


def build_activity_trace(trading_day, scanner_rows=None, scan_id=None, observed_at=None, directory=None, since=None):
    """Every trace event for the day, or -- given `since` -- only the new ones.

    `since` is the per-process state from `_trace_state`. Without it this reads
    all three source files end to end, which is the whole-day behaviour every
    caller outside `write_daily_activity_trace` still wants.
    """

    directory = Path(directory) if directory is not None else daily_path(trading_day, "activity_trace.csv").parent
    offsets = since["offsets"] if since is not None else None
    headers = since["headers"] if since is not None else None
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
    decisions_path = directory / "auto_paper_decisions.csv"
    decision_rows = (
        _read_csv_since(decisions_path, offsets, headers)
        if offsets is not None
        else [row for _, row in _read_csv(decisions_path).iterrows()]
    )

    for row in decision_rows:
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
    timeline_path = directory / "trade_timeline.jsonl"
    timeline_rows = (
        _read_jsonl_since(timeline_path, offsets)
        if offsets is not None
        else _read_jsonl(timeline_path)
    )

    for row in timeline_rows:
        payload = row.get("payload") or {}
        records.append(_record(
            trading_day, row.get("occurred_at"), payload.get("symbol"), row.get("event_type"),
            payload.get("exit_phase") or payload.get("entry_reason"), "Trade lifecycle",
            scan_id=payload.get("scan_id"), trade_id=row.get("trade_id"),
            stage="Trade lifecycle",
        ))
    audit_path = live_path("telegram_dispatch_audit.jsonl")
    audit_rows = (
        _read_jsonl_since(audit_path, offsets)
        if offsets is not None
        else _read_jsonl(audit_path)
    )

    for row in audit_rows:
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
    state = _trace_state(trading_day, path)

    # Read back from disk once per process, then carried in memory. The file
    # reaches five figures of rows by the close and answering "which events are
    # already written" from it on every scan is most of what made a scan cost
    # more at noon than at the open.
    if state["seen"] is None:
        seen, previous_by_symbol, existing_rows, columns = _existing_trace_state(path)
        state["seen"] = seen
        state["previous_by_symbol"] = previous_by_symbol
        state["rows"] = existing_rows
        state["columns"] = columns

    seen = state["seen"]
    existing_rows = state["rows"]
    columns = state["columns"]

    current = build_activity_trace(
        trading_day, scanner_rows, scan_id, observed_at, since=state
    )

    current_records = current.to_dict("records")
    state["previous_by_symbol"] = _annotate_state_changes(
        current_records, state["previous_by_symbol"]
    )

    # `since` means build_activity_trace now returns only what the sources have
    # added, so most of this is already new. The event_id test stays because the
    # scanner rows are rebuilt every scan regardless, and because a seeding read
    # after a restart returns the whole day again.
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

    total_rows = existing_rows + (0 if stale_schema else len(fresh))

    # Carried forward instead of re-read. `columns` in particular: the header is
    # written with the first append, so leaving it None would write a second one
    # mid-file on the next scan.
    state["rows"] = total_rows
    if stale_schema or fresh:
        state["columns"] = list(TRACE_COLUMNS)
        state["seen"] = seen | {str(r.get("event_id")) for r in fresh}

    # Only the new events go to the database. The upsert keys on event_id, so
    # replaying rows already stored changed nothing except the bill.
    return {
        "path": str(path),
        "rows": total_rows,
        "events": fresh,
    }


def persist_activity_trace(events):
    from app.db.activity_trace_repository import ActivityTraceRepository

    return ActivityTraceRepository().batch_upsert(events)