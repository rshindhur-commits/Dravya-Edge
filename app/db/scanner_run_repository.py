from app.db.repository_base import BestEffortRepository


class ScannerRunRepository(BestEffortRepository):
    def upsert(self, row):
        row = row or {}
        return self._execute("""INSERT INTO scanner_run (scan_id,start_time,end_time,runtime_ms,workers,symbols,completed,failed,api_requests,cache_hits,telegram_latency,queue_depth,health_score) VALUES (:scan_id,:start_time,:end_time,:runtime_ms,:workers,:symbols,:completed,:failed,:api_requests,:cache_hits,:telegram_latency,:queue_depth,:health_score) ON CONFLICT (scan_id) DO UPDATE SET end_time=EXCLUDED.end_time,runtime_ms=EXCLUDED.runtime_ms,completed=EXCLUDED.completed,failed=EXCLUDED.failed,api_requests=EXCLUDED.api_requests,health_score=EXCLUDED.health_score""", row)
    def insert(self, row): return self.upsert(row)
    def batch_insert(self, rows): return sum(bool(self.upsert(row)) for row in (rows or []))
    def get(self, *_args, **_kwargs): return None
