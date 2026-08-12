import json

from app.db.persistence import _json_safe
from app.db.repository_base import BestEffortRepository


def _nullable(value):
    if value is None or str(value).strip().lower() in {"", "nan", "nat", "none"}:
        return None
    return value


class CandidateEvidenceRepository(BestEffortRepository):
    def batch_upsert(self, rows):
        records = []
        for row in rows or []:
            row = _json_safe(row or {})
            records.append({
                **row,
                "first_actionable_at": _nullable(row.get("first_actionable_at")),
                "decision_history": json.dumps(row.get("decision_history") or [], default=str),
                "payload": json.dumps(row, default=str),
            })
        return self._batch_execute("""
            INSERT INTO candidate_evidence (
                candidate_id, trading_day, symbol, direction, setup, rr, setup_score,
                holding_profile,
                option_quality, trend_health, regime, top_candidate, quote_freshness, rule_evaluation, decision,
                scanner_recommendation, execution_eligibility, execution_outcome, execution_reason,
                trade_status, telegram_status, telegram_reason,
                latest_decision, first_actionable_decision, first_actionable_at,
                first_actionable_scan_id, decision_history,
                suggestion_status, paper_trade_status, replay_outcome, target_first,
                stop_first, winner, missed_winner, trend_capture, tes,
                engineering_root_cause, payload, evidence_updated_at
            ) VALUES (
                :candidate_id, CAST(:trading_day AS DATE), :symbol, :direction, :setup,
                CAST(:rr AS DOUBLE PRECISION), CAST(:setup_score AS DOUBLE PRECISION),
                :holding_profile,
                CAST(:option_quality AS DOUBLE PRECISION), :trend_health,
                :regime, :top_candidate, :quote_freshness, :rule_evaluation, :decision,
                :scanner_recommendation, :execution_eligibility, :execution_outcome, :execution_reason,
                :trade_status, :telegram_status, :telegram_reason,
                :latest_decision, :first_actionable_decision,
                CAST(:first_actionable_at AS TIMESTAMPTZ), :first_actionable_scan_id,
                CAST(:decision_history AS JSONB),
                :suggestion_status, :paper_trade_status, :replay_outcome, :target_first,
                :stop_first, :winner, :missed_winner, CAST(:trend_capture AS DOUBLE PRECISION),
                CAST(:tes AS DOUBLE PRECISION), :engineering_root_cause,
                CAST(:payload AS JSONB), CAST(:evidence_updated_at AS TIMESTAMPTZ)
            ) ON CONFLICT (candidate_id) DO UPDATE SET
                rr = EXCLUDED.rr,
                setup_score = EXCLUDED.setup_score,
                holding_profile = EXCLUDED.holding_profile,
                option_quality = EXCLUDED.option_quality,
                trend_health = EXCLUDED.trend_health,
                regime = EXCLUDED.regime,
                top_candidate = EXCLUDED.top_candidate,
                quote_freshness = EXCLUDED.quote_freshness,
                rule_evaluation = EXCLUDED.rule_evaluation,
                decision = EXCLUDED.decision,
                scanner_recommendation = EXCLUDED.scanner_recommendation,
                execution_eligibility = EXCLUDED.execution_eligibility,
                execution_outcome = EXCLUDED.execution_outcome,
                execution_reason = EXCLUDED.execution_reason,
                trade_status = EXCLUDED.trade_status,
                telegram_status = EXCLUDED.telegram_status,
                telegram_reason = EXCLUDED.telegram_reason,
                latest_decision = EXCLUDED.latest_decision,
                first_actionable_decision = EXCLUDED.first_actionable_decision,
                first_actionable_at = EXCLUDED.first_actionable_at,
                first_actionable_scan_id = EXCLUDED.first_actionable_scan_id,
                decision_history = EXCLUDED.decision_history,
                suggestion_status = EXCLUDED.suggestion_status,
                paper_trade_status = EXCLUDED.paper_trade_status,
                -- The five outcome columns keep a resolved verdict rather than
                -- taking whatever the rebuild carries. This builder reads them
                -- from `Replay Outcome`, which is NO_REPLAY on every scanner row
                -- ever written, so an unguarded rebuild resolves them all to
                -- false and silently erases what
                -- `tools/resolve_candidate_outcomes.py` established by replaying
                -- the bars. Incoming wins only when it actually resolved
                -- something; otherwise the stored verdict stands.
                replay_outcome = CASE
                    WHEN EXCLUDED.target_first OR EXCLUDED.stop_first
                    THEN EXCLUDED.replay_outcome
                    ELSE COALESCE(candidate_evidence.replay_outcome, EXCLUDED.replay_outcome)
                END,
                target_first = CASE
                    WHEN EXCLUDED.target_first OR EXCLUDED.stop_first
                    THEN EXCLUDED.target_first
                    ELSE candidate_evidence.target_first
                END,
                stop_first = CASE
                    WHEN EXCLUDED.target_first OR EXCLUDED.stop_first
                    THEN EXCLUDED.stop_first
                    ELSE candidate_evidence.stop_first
                END,
                winner = CASE
                    WHEN EXCLUDED.target_first OR EXCLUDED.stop_first
                    THEN EXCLUDED.winner
                    ELSE candidate_evidence.winner
                END,
                missed_winner = CASE
                    WHEN EXCLUDED.target_first OR EXCLUDED.stop_first
                    THEN EXCLUDED.missed_winner
                    ELSE candidate_evidence.missed_winner
                END,
                trend_capture = EXCLUDED.trend_capture,
                tes = EXCLUDED.tes,
                engineering_root_cause = EXCLUDED.engineering_root_cause,
                payload = EXCLUDED.payload,
                evidence_updated_at = EXCLUDED.evidence_updated_at
        """, records)