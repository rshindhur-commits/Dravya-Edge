from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.analytics.loss_attribution import build_loss_attribution
from app.storage.daily_paths import daily_path
from app.utils.json_store import load_json_file


EVIDENCE_COLUMNS = [
    "candidate_id", "trading_day", "symbol", "direction", "setup",
    "first_seen_at", "last_seen_at", "scan_count", "rr", "setup_score",
    "option_quality", "trend_health", "regime", "top_candidate", "quote_freshness", "rule_evaluation",
    "decision", "suggestion_status", "paper_trade_status", "entered",
    "replay_outcome", "target_first", "stop_first", "winner", "missed_winner",
    "trend_capture", "tes", "engineering_root_cause", "evidence_updated_at",
]


def _read_csv(path: Path):
    try:
        return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _read_snapshot(trading_day):
    parquet_path = daily_path(trading_day, "candidate_snapshots.parquet")
    try:
        if parquet_path.exists() and parquet_path.stat().st_size:
            return pd.read_parquet(parquet_path)
    except Exception:
        pass
    snapshots = _read_csv(daily_path(trading_day, "candidate_snapshots.csv"))
    if not snapshots.empty:
        return snapshots
    return _read_csv(daily_path(trading_day, "scanner_output_close.csv"))


def _first_existing(frame, names):
    for name in names:
        if name in frame.columns:
            return name
    return None


def _text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _symbol(value):
    return _text(value).upper()


def _direction(value):
    normalized = _text(value).upper()
    if normalized in {"LONG", "BULLISH"}:
        return "CALL"
    if normalized in {"SHORT", "BEARISH"}:
        return "PUT"
    return normalized


def _candidate_id(trading_day, symbol, direction, setup):
    source = "|".join([trading_day, symbol, direction, setup])
    return hashlib.sha256(source.encode()).hexdigest()[:24]


def _value(row, *names):
    for name in names:
        value = row.get(name)
        if _text(value):
            return value
    return None


def _candidate_key(symbol, direction, setup):
    return symbol, direction, _text(setup).upper()


def _latest_map(frame, key_builder):
    if frame is None or frame.empty:
        return {}
    timestamp = _first_existing(frame, ["event_time_utc", "Exit Time", "Entry Time", "timestamp"])
    rows = frame.sort_values(timestamp) if timestamp else frame
    return {key_builder(row): row for _, row in rows.iterrows()}


def _suggestion_map(suggestions):
    rows = []
    for suggestion in (suggestions or {}).values():
        if not isinstance(suggestion, dict):
            continue
        key = _candidate_key(
            _symbol(suggestion.get("symbol")),
            _direction(suggestion.get("direction")),
            suggestion.get("setup_type"),
        )
        rows.append((key, suggestion))
    return dict(rows)


def _paper_map(paper_events):
    return _latest_map(
        paper_events,
        lambda row: (_symbol(_value(row, "symbol", "Symbol")), _direction(_value(row, "direction", "Direction"))),
    )


def _trade_efficiency_map(trend_capture):
    return _latest_map(
        trend_capture,
        lambda row: _candidate_key(
            _symbol(_value(row, "Symbol", "symbol")),
            _direction(_value(row, "Direction", "direction")),
            _value(row, "Setup", "setup"),
        ),
    )


def _root_cause_map(attribution):
    return _latest_map(
        attribution,
        lambda row: (_symbol(_value(row, "symbol", "Symbol")), _text(_value(row, "setup", "Setup")).upper()),
    )


