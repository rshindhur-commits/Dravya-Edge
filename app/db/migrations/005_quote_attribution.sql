CREATE TABLE IF NOT EXISTS quote_attribution (
    attribution_id TEXT PRIMARY KEY,
    trading_day DATE NOT NULL,
    scan_id TEXT NOT NULL,
    scanner_timestamp TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    option_ticker TEXT,
    quote_timestamp TIMESTAMPTZ,
    quote_age_seconds DOUBLE PRECISION,
    source_timestamp_field TEXT,
    quote_source TEXT,
    allowed_age_seconds DOUBLE PRECISION,
    final_classification TEXT NOT NULL,
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_quote_attribution_day_classification
    ON quote_attribution (trading_day, final_classification);