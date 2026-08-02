from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.connection import get_engine
from app.db.persistence import _json_safe, db_writes_enabled


logger = logging.getLogger(__name__)


class BestEffortRepository:
    """Opt-in Postgres writer. Call only from RuntimeScheduler jobs."""

    def _batch_execute(self, statement, params):
        params = list(params or [])
        if not params or not db_writes_enabled():
            return 0
        try:
            with get_engine().begin() as connection:
                connection.execute(text(statement), [_json_safe(row) for row in params])
            return len(params)
        except Exception:
            logger.warning("Artifact DB batch failed; preserving file-backed flow", exc_info=True)
            return 0

    def _execute(self, statement, params):
        return self._batch_execute(statement, [params])

    def _fetch(self, statement, params=None):
        """Best-effort read. Returns [] rather than raising.

        Deliberately not gated on `db_writes_enabled()`: that flag governs whether
        this process may *write*, and a read-only analytics query is safe even when
        writing is switched off. Callers must treat [] as "no data available", never
        as "no trades happened" -- the difference matters, because reporting zero
        completed trades when the query simply failed is how a broken pipeline
        disguises itself as a quiet day.
        """

        rows = self._fetch_optional(statement, params)

        return [] if rows is None else rows

    def _fetch_optional(self, statement, params=None):
        """Same read, but `None` for failure and `[]` for genuinely empty.

        `_fetch` collapses the two, which is safe for a caller that falls back to
        files and unsafe for one that acts on the absence of rows. The dedup
        check is the second kind: "no record of sending this" and "could not ask"
        must not both mean send. On 2026-08-01 they did, and a weekly summary
        went to subscribers repeatedly, each copy also reporting zero trades
        because the same outage emptied the trade query.
        """

        try:
            with get_engine().connect() as connection:
                rows = connection.execute(text(statement), params or {}).mappings().all()
            return [dict(row) for row in rows]
        except Exception:
            logger.warning("Artifact DB read failed; caller falls back to files", exc_info=True)
            return None
