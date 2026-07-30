from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.analytics.loss_attribution import build_loss_attribution
from app.db.persistence import db_writes_enabled
from app.storage.daily_paths import daily_path
from app.utils.json_store import load_json_file


EVIDENCE_COLUMNS = [
    "candidate_id", "trading_day", "symbol", "direction", "setup",
    "first_seen_at", "last_seen_at", "scan_count", "rr", "setup_score",
    "entry_timing_score", "entry_timing_grade", "trade_quality_score",
    "entry_priority_adjustment", "expected_remaining_trend", "projected_entry_grade",
    "ranking_score", "candidate_rank",
    "option_quality", "trend_health", "regime", "top_candidate", "quote_freshness", "rule_evaluation",
    "scanner_recommendation", "execution_eligibility", "execution_outcome", "execution_reason",
    "trade_status", "telegram_status", "telegram_reason",
    "decision", "latest_decision", "first_actionable_decision", "first_actionable_at",
    "first_actionable_scan_id", "decision_history", "auto_paper_decision",
    "auto_paper_blocked_by", "suggestion_status", "paper_trade_status", "entered",
    "replay_outcome", "target_first", "stop_first", "winner", "missed_winner",
    "final_r", "trend_capture", "mfe", "tes", "engineering_root_cause", "evidence_updated_at",
]


ACTIONABLE_DECISIONS = {"ENTER", "ENTER_PAPER"}


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


def _read_database_snapshots(trading_day):
    if not db_writes_enabled():
        return pd.DataFrame()
    try:
        from sqlalchemy import text

        from app.db.connection import get_engine

        with get_engine().connect() as connection:
            return pd.read_sql(text("""
                SELECT trading_day, scan_id, created_at AS scan_timestamp,
                       created_at AS timestamp, symbol, setup AS setup_type,
                       direction, score AS setup_percent, action AS action_status,
                       blocked_reason AS blocked_by, risk_reward AS candidate_rr,
                       regime AS market_regime, NULL::TEXT AS top_candidate,
                       option_quality AS option_quality_score,
                       candidate_rank, realtime_ready, execution_ready,
                       scanner_recommendation, execution_eligibility,
                      execution_outcome, execution_reason, trade_status,
                      telegram_status, telegram_reason
                FROM candidate_snapshot
                WHERE trading_day = CAST(:trading_day AS DATE)
                ORDER BY created_at
            """), connection, params={"trading_day": trading_day})
    except Exception:
        return pd.DataFrame()


def _canonicalize_snapshot_source(frame):
    frame = frame.copy()
    aliases = {
        "scan_id": ["Scan ID"],
        "scan_timestamp": ["timestamp", "Data Timestamp ET", "Current ET"],
        "symbol": ["Symbol"],
        "setup_type": ["Entry", "setup"],
        "direction": ["Candidate Direction"],
        "action_status": ["Action Status"],
    }
    for canonical, names in aliases.items():
        values = frame[canonical] if canonical in frame.columns else pd.Series(None, index=frame.index)
        for name in names:
            if name in frame.columns:
                values = values.combine_first(frame[name])
        frame[canonical] = values
    return frame


def _merge_snapshot_sources(*frames):
    available = [
        _canonicalize_snapshot_source(frame)
        for frame in frames
        if frame is not None and not frame.empty
    ]
    if not available:
        return pd.DataFrame()
    merged = pd.concat(available, ignore_index=True, sort=False)
    if not all(column in merged.columns for column in ["symbol", "setup_type"]):
        return merged
    identity = merged.apply(
        lambda row: "|".join([
            _text(row.get("scan_id")) or _text(row.get("scan_timestamp")),
            _symbol(row.get("symbol")),
            _direction(row.get("direction")),
            _text(row.get("setup_type")).upper(),
        ]),
        axis=1,
    )
    return merged.loc[~identity.duplicated(keep="last")].reset_index(drop=True)


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


def _decision_history(group, timestamp_column):
    history = []
    previous = None
    for _, observation in group.iterrows():
        decision = _text(_value(observation, "action_status", "Action Status")).upper()
        if not decision or decision == previous:
            continue
        history.append({
            "decision": decision,
            "observed_at": _value(observation, timestamp_column, "timestamp"),
            "scan_id": _value(observation, "scan_id", "Scan ID"),
            "reason": _value(observation, "action_reason", "Action Reason"),
            "blocked_by": _value(observation, "blocked_by", "Blocked By"),
        })
        previous = decision
    return history


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


