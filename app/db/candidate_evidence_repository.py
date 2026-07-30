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
                replay_outcome = EXCLUDED.replay_outcome,
                target_first = EXCLUDED.target_first,
                stop_first = EXCLUDED.stop_first,
                winner = EXCLUDED.winner,
                missed_winner = EXCLUDED.missed_winner,
                trend_capture = EXCLUDED.trend_capture,
                tes = EXCLUDED.tes,
                engineering_root_cause = EXCLUDED.engineering_root_cause,
                payload = EXCLUDED.payload,
                evidence_updated_at = EXCLUDED.evidence_updated_at
        """, records)