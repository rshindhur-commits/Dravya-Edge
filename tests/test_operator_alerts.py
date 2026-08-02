"""Something has to say the machine is broken without being asked.

On 2026-08-01 a container ran for hours unable to reach Postgres -- re-sending a
weekly summary, reporting zero closed trades over a week with seven -- and
nothing reported it. It was noticed because the messages arrived, not because
anything raised a fault.

Two properties matter more than the feature. Operator alerts must never reach the
subscriber channel, and a fault that persists must not repeat itself: a channel
that cries every five minutes is one nobody reads on the day it matters.
"""

import unittest
from unittest.mock import patch

from app.alerts import operator_alerts


class DisabledByDefaultTests(unittest.TestCase):
    """Silence is the safe default. Falling back to the broadcast chat would mean
    a bad deploy narrating its own failure to every subscriber."""

    def setUp(self):
        operator_alerts.reset_operator_alert_state()
        self.addCleanup(operator_alerts.reset_operator_alert_state)

    def test_no_operator_chat_means_nothing_is_sent(self):

        with patch.dict("os.environ", {operator_alerts.OPERATOR_CHAT_ENV: ""},
                        clear=False), \
             patch.object(operator_alerts, "_send_to_operator") as send:

            result = operator_alerts.notify_operator("database", "down")

        send.assert_not_called()
        self.assertEqual(result["reason"], "OPERATOR_CHAT_NOT_CONFIGURED")

    def test_the_subscriber_chat_is_never_the_destination(self):
        """The send reads TELEGRAM_OPERATOR_CHAT_ID, never the credentials'
        chat id, which is the broadcast channel."""

        posted = {}

        class Session:
            def post(self, _url, json=None, timeout=None):
                posted.update(json or {})

                class Response:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"ok": True}

                return Response()

        with patch.dict("os.environ",
                        {operator_alerts.OPERATOR_CHAT_ENV: "-100999"}, clear=False), \
             patch("app.alerts.telegram_alerts.get_telegram_credentials",
                   return_value=("token", "-100SUBSCRIBERS")), \
             patch("app.alerts.telegram_alerts.get_telegram_session",
                   return_value=Session()):

            operator_alerts.notify_operator("database", "down")

        self.assertEqual(posted["chat_id"], "-100999")
        self.assertNotEqual(posted["chat_id"], "-100SUBSCRIBERS")


