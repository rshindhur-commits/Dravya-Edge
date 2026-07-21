from app.db.repository_base import BestEffortRepository

class LossAttributionRepository(BestEffortRepository):
    def batch_insert(self, rows, trading_day, scan_id=None):
        return self._batch_execute("""INSERT INTO missed_winner_analysis (trading_day,scan_id,symbol,setup,move_pct,classification,root_cause,blocked_by,rule,threshold,actual_value,would_have_passed_if,confidence,recommendation) VALUES (:trading_day,:scan_id,:symbol,:setup,:move_pct,:classification,:root_cause,:blocked_by,:rule,CAST(:threshold AS TEXT),:actual_value,:would_have_passed_if,:confidence,:recommendation)""", [{**row,"trading_day":trading_day,"scan_id":scan_id,"classification":row.get("reason"),"actual_value":row.get("threshold")} for row in (rows or [])])
    def insert(self,row,**context): return self.batch_insert([row],**context)
    def upsert(self,row,**context): return self.insert(row,**context)
    def get(self,*_args,**_kwargs): return []
