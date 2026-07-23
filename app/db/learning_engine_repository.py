from __future__ import annotations

import json

from app.db.repository_base import BestEffortRepository


class LearningEngineRepository(BestEffortRepository):

    def persist(self, summary, comparisons):
        summary = summary or {}
        day = summary.get("trading_day")
        payload = json.dumps(summary, default=str)
        self._execute("""
            INSERT INTO daily_engine_summary (trading_day,v1_trades,v2_shadow_trades,avg_v1_r,avg_v2_r,avg_entry_efficiency,avg_trend_capture,avg_exit_confidence,premature_exits,payload)
            VALUES (CAST(:day AS DATE),:v1,:v2,:v1r,:v2r,:eff,:capture,:confidence,:premature,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()
        """, {"day": day, "v1": summary.get("trades_compared"), "v2": summary.get("v2_shadow_trades"), "v1r": summary.get("avg_v1_r"), "v2r": summary.get("avg_v2_r"), "eff": summary.get("avg_entry_efficiency"), "capture": summary.get("avg_trend_capture"), "confidence": summary.get("avg_exit_confidence"), "premature": summary.get("premature_exits"), "payload": payload})
        self._batch_execute("""
            INSERT INTO v2_learning_metrics (trading_day,metric,value,payload) VALUES (CAST(:day AS DATE),:metric,:value,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day,metric) DO UPDATE SET value=EXCLUDED.value,payload=EXCLUDED.payload
        """, [{"day": day, "metric": key, "value": value, "payload": payload} for key, value in summary.items() if isinstance(value, (int, float))])
        self._batch_execute("""
            INSERT INTO rule_performance (trading_day,rule_name,blocked_count,payload) VALUES (CAST(:day AS DATE),:rule,:count,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day,rule_name) DO UPDATE SET blocked_count=EXCLUDED.blocked_count,payload=EXCLUDED.payload
        """, [{"day": day, "rule": stage, "count": count, "payload": payload} for stage, count in (summary.get("blocking_stages") or {}).items()])
        self._batch_execute("""
            INSERT INTO exit_quality_metrics (trading_day,premature_exits,payload) VALUES (CAST(:day AS DATE),:premature,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day) DO UPDATE SET premature_exits=EXCLUDED.premature_exits,payload=EXCLUDED.payload
        """, {"day": day, "premature": summary.get("premature_exits"), "payload": payload})
        self._batch_execute("""
            INSERT INTO trade_comparison (trading_day,symbol,direction,v1_r,v2_r,better_engine,payload)
            VALUES (CAST(:day AS DATE),:symbol,:direction,:v1,:v2,:better,CAST(:payload AS JSONB))
        """, [{"day": day, "symbol": row.get("symbol"), "direction": row.get("direction"), "v1": row.get("final_r_v1"), "v2": row.get("final_r_v2"), "better": "V2" if (row.get("final_r_delta") or 0) > 0 else "V1", "payload": json.dumps(row, default=str)} for row in comparisons or []])