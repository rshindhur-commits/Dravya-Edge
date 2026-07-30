-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
--
-- The auto-paper decision ledger was file-only: append_daily_auto_paper_decision()
-- wrote data/daily/<day>/auto_paper_decisions.csv and a 500-row rolling JSON, and
-- nothing else. On Streamlit Cloud that filesystem is ephemeral, so the record of
-- *why a trade did not happen* -- every BLOCKED, SKIPPED and OPENED with its reason
-- -- was lost whenever the container recycled. It is the highest-value diagnostic
-- table in the system and it was the only one not durable.
--
-- Deliberately append-only with no unique constraint. A duplicate audit row is
-- harmless; a silently dropped one is not, and ON CONFLICT DO NOTHING against
-- (scan_id, symbol, decision) would discard a second, differently-reasoned SKIP
-- for the same symbol in the same scan. Dedupe at read time if it ever matters.

CREATE TABLE IF NOT EXISTS auto_paper_decision (
    id BIGSERIAL PRIMARY KEY,
    trading_day DATE,
    session_id TEXT,
    scan_id TEXT NOT NULL,
    scan_timestamp TIMESTAMPTZ,
    market_session TEXT,
    is_auto_entry_window BOOLEAN,
    is_after_close BOOLEAN,
    minutes_from_open DOUBLE PRECISION,
    minutes_to_close DOUBLE PRECISION,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    trade_key TEXT,
    top_candidate TEXT,
    setup_percent DOUBLE PRECISION,
    candidate_rr DOUBLE PRECISION,
    min_rr_used DOUBLE PRECISION,
    min_setup_used DOUBLE PRECISION,
    setup_valid BOOLEAN,
    realtime_ready BOOLEAN,
    action_status TEXT,
    blocked_by TEXT,
    scanner_blocked_by TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The end-of-day review reads a whole day at a time.
CREATE INDEX IF NOT EXISTS idx_auto_paper_decision_day
    ON auto_paper_decision (trading_day, symbol);

-- "Why was nothing entered between 14:00 and 15:00" reads by scan.
CREATE INDEX IF NOT EXISTS idx_auto_paper_decision_scan
    ON auto_paper_decision (scan_id);

-- Block-reason frequency is the tuning query: which gate costs the most trades.
CREATE INDEX IF NOT EXISTS idx_auto_paper_decision_blocked
    ON auto_paper_decision (trading_day, decision, blocked_by);
