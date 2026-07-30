-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
CREATE TABLE IF NOT EXISTS recommendation_fact (
    recommendation_id TEXT PRIMARY KEY,
    trading_day DATE NOT NULL,
    scan_id TEXT NOT NULL,
    recommended_at TIMESTAMPTZ,
    symbol TEXT NOT NULL,
    direction TEXT,
    setup TEXT,
    candidate_rank DOUBLE PRECISION,
    top_candidate TEXT,
    entry_price DOUBLE PRECISION,
    option_ticker TEXT,
    option_entry_mid DOUBLE PRECISION,
    scanner_recommendation TEXT NOT NULL,
    execution_eligibility TEXT,
    execution_outcome TEXT,
    execution_reason TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_recommendation_fact_day_rank
    ON recommendation_fact (trading_day, candidate_rank, symbol);

CREATE TABLE IF NOT EXISTS recommendation_horizon_outcome (
    recommendation_id TEXT NOT NULL REFERENCES recommendation_fact(recommendation_id),
    horizon_sessions INTEGER NOT NULL,
    evaluation_trading_day DATE NOT NULL,
    evaluated_at TIMESTAMPTZ,
    symbol TEXT NOT NULL,
    direction TEXT,
    entry_price DOUBLE PRECISION,
    evaluation_price DOUBLE PRECISION,
    underlying_return_pct DOUBLE PRECISION,
    directional_return_pct DOUBLE PRECISION,
    option_return_pct DOUBLE PRECISION,
    option_outcome_status TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (recommendation_id, horizon_sessions)
);