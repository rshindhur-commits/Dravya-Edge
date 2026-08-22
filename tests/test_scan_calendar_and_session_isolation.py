"""The scanner's operating calendar, and keeping browsers out of the backend.

`get_market_session()` is clock-only -- at 10:00 on Christmas it still returns
REGULAR -- so weekends and holidays have to be handled by the supervisor. The
weekend guard existed; a midweek holiday still scanned a full day of Polygon
option-chain calls against a market that never opened.

The session-isolation tests cover a subtler failure. Streamlit `session_state`
is per browser session, so a second device opening the dashboard initialised its
sidebar to widget defaults and that render pushed them into process-global
backend state -- changing the scan cadence, and rewriting the auto-paper settings
file, with nobody having clicked anything.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.runtime.scan_supervisor import (
    MARKET_HOLIDAYS_THROUGH,
    _idle_reason,
    is_market_holiday,
)

ET = ZoneInfo("America/New_York")


def _et(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=ET)


def test_labor_day_is_a_holiday():
    assert is_market_holiday(_et("2026-09-07 10:00")) is True


def test_a_normal_monday_is_not():
    assert is_market_holiday(_et("2026-09-14 10:00")) is False


def test_holiday_idles_the_scanner_mid_session():
    """The clock says REGULAR; the calendar has to override it."""

    assert _idle_reason("REGULAR", _et("2026-12-25 11:00")) == "SLEEPING_HOLIDAY"


def test_weekend_still_idles():
    # 2026-08-01 is a Saturday.
    assert _idle_reason("REGULAR", _et("2026-08-01 11:00")) == "SLEEPING_WEEKEND"


def test_an_ordinary_trading_hour_scans():
    assert _idle_reason("REGULAR", _et("2026-08-03 11:00")) is None


def test_observed_holidays_are_listed_not_the_calendar_date():
    """4 July 2026 is a Saturday; the exchange closes on Friday the 3rd."""

    assert is_market_holiday(_et("2026-07-03 11:00")) is True
    assert is_market_holiday(_et("2026-07-04 11:00")) is False


def test_years_beyond_the_table_scan_rather_than_guess():
    """Wasting calls on a holiday beats idling a real trading day."""

    beyond = _et(f"{MARKET_HOLIDAYS_THROUGH + 1}-12-25 11:00")

    assert is_market_holiday(beyond) is False


def test_a_rerun_does_not_restart_a_live_engine():
    """Replaces a test of the `Full Scanner Cadence` control, removed once it
    stopped doing anything: it fed `_ensure_scan_engine_started`, which returns
    early when the worker owns scanning.

    The property that outlives it is the one that test was really protecting --
    an auto-refreshing tab must not reconfigure a running engine on every rerun.
    """

    from unittest.mock import patch

    from app import dashboard

    with patch("app.runtime.scan_supervisor.status",
               return_value={"thread_alive": True, "status": "IDLE"}), \
         patch("app.runtime.scan_supervisor.ensure_started") as ensure_started, \
         patch.dict("os.environ", {"SCAN_ENGINE_OWNER": "dashboard"}, clear=False), \
         patch.object(dashboard, "_prime_scanner_environment"):

        for _ in range(3):
            dashboard._ensure_scan_engine_started()

    ensure_started.assert_not_called()


def test_a_dead_engine_is_restarted():
    """The other half: not touching a live engine must not become not starting
    a dead one. Flipping SCAN_ENGINE_OWNER back to `dashboard` because the
    worker is down is exactly when this has to work."""

    from unittest.mock import patch

    from app import dashboard

    with patch("app.runtime.scan_supervisor.status",
               return_value={"thread_alive": False}), \
         patch("app.runtime.scan_supervisor.ensure_started",
               return_value={"thread_alive": True}) as ensure_started, \
         patch.dict("os.environ", {"SCAN_ENGINE_OWNER": "dashboard"}, clear=False), \
         patch.object(dashboard, "_prime_scanner_environment"):

        dashboard._ensure_scan_engine_started()

    ensure_started.assert_called_once()


def test_the_dashboard_cannot_write_auto_paper_settings():
    """The sidebar must not own settings the scanner reads from another host.

    This previously guarded a narrower bug -- one browser session overwriting
    another's values on every render. The whole file is gone now: with
    SCAN_ENGINE_OWNER=worker the sidebar wrote to the Streamlit container's disk
    while the Render worker read its own, which does not exist, so every control
    was inert and every displayed value a fiction. Env vars are the only thing
    both hosts share.

    A reintroduced writer would restore a UI that reports limits nothing applies.
    """

    from app import dashboard

    for name in (
        "_load_auto_paper_settings",
        "_save_auto_paper_settings",
        "AUTO_PAPER_SETTINGS_FILE",
        "_render_auto_paper_controls",
    ):
        assert not hasattr(dashboard, name), (
            f"{name} is back; auto-paper settings must come from the environment "
            "so the dashboard cannot display a limit the scanner is not applying"
        )


def test_engine_is_restarted_even_when_the_cadence_is_unchanged():
    """Skipping ensure_started must not leave a dead thread down."""

    import inspect

    from app import dashboard

    # Renamed on 2026-08-01 when the duplicated Scan Engine sidebar panel was
    # dropped; the function now only ensures the engine is running, and the
    # System block reports it.
    source = inspect.getsource(dashboard._ensure_scan_engine_started)

    assert 'not engine.get("thread_alive")' in source


def test_daily_cap_comes_from_max_daily_entries():
    """MAX_DAILY_ENTRIES must be the cap that binds.

    A sidebar default of 3 used to defeat this. On a fresh container the settings
    file did not exist, so the widget default became the widget value and was
    written straight back to auto_paper_settings.json; the backend then found the
    key present and never reached its own fallback. MAX_DAILY_ENTRIES=5 was
    enforced as 3, and on 2026-07-31 that bound the instant the third trade
    opened and blocked AMZN five times at RR 2.88.

    Asserted against the loader now rather than against sidebar source, because
    the sidebar no longer has an opinion to defeat it with.
    """

    import os
    from unittest.mock import patch

    from app.runtime.paper_automation_support import load_auto_paper_controls

    with patch.dict(os.environ, {"MAX_DAILY_ENTRIES": "5"}, clear=False):
        assert load_auto_paper_controls()["max_daily"] == 5


def test_candidate_rank_limit_matches_the_daily_entry_cap():
    """Rank must not be the binding constraint.

    At 3 it was the largest single blocker of the first live session -- 79
    events across 11 symbols, ahead of RR -- rejecting AMZN at RR 2.88 and PLTR
    at setup 81 while the three trades taken ran at RR 2.4, 2.75 and 2.26 for
    -0.47R. Throttling belongs to the limits that exist to manage risk.
    """

    from app.runtime.paper_automation_support import AUTO_PAPER_MAX_CANDIDATE_RANK

    assert AUTO_PAPER_MAX_CANDIDATE_RANK == 5


def test_the_worker_honours_the_calendar_without_a_flag():
    """The guard has to be on by default, not opted into.

    Render's start command is a bare `python -m app.runtime.scan_loop`, and
    skip_closed defaulted to False, so the worker scanned Saturday 2026-08-01
    while the dashboard supervisor -- which defaults it True -- slept. A guard
    you must remember to switch on is a guard that is off in production.
    """
    import inspect

    from app.runtime.scan_loop import run_scan_loop

    assert inspect.signature(run_scan_loop).parameters["skip_closed"].default is True


def test_both_engines_share_one_calendar():
    """The supervisor's guards were invisible to the worker until they moved to
    a neutral module. Re-exported names keep existing callers working."""

    from app.runtime import market_calendar, scan_loop, scan_supervisor

    assert scan_supervisor._idle_reason is market_calendar.idle_reason
    assert scan_loop.idle_reason is market_calendar.idle_reason


# ---------------------------------------------------------------------------
# Premarket, which is the after-close tail at the other end of the day.
#
# Measured 2026-08-10..21 over 155 premarket scans: 0 of 3,859 rows passed the
# Decision stage and `auto_paper_decision` recorded 12 SKIPPED and nothing else.
# The cost is not the scanning -- 7.4s a scan -- but the 300s Neon autosuspend
# timer each of the 14 daily wakes buys.
# ---------------------------------------------------------------------------


def test_early_premarket_idles():
    assert _idle_reason("PREMARKET", _et("2026-08-19 04:05")) == "SLEEPING_PREMARKET"
    assert _idle_reason("PREMARKET", _et("2026-08-19 08:59")) == "SLEEPING_PREMARKET"


def test_premarket_resumes_before_the_open():
    assert _idle_reason("PREMARKET", _et("2026-08-19 09:00")) is None
    assert _idle_reason("PREMARKET", _et("2026-08-19 09:29")) is None


def test_the_guard_is_premarket_only():
    """OPENING_RANGE and REGULAR are untouched, whatever the clock says."""

    assert _idle_reason("OPENING_RANGE", _et("2026-08-19 09:31")) is None
    assert _idle_reason("REGULAR", _et("2026-08-19 10:00")) is None


def test_scanning_resumes_before_any_entry_could_be_taken():
    """The falsifying check: this may not cost a single entry.

    Trades cannot open before 09:45, so a resume time at or after it would mean
    the saving came out of the trading day. Pinned against the constant the
    entry gate actually reads rather than a repeated literal.
    """

    from app.runtime.market_calendar import premarket_scan_from_minute
    from app.runtime.paper_automation_support import AUTO_PAPER_ENTRY_START

    entry_minute = AUTO_PAPER_ENTRY_START.hour * 60 + AUTO_PAPER_ENTRY_START.minute

    assert premarket_scan_from_minute() < entry_minute


def test_the_old_behaviour_is_one_env_var_away(monkeypatch):
    monkeypatch.setenv("SCAN_PREMARKET_FROM", "04:00")

    assert _idle_reason("PREMARKET", _et("2026-08-19 04:05")) is None
    assert _idle_reason("PREMARKET", _et("2026-08-19 08:00")) is None


def test_a_bad_or_out_of_range_value_falls_back_to_the_default(monkeypatch):
    from app.runtime.market_calendar import (
        DEFAULT_PREMARKET_SCAN_FROM,
        premarket_scan_from_minute,
    )

    hour, _, minute = DEFAULT_PREMARKET_SCAN_FROM.partition(":")
    default = int(hour) * 60 + int(minute)

    for bad in ("half nine", "09:xx", "99:00", "-1:00"):
        monkeypatch.setenv("SCAN_PREMARKET_FROM", bad)
        assert premarket_scan_from_minute() == default, bad


def test_premarket_scans_are_still_taken_before_the_bell():
    """Not zero, deliberately. The pre-open watchlist survives.

    A resume at 09:00 with PREMARKET's 1800s cadence leaves room for a scan
    before OPENING_RANGE takes over at 09:30.
    """

    from app.runtime.market_calendar import premarket_scan_from_minute
    from app.runtime.scan_loop import SESSION_INTERVALS

    remaining = (9 * 60 + 30) - premarket_scan_from_minute()

    assert remaining * 60 >= SESSION_INTERVALS["PREMARKET"]

