from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd

from app.analytics.decision_waterfall import (
    build_decision_waterfall,
    waterfall_rule_records,
)
from app.analytics.rank_outcome_calibration import load_rank_outcome_calibration
from app.analytics.rule_attribution import build_rule_outcome_attribution
from app.gates.rule_evaluation import build_rule_evaluations
from app.storage.daily_paths import daily_path, state_path


REVIEW_ARTIFACTS = (
    "analytics_summary.csv",
    "daily_engine_summary.csv",
    "candidate_snapshot.csv",
    "candidate_evidence.csv",
    "candidate_outcome.csv",
    "recommendation_outcomes.csv",
    "recommendation_outcome_summary.csv",
    "high_score_execution_audit.csv",
    "decision_waterfall.csv",
    "gate_decisions.csv",
    "rule_evaluation.csv",
    "activity_trace.csv",
    "paper_trades.csv",
    "trade.csv",
    "exit_quality_metrics.csv",
)

# Daily artifacts that live *only* on the container filesystem. Postgres holds
# candidate snapshots, evidence, activity trace, auto-paper decisions, gate
# decisions and the waterfall, so those survive a restart on their own -- these
# do not, and a Streamlit Cloud redeploy is the last moment they exist. Copied
# verbatim rather than rebuilt, so an operator gets the file the engine wrote.
FILE_ONLY_ARTIFACTS = (
    "trade_exit_snapshots.csv",
    "signal_lifecycle_events.csv",
    "signal_state_transitions.csv",
    "entry_exit_v2_shadow.csv",
    "engine_trade_events.csv",
    "engine_trade_comparisons.csv",
    "engine_differences.csv",
    "engine_trend_outcomes.csv",
    "market_opportunity_audit.csv",
    "option_liquidity_audit.csv",
    "option_liquidity_attempts.csv",
    "candidate_intelligence.csv",
    "quote_attribution.csv",
    "scanner_stage_profile.csv",
    "runtime_performance.csv",
    "trade_telemetry.csv",
    "scanner_output_close.csv",
    "auto_paper_decisions.csv",
    "candles_5m.csv",
    "post_market_review.html",
)

# Operator state. `paper_trade_state.json` is the live book and is wiped by every
# redeploy; `auto_paper_settings.json` is gitignored, so a fresh container cannot
# tell you what the controls were set to when the day ran.
STATE_ARTIFACTS = (
    "paper_trade_state.json",
    "suggested_trade_state.json",
    "auto_paper_settings.json",
)

EVIDENCE_STATUS_ARTIFACT = "candidate_evidence_status.json"


def _read_csv(path: Path):
    try:
        return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def _read_timeline(path: Path):
    records = []
    if not path.exists():
        return pd.DataFrame()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return pd.json_normalize(records) if records else pd.DataFrame()


def _read_snapshots(directory: Path):
    csv_path = directory / "candidate_snapshots.csv"
    snapshots = _read_csv(csv_path)
    if not snapshots.empty:
        return snapshots
    parquet_path = directory / "candidate_snapshots.parquet"
    try:
        return pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _summary_frame(engine_summary, evidence, rank_outcomes, attribution):
    return pd.DataFrame([{
        "candidate_evidence_rows": len(evidence),
        "rank_outcome_buckets": len(rank_outcomes),
        "rule_attribution_rows": len(attribution),
        "completed_trades": engine_summary.get("performance", {}).get("completed_trades"),
        "average_trend_capture": engine_summary.get("avg_trend_capture"),
        "average_v1_r": engine_summary.get("avg_v1_r"),
    }])