class TransitionOnlyTests(unittest.TestCase):

    def setUp(self):
        operator_alerts.reset_operator_alert_state()
        self.addCleanup(operator_alerts.reset_operator_alert_state)
        env = patch.dict("os.environ",
                         {operator_alerts.OPERATOR_CHAT_ENV: "-100999"}, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def test_a_persistent_fault_sends_once(self):
        """Six hours of a broken database is one message, not seventy-two."""

        with patch.object(operator_alerts, "_send_to_operator") as send:

            for _ in range(5):
                operator_alerts.notify_operator("database", "down")

        self.assertEqual(send.call_count, 1)

    def test_recovery_sends_and_rearms_the_fault(self):

        with patch.object(operator_alerts, "_send_to_operator") as send:

            operator_alerts.notify_operator("database", "down")
            operator_alerts.notify_operator("database", "back", healthy=True)
            operator_alerts.notify_operator("database", "down again")

        self.assertEqual(send.call_count, 3)
        self.assertIn("RECOVERED", send.call_args_list[1].args[0])

    def test_recovery_for_a_fault_never_raised_is_not_news(self):

        with patch.object(operator_alerts, "_send_to_operator") as send:

            operator_alerts.notify_operator("database", "fine", healthy=True)

        send.assert_not_called()

    def test_separate_keys_do_not_suppress_each_other(self):

        with patch.object(operator_alerts, "_send_to_operator") as send:

            operator_alerts.notify_operator("database", "down")
            operator_alerts.notify_operator("scan_failures", "three in a row")

        self.assertEqual(send.call_count, 2)

    def test_a_failed_send_does_not_retry_on_every_scan(self):
        """Telegram being unreachable during an incident must not become its own
        repeating incident."""

        with patch.object(operator_alerts, "_send_to_operator",
                          side_effect=RuntimeError("telegram down")) as send:

            first = operator_alerts.notify_operator("database", "down")
            second = operator_alerts.notify_operator("database", "down")

        self.assertEqual(first["reason"], "SEND_FAILED")
        self.assertEqual(second["reason"], "NO_CHANGE")
        self.assertEqual(send.call_count, 1)


class ScanLoopIntegrationTests(unittest.TestCase):
    """The loop must alert, and must never be stopped by alerting."""

    def setUp(self):
        operator_alerts.reset_operator_alert_state()
        self.addCleanup(operator_alerts.reset_operator_alert_state)

    def test_an_unreachable_database_alerts_at_startup(self):
        from app.runtime import scan_loop

        with patch("app.db.persistence.database_status", return_value="UNREACHABLE"), \
             patch("app.runtime.scan_loop._publish_heartbeat"), \
             patch("app.alerts.operator_alerts.notify_operator") as notify:

            scan_loop._report_database_state()

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], "database")

    def test_a_switched_off_database_is_not_an_alert(self):
        """DB_WRITE_ENABLED=false is a choice someone made, not a fault."""

        from app.runtime import scan_loop

        with patch("app.db.persistence.database_status", return_value="OFF"), \
             patch("app.runtime.scan_loop._publish_heartbeat"), \
             patch("app.alerts.operator_alerts.notify_operator") as notify:

            scan_loop._report_database_state()

        notify.assert_not_called()

    def test_a_broken_notifier_cannot_stop_the_loop(self):
        from app.runtime import scan_loop

        with patch("app.alerts.operator_alerts.notify_operator",
                   side_effect=RuntimeError("boom")):

            self.assertEqual(scan_loop._notify_operator("k", "m"), {"sent": False})

class ShutdownAnnouncementTests(unittest.TestCase):
    """A deploy sends SIGTERM every time, so alerting on every shutdown would
    make this channel noise -- and the redeploys are mostly evenings and
    weekends, when nothing is missed. During a live session the same event means
    scanning has stopped and nobody would know until they looked."""

    def setUp(self):
        operator_alerts.reset_operator_alert_state()
        self.addCleanup(operator_alerts.reset_operator_alert_state)

    def test_a_shutdown_during_a_live_session_is_announced(self):
        from app.runtime import scan_loop

        with patch("app.runtime.scan_loop.current_session", return_value="REGULAR"), \
             patch("app.runtime.scan_loop.idle_reason", return_value=None), \
             patch("app.alerts.operator_alerts.notify_operator") as notify:

            scan_loop._announce_shutdown("terminated by signal 15", 12)

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], "worker_stopped")
        self.assertIn("REGULAR", notify.call_args.args[1])

    def test_a_weekend_shutdown_is_not_announced(self):
        from app.runtime import scan_loop

        with patch("app.runtime.scan_loop.current_session", return_value="REGULAR"), \
             patch("app.runtime.scan_loop.idle_reason", return_value="SLEEPING_WEEKEND"), \
             patch("app.alerts.operator_alerts.notify_operator") as notify:

            scan_loop._announce_shutdown("terminated by signal 15", 0)

        notify.assert_not_called()

    def test_the_stop_reason_names_the_signal(self):
        """A STOPPED row on its own says the process died and nothing about what
        killed it, which is the first question asked every time."""

        from app.runtime import scan_loop

        scan_loop._stopping = False
        scan_loop._stop_signal = None
        self.addCleanup(setattr, scan_loop, "_stopping", False)
        self.addCleanup(setattr, scan_loop, "_stop_signal", None)

        scan_loop._request_stop(15, None)

        self.assertEqual(scan_loop._stop_signal, 15)
        self.assertTrue(scan_loop._stopping)

if __name__ == "__main__":
    unittest.main()
