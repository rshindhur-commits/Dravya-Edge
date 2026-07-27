import unittest

from app.alerts.telegram_alerts import (
    _can_send_exit_alert,
    _exit_alert_key,
    build_trade_exit_alert_message,
    maybe_send_trade_exit_alert,
)
from unittest.mock import patch


class TelegramExitAlertGuardTests(unittest.TestCase):

    def test_scanner_tracked_trade_can_send_lifecycle_exit_alert(self):

        allowed, reason = _can_send_exit_alert(
            {
                "status": "OPEN",
                "trade_mode": "SCANNER_TRACKED"
            },
            "EXIT"
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "ELIGIBLE")

    def test_paper_trade_can_send_exit_alert_once(self):

        allowed, reason = _can_send_exit_alert(
            {
                "status": "CLOSED",
                "trade_mode": "PAPER",
                "exit_alert_sent": False
            },
            "EXIT"
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "ELIGIBLE")

    def test_paper_trade_exit_alert_sent_blocks_repeat(self):

        allowed, reason = _can_send_exit_alert(
            {
                "status": "CLOSED",
                "trade_mode": "PAPER",
                "exit_alert_sent": True
            },
            "EXIT"
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "EXIT_ALERT_ALREADY_SENT")

    def test_exit_alert_key_is_deterministic(self):

        key = _exit_alert_key(
            "NVDA",
            "O:NVDA260717C00197500",
            {
                "trade_id": "trade-456",
                "opened_at": "2026-07-02 10:00:00"
            },
            "Hard stop hit",
            "EXIT"
        )

        self.assertEqual(
            key,
            "EXIT|trade-456|Hard stop hit"
        )

    def test_exit_is_suppressed_without_delivered_new_trade_alert(self):

        trade = {
            "trade_id": "trade-123",
            "symbol": "NVDA",
            "status": "CLOSED",
            "trade_mode": "PAPER",
        }
        with patch(
            "app.alerts.telegram_alerts.telegram_exit_alerts_enabled",
            return_value=True,
        ), patch(
            "app.alerts.telegram_alerts._load_alert_state",
            return_value={"sent": {}},
        ):
            result = maybe_send_trade_exit_alert(
                "NVDA",
                trade,
                "Hard stop hit",
            )

        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "SUBSCRIBER_NEW_TRADE_NOT_SENT")

    def test_negative_holding_time_is_omitted(self):

        message = build_trade_exit_alert_message(
            "NVDA",
            {
                "direction": "CALL",
                "opened_at": "2026-07-27 10:00:00",
            },
            "Hard stop hit",
            event_timestamp="2026-07-27 09:59:00",
        )

        self.assertNotIn("Holding Time:", message)


if __name__ == "__main__":

    unittest.main()