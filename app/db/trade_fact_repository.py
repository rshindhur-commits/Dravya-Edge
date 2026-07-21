import json

from app.db.repository_base import BestEffortRepository

class TradeFactRepository(BestEffortRepository):
    def insert_entry_snapshot(self, row):
        return self._execute("INSERT INTO entry_snapshot (trade_id,scan_id,trading_day,symbol,direction,setup,entered_at,payload,created_at) VALUES (:trade_id,:scan_id,:trading_day,:symbol,:direction,:setup,:entered_at,CAST(:payload AS JSONB),now()) ON CONFLICT (trade_id) DO NOTHING", {"trade_id":row["trade_id"],"scan_id":row.get("scan_id"),"trading_day":row.get("trading_day"),"symbol":row.get("symbol"),"direction":row.get("direction"),"setup":row.get("setup"),"entered_at":row.get("entered_at"),"payload":json.dumps(row, default=str)})
    def insert_exit_snapshot(self, row):
        return self._execute("INSERT INTO exit_snapshot (trade_id,exit_time,exit_price,primary_exit,payload,created_at) VALUES (:trade_id,:exit_time,:exit_price,:primary_exit,CAST(:payload AS JSONB),now()) ON CONFLICT (trade_id) DO NOTHING", {"trade_id":row["trade_id"],"exit_time":row.get("exit_time"),"exit_price":row.get("exit_price"),"primary_exit":row.get("primary_exit"),"payload":json.dumps(row, default=str)})
    def insert_timeline_event(self, event):
        event = {**event, "payload": json.dumps(event.get("payload") or {}, default=str)}
        return self._execute("INSERT INTO trade_timeline_event (trade_id,event_type,occurred_at,payload) VALUES (:trade_id,:event_type,:occurred_at,CAST(:payload AS JSONB))", event)
