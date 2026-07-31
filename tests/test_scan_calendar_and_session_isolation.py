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


def test_cadence_control_adopts_engine_state_rather_than_its_default():
    """A fresh browser session must not reset the running cadence.

    The label seeded into a new session has to reflect what the engine is
    actually doing, so opening the dashboard on a second device is a read.
    """

    from app import dashboard

    assert dashboard._cadence_label_for_engine() == next(
        iter(dashboard.SCANNER_CADENCE_INTERVALS)
    ), "with no override running, a new session must adopt the session-aware option"


def test_auto_paper_settings_are_written_only_on_change():
    """Rendering must not rewrite the settings file with this session's values."""

    import inspect

    from app import dashboard

    source = inspect.getsource(dashboard._render_auto_paper_controls)

    assert "if persisted != _load_auto_paper_settings():" in source, (
        "an unconditional save lets the most recently rendered browser session "
        "overwrite settings another session changed"
    )


def test_engine_is_restarted_even_when_the_cadence_is_unchanged():
    """Skipping ensure_started must not leave a dead thread down."""

    import inspect

    from app import dashboard

    source = inspect.getsource(dashboard._render_scan_engine_status)

    assert 'not engine.get("thread_alive")' in source


def test_daily_cap_default_defers_to_max_daily_entries():
    """The sidebar default must not defeat the backend's own fallback.

    A bare 3 here was not merely a different default. On a fresh container the
    settings file does not exist, so this value became the widget value, and the
    widget value is written straight back to auto_paper_settings.json. The
    backend then found the key present and never reached its own
    `or env_int("MAX_DAILY_ENTRIES", 3)`, so MAX_DAILY_ENTRIES=5 was enforced
    as 3. On 2026-07-31 that bound the instant the third trade opened and
    blocked AMZN five times at RR 2.88.
    """

    import inspect

    from app import dashboard

    source = inspect.getsource(dashboard._auto_refresh_defaults)

    assert 'env_int("MAX_DAILY_ENTRIES", 3)' in source, (
        "the sidebar default must defer to MAX_DAILY_ENTRIES, as "
        "load_auto_paper_controls does"
    )
