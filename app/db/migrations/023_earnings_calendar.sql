-- Apply manually through DATABASE_DIRECT_URL. No scan executes DDL.
--
-- Upcoming earnings dates, cached from Alpha Vantage EARNINGS_CALENDAR.
--
-- Needed in Postgres rather than on disk because the Streamlit Cloud filesystem is
-- wiped on every container recycle, and a container that restarts mid-session
-- would otherwise scan with no blackout at all until the next refresh -- the exact
-- window in which an earnings trade gets opened.
--
-- The whole calendar is replaced on each successful refresh rather than merged:
-- Alpha Vantage returns the complete upcoming window every time, and a merge would
-- keep dates that have since been rescheduled, which is worse than briefly having
-- none. The replace runs in one transaction so a reader never sees an empty table.

CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol TEXT NOT NULL,
    report_date DATE NOT NULL,
    source TEXT NOT NULL DEFAULT 'ALPHAVANTAGE',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, report_date)
);

-- "What is coming up for this symbol" is the only read the blocker performs.
CREATE INDEX IF NOT EXISTS idx_earnings_calendar_symbol_date
    ON earnings_calendar (symbol, report_date);

-- Sweeping past dates, and answering how stale the cached calendar is.
CREATE INDEX IF NOT EXISTS idx_earnings_calendar_date
    ON earnings_calendar (report_date);
