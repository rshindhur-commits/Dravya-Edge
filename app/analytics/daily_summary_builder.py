from __future__ import annotations

from sqlalchemy import text

from app.db.connection import get_engine
from app.db.daily_summary_repository import DailySummaryRepository
from app.db.persistence import db_writes_enabled


def build_daily_session_summary(trading_day):
    """Aggregate promoted historical facts after a daily validation report exists."""
    if not db_writes_enabled():
        return None
    query = text("""
        WITH trades AS (
            SELECT COUNT(*) AS trades,
                   COUNT(*) FILTER (WHERE r_multiple > 0) AS wins,
                   COUNT(*) FILTER (WHERE r_multiple < 0) AS losses,
                   AVG(r_multiple) AS expectancy
            FROM paper_trade WHERE entry_time::date = CAST(:day AS date) AND completed
        ), efficiency AS (
            SELECT AVG(te.trend_capture) AS avg_capture, AVG(te.tes) AS avg_tes,
                   (SELECT pt.setup FROM trade_efficiency x JOIN paper_trade pt ON pt.trade_id=x.trade_id WHERE pt.entry_time::date=CAST(:day AS date) GROUP BY pt.setup ORDER BY AVG(x.trend_capture) DESC NULLS LAST LIMIT 1) AS best_setup,
                   (SELECT pt.setup FROM trade_efficiency x JOIN paper_trade pt ON pt.trade_id=x.trade_id WHERE pt.entry_time::date=CAST(:day AS date) GROUP BY pt.setup ORDER BY AVG(x.trend_capture) ASC NULLS LAST LIMIT 1) AS worst_setup
            FROM trade_efficiency te JOIN paper_trade pt ON pt.trade_id=te.trade_id WHERE pt.entry_time::date=CAST(:day AS date)
        ), misses AS (SELECT COUNT(*) AS missed_winners FROM missed_winner_analysis WHERE trading_day=CAST(:day AS date))
        SELECT trades.*, efficiency.*, misses.missed_winners FROM trades CROSS JOIN efficiency CROSS JOIN misses
    """)
    with get_engine().connect() as connection:
        row = connection.execute(query, {"day": trading_day}).mappings().one()
    trades = int(row["trades"] or 0)
    confidence = min(95, round(15 + min(35, 2 + max(0, trades - 1) * 33 / 79) + min(42, trades))) if trades else 0
    return {"trading_day": trading_day, **dict(row), "false_entries": 0, "confidence": confidence}


def write_daily_session_summary(trading_day):
    try:
        summary = build_daily_session_summary(trading_day)
        return DailySummaryRepository().upsert(summary) if summary else False
    except Exception:
        return False
