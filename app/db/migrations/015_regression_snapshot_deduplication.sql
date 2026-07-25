-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.

ALTER TABLE scanner_snapshot
    ADD COLUMN IF NOT EXISTS payload_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_scanner_snapshot_symbol_hash_time
    ON scanner_snapshot (symbol, payload_hash, scan_timestamp DESC);