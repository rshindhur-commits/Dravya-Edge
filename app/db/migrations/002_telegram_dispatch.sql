-- Apply manually through DATABASE_DIRECT_URL. No runtime schema DDL.
CREATE TABLE IF NOT EXISTS telegram_dispatch (
    id BIGSERIAL PRIMARY KEY,
    scan_id TEXT,
    trade_id TEXT,
    symbol TEXT,
    message_type TEXT,
    decision TEXT,
    attempted BOOLEAN NOT NULL DEFAULT FALSE,
    delivered BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL,
    failure_reason TEXT,
    telegram_message_id TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_telegram_dispatch_scan_symbol ON telegram_dispatch (scan_id, symbol);
CREATE INDEX IF NOT EXISTS idx_telegram_dispatch_status ON telegram_dispatch (status, timestamp);
