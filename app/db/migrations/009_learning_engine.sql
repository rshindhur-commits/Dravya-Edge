-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
CREATE TABLE IF NOT EXISTS daily_engine_summary (
    trading_day DATE PRIMARY KEY, v1_trades INTEGER, v2_shadow_trades INTEGER,
    avg_v1_r DOUBLE PRECISION, avg_v2_r DOUBLE PRECISION,
    avg_entry_efficiency DOUBLE PRECISION, avg_trend_capture DOUBLE PRECISION,
    avg_exit_confidence DOUBLE PRECISION, premature_exits INTEGER,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS v2_learning_metrics (
    trading_day DATE NOT NULL, metric TEXT NOT NULL, value DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb, PRIMARY KEY (trading_day, metric)
);
CREATE TABLE IF NOT EXISTS trade_comparison (
    id BIGSERIAL PRIMARY KEY, trading_day DATE, symbol TEXT, direction TEXT,
    v1_r DOUBLE PRECISION, v2_r DOUBLE PRECISION, better_engine TEXT, payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS rule_performance (
    trading_day DATE NOT NULL, rule_name TEXT NOT NULL, blocked_count INTEGER,
    blocked_winner INTEGER, prevented_loss INTEGER, payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (trading_day, rule_name)
);
CREATE TABLE IF NOT EXISTS exit_quality_metrics (
    trading_day DATE PRIMARY KEY, avg_trend_health_at_exit DOUBLE PRECISION,
    premature_exits INTEGER, confirmed_trend_failure_exits INTEGER,
    avg_left_on_table DOUBLE PRECISION, payload JSONB NOT NULL DEFAULT '{}'::jsonb
);