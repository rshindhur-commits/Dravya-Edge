"""The weekly subscriber scorecard: window, stats, message and once-a-week dedup."""

import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.alerts.telegram_alerts import (
    build_weekly_outcome_summary_message,
    maybe_send_weekly_outcome_summary,
)
from app.analytics.weekly_summary import (
    build_weekly_summary_stats,
    due_weekly_summary_window,
    weekly_summary_window,
)

ET = ZoneInfo("America/New_York")


def _et(year, month, day, hour=12):

    return datetime(year, month, day, hour, tzinfo=ET)


class WeeklyWindowTests(unittest.TestCase):

    def test_window_is_monday_to_friday_of_the_reference_week(self):

        # 2026-07-29 is a Wednesday.
        start, end = weekly_summary_window(date(2026, 7, 29))

        self.assertEqual(start, date(2026, 7, 27))
        self.assertEqual(end, date(2026, 7, 31))
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(end.weekday(), 4)

    def test_weekend_reports_the_week_that_just_ended(self):

        for reference in (date(2026, 8, 1), date(2026, 8, 2)):

            start, end = weekly_summary_window(reference)

            self.assertEqual((start, end), (date(2026, 7, 27), date(2026, 7, 31)))

    def test_due_after_friday_close_and_through_the_weekend(self):

        self.assertIsNotNone(due_weekly_summary_window(_et(2026, 7, 31, 16)))
        self.assertIsNotNone(due_weekly_summary_window(_et(2026, 8, 1, 9)))
        self.assertIsNotNone(due_weekly_summary_window(_et(2026, 8, 2, 20)))

    def test_not_due_midweek_or_before_friday_close(self):

        self.assertIsNone(due_weekly_summary_window(_et(2026, 7, 29, 12)))
        self.assertIsNone(due_weekly_summary_window(_et(2026, 7, 31, 15)))
        self.assertIsNone(due_weekly_summary_window(_et(2026, 8, 3, 9)))


class WeeklyStatsTests(unittest.TestCase):

    def test_index_validation_trades_are_excluded_from_the_record(self):
        """`include_in_strategy_stats` is False for review-validation entries.
        Counting them flattered the blended record; the weekly number must not
        repeat that."""

        trades = [
            {"r_multiple": -1.0, "include_in_strategy_stats": True},
            {"r_multiple": -0.5, "include_in_strategy_stats": True},
            {"r_multiple": 2.0, "include_in_strategy_stats": False},
        ]

        stats = build_weekly_summary_stats(trades)

        self.assertEqual(stats["completed_trades"], 2)
        self.assertEqual(stats["wins"], 0)

    def test_absent_flag_counts_as_a_strategy_trade(self):

        stats = build_weekly_summary_stats([{"r_multiple": 1.0}])

        self.assertEqual(stats["completed_trades"], 1)

    def test_small_samples_are_flagged_as_not_meaningful(self):

        self.assertFalse(build_weekly_summary_stats([{"r_multiple": 1.0}])["meaningful_sample"])
        self.assertTrue(
            build_weekly_summary_stats(
                [{"r_multiple": 0.1} for _ in range(30)]
            )["meaningful_sample"]
        )

    def test_no_trades_produces_an_empty_but_valid_stat_block(self):

        stats = build_weekly_summary_stats([])

        self.assertEqual(stats["completed_trades"], 0)
        self.assertFalse(stats["meaningful_sample"])


