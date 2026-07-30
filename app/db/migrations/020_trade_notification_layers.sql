-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
ALTER TABLE candidate_snapshot
    ADD COLUMN IF NOT EXISTS trade_status TEXT,
    ADD COLUMN IF NOT EXISTS telegram_status TEXT,
    ADD COLUMN IF NOT EXISTS telegram_reason TEXT;

ALTER TABLE candidate_evidence
    ADD COLUMN IF NOT EXISTS trade_status TEXT,
    ADD COLUMN IF NOT EXISTS telegram_status TEXT,
    ADD COLUMN IF NOT EXISTS telegram_reason TEXT;

ALTER TABLE activity_trace_event
    ADD COLUMN IF NOT EXISTS trade_status TEXT,
    ADD COLUMN IF NOT EXISTS telegram_status TEXT,
    ADD COLUMN IF NOT EXISTS telegram_reason TEXT;