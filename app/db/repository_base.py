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
