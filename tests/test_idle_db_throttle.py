"""An idle pass should leave the database asleep.

Neon bills compute by how long the endpoint stays awake, and it stays awake for
a full 300s autosuspend timer after any query. The idle branch wrote a heartbeat
and then asked five batch jobs whether they were due -- and every `due()` reads
its marker from Postgres before falling back to a file -- so one idle pass woke
the compute six times over.

Measured 2026-08-21: **12.4 CU-hours a day** against a database whose storage
costs 33 cents a month. Weekdays take ~26 idle passes and a weekend day 24, none
of which scans anything.

Scan cadence is deliberately untouched. `SESSION_INTERVALS` is a trading
decision and the existing note in scan_loop says so; this only changes how often
an idle pass *speaks*.
"""

from datetime import datetime, timedelta

import pytest

from app.runtime import scan_loop


@pytest.fixture(autouse=True)
def _reset():
    scan_loop._last_idle_db_at = None
    scan_loop._last_idle_reason = None
    yield
    scan_loop._last_idle_db_at = None
    scan_loop._last_idle_reason = None


def test_the_first_idle_pass_always_speaks():
    """A worker that just started must publish, or it looks dead."""

    assert scan_loop._idle_db_due("SLEEPING_AFTER_CLOSE", datetime(2026, 8, 21, 20, 0)) is True


def test_the_next_pass_within_the_hour_stays_quiet():
    """The saving. At 1800s cadence this silences every other pass."""

    now = datetime(2026, 8, 21, 20, 0)
    assert scan_loop._idle_db_due("SLEEPING_AFTER_CLOSE", now) is True
    assert scan_loop._idle_db_due("SLEEPING_AFTER_CLOSE", now + timedelta(minutes=30)) is False
    assert scan_loop._idle_db_due("SLEEPING_AFTER_CLOSE", now + timedelta(minutes=59)) is False


def test_it_speaks_again_once_the_interval_has_passed():

    now = datetime(2026, 8, 21, 20, 0)
    scan_loop._idle_db_due("SLEEPING_AFTER_CLOSE", now)
    assert scan_loop._idle_db_due("SLEEPING_AFTER_CLOSE", now + timedelta(hours=1)) is True


def test_a_changed_reason_always_speaks():
    """Entering the weekend, or the after-close window, is exactly the
    transition an operator looks for. Throttling must never hide it."""

    now = datetime(2026, 8, 21, 20, 0)
    assert scan_loop._idle_db_due("SLEEPING_AFTER_CLOSE", now) is True
    assert scan_loop._idle_db_due("SLEEPING_WEEKEND", now + timedelta(minutes=1)) is True


def test_the_interval_is_tunable(monkeypatch):
    """Zero restores the old behaviour exactly, for a bisect."""

    monkeypatch.setenv("SCAN_IDLE_DB_INTERVAL_SECONDS", "0")
    now = datetime(2026, 8, 21, 20, 0)

    assert scan_loop._idle_db_due("SLEEPING_WEEKEND", now) is True
    assert scan_loop._idle_db_due("SLEEPING_WEEKEND", now) is True, "0 means never throttle"


def test_the_batch_jobs_still_get_enough_openings():
    """They are gated to once per ET date and need one opening; at hourly they
    get 8 overnight and 24 on a weekend day."""

    interval = scan_loop.IDLE_DB_INTERVAL_SECONDS
    overnight_hours = 8

    assert overnight_hours * 3600 / interval >= 1, "the nightly batch must get a pass"


def test_scan_cadence_is_untouched():
    """The saving must not come from scanning less. SESSION_INTERVALS is a
    trading decision, and the module's own note says it is not being made here."""

    assert scan_loop.SESSION_INTERVALS == {
        "OPENING_RANGE": 120, "REGULAR": 300,
        "PREMARKET": 1800, "AFTERHOURS": 1800, "CLOSED": 3600,
    }
