"""Durable cache for the upcoming earnings calendar.

Reads are deliberately not gated on db_writes_enabled(): the blackout has to work
in a process that is not permitted to write, and a missing calendar means trades
open into earnings.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.db.connection import get_engine
from app.db.repository_base import BestEffortRepository


class EarningsCalendarRepository(BestEffortRepository):

    def replace_all(self, events):
        """Swap in a freshly fetched calendar, in one transaction.

        Delete-then-insert rather than upsert because Alpha Vantage returns the
        complete upcoming window on every call, so a row that has disappeared has
        been rescheduled or withdrawn and must not survive. Both statements share a
        transaction: a concurrent reader sees the old calendar or the new one,
        never an empty table, which would read as "no earnings anywhere" and lift
        every blackout at once.
        """

        rows = [
            {"symbol": symbol, "report_date": report_date.isoformat()}
            for symbol, dates in (events or {}).items()
            for report_date in dates
        ]

        if not rows:
            return 0

        from app.db.persistence import db_writes_enabled

        if not db_writes_enabled():
            return 0

        try:
            with get_engine().begin() as connection:
                connection.execute(text("DELETE FROM earnings_calendar"))
                connection.execute(
                    text("""
                        INSERT INTO earnings_calendar (symbol, report_date, fetched_at)
                        VALUES (:symbol, CAST(:report_date AS date), now())
                        ON CONFLICT (symbol, report_date) DO NOTHING
                    """),
                    rows,
                )

            return len(rows)

        except Exception as exc:
            print(f"[EARNINGS CALENDAR DB WARNING] replace failed: {exc}")
            return 0

    def fetch_all(self):
        """symbol -> sorted upcoming report dates. {} when unavailable."""

        rows = self._fetch("""
            SELECT symbol, report_date
            FROM earnings_calendar
            WHERE report_date >= CURRENT_DATE
            ORDER BY symbol, report_date
        """)

        events = {}

        for row in rows or []:
            symbol = str(row.get("symbol") or "").upper()
            report_date = row.get("report_date")

            if not symbol or report_date is None:
                continue

            if isinstance(report_date, datetime):
                report_date = report_date.date()

            events.setdefault(symbol, []).append(report_date)

        return events

    def fetch_age_hours(self):
        """How stale the cached calendar is, or None when there is none."""

        rows = self._fetch("""
            SELECT EXTRACT(EPOCH FROM (now() - MAX(fetched_at))) / 3600 AS age_hours
            FROM earnings_calendar
        """)

        if not rows:
            return None

        age = rows[0].get("age_hours")

        return float(age) if age is not None else None
