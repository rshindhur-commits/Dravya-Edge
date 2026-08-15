-- Per-trade diagnostics, computed once a night and kept.
--
-- On 2026-08-15 four trades were reviewed by hand from chart screenshots. It
-- took an hour and produced nothing the app did not already hold: where each
-- entry sat in its range, which way price was drifting when it fired, how much
-- of the peak was given back, and what holding to the close would have paid.
--
-- The cost of not storing it is not the hour. It is that every question about
-- the book has been answered by re-deriving these numbers from bars, one script
-- at a time, and each derivation has been a fresh chance to get it wrong -- a
-- lookahead range, a mislabelled timezone, a stop tested after the low instead
-- of before it. Three claims were withdrawn in two days for exactly that.
--
-- With this table the answer to "does entry placement predict outcome" is a
-- GROUP BY over a column, not another afternoon of bar fetching.
--
-- `held_r` is scored honestly: a trade whose stop would have been reached first
-- is -1.00, whatever the session did afterwards. Without that rule the
-- counterfactual quietly assumes the position survived to collect a move it was
-- stopped out before reaching, which is how holding came to look free.

CREATE TABLE IF NOT EXISTS trade_review (
    trade_key            TEXT PRIMARY KEY,
    trading_day          DATE NOT NULL,
    symbol               TEXT NOT NULL,
    direction            TEXT,
    setup                TEXT,
    opened_at            TIMESTAMPTZ,
    closed_at            TIMESTAMPTZ,
    exit_reason          TEXT,

    -- what was booked
    r_multiple           DOUBLE PRECISION,
    cash                 DOUBLE PRECISION,   -- fills: bought the ask, sold the bid
    cash_pct             DOUBLE PRECISION,

    -- where the entry sat. 100 = the best end of the session's range for this
    -- direction (a put sold at the high, a call bought at the low), 0 = the worst.
    placement_pct        DOUBLE PRECISION,
    -- price change over the 45 minutes before entry, and whether the trade went
    -- with it or against it
    drift                DOUBLE PRECISION,
    traded_with_drift    BOOLEAN,

    -- excursion while the position was open, against the risk taken
    mfe_r                DOUBLE PRECISION,
    mae_r                DOUBLE PRECISION,
    giveback_r           DOUBLE PRECISION,

    -- the counterfactual
    held_r               DOUBLE PRECISION,
    stop_would_hit       BOOLEAN,
    target_would_hit     BOOLEAN,

    -- the target's distance as a share of the whole day's range. Above 100 the
    -- trade could not have reached it unless the stock spent its entire daily
    -- movement in one direction from the entry.
    target_vs_day_range  DOUBLE PRECISION,
    risk_pct             DOUBLE PRECISION,

    payload              JSONB,
    reviewed_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trade_review_day_idx
    ON trade_review (trading_day);

CREATE INDEX IF NOT EXISTS trade_review_setup_idx
    ON trade_review (setup);

CREATE INDEX IF NOT EXISTS trade_review_placement_idx
    ON trade_review (placement_pct);
