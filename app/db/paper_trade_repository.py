from app.db.repository_base import BestEffortRepository

class PaperTradeRepository(BestEffortRepository):
    def upsert(self, trade):
        trade = trade or {}; context = trade.get("scanner_context") or {}
        return self._execute("""INSERT INTO paper_trade (trade_id,symbol,direction,setup,entry_time,entry_price,exit_time,exit_price,stop,target,rr,result,r_multiple,completed) VALUES (:trade_id,:symbol,:direction,:setup,:entry_time,:entry_price,:exit_time,:exit_price,:stop,:target,:rr,:result,:r_multiple,:completed) ON CONFLICT (trade_id) DO UPDATE SET exit_time=EXCLUDED.exit_time,exit_price=EXCLUDED.exit_price,result=EXCLUDED.result,r_multiple=EXCLUDED.r_multiple,completed=EXCLUDED.completed""", {"trade_id": trade.get("trade_id") or trade.get("trade_key"), "symbol":trade.get("symbol"),"direction":trade.get("direction"),"setup":trade.get("entry_type"),"entry_time":trade.get("opened_at_et") or trade.get("opened_at"),"entry_price":trade.get("entry_price"),"exit_time":trade.get("closed_at_et") or trade.get("closed_at"),"exit_price":trade.get("close_price"),"stop":trade.get("stop_loss"),"target":trade.get("take_profit"),"rr":trade.get("planned_rr") or context.get("Candidate RR"),"result":trade.get("outcome"),"r_multiple":trade.get("r_multiple"),"completed":str(trade.get("status")).upper()=="CLOSED"})
    def insert(self, trade): return self.upsert(trade)
    def batch_insert(self, trades): return sum(bool(self.upsert(t)) for t in (trades or []))
    def get(self, *_args, **_kwargs): return None
