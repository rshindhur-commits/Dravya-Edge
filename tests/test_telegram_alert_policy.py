import unittest
from unittest.mock import Mock, patch

import requests

from app.alerts.telegram_alerts import (
    TelegramDeliveryError,
    _send_telegram_alert_direct,
    build_paper_entry_alert_message,
    build_scanner_entry_alert_message,
    calculate_entry_alert_score,
    classify_scanner_entry_alert,
    _entry_alert_policy,
    maybe_send_paper_trade_update_alert,
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
            "app.alerts.telegram_alerts.requests.post",
            return_value=response,
        ):

            with self.assertRaises(TelegramDeliveryError) as error:

                _send_telegram_alert_direct("<b>broken")

        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(
            error.exception.telegram_response["description"],
            "Bad Request: can't parse entities",
        )

    def _scanner_alert_decision_kwargs(self):

        return {
            "action_decision": {
                "action_status": "REVIEW_TV_CHART"
            },
            "entry_setup": {
                "entry_type": "EMA_PULLBACK"
            },
        }

    def test_scanner_review_alert_is_suppressed_by_lifecycle_contract(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "PAPER",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_MIN_ENTRY_ALERT_SCORE": "0",
            },
            clear=False,
        ):

            reason = classify_scanner_entry_alert(
                **self._scanner_alert_decision_kwargs()
            )

        self.assertEqual(reason, "REVIEW_ALERT_SUPPRESSED")

    def test_scanner_review_alert_remains_suppressed_despite_score_setting(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_MIN_ENTRY_ALERT_SCORE": "100",
            },
            clear=False,
        ):

            reason = classify_scanner_entry_alert(
                **self._scanner_alert_decision_kwargs()
            )

        self.assertEqual(reason, "REVIEW_ALERT_SUPPRESSED")

    def test_scanner_review_alert_is_suppressed_under_real_review_policy(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "REAL_REVIEW",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
            },
            clear=False,
        ):

            reason = classify_scanner_entry_alert(
                **self._scanner_alert_decision_kwargs()
            )

        self.assertEqual(reason, "REVIEW_ALERT_SUPPRESSED")

    def test_scanner_entry_waits_for_confirmed_trade_open(self):

        kwargs = self._scanner_alert_decision_kwargs()
        kwargs["action_decision"] = {"action_status": "ENTER_PAPER"}

        with patch.dict(
            "os.environ",
            {"TELEGRAM_ALERTS_ENABLED": "1", "TELEGRAM_ENTRY_ALERTS_ENABLED": "1"},
            clear=False,
        ):

            reason = classify_scanner_entry_alert(**kwargs)

        self.assertEqual(reason, "ENTRY_AWAITING_TRADE_OPEN")

    def test_active_trade_scanner_alert_is_suppressed(self):

        kwargs = self._scanner_alert_decision_kwargs()
        kwargs["entry_setup"] = {"entry_type": "ACTIVE_TRADE"}

        with patch.dict(
            "os.environ",
            {"TELEGRAM_ALERTS_ENABLED": "1", "TELEGRAM_ENTRY_ALERTS_ENABLED": "1"},
            clear=False,
        ):

            reason = classify_scanner_entry_alert(**kwargs)

        self.assertEqual(reason, "ACTIVE_TRADE_SUPPRESSED")

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
            {"TELEGRAM_ALERTS_ENABLED": "1", "TELEGRAM_ENTRY_ALERTS_ENABLED": "1"},
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

    def test_scanner_review_alert_does_not_queue_or_mark_sent(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "PAPER",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_MIN_ENTRY_ALERT_SCORE": "0",
                "TELEGRAM_DISPATCH_MODE": "QUEUED",
            },
            clear=False,
        ), patch(
            "app.runtime.telegram_dispatcher.get_runtime_scheduler"
        ) as scheduler_factory, patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ) as mark_sent:

            reason = classify_scanner_entry_alert(
                **self._scanner_alert_decision_kwargs()
            )

        self.assertEqual(reason, "REVIEW_ALERT_SUPPRESSED")
        scheduler_factory.assert_not_called()
        mark_sent.assert_not_called()

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

    def test_instant_alert_default_matches_current_policy(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_INSTANT_ENTRY_ALERT_SCORE": ""
            },
            clear=False,
        ):

            policy = _entry_alert_policy()

        self.assertEqual(policy["instant_alert_score"], 88.0)


if __name__ == "__main__":

    unittest.main()