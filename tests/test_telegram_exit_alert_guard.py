import unittest

from app.alerts.telegram_alerts import (
    _can_send_exit_alert,
    _exit_alert_key
)


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
                "opened_at": "2026-07-02 10:00:00"
            },
            "Hard stop hit",
            "EXIT"
        )

        self.assertEqual(
            key,
            "EXIT|NVDA|O:NVDA260717C00197500|2026-07-02 10:00:00|Hard stop hit"
        )


if __name__ == "__main__":

    unittest.main()