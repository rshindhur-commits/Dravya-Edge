from __future__ import annotations

from app.db.repository_base import BestEffortRepository


class RuleEvaluationRepository(BestEffortRepository):
    def batch_insert(self, evaluations):
        return self._batch_execute("""
            INSERT INTO rule_evaluation (scan_id, symbol, setup, rule_name, rule_group, actual_value, required_value, passed, blocked_trade, priority, evaluation_phase, timestamp)
            VALUES (:scan_id, :symbol, :setup, :rule_name, :rule_group, CAST(:actual_value AS TEXT), CAST(:required_value AS TEXT), :passed, :blocked_trade, :priority, :evaluation_phase, now())
        """, [item.to_record() if hasattr(item, "to_record") else item for item in (evaluations or [])])

    def insert(self, evaluation): return self.batch_insert([evaluation])
    def upsert(self, evaluation): return self.insert(evaluation)
    def get(self, *_args, **_kwargs): return []