def _auto_paper_map(decisions):
    return _latest_map(
        decisions,
        lambda row: _symbol(_value(row, "symbol", "Symbol")),
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
    auto_paper_decisions=None,
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
        snapshots = snapshots.assign(
            _evidence_sort_time=pd.to_datetime(
                snapshots[timestamp_column],
                errors="coerce",
                utc=True,
            )
        ).sort_values("_evidence_sort_time")

    suggestion_by_key = _suggestion_map(suggestions)
    paper_by_key = _paper_map(paper_events)
    auto_paper_by_symbol = _auto_paper_map(auto_paper_decisions)
    trend_by_key = _trade_efficiency_map(trend_capture)
    root_by_key = _root_cause_map(attribution)
    records = []

    for (symbol, direction, setup), group in snapshots.groupby(["_symbol", "_direction", "_setup"], dropna=False):
        latest = group.iloc[-1]
        first = group.iloc[0]
        decision_history = _decision_history(group, timestamp_column)
        actionable = next(
            (
                observation
                for _, observation in group.iterrows()
                if _text(_value(observation, "action_status", "Action Status")).upper()
                in ACTIONABLE_DECISIONS
            ),
            None,
        )
        latest_decision = _value(latest, "action_status", "Action Status")
        first_actionable_decision = _value(
            actionable,
            "action_status",
            "Action Status",
        ) if actionable is not None else None
        candidate_id = _candidate_id(trading_day, symbol, direction, setup)
        suggestion = suggestion_by_key.get(_candidate_key(symbol, direction, setup), {})
        paper = paper_by_key.get((symbol, direction), {})
        auto_paper = auto_paper_by_symbol.get(symbol, {})
        trend = trend_by_key.get(_candidate_key(symbol, direction, setup), {})
        root = root_by_key.get((symbol, setup), {})
        replay_outcome = _text(_value(latest, "replay_outcome", "Replay Outcome"))
        target_first = "TARGET" in replay_outcome.upper()
        stop_first = "STOP" in replay_outcome.upper()
        paper_event_type = _text(_value(paper, "event_type")).upper()
        paper_status = _text(_value(paper, "status")) or paper_event_type
        auto_paper_decision = _text(_value(auto_paper, "decision")).upper()
        auto_paper_blocked_by = (
            _value(auto_paper, "reason")
            if auto_paper_decision == "BLOCKED"
            else None
        )
        entered = (
            paper_event_type == "OPEN"
            or paper_status.upper() == "OPEN"
            or auto_paper_decision == "OPENED"
        )
        decision = first_actionable_decision or latest_decision
        scanner_rule = _value(latest, "blocked_by", "Blocked By")
        if _text(scanner_rule).upper() == _text(decision).upper():
            scanner_rule = None
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
            "entry_timing_score": _value(latest, "entry_timing_score", "Entry Timing Score"),
            "entry_timing_grade": _value(latest, "entry_timing_grade", "Entry Timing Grade"),
            "trade_quality_score": _value(latest, "trade_quality_score", "Trade Quality Score"),
            "entry_priority_adjustment": _value(
                latest, "entry_priority_adjustment", "Entry Priority Adjustment"
            ),
            "expected_remaining_trend": _value(
                latest, "expected_remaining_trend", "Expected Remaining Trend"
            ),
            "projected_entry_grade": _value(
                latest, "projected_entry_grade", "Projected Entry Grade"
            ),
            "ranking_score": _value(latest, "ranking_score", "Ranking Score"),
            "candidate_rank": _value(latest, "candidate_rank", "Candidate Rank"),
            "option_quality": _value(latest, "option_quality_score", "Option Quality Score"),
            "trend_health": _value(latest, "trend_health", "Trend Health State"),
            "regime": _value(latest, "market_regime", "Market Regime"),
            "top_candidate": _value(latest, "top_candidate", "Top Candidate"),
            "quote_freshness": _value(latest, "option_quote_freshness", "Option Quote Freshness"),
            "rule_evaluation": auto_paper_blocked_by or scanner_rule or _value(latest, "Action Reason"),
            "scanner_recommendation": _value(latest, "scanner_recommendation", "Scanner Recommendation", "action_status", "Action Status"),
            "execution_eligibility": _value(latest, "execution_eligibility", "Execution Eligibility"),
            "execution_outcome": _value(latest, "execution_outcome", "Execution Outcome"),
            "execution_reason": _value(latest, "execution_reason", "Execution Reason"),
            "trade_status": _value(latest, "trade_status", "Trade Status"),
            "telegram_status": _value(latest, "telegram_status", "Telegram Status"),
            "telegram_reason": _value(latest, "telegram_reason", "Telegram Reason"),
            "decision": decision,
            "latest_decision": latest_decision,
            "first_actionable_decision": first_actionable_decision,
            "first_actionable_at": _value(actionable, timestamp_column, "timestamp") if actionable is not None else None,
            "first_actionable_scan_id": _value(actionable, "scan_id", "Scan ID") if actionable is not None else None,
            "decision_history": decision_history,
            "auto_paper_decision": auto_paper_decision or None,
            "auto_paper_blocked_by": auto_paper_blocked_by,
            "suggestion_status": suggestion.get("status"),
            "paper_trade_status": paper_status or None,
            "entered": entered,
            "replay_outcome": replay_outcome or None,
            "target_first": target_first,
            "stop_first": stop_first,
            "winner": winner,
            "missed_winner": winner and not entered,
            "final_r": _value(paper, "r_multiple", "R Multiple"),
            "trend_capture": _value(trend, "Trend Capture %"),
            "mfe": _value(trend, "MFE", "Maximum Favorable Excursion"),
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


def build_candidate_evidence(trading_day, candidate_snapshots=None):
    suggestions = load_json_file(str(daily_path(trading_day, "suggested_trade_state.json")), {})
    if not suggestions:
        suggestions = load_json_file("app/state/suggested_trade_state.json", {})
    accumulated_snapshots = _read_snapshot(trading_day)
    snapshots = _merge_snapshot_sources(
        _read_database_snapshots(trading_day),
        accumulated_snapshots,
        candidate_snapshots,
    )
    return build_candidate_evidence_from_frames(
        trading_day,
        snapshots,
        suggestions=suggestions,
        paper_events=_read_csv(daily_path(trading_day, "paper_trade_events.csv")),
        auto_paper_decisions=_read_csv(daily_path(trading_day, "auto_paper_decisions.csv")),
        trend_capture=_read_csv(daily_path(trading_day, "trend_capture_analysis.csv")),
        attribution=build_loss_attribution(trading_day),
    )


def write_candidate_evidence(trading_day, candidate_snapshots=None):
    evidence_source = _merge_snapshot_sources(
        _read_database_snapshots(trading_day),
        _read_snapshot(trading_day),
        candidate_snapshots,
    )
    evidence = build_candidate_evidence(
        trading_day,
        candidate_snapshots=evidence_source,
    )
    source_rows = int(len(evidence_source)) if evidence_source is not None else None
    csv_path = daily_path(trading_day, "candidate_evidence.csv")
    parquet_path = daily_path(trading_day, "candidate_evidence.parquet")
    evidence.to_csv(csv_path, index=False)
    path = csv_path
    if not evidence.empty:
        try:
            evidence.to_parquet(parquet_path, index=False)
            path = parquet_path
        except Exception:
            pass

    db_rows = 0
    db_enabled = db_writes_enabled()
    db_status = "PERSISTED" if db_enabled else "DISABLED"
    if db_enabled and not evidence.empty:
        try:
            from app.db.candidate_evidence_repository import CandidateEvidenceRepository
            db_rows = int(CandidateEvidenceRepository().batch_upsert(evidence.to_dict("records")) or 0)
            db_status = "PERSISTED" if db_rows == len(evidence) else "FAILED"
        except Exception:
            db_rows = 0
            db_status = "FAILED"

    status = {
        "trading_day": trading_day,
        "source_rows": source_rows,
        "evidence_rows": int(len(evidence)),
        "rows_expected": source_rows,
        "rows_written": int(len(evidence)),
        "duplicates_removed": (
            max(0, source_rows - len(evidence))
            if source_rows is not None
            else None
        ),
        "local_path": str(path),
        "database_rows": int(db_rows),
        "db_rows_persisted": int(db_rows),
        "database_status": db_status,
    }
    status_path = daily_path(trading_day, "candidate_evidence_status.json")
    status_path.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    return {"path": str(path), "rows": len(evidence), "status": status, "status_path": str(status_path)}