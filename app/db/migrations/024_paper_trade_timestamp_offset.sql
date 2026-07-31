-- Correct paper_trades.opened_at / closed_at, which were written four hours early.
--
-- `_timestamp_for_key` produces a naive ET wall-clock string ("2026-07-31
-- 12:57:59"). Written into a `timestamptz` column, Postgres read it as UTC, so
-- a trade opened at 14:58:46 UTC was stored as 10:58:46+00:00. `created_at` on
-- the same row is correct, which is how the offset was spotted.
--
-- The conversion is DST-safe rather than a blunt +4 hours: rows span EDT today
-- but this table will outlive the changeover, and a fixed offset would corrupt
-- any EST row.
--
--   AT TIME ZONE 'UTC'              -- timestamptz -> the naive wall-clock we wrote
--   AT TIME ZONE 'America/New_York' -- naive ET     -> the instant it meant
--
-- Both columns move in one statement, under one condition evaluated before
-- either is touched. Correcting them separately does not work: once opened_at
-- is shifted forward, any guard comparing closed_at against it stops holding.
--
-- Idempotent by construction. The condition is opened_at being implausibly
-- earlier than created_at, which cannot be true of a correctly written row --
-- a trade is created at, or just after, the moment it is opened. Re-running
-- after a successful pass matches nothing.

BEGIN;

UPDATE paper_trades
   SET opened_at = (opened_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York',
       closed_at = CASE
                     WHEN closed_at IS NOT NULL
                     THEN (closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York'
                   END
 WHERE opened_at IS NOT NULL
   AND created_at IS NOT NULL
   AND opened_at < created_at - INTERVAL '2 hours';

COMMIT;