def build_candidate_evidence_from_frames(
    trading_day,
    candidate_snapshots,
    suggestions=None,
    paper_events=None,
    trend_capture=None,
    attribution=None,
):
    snapshots = candidate_snapshots.copy() if candidate_snapshots is not None else pd.DataFrame()
    if snapshots.empty:
        return pd.DataFrame(columns=EVIDENCE_COLUMNS)

    symbol_column = _first_existing(snapshots, ["symbol", "Symbol"])
    setup_column = _first_existing(snapshots, ["setup_type", "Entry", "setup"])
    direction_column = _first_existing(snapshots, ["direction", "Candidate Direction"])
    if not symbol_column or not setup_column:
        return pd.DataFrame(columns=EVIDENCE_COLUMNS)

    snapshots = snapshots.copy()
    snapshots["_symbol"] = snapshots[symbol_column].map(_symbol)
    snapshots["_setup"] = snapshots[setup_column].map(lambda value: _text(value).upper())
    snapshots["_direction"] = snapshots[direction_column].map(_direction) if direction_column else ""
    snapshots = snapshots[(snapshots["_symbol"] != "") & (snapshots["_setup"] != "")].copy()
    if snapshots.empty:
        return pd.DataFrame(columns=EVIDENCE_COLUMNS)

    timestamp_column = _first_existing(snapshots, ["scan_timestamp", "timestamp", "Data Timestamp ET", "Current ET"])
    if timestamp_column:
        snapshots = snapshots.sort_values(timestamp_column)

    suggestion_by_key = _suggestion_map(suggestions)
    paper_by_key = _paper_map(paper_events)
    trend_by_key = _trade_efficiency_map(trend_capture)
    root_by_key = _root_cause_map(attribution)
    records = []

    for (symbol, direction, setup), group in snapshots.groupby(["_symbol", "_direction", "_setup"], dropna=False):
        latest = group.iloc[-1]
        first = group.iloc[0]
        candidate_id = _candidate_id(trading_day, symbol, direction, setup)
        suggestion = suggestion_by_key.get(_candidate_key(symbol, direction, setup), {})
        paper = paper_by_key.get((symbol, direction), {})
        trend = trend_by_key.get(_candidate_key(symbol, direction, setup), {})
        root = root_by_key.get((symbol, setup), {})
        replay_outcome = _text(_value(latest, "replay_outcome", "Replay Outcome"))
        target_first = "TARGET" in replay_outcome.upper()
        stop_first = "STOP" in replay_outcome.upper()
        paper_event_type = _text(_value(paper, "event_type")).upper()
        paper_status = _text(_value(paper, "status")) or paper_event_type
        entered = paper_event_type == "OPEN" or paper_status.upper() == "OPEN"
        winner = target_first or _text(_value(trend, "Replay Outcome")).upper().find("TARGET") >= 0
        records.append({
            "candidate_id": candidate_id,
            "trading_day": trading_day,
            "symbol": symbol,
            "direction": direction,
            "setup": setup,
            "first_seen_at": _value(first, "scan_timestamp", "timestamp"),
            "last_seen_at": _value(latest, "scan_timestamp", "timestamp"),
            "scan_count": int(len(group)),
            "rr": _value(latest, "candidate_rr", "Candidate RR", "Risk Reward"),
            "setup_score": _value(latest, "setup_percent", "Setup %", "15m Score"),
            "option_quality": _value(latest, "option_quality_score", "Option Quality Score"),
            "trend_health": _value(latest, "trend_health", "Trend Health State"),
            "regime": _value(latest, "market_regime", "Market Regime"),
            "top_candidate": _value(latest, "top_candidate", "Top Candidate"),
            "quote_freshness": _value(latest, "option_quote_freshness", "Option Quote Freshness"),
            "rule_evaluation": _value(latest, "blocked_by", "Blocked By", "Action Reason"),
            "decision": _value(latest, "action_status", "Action Status"),
            "suggestion_status": suggestion.get("status"),
            "paper_trade_status": paper_status or None,
            "entered": entered,
            "replay_outcome": replay_outcome or None,
            "target_first": target_first,
            "stop_first": stop_first,
            "winner": winner,
            "missed_winner": winner and not entered,
            "trend_capture": _value(trend, "Trend Capture %"),
            "tes": _value(trend, "Trade Efficiency Score"),
            "engineering_root_cause": _value(root, "root_cause", "blocked_reason"),
            "evidence_updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    return pd.DataFrame(records, columns=EVIDENCE_COLUMNS)


def load_candidate_evidence(trading_day):
    parquet_path = daily_path(trading_day, "candidate_evidence.parquet")
    try:
        if parquet_path.exists() and parquet_path.stat().st_size:
            return pd.read_parquet(parquet_path)
    except Exception:
        pass
    return _read_csv(daily_path(trading_day, "candidate_evidence.csv"))


def build_candidate_evidence(trading_day):
    suggestions = load_json_file(str(daily_path(trading_day, "suggested_trade_state.json")), {})
    if not suggestions:
        suggestions = load_json_file("app/state/suggested_trade_state.json", {})
    return build_candidate_evidence_from_frames(
        trading_day,
        _read_snapshot(trading_day),
        suggestions=suggestions,
        paper_events=_read_csv(daily_path(trading_day, "paper_trade_events.csv")),
        trend_capture=_read_csv(daily_path(trading_day, "trend_capture_analysis.csv")),
        attribution=build_loss_attribution(trading_day),
    )


def write_candidate_evidence(trading_day):
    evidence = build_candidate_evidence(trading_day)
    if evidence.empty:
        return None
    csv_path = daily_path(trading_day, "candidate_evidence.csv")
    parquet_path = daily_path(trading_day, "candidate_evidence.parquet")
    evidence.to_csv(csv_path, index=False)
    path = csv_path
    try:
        evidence.to_parquet(parquet_path, index=False)
        path = parquet_path
    except Exception:
        pass
    from app.db.candidate_evidence_repository import CandidateEvidenceRepository
    CandidateEvidenceRepository().batch_upsert(evidence.to_dict("records"))
    return {"path": str(path), "rows": len(evidence)}