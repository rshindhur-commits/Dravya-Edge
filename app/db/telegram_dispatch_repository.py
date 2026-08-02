from app.db.repository_base import BestEffortRepository


class TelegramDispatchRepository(BestEffortRepository):

    def fetch_for_day(self, trading_day):
        """Dispatches for a trading day, shaped like the local audit rows.

        The dashboard counted sends by reading
        `data/live/telegram_dispatch_audit.jsonl`, which is written by whichever
        container did the sending. With alerting on the Render worker that file
        never exists on Streamlit, so the Telegram health cell would have read
        `0 sent / 0 failed` all session while alerts were going out.

        Returns `None` when the read fails, so the caller can show "unavailable"
        rather than a confident zero.
        """

        rows = self._fetch_optional(
            """
            SELECT status, trade_id, symbol, message_type, timestamp
            FROM telegram_dispatch
            WHERE timestamp >= CAST(:day AS date)
              AND timestamp <  CAST(:day AS date) + INTERVAL '1 day'
            ORDER BY timestamp
            """,
            {"day": str(trading_day)},
        )

        if rows is None:

            return None

        # DELIVERED/FAILED are the audit file's SENT/FAILED. ATTEMPTED is carried
        # through as ATTEMPT so a queued-but-undelivered send is not counted as
        # either -- it is neither, and calling it a success is how a stuck queue
        # would read as a healthy one.
        events = {"DELIVERED": "SENT", "FAILED": "FAILED", "ATTEMPTED": "ATTEMPT"}

        return [
            {
                "event": events.get(str(row.get("status") or "").upper(), "ATTEMPT"),
                "trade_id": row.get("trade_id"),
                "symbol": row.get("symbol"),
                "message_type": row.get("message_type"),
                "observed_at_utc": str(row.get("timestamp") or ""),
            }
            for row in rows
        ]

    def insert(self, row):
        row = row or {}
        return self._execute("""INSERT INTO telegram_dispatch (scan_id,trade_id,symbol,direction,candidate_key,message_type,decision,policy,parse_mode,message_length,telegram_response,attempt,latency_ms,attempted,delivered,status,failure_reason,telegram_message_id,timestamp) VALUES (:scan_id,:trade_id,:symbol,:direction,:candidate_key,:message_type,:decision,:policy,:parse_mode,:message_length,CAST(:telegram_response AS JSONB),:attempt,:latency_ms,:attempted,:delivered,:status,:failure_reason,:telegram_message_id,:timestamp)""", row)
