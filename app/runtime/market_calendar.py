"""When the exchange is shut, for whichever engine is asking.

This lived in `scan_supervisor` while that module was the only thing scheduling
scans. Moving scanning to the Render worker made `scan_loop` a second scheduler,
and it had none of this -- so on Saturday 2026-08-01 the worker scanned all day
against stale weekend data while the dashboard supervisor correctly slept.

A neutral module rather than an import between the two engines: the supervisor
already imports `scan_loop`, so putting it in either one makes the dependency
circular or arbitrary.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")

DEFAULT_AFTER_CLOSE_TAIL_MINUTES = 20.0

# ET clock time before which the premarket session is not scanned, as "HH:MM".
# PREMARKET runs 04:00-09:30, so this skips 04:00-09:00.
DEFAULT_PREMARKET_SCAN_FROM = "09:00"


def after_close_tail_minutes():
    """Minutes past 16:00 ET to keep scanning. Env-tunable; large values restore
    the previous behaviour of scanning until AFTERHOURS ends at 20:00."""

    raw = os.getenv("SCAN_AFTER_CLOSE_MINUTES", "").strip()

    if not raw:
        return DEFAULT_AFTER_CLOSE_TAIL_MINUTES

    try:
        return float(raw)
    except ValueError:
        print(f"[MARKET CALENDAR WARNING] bad SCAN_AFTER_CLOSE_MINUTES={raw!r}; using default.")
        return DEFAULT_AFTER_CLOSE_TAIL_MINUTES


def premarket_scan_from_minute():
    """Minutes past ET midnight before which premarket scanning is skipped.

    Env-tunable through `SCAN_PREMARKET_FROM`; setting it to "04:00" restores the
    previous behaviour of scanning the whole premarket session.
    """

    raw = os.getenv("SCAN_PREMARKET_FROM", "").strip() or DEFAULT_PREMARKET_SCAN_FROM

    try:
        hour, _, minute = raw.partition(":")
        parsed = int(hour) * 60 + int(minute or 0)
    except ValueError:
        print(
            f"[MARKET CALENDAR WARNING] bad SCAN_PREMARKET_FROM={raw!r}; using default."
        )
        hour, _, minute = DEFAULT_PREMARKET_SCAN_FROM.partition(":")
        return int(hour) * 60 + int(minute)

    if not 0 <= parsed <= 24 * 60:
        print(
            f"[MARKET CALENDAR WARNING] SCAN_PREMARKET_FROM={raw!r} is out of range; "
            "using default."
        )
        hour, _, minute = DEFAULT_PREMARKET_SCAN_FROM.partition(":")
        return int(hour) * 60 + int(minute)

    return parsed


def minutes_past_close(now):
    """Signed minutes relative to the 16:00 ET bell. Negative before the close."""

    close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return (now - close).total_seconds() / 60.0


# NYSE/Nasdaq full closures. Half days (the 13:00 ET closes around Thanksgiving,
# Christmas Eve and 3 July) are deliberately absent: the after-close tail already
# stops scanning once `_minutes_past_close` runs out, and it measures from 16:00,
# so a half day simply scans a few idle cycles rather than doing anything wrong.
#
# A literal table rather than a calculated calendar. Good Friday moves with
# Easter and Juneteenth is recent enough that libraries disagree about it, so a
# list that is obviously auditable beats arithmetic nobody will re-derive. It
# needs extending each year; `MARKET_HOLIDAYS_THROUGH` makes running past the
# end of the table loud instead of silent.
MARKET_HOLIDAYS_THROUGH = 2027

MARKET_HOLIDAYS = frozenset({
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Washington's Birthday
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed, 4 July falls on a Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
    # 2027
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-03-26",
    "2027-05-31",
    "2027-06-18",  # Juneteenth observed, 19 June falls on a Saturday
    "2027-07-05",  # Independence Day observed, 4 July falls on a Sunday
    "2027-09-06",
    "2027-11-25",
    "2027-12-24",  # Christmas observed, 25 December falls on a Saturday
})


def is_market_holiday(now):
    """Whether the exchange is shut for the day.

    `get_market_session()` is clock-only: at 10:00 on Christmas it still returns
    REGULAR, and the weekday guard alone does not catch a holiday that falls
    midweek. Without this the scanner burns a full day of Polygon option-chain
    calls and writes a day of candidate rows against a market that never opened.

    Unknown years return False -- scanning a holiday wastes calls but breaks
    nothing, whereas guessing a calendar could idle a real trading day.
    """

    if now.year > MARKET_HOLIDAYS_THROUGH:

        print(
            f"[MARKET CALENDAR WARNING] no holiday calendar beyond "
            f"{MARKET_HOLIDAYS_THROUGH}; {now.year} holidays will scan. "
            f"Extend MARKET_HOLIDAYS in app/runtime/market_calendar.py."
        )
        return False

    return now.strftime("%Y-%m-%d") in MARKET_HOLIDAYS


def idle_reason(session, now):
    """Why this cycle should not scan, or None to scan.

    Weekends are skipped because `get_market_session()` is a clock-only function:
    at 10:00 on a Saturday it still returns REGULAR, which would otherwise scan
    every 5 minutes all weekend. Exchange holidays are not modelled anywhere in
    the codebase, so a holiday still scans.

    Scanning also stops shortly after the close. On 2026-07-30, 9 of the day's 26
    scans ran between 16:01 and 18:01 -- 35% of the day's compute and Polygon
    option-chain calls -- and every decision in all nine was "outside auto-entry
    window". They could not open a trade by construction.

    The tail is not zero because the last scan of the day writes the closing
    archive. What guarantees one scan lands after the bell is that the tail is
    wider than the **REGULAR** interval (300s), not the AFTERHOURS one: the
    successor to the final pre-close scan is scheduled while the session is still
    REGULAR, so a scan at 15:59 puts one at ~16:04 whatever AFTERHOURS is set to.
    Stated against REGULAR because AFTERHOURS was widened to 1800s for the
    database bill and would otherwise appear to have broken this.

    Intraday force-close is unaffected -- AUTO_PAPER_EOD_CLOSE fires at 15:55,
    before the bell, so it never depended on a post-close scan.

    Premarket is the same case at the other end of the day. `evaluate_action`
    returns PREMARKET_WATCH unconditionally for every candidate the premarket
    session produces, and the entry window does not open until 09:45, so nothing
    scanned before it can open a trade by construction either. Measured over
    2026-08-10..21, 155 premarket scans: **0 of 3,859 rows passed the Decision
    stage**, 0 passed Telegram, 1 of 50 passed Option, and `auto_paper_decision`
    recorded 12 SKIPPED and nothing else. The 2026-08-11 measurement behind
    PREMARKET's 600s -> 1800s cadence change said the same thing.

    The premarket gap does not depend on these scans. `_calculate_premarket_gap_pct`
    reads the current day's first Open against the previous day's last Close out
    of the scan's own 5-minute frame, so a 10:00 scan computes the identical
    value -- there is no premarket scan history to lose.

    What this costs is wakes, not work. Those 14 daily scans average 7.4 seconds
    each and 1.8 minutes of scanning in total, but each one buys a full 300s Neon
    autosuspend timer: 1.20 compute-hours a day, ~10% of the bill, of which 97%
    is the timer. Skipping them leaves the endpoint asleep through the window.

    Deliberately not zero, for the same reason the after-close tail is not zero:
    the default resumes at 09:00, which keeps a pre-open scan and a fresh
    watchlist before the bell. `SCAN_PREMARKET_FROM=04:00` restores the old
    behaviour exactly.
    """

    if now.weekday() >= 5:
        return "SLEEPING_WEEKEND"

    if is_market_holiday(now):
        return "SLEEPING_HOLIDAY"

    if str(session).upper() == "CLOSED":
        return "SLEEPING_CLOSED"

    if minutes_past_close(now) > after_close_tail_minutes():
        return "SLEEPING_AFTER_CLOSE"

    if (
        str(session).upper() == "PREMARKET"
        and now.hour * 60 + now.minute < premarket_scan_from_minute()
    ):
        return "SLEEPING_PREMARKET"

    return None
