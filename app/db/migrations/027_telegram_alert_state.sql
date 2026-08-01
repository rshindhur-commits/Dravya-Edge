-- Telegram alert dedup state.
--
-- `app/state/telegram_alert_state.json` is what stops a candidate alerting twice.
-- It is gitignored and lives on an ephemeral filesystem, so every container
-- restart empties it and every dedup key resets. Subscribers then receive the
-- day's review alerts a second time, and -- since 2026-08-01 -- potentially a
-- second copy of the weekly results.
--
-- That has been survivable while restarts were rare and the audience was small.
-- The always-on worker makes restarts routine (every deploy) and growing the
-- channel makes duplicates expensive, so the state needs to outlive the process.
--
-- The JSON file stays as the hot path: `alert_was_sent` is called once per
-- candidate per scan and must not become a database round trip. This table is
-- hydrated into that file once per process, which is exactly when it matters --
-- a fresh container is the only time the file is wrong.
--
-- Columns are lifted out of the metadata blob only where they are queried:
-- `mark_alert_closed` matches on event_type + symbol + option_ticker. Everything
-- else stays in JSONB so hydration reconstructs the original dict exactly.

CREATE TABLE IF NOT EXISTS telegram_alert_state (
    alert_key     TEXT PRIMARY KEY,
    event_type    TEXT,
    message_type  TEXT,
    symbol        TEXT,
    direction     TEXT,
    option_ticker TEXT,
    candidate_key TEXT,
    closed        BOOLEAN NOT NULL DEFAULT FALSE,
    closed_at     TIMESTAMPTZ,
    sent_at       TIMESTAMPTZ,
    metadata      JSONB,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hydration reads a trailing window, newest first.
CREATE INDEX IF NOT EXISTS idx_telegram_alert_state_sent_at
    ON telegram_alert_state (sent_at DESC);

-- `mark_alert_closed` matches open ENTRY alerts for one symbol.
CREATE INDEX IF NOT EXISTS idx_telegram_alert_state_symbol_event
    ON telegram_alert_state (symbol, event_type)
    WHERE closed = FALSE;
