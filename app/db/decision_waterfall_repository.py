from __future__ import annotations

from app.db.repository_base import BestEffortRepository


class DecisionWaterfallRepository(BestEffortRepository):

    def batch_insert(self, rows):

        records = [
            {
                **(row or {}),
                "actual": (
                    None
                    if (row or {}).get("actual") is None
                    else str((row or {}).get("actual"))
                ),
                "required": (
                    None
                    if (row or {}).get("required") is None
                    else str((row or {}).get("required"))
                ),
            }
            for row in rows or []
        ]
        return self._batch_execute(
            """
            INSERT INTO decision_waterfall (
                scan_id, symbol, stage, stage_order, passed, blocking,
                rule_name, actual_value, required_value, priority, summary
            ) VALUES (
                :scan_id, :symbol, :stage, :stage_order, :passed, :blocking,
                :rule_name, :actual, :required, :priority, :summary
            )
            """,
            records,
        )