-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
CREATE TABLE IF NOT EXISTS analytics_summary (
    summary_type TEXT NOT NULL, dimension TEXT NOT NULL, dimension_value TEXT NOT NULL,
    sample_size INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0,
    average_r DOUBLE PRECISION, profit_factor DOUBLE PRECISION, payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (summary_type, dimension, dimension_value)
);
CREATE TABLE IF NOT EXISTS promotion_review (
    id BIGSERIAL PRIMARY KEY, feature_name TEXT NOT NULL, version TEXT NOT NULL DEFAULT 'v2',
    status TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT, reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);