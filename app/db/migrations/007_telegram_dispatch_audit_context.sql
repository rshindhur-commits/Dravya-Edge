-- Apply manually through DATABASE_DIRECT_URL. No runtime schema DDL.
ALTER TABLE telegram_dispatch
    ADD COLUMN IF NOT EXISTS direction TEXT,
    ADD COLUMN IF NOT EXISTS candidate_key TEXT,
    ADD COLUMN IF NOT EXISTS policy TEXT,
    ADD COLUMN IF NOT EXISTS parse_mode TEXT,
    ADD COLUMN IF NOT EXISTS message_length INTEGER,
    ADD COLUMN IF NOT EXISTS telegram_response JSONB,
    ADD COLUMN IF NOT EXISTS attempt INTEGER,
    ADD COLUMN IF NOT EXISTS latency_ms DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_telegram_dispatch_candidate_key
    ON telegram_dispatch (candidate_key, timestamp);