from app.db.repository_base import BestEffortRepository


class TelegramDispatchRepository(BestEffortRepository):
    def insert(self, row):
        row = row or {}
        return self._execute("""INSERT INTO telegram_dispatch (scan_id,trade_id,symbol,direction,candidate_key,message_type,decision,policy,parse_mode,message_length,telegram_response,attempt,latency_ms,attempted,delivered,status,failure_reason,telegram_message_id,timestamp) VALUES (:scan_id,:trade_id,:symbol,:direction,:candidate_key,:message_type,:decision,:policy,:parse_mode,:message_length,CAST(:telegram_response AS JSONB),:attempt,:latency_ms,:attempted,:delivered,:status,:failure_reason,:telegram_message_id,:timestamp)""", row)
