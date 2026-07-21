import json

from app.db.repository_base import BestEffortRepository

class TradeFactRepository(BestEffortRepository):
    def insert_completed_trade(self, trade):
        return self._execute("INSERT INTO trade (trade_id,scan_id,trading_day,symbol,direction,setup,entry_facts,exit_facts,outcome,completed_at,created_at) VALUES (:trade_id,:scan_id,:trading_day,:symbol,:direction,:setup,CAST(:entry_facts AS JSONB),CAST(:exit_facts AS JSONB),CAST(:outcome AS JSONB),:completed_at,now()) ON CONFLICT (trade_id) DO NOTHING", {"trade_id":trade["trade_id"],"scan_id":trade.get("scan_id"),"trading_day":trade.get("trading_day"),"symbol":trade.get("symbol"),"direction":trade.get("direction"),"setup":trade.get("setup"),"entry_facts":json.dumps(trade.get("entry_facts") or {}, default=str),"exit_facts":json.dumps(trade.get("exit_facts") or {}, default=str),"outcome":json.dumps(trade.get("outcome") or {}, default=str),"completed_at":trade.get("completed_at")})
    def insert_event(self, event):
        event = {**event, "payload": json.dumps(event.get("payload") or {}, default=str)}
        return self._execute("INSERT INTO event_stream (trade_id,event_type,occurred_at,payload) VALUES (:trade_id,:event_type,:occurred_at,CAST(:payload AS JSONB))", event)

    def batch_insert_events(self, events):
        return sum(bool(self.insert_event(event)) for event in (events or []))
