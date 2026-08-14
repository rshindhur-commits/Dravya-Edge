"""Daily bars must not be judged by an intraday staleness clock.

`get_polygon_data` blocked any series whose newest bar was older than 20-25
minutes during a live session. A daily bar is stamped 00:00 of its own day, so
today's bar is already 1,193 minutes "late" by 19:53 ET and could never clear
that threshold.

The failure was silent and total. On 2026-08-13 every daily fetch returned an
empty frame, so `daily_context` fell through to `NO_DAILY_DATA`: `Daily Trend`
read UNKNOWN on 2,485 of 2,499 scanner rows, 22 of 26 symbols never had it on a
single scan, and the entry gate's counter-trend penalty -- which raises
min_setup and min_rr when a candidate fights the daily trend -- never applied
once all session.

These cover both halves: daily bars are judged in days, intraday bars keep the
minute clock they were designed for.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from app.indicators import technical_indicators as ti

ET = ZoneInfo("America/New_York")


def _aggs(last_ts, count=90, step_days=1):
    """Polygon-shaped bars ending at `last_ts` (a tz-aware datetime)."""

    rows = []
    for i in range(count):
        ts = last_ts - timedelta(days=step_days * (count - 1 - i))
        rows.append({
            "t": int(ts.timestamp() * 1000),
            "o": 100.0 + i * 0.1,
            "h": 100.5 + i * 0.1,
            "l": 99.5 + i * 0.1,
            "c": 100.2 + i * 0.1,
            "v": 1_000_000,
        })
    return rows


class DailyBarFreshnessTests(unittest.TestCase):

    def test_todays_daily_bar_is_not_stale_during_the_session(self):
        """The regression: stamped 00:00, ~20 hours 'late', must still pass."""

        now = datetime.now(ET).replace(hour=14, minute=30, second=0, microsecond=0)
        midnight = now.replace(hour=0, minute=0)

        with patch.object(ti, "get_aggs_cached", return_value=_aggs(midnight)), \
             patch.object(ti, "USE_MOCK_MARKET_DATA", False):

            df = ti.get_polygon_data("PLTR", 1, "day", 120)

        self.assertFalse(
            df.empty,
            "today's daily bar was rejected by the intraday staleness clock"
        )

    def test_a_genuinely_old_daily_series_is_still_rejected(self):
        """Exempting the minute clock must not mean accepting a dead feed."""

        now = datetime.now(ET).replace(hour=14, minute=30, second=0, microsecond=0)
        ancient = now.replace(hour=0, minute=0) - timedelta(
            days=ti.MAX_DAILY_BAR_AGE_DAYS + 3
        )

        with patch.object(ti, "get_aggs_cached", return_value=_aggs(ancient)), \
             patch.object(ti, "USE_MOCK_MARKET_DATA", False):

            df = ti.get_polygon_data("PLTR", 1, "day", 120)

        self.assertTrue(df.empty, "a stale daily feed should still be blocked")

    def test_a_long_weekend_still_passes(self):
        """Friday's bar read on Monday is normal, not stale."""

        now = datetime.now(ET).replace(hour=14, minute=30, second=0, microsecond=0)
        recent = now.replace(hour=0, minute=0) - timedelta(days=3)

        with patch.object(ti, "get_aggs_cached", return_value=_aggs(recent)), \
             patch.object(ti, "USE_MOCK_MARKET_DATA", False):

            df = ti.get_polygon_data("PLTR", 1, "day", 120)

        self.assertFalse(df.empty, "a 3-day-old daily bar is a normal weekend")


def _frozen(fixed_et):
    """A datetime whose now() is pinned, so session detection is deterministic.

    `get_polygon_data` derives the market session from `datetime.now(...)`
    inline, and the intraday staleness check only runs during PREMARKET,
    REGULAR or AFTERHOURS. Without pinning the clock this test passes or fails
    depending on what time of day it is run.
    """

    class _Frozen(datetime):

        @classmethod
        def now(cls, tz=None):
            return fixed_et if tz is None else fixed_et.astimezone(tz)

    return _Frozen


class IntradayFreshnessUnchangedTests(unittest.TestCase):

    def test_a_stale_intraday_series_is_still_blocked(self):
        """The minute clock is correct for minute bars and must survive."""

        fixed = datetime.now(ET).replace(
            hour=14, minute=30, second=0, microsecond=0
        )
        old = fixed.astimezone(timezone.utc) - timedelta(
            minutes=ti.MAX_DELAY_REGULAR + 90
        )

        with patch.object(ti, "get_aggs_cached",
                          return_value=_aggs(old, count=200, step_days=0)), \
             patch.object(ti, "USE_MOCK_MARKET_DATA", False), \
             patch.object(ti, "datetime", _frozen(fixed)):

            df = ti.get_polygon_data("PLTR", 5, "minute", 5)

        self.assertTrue(
            df.empty,
            "stale 5-minute data must still be refused"
        )

    def test_a_fresh_intraday_series_still_passes(self):
        """The block must be about staleness, not about being intraday."""

        fixed = datetime.now(ET).replace(
            hour=14, minute=30, second=0, microsecond=0
        )
        recent = fixed.astimezone(timezone.utc) - timedelta(minutes=5)

        with patch.object(ti, "get_aggs_cached",
                          return_value=_aggs(recent, count=200, step_days=0)), \
             patch.object(ti, "USE_MOCK_MARKET_DATA", False), \
             patch.object(ti, "datetime", _frozen(fixed)):

            df = ti.get_polygon_data("PLTR", 5, "minute", 5)

        self.assertFalse(df.empty, "fresh 5-minute data must still be accepted")

    def test_the_timespan_split_covers_the_names_polygon_uses(self):

        self.assertIn("minute", ti.INTRADAY_TIMESPANS)
        self.assertIn("hour", ti.INTRADAY_TIMESPANS)
        self.assertNotIn("day", ti.INTRADAY_TIMESPANS)
        self.assertNotIn("week", ti.INTRADAY_TIMESPANS)


if __name__ == "__main__":
    unittest.main()
