from __future__ import annotations

from app.analytics.candidate_snapshot_writer import normalize_candidate_row
from app.db.repository_base import BestEffortRepository


class CandidateSnapshotRepository(BestEffortRepository):
    def batch_insert(self, snapshots, trading_day=None, scan_id=None):
        rows = [normalize_candidate_row(row, trading_day=trading_day, scan_id=scan_id) for row in (snapshots or [])]
        return self._batch_execute("""
            INSERT INTO candidate_snapshot (trading_day, scan_id, symbol, direction, setup, score, regime, entry_readiness, risk_reward, option_quality, candidate_rank, action, scanner_recommendation, execution_eligibility, execution_outcome, execution_reason, trade_status, telegram_status, telegram_reason, blocked_reason, realtime_ready, execution_ready, candidate_persistence, created_at)
            VALUES (:trading_day, :scan_id, :symbol, :direction, :setup, :score, :regime, :entry_readiness, :risk_reward, :option_quality, :candidate_rank, :action, :scanner_recommendation, :execution_eligibility, :execution_outcome, :execution_reason, :trade_status, :telegram_status, :telegram_reason, :blocked_reason, :realtime_ready, :execution_ready, :candidate_persistence, now())
        """, [{
            "trading_day": row.get("trading_day"), "scan_id": row.get("scan_id"), "symbol": row.get("symbol"), "direction": row.get("direction"), "setup": row.get("setup_type"), "score": row.get("setup_percent"), "regime": row.get("market_regime"), "entry_readiness": raw.get("ENTRY_READINESS"), "risk_reward": row.get("candidate_rr"), "option_quality": row.get("option_quality_score"), "candidate_rank": raw.get("Candidate Rank"), "action": row.get("action_status"), "scanner_recommendation": row.get("scanner_recommendation"), "execution_eligibility": row.get("execution_eligibility"), "execution_outcome": row.get("execution_outcome"), "execution_reason": row.get("execution_reason"), "trade_status": row.get("trade_status"), "telegram_status": row.get("telegram_status"), "telegram_reason": row.get("telegram_reason"), "blocked_reason": row.get("blocked_by"), "realtime_ready": raw.get("Realtime Ready"), "execution_ready": raw.get("Execution Ready"), "candidate_persistence": raw.get("Candidate Persistence"),
        } for row, raw in zip(rows, snapshots or [])])

    def insert(self, snapshot, **context): return self.batch_insert([snapshot], **context)
    def upsert(self, snapshot, **context): return self.insert(snapshot, **context)
    def get(self, *_args, **_kwargs): return []
