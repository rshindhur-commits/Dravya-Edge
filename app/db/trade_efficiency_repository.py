from app.db.repository_base import BestEffortRepository

class TradeEfficiencyRepository(BestEffortRepository):
    def upsert(self, row):
        row=row or {}
        return self._execute("""INSERT INTO trade_efficiency (trade_id,trend_capture,tes,mfe,mae,available_move,captured_move,left_on_table,entry_grade,exit_grade,exit_trigger,exit_verdict,engineering_recommendation,bars_held,trend_health,created_at) VALUES (:trade_id,:trend_capture,:tes,:mfe,:mae,:available_move,:captured_move,:left_on_table,:entry_grade,:exit_grade,:exit_trigger,:exit_verdict,:engineering_recommendation,:bars_held,:trend_health,now()) ON CONFLICT (trade_id) DO UPDATE SET trend_capture=EXCLUDED.trend_capture,tes=EXCLUDED.tes,left_on_table=EXCLUDED.left_on_table,exit_trigger=EXCLUDED.exit_trigger,exit_verdict=EXCLUDED.exit_verdict""", {"trade_id":row.get("Trade Key"),"trend_capture":row.get("Trend Capture %"),"tes":row.get("Trade Efficiency Score"),"mfe":row.get("MFE"),"mae":row.get("MAE"),"available_move":row.get("Available Move"),"captured_move":row.get("Captured Move"),"left_on_table":row.get("Left On Table"),"entry_grade":row.get("Entry Grade"),"exit_grade":row.get("Exit Grade"),"exit_trigger":row.get("Exit Trigger"),"exit_verdict":row.get("Exit Verdict"),"engineering_recommendation":row.get("Engineering Recommendation"),"bars_held":row.get("Bars Held"),"trend_health":row.get("Trend Health State")})
    def insert(self,row): return self.upsert(row)
    def batch_insert(self,rows): return sum(bool(self.upsert(row)) for row in (rows or []))
    def get(self,*_args,**_kwargs): return None
