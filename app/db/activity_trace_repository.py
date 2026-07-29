from __future__ import annotations

import json

from app.db.persistence import _json_safe
from app.db.repository_base import BestEffortRepository


class ActivityTraceRepository(BestEffortRepository):
    def batch_upsert(self, events):
        rows = []
        for event in events or []:
            record = _json_safe(dict(event or {}))
            if not record.get("event_id"):
                continue
            rows.append({
                "event_id": record["event_id"],
                "trading_day": record.get("trading_day"),
                "occurred_at": record.get("time"),
                "symbol": record.get("symbol"),
                "category": record.get("category"),
                "event": record.get("event"),
                "context": record.get("context"),
                "origin": record.get("origin"),
                "stage": record.get("stage"),
                "rule": record.get("rule"),
                "passed": record.get("passed"),
                "actual": record.get("actual"),
                "required": record.get("required"),
                "previous_state": record.get("previous_state"),
                "state_changed": record.get("state_changed"),
                "setup_score": record.get("setup_score"),
                "rr": record.get("rr"),
                "option_quality": record.get("option_quality"),
                "candle_time": record.get("candle_time"),
                "candle_open": record.get("candle_open"),
                "candle_high": record.get("candle_high"),
                "candle_low": record.get("candle_low"),
                "candle_close": record.get("candle_close"),
                "candle_volume": record.get("candle_volume"),
                "scan_id": record.get("scan_id"),
                "candidate_key": record.get("candidate_key"),
                "trade_id": record.get("trade_id"),
                "payload": json.dumps(record, default=str),
            })
        if not rows:
            return 0
        return self._batch_execute("""
            INSERT INTO activity_trace_event (
                event_id, trading_day, occurred_at, symbol, category, event, context,
                origin, stage, rule, passed, actual, required, scan_id, candidate_key,
                trade_id, previous_state, state_changed, setup_score, rr, option_quality,
                candle_time, candle_open, candle_high, candle_low, candle_close,
                candle_volume, payload
            ) VALUES (
                :event_id, :trading_day, CAST(:occurred_at AS TIMESTAMPTZ), :symbol,
                :category, :event, :context, :origin, :stage, :rule, :passed,
                :actual, :required, :scan_id, :candidate_key, :trade_id,
                :previous_state, :state_changed, :setup_score, :rr, :option_quality,
                CAST(:candle_time AS TIMESTAMPTZ), :candle_open, :candle_high,
                :candle_low, :candle_close, :candle_volume,
                CAST(:payload AS JSONB)
            ) ON CONFLICT (event_id) DO UPDATE SET
                context = EXCLUDED.context,
                stage = EXCLUDED.stage,
                rule = EXCLUDED.rule,
                passed = EXCLUDED.passed,
                actual = EXCLUDED.actual,
                required = EXCLUDED.required,
                previous_state = EXCLUDED.previous_state,
                state_changed = EXCLUDED.state_changed,
                setup_score = EXCLUDED.setup_score,
                rr = EXCLUDED.rr,
                option_quality = EXCLUDED.option_quality,
                candle_time = EXCLUDED.candle_time,
                candle_open = EXCLUDED.candle_open,
                candle_high = EXCLUDED.candle_high,
                candle_low = EXCLUDED.candle_low,
                candle_close = EXCLUDED.candle_close,
                candle_volume = EXCLUDED.candle_volume,
                payload = EXCLUDED.payload,
                persisted_at = now()
        """, rows)
