import unittest
from unittest.mock import patch

from app.alerts.telegram_alerts import (
    calculate_entry_alert_score,
    _entry_alert_policy,
)
from app.gates import EntryGateConfig, evaluate_entry_gate


class TelegramAlertPolicyTests(unittest.TestCase):

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