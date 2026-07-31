from __future__ import annotations

import json

from sqlalchemy import text

from app.db.repository_base import BestEffortRepository


class LearningEngineRepository(BestEffortRepository):

    def latest_summary(self):
        try:
            from app.db.connection import get_engine
            from app.db.persistence import db_writes_enabled
            if not db_writes_enabled():
                return None
            with get_engine().connect() as connection:
                row = connection.execute(text("SELECT payload FROM daily_engine_summary ORDER BY trading_day DESC LIMIT 1")).mappings().first()
            return dict(row["payload"]) if row and row.get("payload") else None
        except Exception:
            return None

    def get_lifetime_summary(self):
        try:
            from app.db.connection import get_engine
            from app.db.persistence import db_writes_enabled
            if not db_writes_enabled():
                return None
            with get_engine().connect() as connection:
                row = connection.execute(text("SELECT COUNT(*) AS days, SUM(v2_shadow_trades) AS v2_trades, AVG(avg_v1_r) AS avg_v1_r, AVG(avg_v2_r) AS avg_v2_r FROM daily_engine_summary")).mappings().first()
            return dict(row) if row else None
        except Exception:
            return None

    def get_feature_statistics(self):
        try:
            from app.db.connection import get_engine
            from app.db.persistence import db_writes_enabled
            if not db_writes_enabled():
                return []
            with get_engine().connect() as connection:
                rows = connection.execute(text("SELECT feature_name, version, sample_size, improvement_pct, confidence, promotion_ready FROM feature_statistics ORDER BY feature_name")).mappings().all()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def get_daily_summary(self, trading_day=None):
        try:
            from app.db.connection import get_engine
            from app.db.persistence import db_writes_enabled
            if not db_writes_enabled(): return None
            statement = "SELECT payload FROM daily_engine_summary " + ("WHERE trading_day = CAST(:day AS DATE) " if trading_day else "") + "ORDER BY trading_day DESC LIMIT 1"
            with get_engine().connect() as connection:
                row = connection.execute(text(statement), {"day": trading_day} if trading_day else {}).mappings().first()
            return dict(row["payload"]) if row and row.get("payload") else None
        except Exception:
            return None

    def get_promotion_candidates(self):
        return [row for row in self.get_feature_statistics() if row.get("promotion_ready")]

    def get_statistics(self, summary_type):
        try:
            from app.db.connection import get_engine
            from app.db.persistence import db_writes_enabled
            if not db_writes_enabled(): return []
            with get_engine().connect() as connection:
                rows = connection.execute(text("SELECT dimension, dimension_value, sample_size, wins, losses, average_r, profit_factor, payload FROM analytics_summary WHERE summary_type=:kind ORDER BY sample_size DESC"), {"kind": summary_type}).mappings().all()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def record_promotion_review(self, review):
        review = review or {}
        return self._execute("INSERT INTO promotion_review (feature_name,version,status,reviewer,note,payload) VALUES (:feature,:version,:status,:reviewer,:note,CAST(:payload AS JSONB))", {"feature": review.get("feature_name"), "version": review.get("version", "v2"), "status": review.get("status"), "reviewer": review.get("reviewer"), "note": review.get("note"), "payload": json.dumps(review, default=str)})

    def persist(self, summary, comparisons):
        summary = summary or {}
        day = summary.get("trading_day")
        payload = json.dumps(summary, default=str)
        results = []
        results.append(self._execute("""
            INSERT INTO daily_engine_summary (trading_day,v1_trades,v2_shadow_trades,avg_v1_r,avg_v2_r,avg_entry_efficiency,avg_trend_capture,avg_exit_confidence,premature_exits,payload)
            VALUES (CAST(:day AS DATE),:v1,:v2,:v1r,:v2r,:eff,:capture,:confidence,:premature,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()
        """, {"day": day, "v1": summary.get("trades_compared"), "v2": summary.get("v2_shadow_trades"), "v1r": summary.get("avg_v1_r"), "v2r": summary.get("avg_v2_r"), "eff": summary.get("avg_entry_efficiency"), "capture": summary.get("avg_trend_capture"), "confidence": summary.get("avg_exit_confidence"), "premature": summary.get("premature_exits"), "payload": payload}))
        results.append(self._batch_execute("""
            INSERT INTO v2_learning_metrics (trading_day,metric,value,payload) VALUES (CAST(:day AS DATE),:metric,:value,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day,metric) DO UPDATE SET value=EXCLUDED.value,payload=EXCLUDED.payload
        """, [{"day": day, "metric": key, "value": value, "payload": payload} for key, value in summary.items() if isinstance(value, (int, float))]))
        results.append(self._batch_execute("""
            INSERT INTO rule_performance (trading_day,rule_name,blocked_count,payload) VALUES (CAST(:day AS DATE),:rule,:count,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day,rule_name) DO UPDATE SET blocked_count=EXCLUDED.blocked_count,payload=EXCLUDED.payload
        """, [{"day": day, "rule": rule, "count": count, "payload": payload} for rule, count in (summary.get("blocking_rules") or summary.get("blocking_stages") or {}).items()]))
        results.append(self._execute("""
            INSERT INTO exit_quality_metrics (trading_day,premature_exits,payload) VALUES (CAST(:day AS DATE),:premature,CAST(:payload AS JSONB))
            ON CONFLICT (trading_day) DO UPDATE SET premature_exits=EXCLUDED.premature_exits,payload=EXCLUDED.payload
        """, {"day": day, "premature": summary.get("premature_exits"), "payload": payload}))
        results.append(self._batch_execute("""
            INSERT INTO trade_comparison (trading_day,symbol,direction,v1_r,v2_r,better_engine,payload)
            VALUES (CAST(:day AS DATE),:symbol,:direction,:v1,:v2,:better,CAST(:payload AS JSONB))
        """, [{"day": day, "symbol": row.get("symbol"), "direction": row.get("direction"), "v1": row.get("final_r_v1"), "v2": row.get("final_r_v2"), "better": "V2" if (row.get("final_r_delta") or 0) > 0 else "V1", "payload": json.dumps(row, default=str)} for row in comparisons or []]))
        results.append(self._batch_execute("""
            INSERT INTO feature_statistics (feature_name,version,sample_size,improvement_pct,confidence,promotion_ready,payload)
            VALUES (:name,'v2',:samples,:lift,:confidence,:ready,CAST(:payload AS JSONB))
            ON CONFLICT (feature_name,version) DO UPDATE SET sample_size=EXCLUDED.sample_size,improvement_pct=EXCLUDED.improvement_pct,confidence=EXCLUDED.confidence,promotion_ready=EXCLUDED.promotion_ready,payload=EXCLUDED.payload
        """, [{"name": row.get("feature_name"), "samples": row.get("completed_trades"), "lift": row.get("improvement_pct"), "confidence": row.get("confidence"), "ready": row.get("status") == "PROMOTION_CANDIDATE", "payload": json.dumps(row, default=str)} for row in (summary.get("feedback_loop") or {}).get("feature_promotion", [])]))
        results.append(self._batch_execute("""
            INSERT INTO analytics_summary (summary_type,dimension,dimension_value,sample_size,wins,losses,average_r,profit_factor,payload)
            VALUES (:summary_type,:dimension,:dimension_value,:sample_size,:wins,:losses,:average_r,:profit_factor,CAST(:payload AS JSONB))
            ON CONFLICT (summary_type,dimension,dimension_value) DO UPDATE SET sample_size=EXCLUDED.sample_size,wins=EXCLUDED.wins,losses=EXCLUDED.losses,average_r=EXCLUDED.average_r,profit_factor=EXCLUDED.profit_factor,payload=EXCLUDED.payload,updated_at=now()
        """, [{**row, "payload": json.dumps(row, default=str)} for row in summary.get("aggregate_statistics", [])]))
        return all(bool(result) for result in results if result is not None)
        self._batch_execute("""
            INSERT INTO feature_statistics (feature_name,version,sample_size,improvement_pct,confidence,promotion_ready,payload)
            VALUES (:name,'v2',:samples,:lift,:confidence,:ready,CAST(:payload AS JSONB))
            ON CONFLICT (feature_name,version) DO UPDATE SET sample_size=EXCLUDED.sample_size,improvement_pct=EXCLUDED.improvement_pct,confidence=EXCLUDED.confidence,promotion_ready=EXCLUDED.promotion_ready,payload=EXCLUDED.payload
        """, [{"name": row.get("feature_name"), "samples": row.get("completed_trades"), "lift": row.get("improvement_pct"), "confidence": row.get("confidence"), "ready": row.get("recommended_action") == "PROMOTION_CANDIDATE", "payload": json.dumps(row, default=str)} for row in (summary.get("feedback_loop") or {}).get("feature_promotion", [])])