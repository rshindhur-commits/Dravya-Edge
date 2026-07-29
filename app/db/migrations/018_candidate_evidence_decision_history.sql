-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
ALTER TABLE candidate_evidence
    ADD COLUMN IF NOT EXISTS latest_decision TEXT,
    ADD COLUMN IF NOT EXISTS first_actionable_decision TEXT,
    ADD COLUMN IF NOT EXISTS first_actionable_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS first_actionable_scan_id TEXT,
    ADD COLUMN IF NOT EXISTS decision_history JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_candidate_evidence_actionable_day_symbol
    ON candidate_evidence (trading_day, first_actionable_decision, symbol);