class WeeklyMessageTests(unittest.TestCase):

    def test_message_reports_r_and_premium_and_the_sample_caveat(self):

        stats = {
            "completed_trades": 7,
            "wins": 3,
            "losses": 4,
            "win_rate": 42.9,
            "average_r": -0.09,
            "total_r": -0.66,
            "profit_factor": 0.81,
            "max_drawdown_r": 2.0,
            "priced_trades": 3,
            "net_win_rate": 0.0,
            "average_option_pnl_pct": -4.45,
            "average_spread_cost_pct": 7.95,
            "meaningful_sample": False,
        }

        message = build_weekly_outcome_summary_message(
            stats, date(2026, 7, 27), date(2026, 7, 31)
        )

        self.assertIn("WEEKLY RESULTS", message)
        self.assertIn("27 Jul", message)
        self.assertIn("Closed: 7", message)
        self.assertIn("Won 3", message)
        self.assertIn("-0.09R", message)
        self.assertIn("0.81", message)
        # Premium is the honest instrument and must not be omitted when a losing
        # week looks better in R than it does after costs.
        self.assertIn("IN PREMIUM", message)
        self.assertIn("Win rate after costs: 0%", message)
        self.assertIn("-4.5%", message)
        self.assertIn("not a statistically meaningful sample", message)

    def test_meaningful_sample_drops_the_caveat(self):

        stats = {
            "completed_trades": 40,
            "wins": 20,
            "losses": 20,
            "win_rate": 50.0,
            "average_r": 0.2,
            "total_r": 8.0,
            "profit_factor": 1.4,
            "meaningful_sample": True,
        }

        message = build_weekly_outcome_summary_message(
            stats, date(2026, 7, 27), date(2026, 7, 31)
        )

        self.assertNotIn("not a statistically meaningful sample", message)

    def test_quiet_week_still_reports(self):
        """Silence is ambiguous -- a subscriber cannot tell 'no setups' from
        'the scanner was down'. A zero-trade week says so explicitly."""

        message = build_weekly_outcome_summary_message(
            {"completed_trades": 0}, date(2026, 7, 27), date(2026, 7, 31)
        )

        self.assertIn("No positions were closed this week", message)
        self.assertNotIn("IN R", message)


class WeeklyDispatchTests(unittest.TestCase):

    def _env(self, **extra):

        env = {
            "TELEGRAM_ALERTS_ENABLED": "1",
            "TELEGRAM_WEEKLY_SUMMARY_ENABLED": "1",
        }
        env.update(extra)

        return env

    def test_summary_is_sent_once_per_iso_week(self):

        stats = {"completed_trades": 3, "wins": 1, "losses": 2}

        with patch.dict("os.environ", self._env(), clear=False), patch(
            "app.alerts.telegram_alerts.alert_was_sent", return_value=True
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert"
        ) as send:

            result = maybe_send_weekly_outcome_summary(
                stats, date(2026, 7, 27), date(2026, 7, 31)
            )

        self.assertEqual(result["reason"], "DUPLICATE_ALERT")
        send.assert_not_called()

    def test_force_overrides_the_dedup(self):

        with patch.dict("os.environ", self._env(), clear=False), patch(
            "app.alerts.telegram_alerts.alert_was_sent", return_value=True
        ), patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert", return_value={"ok": True}
        ) as send:

            maybe_send_weekly_outcome_summary(
                {"completed_trades": 0}, date(2026, 7, 27), date(2026, 7, 31), force=True
            )

        send.assert_called_once()

    def test_disabled_flag_stops_the_send(self):

        with patch.dict(
            "os.environ",
            self._env(TELEGRAM_WEEKLY_SUMMARY_ENABLED="0"),
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert"
        ) as send:

            result = maybe_send_weekly_outcome_summary(
                {"completed_trades": 0}, date(2026, 7, 27), date(2026, 7, 31)
            )

        self.assertEqual(result["reason"], "TELEGRAM_WEEKLY_SUMMARY_DISABLED")
        send.assert_not_called()

    def test_dedup_key_is_stable_across_days_in_the_same_week(self):

        from app.alerts.telegram_alerts import _weekly_summary_alert_key

        self.assertEqual(
            _weekly_summary_alert_key(date(2026, 7, 27)),
            _weekly_summary_alert_key(date(2026, 7, 27)),
        )
        self.assertNotEqual(
            _weekly_summary_alert_key(date(2026, 7, 27)),
            _weekly_summary_alert_key(date(2026, 8, 3)),
        )


if __name__ == "__main__":

    unittest.main()
