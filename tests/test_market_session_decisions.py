import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import app.main as scanner


class FixedDateTime(datetime):

    fixed_now = None

    @classmethod
    def now(cls, tz=None):

        if tz is None:

            return cls.fixed_now.replace(
                tzinfo=None
            )

        return cls.fixed_now.astimezone(
            tz
        )


class MarketSessionDecisionTests(unittest.TestCase):

    def setUp(self):

        self.original_datetime = scanner.datetime

    def tearDown(self):

        scanner.datetime = self.original_datetime

    def set_now(self, hour, minute):

        FixedDateTime.fixed_now = datetime(
            2026,
            6,
            30,
            hour,
            minute,
            tzinfo=ZoneInfo("America/New_York")
        )

        scanner.datetime = FixedDateTime

        return FixedDateTime.fixed_now

    def make_5m_df(self, now):

        index = pd.DatetimeIndex([
            now - timedelta(minutes=10),
            now - timedelta(minutes=5)
        ]).tz_convert(
            "UTC"
        )

        return pd.DataFrame(
            {"Close": [100, 101]},
            index=index
        )

    def make_action_decision(self, status):

        return scanner.build_action_decision(
            final_signal="BULLISH",
            entry_setup={"entry_type": "BREAKOUT"},
            risk_setup={
                "trade_allowed": True,
                "risk_reward": 2.0,
                "reasons": []
            },
            risk_passed_before_options=True,
            projection=object(),
            option_quote_status=None,
            option_rejection_reason=None,
            market_data_status=status
        )

    def test_5m_bucket_start_timestamp_is_live_in_premarket(self):

        now = self.set_now(
            8,
            37
        )

        status = scanner.get_market_data_status(
            self.make_5m_df(now)
        )

        self.assertEqual(
            status["stock_data_freshness"],
            "LIVE"
        )

        self.assertEqual(
            status["delay_minutes"],
            0
        )

        self.assertEqual(
            status["raw_delay_minutes"],
            5.0
        )

        self.assertEqual(
            status["market_session"],
            "PREMARKET"
        )

    def test_premarket_candidate_is_watch_only(self):

        now = self.set_now(
            8,
            37
        )

        status = scanner.get_market_data_status(
            self.make_5m_df(now)
        )

        decision = self.make_action_decision(
            status
        )

        self.assertEqual(
            decision["action_status"],
            "PREMARKET_WATCH"
        )

        self.assertFalse(
            decision["realtime_confirmation_needed"]
        )

    def test_opening_range_candidate_waits_for_confirmation(self):

        now = self.set_now(
            9,
            37
        )

        status = scanner.get_market_data_status(
            self.make_5m_df(now)
        )

        decision = self.make_action_decision(
            status
        )

        self.assertEqual(
            decision["action_status"],
            "OPENING_RANGE_CONFIRMATION"
        )

        self.assertTrue(
            decision["realtime_confirmation_needed"]
        )

    def test_regular_session_can_enter_after_opening_range(self):

        now = self.set_now(
            9,
            47
        )

        status = scanner.get_market_data_status(
            self.make_5m_df(now)
        )

        decision = self.make_action_decision(
            status
        )

        self.assertIn(
            decision["action_status"],
            ["ENTER", "ENTER_PAPER"]
        )


if __name__ == "__main__":

    unittest.main()