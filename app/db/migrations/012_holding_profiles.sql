-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
-- Keeps the JSON payload authoritative while exposing lifecycle dimensions for reporting.

ALTER TABLE IF EXISTS paper_trades
    ADD COLUMN IF NOT EXISTS holding_profile TEXT NOT NULL DEFAULT 'INTRADAY',
    ADD COLUMN IF NOT EXISTS overnight_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS days_held INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS forced_eod_exit BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS session_id_open TEXT,
    ADD COLUMN IF NOT EXISTS session_id_close TEXT;

CREATE INDEX IF NOT EXISTS idx_paper_trades_holding_profile_status
    ON paper_trades (holding_profile, status);

ALTER TABLE IF EXISTS candidate_evidence
    ADD COLUMN IF NOT EXISTS holding_profile TEXT;

ALTER TABLE IF EXISTS event_stream
    ADD COLUMN IF NOT EXISTS overnight_transition BOOLEAN NOT NULL DEFAULT FALSE;