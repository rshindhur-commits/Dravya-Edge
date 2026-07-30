-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
--
-- Baseline schema for the four core tables that were created out-of-band and had
-- no migration file. Without this, `app/db/migrations/` could not rebuild a working
-- database: the scanner writes to all four on every run, but nothing here created
-- them. Reverse-engineered from the live schema on 2026-07-29.
--
-- Numbered 000 so it applies before 001. Migration 012 later adds the holding
-- profile / session lifecycle columns to paper_trades, so they are deliberately
-- NOT duplicated here -- each migration keeps a single responsibility, and 012 is
-- idempotent (ADD COLUMN IF NOT EXISTS).
--
-- Safe to run against the existing database: every statement is IF NOT EXISTS.

-- Live paper-trade state mirror. Upserted on every open, update, and close by
-- app/db/persistence.py::upsert_paper_trade. `trade_key` is the natural key and
-- carries the ON CONFLICT target. Distinct from the `trade` table in 001, which
-- holds immutable completed-trade facts.
CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    trade_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    direction TEXT,
    option_ticker TEXT,
    status TEXT NOT NULL,
    entry_source TEXT,
    entry_price NUMERIC,
    option_entry_mid NUMERIC,
    close_price NUMERIC,
    option_close_mid NUMERIC,
    pnl_pct NUMERIC,
    r_multiple NUMERIC,
    payload JSONB,
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Per-symbol gate outcome summary, batched per scan.
CREATE TABLE IF NOT EXISTS gate_decisions (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT,
    symbol TEXT,
    decision TEXT NOT NULL,
    reason TEXT,
    action_status TEXT,
    blocked_by TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gate_decisions_run_symbol
    ON gate_decisions (run_id, symbol);

-- Telegram alert audit. `dedupe_key` is unique and carries the ON CONFLICT target
-- used for duplicate-alert protection.
CREATE TABLE IF NOT EXISTS alert_events (
    id BIGSERIAL PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    alert_type TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    option_ticker TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    sent_at TIMESTAMPTZ,
    telegram_message_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_alert_events_symbol_created
    ON alert_events (symbol, created_at);

-- Scanner run records. `run_id` is the scan id and is unique.
CREATE TABLE IF NOT EXISTS scanner_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    rows_count INTEGER,
    started_at TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    payload JSONB
);
