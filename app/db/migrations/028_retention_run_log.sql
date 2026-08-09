-- A durable record of when retention last ran.
--
-- `maybe_run_retention` gates on data/live/retention_state.json, a file on an
-- ephemeral filesystem. app/runtime/retention_scheduler.py argues that this is
-- fine, and for correctness it is: retention is idempotent, so a marker lost to
-- a container restart costs one extra pass of cheap COUNT queries.
--
-- What that argument misses is that it leaves no way to answer "did retention
-- run". On 2026-08-08 the only available check was to query every retained table
-- for rows older than its window and infer the answer from their absence -- the
-- same shape of problem as reading a memory leak off a dashboard graph, and the
-- second instance of it found that day.
--
-- The correctness benefit is real but secondary: while the worker was restarting
-- roughly hourly on the memory leak, the marker reset with it and retention ran
-- on most idle passes rather than once a day. Reading the last run from here
-- instead makes the once-a-day guard survive a restart.

CREATE TABLE IF NOT EXISTS retention_run (
    id BIGSERIAL PRIMARY KEY,
    ran_on DATE NOT NULL,
    ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_deleted INTEGER NOT NULL DEFAULT 0,
    report JSONB
);

-- The scheduler asks only for the most recent run, once per idle pass.
CREATE INDEX IF NOT EXISTS retention_run_ran_on_idx
    ON retention_run (ran_on DESC);
