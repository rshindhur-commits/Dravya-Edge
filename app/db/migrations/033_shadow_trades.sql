-- The V2 shadow engine's positions, so a restart cannot erase them.
--
-- The shadow exists to A/B the V2 entry and exit engines against the live ones.
-- It has recorded **2 trades across 23 days** and zero on each of the last 14,
-- so every V1-vs-V2 comparison in `daily_engine_summary` has been empty.
--
-- Shadow entries do fire: 8 of 53 recent paper trades suggested one and 3 were
-- open. The state lived only in `app/state/entry_exit_v2_shadow_state.json`
-- while paper trades are mirrored here by `upsert_paper_trade`. On Render's
-- ephemeral filesystem that file does not survive a restart -- the worker
-- restarted four times on 2026-08-19 alone -- so an open shadow trade could
-- never reach `close_shadow_trade` and no final R was ever written.
--
-- Same shape and same terminal-close rule as `paper_trades`, for the same
-- reason: upserts are queued jobs carrying a snapshot taken when they were
-- queued, and nothing orders them.

CREATE TABLE IF NOT EXISTS shadow_trades (
    trade_key       TEXT        PRIMARY KEY,
    symbol          TEXT        NOT NULL,
    direction       TEXT,
    entry_type      TEXT,
    status          TEXT        NOT NULL,
    trading_day     DATE,
    entry_price     NUMERIC(12, 4),
    stop_loss       NUMERIC(12, 4),
    take_profit     NUMERIC(12, 4),
    close_price     NUMERIC(12, 4),
    r_multiple      NUMERIC(10, 4),
    exit_reason     TEXT,
    payload         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    opened_at       TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Restoring open positions after a restart is the whole point of the table.
CREATE INDEX IF NOT EXISTS idx_shadow_trades_open
    ON shadow_trades (status, symbol);

-- The comparison reads a day at a time.
CREATE INDEX IF NOT EXISTS idx_shadow_trades_day
    ON shadow_trades (trading_day, closed_at);
