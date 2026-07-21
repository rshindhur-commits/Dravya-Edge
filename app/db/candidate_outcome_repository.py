import json
from app.db.repository_base import BestEffortRepository

class CandidateOutcomeRepository(BestEffortRepository):
    def batch_insert(self, rows):
        return self._batch_execute("INSERT INTO candidate_outcome (candidate_id,entered,profitable,trend_developed,target_hit,stop_hit,became_winner,became_loser,became_neutral,payload,created_at) VALUES (:candidate_id,:entered,:profitable,:trend_developed,:target_hit,:stop_hit,:became_winner,:became_loser,:became_neutral,CAST(:payload AS JSONB),now()) ON CONFLICT (candidate_id) DO UPDATE SET payload=EXCLUDED.payload", [{**row, "payload": json.dumps(row, default=str)} for row in (rows or [])])
