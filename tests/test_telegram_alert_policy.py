import unittest
from unittest.mock import patch

from app.alerts.telegram_alerts import (
    calculate_entry_alert_score,
    _entry_alert_policy,
    maybe_send_scanner_entry_alert,
)
from app.gates import EntryGateConfig, evaluate_entry_gate


class TelegramAlertPolicyTests(unittest.TestCase):

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

    def test_scanner_alert_paper_policy_does_not_require_high_conviction(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "PAPER",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
                "TELEGRAM_MIN_ENTRY_ALERT_SCORE": "0",
            },
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts._entry_alert_time_bucket",
            return_value="MORNING"
        ), patch(
            "app.alerts.telegram_alerts._load_alert_state",
            return_value={}
        ), patch(
            "app.alerts.telegram_alerts._entry_alerts_today",
            return_value=0
        ), patch(
            "app.alerts.telegram_alerts._active_entry_alerts",
            return_value=[]
        ), patch(
            "app.alerts.telegram_alerts._entry_alerts_in_bucket",
            return_value=0
        ), patch(
            "app.alerts.telegram_alerts._recent_matching_entry_alert",
            return_value=False
        ), patch(
            "app.alerts.telegram_alerts._recent_closed_symbol_alert",
            return_value=False
        ), patch(
            "app.alerts.telegram_alerts.alert_was_sent",
            return_value=False
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert"
        ), patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ):

            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        self.assertTrue(result["sent"])
        self.assertEqual(result["reason"], "SENT")

    def test_scanner_alert_real_review_policy_requires_high_conviction(self):

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_ALERT_POLICY": "REAL_REVIEW",
                "TELEGRAM_ALERTS_ENABLED": "1",
                "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
            },
            clear=False,
        ):

            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "NOT_HIGH_CONVICTION")

    def test_scanner_alert_queued_mode_returns_queued_without_marking_sent(self):

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
            "app.alerts.telegram_alerts._entry_alert_time_bucket",
            return_value="MORNING"
        ), patch(
            "app.alerts.telegram_alerts._load_alert_state",
            return_value={}
        ), patch(
            "app.alerts.telegram_alerts._entry_alerts_today",
            return_value=0
        ), patch(
            "app.alerts.telegram_alerts._active_entry_alerts",
            return_value=[]
        ), patch(
            "app.alerts.telegram_alerts._entry_alerts_in_bucket",
            return_value=0
        ), patch(
            "app.alerts.telegram_alerts._recent_matching_entry_alert",
            return_value=False
        ), patch(
            "app.alerts.telegram_alerts._recent_closed_symbol_alert",
            return_value=False
        ), patch(
            "app.alerts.telegram_alerts.alert_was_sent",
            return_value=False
        ), patch(
            "app.runtime.telegram_dispatcher.get_runtime_scheduler"
        ) as scheduler_factory, patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ) as mark_sent:

            scheduler_factory.return_value.submit_critical.return_value = "job-id"
            result = maybe_send_scanner_entry_alert(
                **self._scanner_alert_kwargs()
            )

        self.assertFalse(result["sent"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["reason"], "QUEUED")
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