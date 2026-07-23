-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
CREATE TABLE IF NOT EXISTS decision_waterfall (
    id BIGSERIAL PRIMARY KEY,
    scan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    stage TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    passed BOOLEAN NOT NULL,
    blocking BOOLEAN NOT NULL DEFAULT FALSE,
    rule_name TEXT NOT NULL,
    actual_value TEXT,
    required_value TEXT,
    priority INTEGER,
    summary TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_decision_waterfall_scan_symbol
    ON decision_waterfall (scan_id, symbol, stage_order);
CREATE INDEX IF NOT EXISTS idx_decision_waterfall_blocking
    ON decision_waterfall (stage, blocking, timestamp);