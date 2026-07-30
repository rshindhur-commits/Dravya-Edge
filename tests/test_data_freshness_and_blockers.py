"""Freshness must not fail on clock phase, and only entry gates may block trades.

Both regressions come from the 2026-07-29 session:

* 277 of 884 evaluations (31%) were rejected STALE_STOCK_DATA. The interval was
  already subtracted correctly, but a 2-minute allowance against 5-minute candles
  is unsatisfiable for 3 of every 5 minutes, so good setups were discarded purely
  on when the scan happened to run.
* The Telegram rule reported blocked_trade=True on all 884 rows -- including the
  one trade that opened -- misattributing the cause of every no-trade.
"""

import unittest

from app.gates.rule_evaluation import (
    OPERATIONAL_RULE_GROUPS,
    build_rule_evaluations,
    resolve_blocked_trade,
    rule_domain,
)


class OperationalBlockerTests(unittest.TestCase):

    def test_operational_groups_never_block_a_trade(self):

        for group in OPERATIONAL_RULE_GROUPS:
            self.assertFalse(
                resolve_blocked_trade(group, passed=False),
                f"{group} must not report blocked_trade",
            )
            self.assertFalse(
                resolve_blocked_trade(group, passed=False, blocked_trade=True),
                f"{group} must not be forced to report blocked_trade",
            )

    def test_trading_groups_still_block_on_failure(self):

        for group in ("Entry", "Risk", "Option", "Realtime", "Affordability"):
            self.assertEqual(rule_domain(group), "TRADING")
            self.assertTrue(resolve_blocked_trade(group, passed=False))
            self.assertFalse(resolve_blocked_trade(group, passed=True))

    def test_telegram_paper_review_do_not_block_via_the_row_builder(self):
        """The 2026-07-29 shape: quality gates pass, operational outcomes do not."""

        row = {
            "Symbol": "NVDA",
            "Entry": "BREAKOUT",
            "Telegram Eligibility": "NOT_LIFECYCLE_EVENT",
            "Paper Trade Opened": False,
            "Real Trade Readiness": "PAPER_ONLY",
        }

        by_group = {
            rule.rule_group: rule
            for rule in build_rule_evaluations(row, "2026-07-29_140505")
        }

        for group in ("Telegram", "Paper", "Review"):
            self.assertIn(group, by_group)
            self.assertFalse(by_group[group].passed)
            self.assertFalse(
                by_group[group].blocked_trade,
                f"{group} claimed to block the trade",
            )


class FreshnessAllowanceTests(unittest.TestCase):
    """The allowance is max(configured, candle interval)."""

    @staticmethod
    def _allowance(configured, interval):
        return max(configured, interval)

    def test_five_minute_candles_tolerate_a_full_interval(self):

        allowance = self._allowance(2, 5)
        self.assertEqual(allowance, 5)

        # A bar that closed 3 minutes ago used to be STALE on clock phase alone.
        self.assertLessEqual(3, allowance)
        # A whole missing bar is still STALE.
        self.assertGreater(6, allowance)

    def test_a_configured_allowance_above_the_interval_still_wins(self):

        self.assertEqual(self._allowance(10, 5), 10)

    def test_unknown_interval_falls_back_to_the_configured_limit(self):

        self.assertEqual(self._allowance(2, 0), 2)


class FreshnessIntegrationTests(unittest.TestCase):

    def _status(self, minutes_since_bar_open, interval=5):
        import pandas as pd
        from app.main import get_market_data_status

        now = pd.Timestamp("2026-07-29 11:00", tz="America/New_York")
        last_bar_open = now - pd.Timedelta(minutes=minutes_since_bar_open)
        index = pd.date_range(end=last_bar_open, periods=30, freq=f"{interval}min",
                              tz="America/New_York")
        df = pd.DataFrame({"Close": [100.0] * 30}, index=index)
        return get_market_data_status(df, current_et=now)

    def test_mid_candle_is_live_not_stale(self):
        """8 minutes since bar open = 3 minutes since it closed."""

        status = self._status(8)
        self.assertEqual(status["aggregate_interval_minutes"], 5)
        self.assertEqual(status["delay_minutes"], 3)
        self.assertEqual(status["freshness_allowance_minutes"], 5)
        self.assertEqual(status["stock_data_freshness"], "LIVE")

    def test_a_missing_bar_is_still_stale(self):
        """13 minutes since bar open = 8 minutes since close, a bar is overdue."""

        status = self._status(13)
        self.assertEqual(status["delay_minutes"], 8)
        self.assertEqual(status["stock_data_freshness"], "STALE")


if __name__ == "__main__":
    unittest.main()
