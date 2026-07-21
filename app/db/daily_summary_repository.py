from app.db.repository_base import BestEffortRepository


class DailySummaryRepository(BestEffortRepository):
    def upsert(self, row):
        return self._execute("""INSERT INTO daily_session_summary (trading_day,trades,wins,losses,expectancy,avg_capture,avg_tes,best_setup,worst_setup,missed_winners,false_entries,confidence,created_at) VALUES (:trading_day,:trades,:wins,:losses,:expectancy,:avg_capture,:avg_tes,:best_setup,:worst_setup,:missed_winners,:false_entries,:confidence,now()) ON CONFLICT (trading_day) DO UPDATE SET trades=EXCLUDED.trades,wins=EXCLUDED.wins,losses=EXCLUDED.losses,expectancy=EXCLUDED.expectancy,avg_capture=EXCLUDED.avg_capture,avg_tes=EXCLUDED.avg_tes,best_setup=EXCLUDED.best_setup,worst_setup=EXCLUDED.worst_setup,missed_winners=EXCLUDED.missed_winners,false_entries=EXCLUDED.false_entries,confidence=EXCLUDED.confidence,created_at=now()""", row or {})
    def insert(self, row): return self.upsert(row)
    def batch_insert(self, rows): return sum(bool(self.upsert(row)) for row in (rows or []))
    def get(self, *_args, **_kwargs): return None