def _generated_review_frames(trading_day, directory: Path):
    scanner = _read_csv(directory / "scanner_output_close.csv")
    evidence = _read_csv(directory / "candidate_evidence.csv")
    engine_summary = _read_json(directory / "daily_engine_summary.json")
    rank_outcomes = load_rank_outcome_calibration(trading_day)
    attribution = build_rule_outcome_attribution(evidence)
    waterfalls = []
    gate_decisions = []
    evaluations = []
    for row in scanner.to_dict("records"):
        scan_id = str(row.get("Scan ID") or row.get("scan_id") or "")
        waterfall = build_decision_waterfall(row, scan_id=scan_id)
        waterfalls.extend(waterfall_rule_records(waterfall, scan_id))
        gate_decisions.append({
            "scan_id": scan_id,
            "symbol": row.get("Symbol") or row.get("symbol"),
            "action": row.get("Action Status") or row.get("action_status"),
            "blocked_by": row.get("Blocked By") or row.get("blocked_by"),
            "reason": row.get("Action Reason") or row.get("action_reason"),
            "gate_failure_stage": row.get("ENTRY_GATE_FAILURE_STAGE"),
        })
        evaluations.extend(item.to_record() for item in build_rule_evaluations(row, scan_id))
    return {
        "analytics_summary.csv": _summary_frame(engine_summary, evidence, rank_outcomes, attribution),
        "daily_engine_summary.csv": pd.json_normalize(engine_summary) if engine_summary else pd.DataFrame(),
        "candidate_snapshot.csv": _read_snapshots(directory),
        "candidate_evidence.csv": evidence,
        "candidate_outcome.csv": _read_csv(directory / "candidate_outcomes.csv"),
        "recommendation_outcomes.csv": _read_csv(directory / "recommendation_outcomes.csv"),
        "recommendation_outcome_summary.csv": _read_csv(directory / "recommendation_outcome_summary.csv"),
        "high_score_execution_audit.csv": _read_csv(directory / "high_score_execution_audit.csv"),
        "decision_waterfall.csv": pd.DataFrame(waterfalls),
        "gate_decisions.csv": pd.DataFrame(gate_decisions),
        "rule_evaluation.csv": pd.DataFrame(evaluations),
        "activity_trace.csv": _read_csv(directory / "activity_trace.csv"),
        "paper_trades.csv": _read_csv(directory / "paper_trade_events.csv"),
        "trade.csv": _read_timeline(directory / "trade_timeline.jsonl"),
        "exit_quality_metrics.csv": _read_csv(directory / "trend_capture_analysis.csv"),
    }


def _raw_copies(directory):
    """Verbatim copies of the artifacts nothing else preserves."""
    copies = {}
    for name in FILE_ONLY_ARTIFACTS:
        path = directory / name
        try:
            if path.exists() and path.stat().st_size:
                copies[f"raw/{name}"] = path.read_bytes()
        except OSError:
            continue
    for name in STATE_ARTIFACTS:
        try:
            path = state_path(name)
            if path.exists() and path.stat().st_size:
                copies[f"state/{name}"] = path.read_bytes()
        except OSError:
            continue
    return copies


def build_daily_review_export(trading_day, directory=None):
    directory = Path(directory) if directory is not None else daily_path(trading_day, "review_export.zip").parent
    frames = _generated_review_frames(trading_day, directory)
    evidence_status = _read_json(directory / EVIDENCE_STATUS_ARTIFACT)
    raw_copies = _raw_copies(directory)
    manifest = {
        "trading_day": trading_day,
        "artifacts": {
            name: {"rows": int(len(frame)), "available": not frame.empty}
            for name, frame in frames.items()
        },
        "notes": [
            "Rule attribution is observational matched-cohort association, not causal attribution.",
            "Empty CSV files mean the selected daily artifact was not available.",
            "raw/ holds verbatim copies of artifacts that exist nowhere but this "
            "container's filesystem; state/ holds the operator state at export time.",
        ],
    }
    manifest["artifacts"][EVIDENCE_STATUS_ARTIFACT] = {
        "rows": evidence_status.get("evidence_rows") if evidence_status else 0,
        "available": bool(evidence_status),
    }
    for name, payload in raw_copies.items():
        manifest["artifacts"][name] = {"bytes": len(payload), "available": True}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in REVIEW_ARTIFACTS:
            archive.writestr(name, frames[name].to_csv(index=False))
        for name, payload in raw_copies.items():
            archive.writestr(name, payload)
        archive.writestr(
            EVIDENCE_STATUS_ARTIFACT,
            json.dumps(
                evidence_status or {
                    "available": False,
                    "reason": "Candidate evidence status was not written for this trading day.",
                },
                indent=2,
            ),
        )
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buffer.getvalue(), manifest