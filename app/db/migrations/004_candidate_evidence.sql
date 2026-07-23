CREATE TABLE IF NOT EXISTS candidate_evidence (
    candidate_id TEXT PRIMARY KEY,
    trading_day DATE NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT,
    setup TEXT NOT NULL,
    rr DOUBLE PRECISION,
    setup_score DOUBLE PRECISION,
    regime TEXT,
    top_candidate TEXT,
    quote_freshness TEXT,
    rule_evaluation TEXT,
    decision TEXT,
    suggestion_status TEXT,
    paper_trade_status TEXT,
    replay_outcome TEXT,
    target_first BOOLEAN,
    stop_first BOOLEAN,
    winner BOOLEAN,
    missed_winner BOOLEAN,
    trend_capture DOUBLE PRECISION,
    tes DOUBLE PRECISION,
    engineering_root_cause TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_candidate_evidence_day_setup ON candidate_evidence (trading_day, setup);
CREATE INDEX IF NOT EXISTS idx_candidate_evidence_outcomes ON candidate_evidence (winner, missed_winner, quote_freshness);