from app.db.repository_base import BestEffortRepository


class TelegramDispatchRepository(BestEffortRepository):
    def insert(self, row):
        row = row or {}
        return self._execute("""INSERT INTO telegram_dispatch (scan_id,trade_id,symbol,message_type,decision,attempted,delivered,status,failure_reason,telegram_message_id,timestamp) VALUES (:scan_id,:trade_id,:symbol,:message_type,:decision,:attempted,:delivered,:status,:failure_reason,:telegram_message_id,:timestamp)""", row)
