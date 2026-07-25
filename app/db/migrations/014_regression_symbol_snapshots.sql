-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
-- Extends the original scan-level recorder into a hybrid per-symbol flight recorder.

ALTER TABLE scanner_snapshot
    DROP CONSTRAINT IF EXISTS scanner_snapshot_scan_id_key;

ALTER TABLE scanner_snapshot
    ADD COLUMN IF NOT EXISTS symbol TEXT,
    ADD COLUMN IF NOT EXISTS market_payload JSONB,
    ADD COLUMN IF NOT EXISTS decision_payload JSONB;

CREATE UNIQUE INDEX IF NOT EXISTS idx_scanner_snapshot_scan_symbol
    ON scanner_snapshot (scan_id, symbol)
    WHERE symbol IS NOT NULL;

CREATE TABLE IF NOT EXISTS regression_run (
    run_id TEXT PRIMARY KEY,
    trading_day DATE NOT NULL,
    strategy_version TEXT NOT NULL,
    git_commit TEXT,
    status TEXT NOT NULL,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_regression_run_day_started
    ON regression_run (trading_day, started_at DESC);

CREATE TABLE IF NOT EXISTS regression_result (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES regression_run(run_id),
    trade_key TEXT NOT NULL,
    classification TEXT NOT NULL,
    baseline_r DOUBLE PRECISION,
    current_r DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_regression_result_run
    ON regression_result (run_id, classification);