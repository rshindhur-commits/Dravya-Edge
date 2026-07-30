from __future__ import annotations

import json

from app.db.repository_base import BestEffortRepository


class RecommendationOutcomeRepository(BestEffortRepository):
    def batch_insert_facts(self, rows):
        return self._batch_execute("""
            INSERT INTO recommendation_fact (
                recommendation_id, trading_day, scan_id, recommended_at, symbol, direction,
                setup, candidate_rank, top_candidate, entry_price, option_ticker,
                option_entry_mid, scanner_recommendation, execution_eligibility,
                execution_outcome, execution_reason, payload
            ) VALUES (
                :recommendation_id, CAST(:trading_day AS DATE), :scan_id,
                CAST(:recommended_at AS TIMESTAMPTZ), :symbol, :direction, :setup,
                :candidate_rank, :top_candidate, :entry_price, :option_ticker,
                :option_entry_mid, :scanner_recommendation, :execution_eligibility,
                :execution_outcome, :execution_reason, CAST(:payload AS JSONB)
            ) ON CONFLICT (recommendation_id) DO NOTHING
        """, [{**row, "payload": json.dumps(row, default=str)} for row in rows or []])

    def batch_insert_outcomes(self, rows):
        return self._batch_execute("""
            INSERT INTO recommendation_horizon_outcome (
                recommendation_id, horizon_sessions, evaluation_trading_day, evaluated_at,
                symbol, direction, entry_price, evaluation_price, underlying_return_pct,
                directional_return_pct, option_return_pct, option_outcome_status, payload
            ) VALUES (
                :recommendation_id, :horizon_sessions, CAST(:evaluation_trading_day AS DATE),
                CAST(:evaluated_at AS TIMESTAMPTZ), :symbol, :direction, :entry_price,
                :evaluation_price, :underlying_return_pct, :directional_return_pct,
                :option_return_pct, :option_outcome_status, CAST(:payload AS JSONB)
            ) ON CONFLICT (recommendation_id, horizon_sessions) DO NOTHING
        """, [{**row, "payload": json.dumps(row, default=str)} for row in rows or []])