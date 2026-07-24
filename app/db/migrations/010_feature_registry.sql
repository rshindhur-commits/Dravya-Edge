-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
CREATE TABLE IF NOT EXISTS feature_registry (
    feature_name TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    introduced_date DATE,
    promoted_date DATE,
    retired_date DATE,
    sample_size INTEGER NOT NULL DEFAULT 0,
    owner TEXT,
    description TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (feature_name, version)
);
CREATE TABLE IF NOT EXISTS feature_statistics (
    feature_name TEXT NOT NULL,
    version TEXT NOT NULL,
    sample_size INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    average_r DOUBLE PRECISION,
    improvement_pct DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    promotion_ready BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (feature_name, version)
);