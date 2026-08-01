-- Scan engine heartbeat.
--
-- Until now the dashboard learned whether scanning was alive by reading
-- data/live/scanner_run_status.json and runtime_state.json -- files written by
-- the scanner into its own container. That works only while the scanner and the
-- dashboard are the same process, which is exactly the coupling the always-on
-- worker migration removes. Once the scanner runs on Render and the dashboard on
-- Streamlit Cloud, those files are on a filesystem the dashboard cannot see, and
-- the System panel would go blind rather than go wrong -- the worse failure,
-- because a blank panel reads as "nothing to report".
--
-- Keyed on instance_id rather than a single row on purpose. During the cutover
-- both engines can be running, and a table that can only hold one of them would
-- hide precisely the failure that matters: two scanners double-opening
-- positions, which the file-based scan lock cannot prevent across hosts.

CREATE TABLE IF NOT EXISTS scan_engine_heartbeat (
    instance_id       TEXT PRIMARY KEY,
    owner             TEXT,
    hostname          TEXT,
    status            TEXT,
    session           TEXT,
    last_scan_id      TEXT,
    last_scan_at      TIMESTAMPTZ,
    last_duration_sec NUMERIC,
    next_due_at       TIMESTAMPTZ,
    interval_seconds  INTEGER,
    scans             INTEGER DEFAULT 0,
    failures          INTEGER DEFAULT 0,
    last_error        TEXT,
    payload           JSONB,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- "Which engines have reported recently" is the only read this table serves.
CREATE INDEX IF NOT EXISTS idx_scan_engine_heartbeat_updated
    ON scan_engine_heartbeat (updated_at DESC);
