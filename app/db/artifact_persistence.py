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
    TradeFactRepository().batch_insert_events([
        {"trade_id": None, "event_type": "RuleEvaluated", "occurred_at": (health_payload or {}).get("timestamp"), "payload": evaluation.to_record()}
        for evaluation in rule_evaluations
    ])
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


def persist_timeline_event(timeline_event):
    TradeFactRepository().insert_event(timeline_event)


def persist_completed_trade(trade, exit_snapshot, timeline_event):
    repository = TradeFactRepository()
    from app.trades.entry_snapshot import create_entry_snapshot
    entry_snapshot = create_entry_snapshot(trade).to_record()
    aggregate = {
        "trade_id": entry_snapshot["trade_id"], "scan_id": entry_snapshot.get("scan_id"),
        "trading_day": entry_snapshot.get("trading_day"), "symbol": entry_snapshot.get("symbol"),
        "direction": entry_snapshot.get("direction"), "setup": entry_snapshot.get("setup"),
        "entry_facts": entry_snapshot, "exit_facts": exit_snapshot,
        "outcome": {"result": trade.get("outcome"), "r_multiple": trade.get("r_multiple"), "pnl_pct": trade.get("pnl_pct")},
        "completed_at": exit_snapshot.get("exit_time"),
    }
    repository.insert_completed_trade(aggregate)
    repository.insert_event(timeline_event)
