import json
from app.db.repository_base import BestEffortRepository

# The conflict clause used to set `payload` alone, so a re-run refreshed the JSON
# and left `target_hit`, `stop_hit` and `became_neutral` at whatever the first
# write said. Every one of the 888 stored rows says `became_neutral`, so a
# backfill that resolved them would have changed nothing visible.
#
# Updating the columns needs one guard. Two writers touch this table and they
# know different amounts:
#
#   * `analytics/candidate_outcomes.py` runs in the daily pipeline and reads its
#     verdict from the `Replay Outcome` column, which is NO_REPLAY on every row
#     ever written -- so it can only ever produce `became_neutral`.
#   * `tools/resolve_candidate_outcomes.py` replays the bars that followed and
#     resolves target-first vs stop-first for real.
#
# Without the WHERE, whichever ran last won, and the daily one running last
# would quietly erase a resolved outcome back to neutral. So an incoming row may
# overwrite only when it resolves something, or when the stored row is unresolved
# too. Neutral never overwrites a verdict.
_UPSERT = """
INSERT INTO candidate_outcome (
    candidate_id, entered, profitable, trend_developed, target_hit, stop_hit,
    became_winner, became_loser, became_neutral, payload, created_at
) VALUES (
    :candidate_id, :entered, :profitable, :trend_developed, :target_hit,
    :stop_hit, :became_winner, :became_loser, :became_neutral,
    CAST(:payload AS JSONB), now()
)
ON CONFLICT (candidate_id) DO UPDATE SET
    entered = EXCLUDED.entered,
    profitable = EXCLUDED.profitable,
    trend_developed = EXCLUDED.trend_developed,
    target_hit = EXCLUDED.target_hit,
    stop_hit = EXCLUDED.stop_hit,
    became_winner = EXCLUDED.became_winner,
    became_loser = EXCLUDED.became_loser,
    became_neutral = EXCLUDED.became_neutral,
    payload = EXCLUDED.payload
WHERE EXCLUDED.target_hit
   OR EXCLUDED.stop_hit
   OR NOT (candidate_outcome.target_hit OR candidate_outcome.stop_hit)
"""


class CandidateOutcomeRepository(BestEffortRepository):
    def batch_insert(self, rows):
        return self._batch_execute(
            _UPSERT,
            [
                {**row, "payload": json.dumps(row, default=str)}
                for row in (rows or [])
            ],
        )
