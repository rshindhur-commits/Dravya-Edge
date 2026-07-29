-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
ALTER TABLE activity_trace_event
    ADD COLUMN IF NOT EXISTS previous_state TEXT,
    ADD COLUMN IF NOT EXISTS state_changed BOOLEAN,
    ADD COLUMN IF NOT EXISTS setup_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rr DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS option_quality DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS candle_time TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS candle_open DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS candle_high DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS candle_low DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS candle_close DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS candle_volume DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_activity_trace_event_state_change
    ON activity_trace_event (trading_day, symbol, state_changed, occurred_at);