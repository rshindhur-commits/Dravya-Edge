from __future__ import annotations

from app.db.candidate_snapshot_repository import CandidateSnapshotRepository
from app.db.persistence import record_gate_decisions, record_scanner_run_finish
from app.db.rule_evaluation_repository import RuleEvaluationRepository
from app.db.trade_fact_repository import TradeFactRepository
from app.gates.rule_evaluation import build_rule_evaluations


def persist_scan_artifacts(records, trading_day, scan_id, health_payload, output_file=None):
    """Best-effort DB promotion of already-written scanner artifacts.

    This function must be invoked only by a RuntimeScheduler job.
    """
    records = list(records or [])
    CandidateSnapshotRepository().batch_insert(records, trading_day=trading_day, scan_id=scan_id)
    rule_evaluations = [
        evaluation
        for row in records
        for evaluation in build_rule_evaluations(row, scan_id)
    ]
    RuleEvaluationRepository().batch_insert(rule_evaluations)
    record_gate_decisions(records, run_id=scan_id)
    record_scanner_run_finish(
        scan_id,
        status="FINISHED",
        rows_count=len(records),
        payload={
            "trading_day": trading_day,
            "output_file": str(output_file) if output_file else None,
            "health": health_payload or {},
            "rule_evaluations": len(rule_evaluations),
        },
    )


def persist_entry_snapshot(snapshot, timeline_event):
    repository = TradeFactRepository()
    repository.insert_entry_snapshot(snapshot)
    repository.insert_timeline_event(timeline_event)


def persist_exit_snapshot(snapshot, timeline_event):
    repository = TradeFactRepository()
    repository.insert_exit_snapshot(snapshot)
    repository.insert_timeline_event(timeline_event)
