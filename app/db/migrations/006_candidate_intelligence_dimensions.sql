ALTER TABLE candidate_evidence
    ADD COLUMN IF NOT EXISTS option_quality DOUBLE PRECISION;
ALTER TABLE candidate_evidence
    ADD COLUMN IF NOT EXISTS trend_health TEXT;