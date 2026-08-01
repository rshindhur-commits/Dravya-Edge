import json

from app.db.repository_base import BestEffortRepository


class TelegramAlertStateRepository(BestEffortRepository):
    """Durable home for the Telegram dedup keys. See migration 027.

    Not the hot path: `alert_was_sent` reads the JSON file, which this hydrates
    once per process. The only moment the file is wrong is the first scan in a
    fresh container, and that is the moment this exists for.
    """

    def upsert(self, alert_key, metadata=None):
        metadata = dict(metadata or {})

        return self._execute(
            """
            INSERT INTO telegram_alert_state (
                alert_key, event_type, message_type, symbol, direction,
                option_ticker, candidate_key, closed, closed_at, sent_at,
                metadata, updated_at
            ) VALUES (
                :alert_key, :event_type, :message_type, :symbol, :direction,
                :option_ticker, :candidate_key, :closed, :closed_at, :sent_at,
                CAST(:metadata AS JSONB), NOW()
            ) ON CONFLICT (alert_key) DO UPDATE SET
                event_type = EXCLUDED.event_type,
                message_type = EXCLUDED.message_type,
                symbol = EXCLUDED.symbol,
                direction = EXCLUDED.direction,
                option_ticker = EXCLUDED.option_ticker,
                candidate_key = EXCLUDED.candidate_key,
                closed = EXCLUDED.closed,
                closed_at = EXCLUDED.closed_at,
                sent_at = EXCLUDED.sent_at,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            {
                "alert_key": str(alert_key),
                "event_type": metadata.get("event_type"),
                "message_type": metadata.get("message_type"),
                "symbol": metadata.get("symbol"),
                "direction": metadata.get("direction"),
                "option_ticker": metadata.get("option_ticker"),
                "candidate_key": metadata.get("candidate_key"),
                "closed": bool(metadata.get("closed")),
                "closed_at": metadata.get("closed_at"),
                "sent_at": metadata.get("sent_at"),
                "metadata": json.dumps(metadata, default=str),
            },
        )

    def insert(self, alert_key, metadata=None):
        return self.upsert(alert_key, metadata)

    def get(self, *_args, **_kwargs):
        return None

    def fetch_recent(self, within_days=30):
        """Dedup keys from the trailing window, as `{alert_key: metadata}`.

        Bounded rather than unbounded because most keys are date-scoped and go
        dead within a day -- `_review_alert_key` carries the date, the weekly
        summary carries the ISO week. The window only needs to outlive the
        longest-lived key, which is an ENTRY alert for a multiday position.
        """

        rows = self._fetch(
            """
            SELECT alert_key, metadata, sent_at
            FROM telegram_alert_state
            WHERE sent_at IS NULL
               OR sent_at >= NOW() - make_interval(days => :within_days)
            ORDER BY sent_at DESC NULLS LAST
            """,
            {"within_days": int(within_days)},
        )

        hydrated = {}

        for row in rows:
            metadata = row.get("metadata")

            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except ValueError:
                    metadata = {}

            hydrated[row["alert_key"]] = metadata or {}

        return hydrated

    def mark_closed(self, symbol, option_ticker=None, event_type="ENTRY", closed_at=None):
        """Close every open alert for a symbol, matching `mark_alert_closed`."""

        return self._execute(
            """
            UPDATE telegram_alert_state
               SET closed = TRUE,
                   closed_at = COALESCE(CAST(:closed_at AS TIMESTAMPTZ), NOW()),
                   metadata = COALESCE(metadata, '{}'::jsonb)
                              || jsonb_build_object('closed', TRUE),
                   updated_at = NOW()
             WHERE symbol = :symbol
               AND event_type = :event_type
               AND closed = FALSE
               AND (:option_ticker IS NULL OR option_ticker = :option_ticker)
            """,
            {
                "symbol": symbol,
                "event_type": event_type,
                "option_ticker": option_ticker,
                "closed_at": closed_at,
            },
        )

    def prune(self, keep_days=90):
        """Bound the table. Keys older than this cannot dedup anything."""

        return self._execute(
            """
            DELETE FROM telegram_alert_state
            WHERE sent_at IS NOT NULL
              AND sent_at < NOW() - make_interval(days => :keep_days)
            """,
            {"keep_days": int(keep_days)},
        )
