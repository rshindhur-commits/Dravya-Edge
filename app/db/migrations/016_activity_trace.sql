-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
CREATE TABLE IF NOT EXISTS activity_trace_event (
    event_id TEXT PRIMARY KEY,
    trading_day DATE NOT NULL,
    occurred_at TIMESTAMPTZ,
    symbol TEXT,
    category TEXT NOT NULL,
    event TEXT NOT NULL,
    context TEXT,
    origin TEXT NOT NULL,
    stage TEXT,
    rule TEXT,
    passed BOOLEAN,
    actual TEXT,
    required TEXT,
    scan_id TEXT,
    candidate_key TEXT,
    trade_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    persisted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_trace_event_day_symbol_time
    ON activity_trace_event (trading_day, symbol, occurred_at);
CREATE INDEX IF NOT EXISTS idx_activity_trace_event_trade_time
    ON activity_trace_event (trade_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_activity_trace_event_scan
    ON activity_trace_event (scan_id);
