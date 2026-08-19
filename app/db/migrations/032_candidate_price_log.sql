-- Sub-scan prices for candidates that have not entered.
--
-- The archive is 5-minute snapshots, so the price movement *inside* a scan gap
-- has never been recorded. That gap is exactly where the open questions live:
-- on 2026-08-18 the best price in the five minutes before an entry was 0.25-0.37R
-- better than the entry taken, and SPCX's whole reversal happened between two
-- scans. Neither can be settled from 5-minute data, however it is sliced -- an
-- A/B on "one scan earlier" split train +/- holdout with opposite signs on 14
-- trades, which is the shape of having no data rather than no effect.
--
-- The position monitor already wakes every 20s. This is where it writes what it
-- sees for symbols that are forming a setup but hold no position. Observational
-- only: nothing reads it to make a decision, and no trading path depends on it.
--
-- Deliberately narrow. One row per symbol per poll, no payload, no indicators --
-- a wide table here would cost more in Neon storage than the question is worth.

CREATE TABLE IF NOT EXISTS candidate_price_log (
    id            BIGSERIAL PRIMARY KEY,
    trading_day   DATE        NOT NULL,
    symbol        TEXT        NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    price         NUMERIC(12, 4) NOT NULL,
    signal        TEXT,
    setup         TEXT,
    direction     TEXT,
    source        TEXT
);

-- The only query this table is built for: one symbol, one day, in time order.
CREATE INDEX IF NOT EXISTS idx_candidate_price_log_symbol_day
    ON candidate_price_log (symbol, trading_day, observed_at);

-- Retention has to reach it or a 20s poll across a handful of symbols will
-- outgrow every other table in the database within a quarter.
CREATE INDEX IF NOT EXISTS idx_candidate_price_log_observed
    ON candidate_price_log (observed_at);
