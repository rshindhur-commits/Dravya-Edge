-- What the option would have returned on candidates that were never traded.
--
-- `tools/replay_option_leg.py` reconstructs the chain, buys at the decision and
-- sells at the resolution, and had nowhere to put the answer. The nightly job
-- printed a summary to the worker log and wrote a marker file on Render's local
-- disk, which `DB_STORAGE_AND_BACKUP.md` records as wiped on every deploy. So it
-- spent the Polygon quota each night and kept nothing.
--
-- This matters more than the usual diagnostic table. The underlying replay says
-- whether a refused candidate reached its target; only this says whether buying
-- the option would have made money, and on 2026-08-10 the one candidate that did
-- reach its target returned -4.00% on the contract once the spread was paid at
-- both ends. Until this exists the toll stays a single constant -- 0.321R, fitted
-- once across 291 trades -- and every conclusion that leans on it is leaning on
-- one number.

CREATE TABLE IF NOT EXISTS option_leg_replay (
    candidate_id       TEXT PRIMARY KEY,
    trading_day        DATE NOT NULL,
    symbol             TEXT NOT NULL,
    direction          TEXT,
    setup              TEXT,
    verdict            TEXT,               -- TARGET_FIRST | STOP_FIRST
    option_ticker      TEXT,
    -- Fills, not mids: buying lifts the ask and selling hits the bid. Pricing a
    -- round trip off mids is the easiest way to make an options backtest read
    -- better than the account ever will.
    entry_fill         DOUBLE PRECISION,
    exit_fill          DOUBLE PRECISION,
    option_return_pct  DOUBLE PRECISION,
    entry_spread_pct   DOUBLE PRECISION,
    contract_cost      DOUBLE PRECISION,
    underlying_rr      DOUBLE PRECISION,
    resolved_at        TIMESTAMPTZ,
    payload            JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS option_leg_replay_day_idx
    ON option_leg_replay (trading_day);

CREATE INDEX IF NOT EXISTS option_leg_replay_verdict_idx
    ON option_leg_replay (verdict);
