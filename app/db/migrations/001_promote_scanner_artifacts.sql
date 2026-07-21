-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
-- CSV/JSON/Parquet remain the live artifacts; these tables hold immutable facts.

CREATE TABLE IF NOT EXISTS candidate_snapshot (
 id BIGSERIAL PRIMARY KEY, trading_day DATE, scan_id TEXT NOT NULL, symbol TEXT NOT NULL,
 direction TEXT, setup TEXT, score DOUBLE PRECISION, regime TEXT, entry_readiness DOUBLE PRECISION,
 risk_reward DOUBLE PRECISION, option_quality DOUBLE PRECISION, candidate_rank INTEGER, action TEXT,
 blocked_reason TEXT, realtime_ready BOOLEAN, execution_ready BOOLEAN, candidate_persistence TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_candidate_snapshot_scan_symbol ON candidate_snapshot(scan_id,symbol);

CREATE TABLE IF NOT EXISTS rule_evaluation (
 id BIGSERIAL PRIMARY KEY, scan_id TEXT NOT NULL, symbol TEXT NOT NULL, setup TEXT,
 rule_name TEXT NOT NULL, rule_group TEXT NOT NULL, actual_value TEXT, required_value TEXT,
 passed BOOLEAN NOT NULL, blocked_trade BOOLEAN NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
 timestamp TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_rule_evaluation_blocked ON rule_evaluation(rule_name,blocked_trade);

CREATE TABLE IF NOT EXISTS entry_snapshot (
 trade_id TEXT PRIMARY KEY, scan_id TEXT, trading_day DATE, symbol TEXT, direction TEXT, setup TEXT,
 entered_at TIMESTAMPTZ, payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS exit_snapshot (
 trade_id TEXT PRIMARY KEY REFERENCES entry_snapshot(trade_id), exit_time TIMESTAMPTZ,
 exit_price DOUBLE PRECISION, primary_exit TEXT, payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS trade_timeline_event (
 id BIGSERIAL PRIMARY KEY, trade_id TEXT NOT NULL, event_type TEXT NOT NULL,
 occurred_at TIMESTAMPTZ, payload JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS idx_trade_timeline_trade_time ON trade_timeline_event(trade_id,occurred_at);

CREATE TABLE IF NOT EXISTS candidate_outcome (
 candidate_id TEXT PRIMARY KEY, entered BOOLEAN, profitable BOOLEAN, trend_developed BOOLEAN,
 target_hit BOOLEAN, stop_hit BOOLEAN, became_winner BOOLEAN, became_loser BOOLEAN,
 became_neutral BOOLEAN, payload JSONB NOT NULL DEFAULT '{}'::jsonb,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
