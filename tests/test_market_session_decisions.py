import os
import unittest
from unittest.mock import patch
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


class SpreadBlockedSignalsStillReachSubscribersTests(unittest.TestCase):
    """A signal refused only on contract spread must not be deleted.

    Everything before that check has already passed -- direction, entry trigger,
    risk geometry, RR, session window. What remains is a qualified signal for
    which no contract quoted tightly enough. AVOID is never alertable, so the
    subscriber was told nothing: AMD signalled 87 times over 2026-08-19..21 and
    produced not one message, its best contract quoting 1.79%.

    This is a signal product. The spread is a fact about the contract and the
    subscriber can answer it -- another strike, another expiry, or skip.
    """

    OPEN_SESSION = {
        "market_session": "REGULAR",
        "delay_minutes": 0,
        "is_market_open": True,
    }

    def _decide(self, rejection, enabled="true"):
        with patch.dict(
            os.environ, {"ALERT_SPREAD_BLOCKED_SIGNALS": enabled}, clear=False
        ):
            return scanner.build_action_decision(
                final_signal="BULLISH",
                entry_setup={"entry_type": "BREAKOUT"},
                risk_setup={"trade_allowed": True, "risk_reward": 2.0, "reasons": []},
                risk_passed_before_options=True,
                projection=object(),
                option_quote_status=None,
                option_rejection_reason=rejection,
                market_data_status=self.OPEN_SESSION,
            )

    def test_a_spread_refusal_becomes_reviewable_rather_than_avoid(self):
        decision = self._decide("Wide bid/ask spread")

        self.assertEqual(decision["action_status"], "REVIEW_TV_CHART")
        self.assertEqual(decision["action_reason"], "Wide bid/ask spread")

    def test_liquidity_refusals_are_untouched(self):
        """Thin volume and absent interest say the contract cannot be bought.

        An alert naming one of those is a promise that cannot be kept, so they
        stay AVOID.
        """

        for reason in ("Low open interest", "Low option volume",
                       "Missing bid/ask", "Option too expensive"):
            self.assertEqual(
                self._decide(reason)["action_status"], "AVOID", reason
            )

    def test_the_switch_restores_the_previous_silence(self):
        self.assertEqual(
            self._decide("Wide bid/ask spread", enabled="false")["action_status"],
            "AVOID",
        )

    def test_it_cannot_send_anything_by_itself(self):
        """Two existing switches still gate delivery and the paper book."""

        from app.alerts import telegram_alerts

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_REVIEW_ALERTS_ENABLED", None)
            self.assertFalse(telegram_alerts._review_alerts_enabled())

    def test_a_clean_candidate_is_unaffected(self):
        self.assertIn(
            self._decide(None)["action_status"], ("ENTER", "ENTER_PAPER")
        )

