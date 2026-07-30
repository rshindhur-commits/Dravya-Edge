-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
ALTER TABLE candidate_snapshot
    ADD COLUMN IF NOT EXISTS scanner_recommendation TEXT,
    ADD COLUMN IF NOT EXISTS execution_eligibility TEXT,
    ADD COLUMN IF NOT EXISTS execution_outcome TEXT,
    ADD COLUMN IF NOT EXISTS execution_reason TEXT;

ALTER TABLE candidate_evidence
    ADD COLUMN IF NOT EXISTS scanner_recommendation TEXT,
    ADD COLUMN IF NOT EXISTS execution_eligibility TEXT,
    ADD COLUMN IF NOT EXISTS execution_outcome TEXT,
    ADD COLUMN IF NOT EXISTS execution_reason TEXT;

ALTER TABLE activity_trace_event
    ADD COLUMN IF NOT EXISTS scanner_recommendation TEXT,
    ADD COLUMN IF NOT EXISTS execution_eligibility TEXT,
    ADD COLUMN IF NOT EXISTS execution_outcome TEXT,
    ADD COLUMN IF NOT EXISTS execution_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_candidate_snapshot_execution_eligibility
    ON candidate_snapshot (trading_day, execution_eligibility, execution_outcome);