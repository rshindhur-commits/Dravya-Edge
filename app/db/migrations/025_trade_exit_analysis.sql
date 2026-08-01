-- Per-trade exit analysis, promoted from `trend_capture_analysis.csv`.
--
-- Apply manually through DATABASE_DIRECT_URL. No runtime schema DDL.
--
-- Why: the indicator state at exit -- ema9, vwap, macd, rsi, atr, bars_held --
-- and the analysis built on it -- Trend Capture %, Left On Table, Exit Quality,
-- Exit Verdict -- existed only in a CSV on the Streamlit Cloud container's
-- ephemeral filesystem. A redeploy wiped 2026-07-31's copy, and nothing in
-- Postgres held it: `candidate_evidence.trend_capture` is candidate-grain, and
-- `exit_quality_metrics` stores only a daily aggregate (which was itself all
-- nulls that day). These are the numbers that answer "was this exit right", so
-- losing them loses the post-market review entirely.
--
-- Grain: one row per completed trade. `trade_key` already embeds symbol, option
-- and open timestamp, so it is the natural key; `trading_day` is carried
-- alongside it for the per-day reads the review does.
--
-- Typed columns are the ones the report and future queries filter or sort on.
-- `payload` keeps the full row so nothing is lost to schema drift -- the CSV has
-- grown columns twice already, and a re-run should not need a migration.

CREATE TABLE IF NOT EXISTS trade_exit_analysis (
    trading_day DATE NOT NULL,
    trade_key TEXT NOT NULL,
    session_id TEXT,
    symbol TEXT,
    direction TEXT,
    setup TEXT,
    market_regime TEXT,

    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    entry_price DOUBLE PRECISION,
    exit_price DOUBLE PRECISION,
    bars_held INTEGER,

    -- What the trade was worth versus what it took.
    trend_capture_pct DOUBLE PRECISION,
    available_move DOUBLE PRECISION,
    captured_move DOUBLE PRECISION,
    left_on_table DOUBLE PRECISION,
    mfe DOUBLE PRECISION,
    mae DOUBLE PRECISION,
    risk_reward DOUBLE PRECISION,
    peak_price DOUBLE PRECISION,
    peak_time TIMESTAMPTZ,

    -- Indicator state at the moment of exit.
    ema9 DOUBLE PRECISION,
    ema20 DOUBLE PRECISION,
    vwap DOUBLE PRECISION,
    macd DOUBLE PRECISION,
    macd_signal DOUBLE PRECISION,
    macd_histogram DOUBLE PRECISION,
    rsi DOUBLE PRECISION,
    atr DOUBLE PRECISION,
    relative_volume DOUBLE PRECISION,

    -- The judgement.
    trend_health_score DOUBLE PRECISION,
    trend_health_state TEXT,
    exit_reason TEXT,
    primary_exit TEXT,
    exit_quality TEXT,
    exit_verdict TEXT,
    exit_comments TEXT,
    trend_continued BOOLEAN,
    remaining_move DOUBLE PRECISION,

    payload JSONB,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (trading_day, trade_key)
);

CREATE INDEX IF NOT EXISTS trade_exit_analysis_day_idx
    ON trade_exit_analysis (trading_day);

CREATE INDEX IF NOT EXISTS trade_exit_analysis_symbol_idx
    ON trade_exit_analysis (symbol, trading_day);
