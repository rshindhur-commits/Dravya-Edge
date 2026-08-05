import unittest
from unittest.mock import Mock, patch

import requests

from app.alerts.telegram_alerts import (
    TelegramDeliveryError,
    _send_telegram_alert_direct,
    build_paper_entry_alert_message,
    build_scanner_entry_alert_message,
    calculate_entry_alert_score,
    _entry_alert_policy,
    maybe_send_paper_trade_update_alert,
    maybe_send_scanner_entry_alert,
)
from app.gates import EntryGateConfig, evaluate_entry_gate


class TelegramAlertPolicyTests(unittest.TestCase):

    def test_direct_send_preserves_telegram_bad_request_description(self):

        response = Mock()
        response.status_code = 400
        response.json.return_value = {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: can't parse entities",
        }
        response.raise_for_status.side_effect = requests.HTTPError(
            "400 Bad Request"
        )

        with patch(
            "app.alerts.telegram_alerts.get_telegram_credentials",
            return_value=("token", "chat-id"),
        ), patch(
            "app.alerts.telegram_alerts.get_telegram_session"
        ) as session_factory:

            session_factory.return_value.post.return_value = response

            with self.assertRaises(TelegramDeliveryError) as error:

                _send_telegram_alert_direct("<b>broken")

        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(
            error.exception.telegram_response["description"],
            "Bad Request: can't parse entities",
        )

    def _scanner_alert_kwargs(self):

        return {
            "symbol": "SPCX",
            "final_signal": "BULLISH",
            "action_decision": {
                "action_status": "REVIEW_TV_CHART"
            },
            "entry_setup": {
                "entry_type": "EMA_PULLBACK"
            },
            "risk_setup": {
                "risk_reward": 4.17
            },
            "option_contract": {
                "ticker": "SPCX250718C00050000",
                "option_quality_score": 100,
                "spread_pct": 2.44,
                "quote_freshness": "LIVE_QUOTE",
                "affordable": True,
            },
            "latest_price": 50,
            "bar_timestamp": "2026-07-17 10:00:00 EDT",
            "next_condition": "Confirm live chart",
            "top_candidate": "BULLISH_TOP_1",
            "option_quote_freshness": "LIVE_QUOTE",
            "option_quality_score": 100,
            "option_spread_pct": 2.44,
            "setup_score": 90,
            "alignment_score": 5,
            "rs_rank_score": 2,
            "relative_volume": 2,
        }

    def test_scanner_review_alert_is_sent(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "PAPER",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_REVIEW_ALERTS_ENABLED": "1",
                "TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE": "0",
            },
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts._load_alert_state",
            return_value={}
        ), patch(
            "app.alerts.telegram_alerts.alert_was_sent",
            return_value=False
        ), patch("app.alerts.telegram_alerts.send_telegram_alert") as send_alert:

            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        self.assertTrue(result["sent"])
        self.assertEqual(result["reason"], "SENT")
        send_alert.assert_called_once()

    def test_scanner_review_alert_ignores_the_paper_entry_setup_floor(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_REVIEW_ALERTS_ENABLED": "1",
                "TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE": "100",
            },
            clear=False,
        ), patch("app.alerts.telegram_alerts.send_telegram_alert") as send_alert:

            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        # TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE is the floor for a position that
        # opened, not for a chart-review nudge, so 100 must not gag this.
        self.assertTrue(result["sent"])
        send_alert.assert_called_once()

    def test_scanner_review_alert_is_sent_under_real_review_policy(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "REAL_REVIEW",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_REVIEW_ALERTS_ENABLED": "1",
            },
            clear=False,
        ), patch("app.alerts.telegram_alerts.send_telegram_alert") as send_alert:

            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        self.assertTrue(result["sent"])
        send_alert.assert_called_once()

    def test_scanner_entry_waits_for_confirmed_trade_open(self):

        kwargs = self._scanner_alert_kwargs()
        kwargs["action_decision"] = {"action_status": "ENTER_PAPER"}

        with patch.dict(
            "os.environ",
            {"TELEGRAM_ALERTS_ENABLED": "1", "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
             "TELEGRAM_REVIEW_ALERTS_ENABLED": "1"},
            clear=False,
        ), patch("app.alerts.telegram_alerts.send_telegram_alert") as send_alert:

            result = maybe_send_scanner_entry_alert(**kwargs)

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "ENTRY_AWAITING_TRADE_OPEN")
        send_alert.assert_not_called()

    def test_active_trade_scanner_alert_is_suppressed(self):

        kwargs = self._scanner_alert_kwargs()
        kwargs["entry_setup"] = {"entry_type": "ACTIVE_TRADE"}

        with patch.dict(
            "os.environ",
            {"TELEGRAM_ALERTS_ENABLED": "1", "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
             "TELEGRAM_REVIEW_ALERTS_ENABLED": "1"},
            clear=False,
        ), patch("app.alerts.telegram_alerts.send_telegram_alert") as send_alert:

            result = maybe_send_scanner_entry_alert(**kwargs)

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "ACTIVE_TRADE_SUPPRESSED")
        send_alert.assert_not_called()

    def test_confirmed_trade_message_uses_subscriber_facing_language(self):

        message = build_paper_entry_alert_message(
            {
                "symbol": "NFLX",
                "direction": "CALL",
                "entry_type": "EMA_PULLBACK",
                "entry_price": 100,
                "stop_loss": 98,
                "take_profit": 104,
                "planned_rr": 2,
            },
            {
                "Action Status": "ENTER_PAPER",
                "Expected Remaining Trend": 86,
                "Projected Entry Grade": "A",
                "Trade Quality Score": 97,
                "Option Strike": 700,
                "Option Expiration": "2026-08-21",
                "Option Mid Price": 2.35,
                "Option Contract Cost": 235,
                "Option Risk At Stop": 47,
                "Option Spread %": 0.85,
                "Option Quality Score": 96,
                "V2 Trend Health Status": "STRONG",
                "V2 Pullback Number": 1,
                "Relative Volume": 1.5,
                "RS Rank Score": 2,
            },
        )

        self.assertIn("NEW TRADE", message)
        self.assertIn("BUY NOW", message)
        self.assertIn("TRADE", message)
        self.assertIn("SETUP", message)
        self.assertIn("OPTION", message)
        self.assertIn("EMA Pullback", message)
        self.assertIn("Contract: 700C", message)
        self.assertIn("Expiry: 2026-08-21", message)
        self.assertIn("Premium: $2.35", message)
        self.assertIn("Contract Cost: $235.00", message)
        self.assertIn("RR: 2R", message)
        self.assertIn("ET", message)
        self.assertIn("🟢 Open", message)
        self.assertNotIn("ENTER_PAPER", message)

    def test_scanner_entry_message_includes_option_expiry(self):

        message = build_scanner_entry_alert_message(
            "NFLX",
            "BULLISH",
            "ENTER_PAPER",
            {"entry_type": "EMA_PULLBACK"},
            {"risk_reward": 2.0},
            {
                "ticker": "O:NFLX260821C00700000",
                "type": "CALL",
                "expiration": "2026-08-21",
                "contract_cost": 235,
            },
            700,
            "Confirm entry",
        )

        self.assertIn("Expiry: 2026-08-21", message)
        self.assertIn("Contract Cost: $235", message)

    def test_trade_update_requires_material_change(self):

        trade = {
            "symbol": "NFLX",
            "direction": "CALL",
            "entry_price": 100,
            "stop_loss": 98,
            "opened_at": "2026-07-24 09:24:00",
            "status": "OPEN",
        }
        with patch.dict(
            "os.environ",
            {"TELEGRAM_ALERTS_ENABLED": "1", "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
             "TELEGRAM_REVIEW_ALERTS_ENABLED": "1"},
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts._last_trade_lifecycle_metadata",
            return_value={"last_r_multiple": 0.0, "last_trend_health": "STRONG"},
        ), patch(
            "app.alerts.telegram_alerts._subscriber_entry_metadata",
            return_value={"message_type": "PAPER_ENTRY"},
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert"
        ) as send_alert, patch(
            "app.alerts.telegram_alerts._record_alert_attempt"
        ):

            sent = maybe_send_paper_trade_update_alert(
                trade,
                101.5,
                {"V2 Trend Health Status": "HEALTHY"},
            )
            unchanged = maybe_send_paper_trade_update_alert(
                trade,
                100.4,
                {"V2 Trend Health Status": "STRONG"},
            )

        self.assertTrue(sent["sent"])
        self.assertFalse(unchanged["sent"])
        self.assertEqual(unchanged["reason"], "NO_MATERIAL_TRADE_CHANGE")
        self.assertEqual(send_alert.call_count, 1)

    def test_scanner_review_alert_queues_through_the_dispatcher(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "PAPER",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_REVIEW_ALERTS_ENABLED": "1",
                "TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE": "0",
                "TELEGRAM_DISPATCH_MODE": "QUEUED",
            },
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts._load_alert_state",
            return_value={}
        ), patch(
            "app.runtime.telegram_dispatcher.get_telegram_sender"
        ) as sender_factory, patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ) as mark_sent:

            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        # Queued dispatch reports "not yet sent" because the send has been handed
        # to the dedicated dispatcher thread, not because the alert was refused.
        self.assertEqual(result["reason"], "QUEUED")
        self.assertTrue(result["queued"])
        sender_factory.return_value.submit.assert_called_once()

    def test_scanner_review_alert_fires_once_per_symbol_and_setup_per_day(self):
        """Dedup, not the removed suppression, is what bounds review volume.

        `_review_alert_key` keys on symbol, setup and date, so a candidate that
        appears in thirty scans alerts once. On 2026-07-31 that was 11 alerts
        against 65 raw review events.
        """

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_REVIEW_ALERTS_ENABLED": "1",
            },
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts.alert_was_sent",
            return_value=True
        ), patch("app.alerts.telegram_alerts.send_telegram_alert") as send_alert:

            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "DUPLICATE_ALERT")
        send_alert.assert_not_called()

    def test_telegram_gate_accepts_enter_paper_candidate(self):

        allowed, reason = evaluate_entry_gate(
            {
                "Action Status": "ENTER_PAPER",
                "Setup %": 86,
                "Candidate RR": 2.1,
                "Option Quality Score": 72,
                "Option Spread %": 6,
                "Option Quote Freshness": "LIVE_QUOTE",
            },
            EntryGateConfig(
                min_rr=2.0,
                min_setup_percent=70.0,
                min_option_quality=65.0,
                max_spread_pct=8.0,
            ),
            mode="telegram",
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "ELIGIBLE")

    def test_telegram_gate_rejects_low_rr_candidate(self):

        allowed, reason = evaluate_entry_gate(
            {
                "Action Status": "ENTER_PAPER",
                "Setup %": 86,
                "Candidate RR": 1.8,
                "Option Quality Score": 72,
                "Option Spread %": 6,
            },
            EntryGateConfig(
                min_rr=2.0,
                min_setup_percent=70.0,
                min_option_quality=65.0,
                max_spread_pct=8.0,
            ),
            mode="telegram",
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "RR_BELOW_THRESHOLD")

    def test_alert_score_keeps_high_quality_candidates_rankable(self):

        score = calculate_entry_alert_score(
            setup_score=88,
            alignment_score=5,
            rs_rank_score=2,
            option_quality_score=90,
            risk_reward=2.4,
            relative_volume=2.0,
            option_spread_pct=4,
        )

        self.assertGreaterEqual(score, 88.0)

    def test_entry_alert_policy_exposes_only_enforced_bars(self):
        """Every key in the policy must be one the alert path actually reads.

        This replaces a test that pinned the default of instant_alert_score, a
        setting nothing ever consumed. Eleven such keys existed -- a daily cap, a
        concurrent cap, two cooldowns, a top-candidate limit, three score
        thresholds and three per-session caps -- all inert, and a test asserting
        their defaults made them look supported.
        """

        self.assertEqual(
            set(_entry_alert_policy()),
            {"min_option_quality", "min_rr", "max_spread_pct"},
        )

    def test_alert_bars_are_never_stricter_than_the_entry_gate(self):
        """A position that opened must not be unalertable on a bar entry already passed.

        TELEGRAM_MIN_RR sat at 2.0 against an entry floor of 1.8, so a setup
        entering at 1.9 opened a trade and told no subscriber about it.
        """

        import os

        from app.runtime.paper_automation_support import (
            DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT,
            DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY,
            DEFAULT_AUTO_PAPER_MIN_RR,
        )

        # Cleared deliberately. Code defaults are what reaches Streamlit Cloud --
        # .env is gitignored and never deployed -- so the shipped defaults are what
        # this invariant has to hold for.
        overrides = {
            key: "" for key in (
                "TELEGRAM_MIN_RR",
                "TELEGRAM_MIN_OPTION_QUALITY_SCORE",
                "TELEGRAM_MAX_SPREAD_PCT",
            )
        }

        with patch.dict("os.environ", overrides, clear=False):
            policy = _entry_alert_policy()

        self.assertLessEqual(policy["min_rr"], DEFAULT_AUTO_PAPER_MIN_RR)
        self.assertLessEqual(
            policy["min_option_quality"], DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY
        )
        self.assertLessEqual(
            policy["max_spread_pct"], DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT
        )


if __name__ == "__main__":

    unittest.main()