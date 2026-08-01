"""Review alerts: the quality floor that bounds volume, and the enriched message.

Review alerts are the bulk of what a subscriber receives -- 11 of roughly 14
messages on 2026-07-31 -- so both the count and the content of this one message
matter more than any other.
"""

import unittest
from unittest.mock import patch

from app.alerts.telegram_alerts import (
    build_review_alert_message,
    maybe_send_scanner_entry_alert,
)


def _kwargs(**overrides):

    base = {
        "symbol": "NVDA",
        "final_signal": "BULLISH",
        "action_decision": {"action_status": "REVIEW_TV_CHART"},
        "entry_setup": {"entry_type": "EMA_PULLBACK"},
        "risk_setup": {"risk_reward": 1.9},
        "option_contract": {
            "ticker": "O:NVDA260814C00200000",
            "type": "CALL",
            "contract_cost": 310.0,
            "spread_pct": 2.2,
        },
        "latest_price": 198.24,
        "bar_timestamp": "2026-07-31 10:05:00 EDT",
        "next_condition": "Reclaim VWAP on the 5m close",
        "setup_score": 82,
        "alignment_score": 5,
        "rs_rank_score": 12,
        "relative_volume": 1.4,
    }
    base.update(overrides)

    return base


class ReviewAlertFloorTests(unittest.TestCase):

    def _env(self, **extra):

        env = {
            "TELEGRAM_ALERTS_ENABLED": "1",
            "TELEGRAM_ENTRY_ALERTS_ENABLED": "1",
        }
        env.update(extra)

        return env

    def test_candidate_below_the_floor_is_not_alerted(self):

        with patch.dict(
            "os.environ",
            self._env(TELEGRAM_MIN_REVIEW_SETUP_SCORE="60"),
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert"
        ) as send:

            result = maybe_send_scanner_entry_alert(**_kwargs(setup_score=41))

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "REVIEW_SETUP_BELOW_FLOOR")
        send.assert_not_called()

    def test_candidate_at_the_floor_is_alerted(self):

        with patch.dict(
            "os.environ",
            self._env(TELEGRAM_MIN_REVIEW_SETUP_SCORE="60"),
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts.alert_was_sent", return_value=False
        ), patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert", return_value={"ok": True}
        ) as send:

            result = maybe_send_scanner_entry_alert(**_kwargs(setup_score=60))

        send.assert_called_once()
        self.assertTrue(result["sent"])

    def test_blocked_candidate_does_not_consume_its_once_a_day_key(self):
        """The floor is checked before dedup, so a setup that strengthens later
        in the session can still alert. Checking dedup first would burn the key
        on the weakest reading of the day."""

        with patch.dict(
            "os.environ",
            self._env(TELEGRAM_MIN_REVIEW_SETUP_SCORE="60"),
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts.alert_was_sent"
        ) as was_sent, patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ) as mark_sent, patch(
            "app.alerts.telegram_alerts.send_telegram_alert"
        ):

            maybe_send_scanner_entry_alert(**_kwargs(setup_score=20))

        was_sent.assert_not_called()
        mark_sent.assert_not_called()

    def test_floor_is_env_tunable_without_a_deploy(self):

        with patch.dict(
            "os.environ",
            self._env(TELEGRAM_MIN_REVIEW_SETUP_SCORE="85"),
            clear=False,
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert"
        ) as send:

            result = maybe_send_scanner_entry_alert(**_kwargs(setup_score=82))

        self.assertEqual(result["reason"], "REVIEW_SETUP_BELOW_FLOOR")
        send.assert_not_called()

    def test_floor_defaults_to_off(self):
        """Calibration against 9 live sessions found the setup score is the wrong
        axis: review candidates run median 86, so any floor low enough to be safe
        filtered nothing. It ships off, as a circuit breaker for a volume spike
        rather than as a quality filter."""

        env = self._env()
        env.pop("TELEGRAM_MIN_REVIEW_SETUP_SCORE", None)

        with patch.dict("os.environ", env, clear=False), patch(
            "app.alerts.telegram_alerts.alert_was_sent", return_value=False
        ), patch(
            "app.alerts.telegram_alerts.mark_alert_sent"
        ), patch(
            "app.alerts.telegram_alerts.send_telegram_alert", return_value={"ok": True}
        ) as send:

            result = maybe_send_scanner_entry_alert(**_kwargs(setup_score=38))

        self.assertTrue(result["sent"])
        send.assert_called_once()


class ReviewAlertMessageTests(unittest.TestCase):

    def test_message_carries_price_direction_evidence_and_contract(self):

        message = build_review_alert_message(
            "NVDA",
            "EMA_PULLBACK",
            "Reclaim VWAP on the 5m close",
            direction="CALL",
            latest_price=198.24,
            setup_score=82,
            alignment_score=5,
            relative_volume=1.4,
            rs_rank_score=12,
            risk_reward=1.9,
            option_contract={
                "ticker": "O:NVDA260814C00200000",
                "contract_cost": 310.0,
                "spread_pct": 2.2,
            },
        )

        self.assertIn("NVDA", message)
        self.assertIn("CALL", message)
        self.assertIn("198.24", message)
        self.assertIn("EMA Pullback", message)
        self.assertIn("Setup strength 82%", message)
        self.assertIn("Volume 1.4x average", message)
        self.assertIn("Relative strength +12", message)
        self.assertIn("Reclaim VWAP on the 5m close", message)
        self.assertIn("1.9R", message)
        self.assertIn("O:NVDA260814C00200000", message)
        self.assertIn("Spread 2.2%", message)
        self.assertIn("Watch only", message)

    def test_message_omits_sections_it_has_no_data_for(self):
        """A missing field drops its section rather than printing a dash."""

        message = build_review_alert_message(
            "AAPL",
            "BREAKOUT",
            "Break and hold the opening range high",
        )

        self.assertIn("AAPL", message)
        self.assertIn("Breakout", message)
        self.assertIn("Break and hold the opening range high", message)
        self.assertNotIn("WHY IT IS ON THE LIST", message)
        self.assertNotIn("IF IT CONFIRMS", message)
        self.assertNotIn("Contract:", message)

    def test_setup_label_keeps_ema_and_vwap_uppercase(self):

        self.assertIn(
            "VWAP Rejection",
            build_review_alert_message("QQQ", "VWAP_REJECTION", "-"),
        )
        self.assertIn(
            "EMA Rejection Short",
            build_review_alert_message("QQQ", "EMA_REJECTION_SHORT", "-"),
        )


if __name__ == "__main__":

    unittest.main()
