-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
-- Neon is the durable system of record; local snapshots are cache/development artifacts.

CREATE TABLE IF NOT EXISTS scanner_snapshot (
    id BIGSERIAL PRIMARY KEY,
    trading_day DATE NOT NULL,
    scan_id TEXT NOT NULL UNIQUE,
    scan_timestamp TIMESTAMPTZ NOT NULL,
    scanner_version TEXT,
    data_version TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scanner_snapshot_day_time
    ON scanner_snapshot (trading_day, scan_timestamp);

CREATE TABLE IF NOT EXISTS scanner_regression_baseline (
    trading_day DATE PRIMARY KEY,
    baseline_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    frozen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);