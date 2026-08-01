import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.alerts.telegram_alerts import TelegramDeliveryError
from app.runtime.telegram_dispatcher import (
    dispatch_telegram_message,
    drain_telegram_dispatches,
    recover_pending_telegram_dispatches,
    telegram_dispatch_mode,
)


class TelegramDispatcherTests(unittest.TestCase):

    def test_direct_mode_sends_immediately(self):

        sent = []
        callbacks = []

        def send(message):

            sent.append(message)
            return "ok"

        with patch.dict("os.environ", {"TELEGRAM_DISPATCH_MODE": "DIRECT"}, clear=False), patch(
            "app.runtime.telegram_dispatcher._append_jsonl"
        ) as append_jsonl:

            result = dispatch_telegram_message(
                send,
                "hello",
                after_success=lambda result: callbacks.append(result)
            )

        self.assertEqual(result, "ok")
        self.assertEqual(sent, ["hello"])
        self.assertEqual(callbacks, ["ok"])
        self.assertEqual(append_jsonl.call_count, 2)

    def test_queued_mode_hands_off_to_the_dedicated_sender(self):

        with patch.dict("os.environ", {"TELEGRAM_DISPATCH_MODE": "QUEUED"}, clear=False), patch(
            "app.runtime.telegram_dispatcher.get_telegram_sender"
        ) as sender_factory, patch(
            "app.runtime.telegram_dispatcher._append_jsonl"
        ) as append_jsonl:

            result = dispatch_telegram_message(
                lambda message: None,
                "hello",
                scan_id="scan",
                dispatch_metadata={"alert_key": "abc"}
            )

        self.assertTrue(result["queued"])
        self.assertTrue(result["job_id"])
        sender_factory.return_value.submit.assert_called_once()

        # The durable queue row is written before hand-off and carries the same
        # job id the sender was given, so recovery can match the two.
        append_jsonl.assert_called_once()
        self.assertEqual(append_jsonl.call_args.args[0], "telegram_dispatch_queue.jsonl")
        self.assertEqual(append_jsonl.call_args.args[1]["message"], "hello")
        self.assertEqual(append_jsonl.call_args.args[1]["job_id"], result["job_id"])
        self.assertEqual(append_jsonl.call_args.args[1]["dispatch_metadata"]["alert_key"], "abc")
        self.assertEqual(sender_factory.return_value.submit.call_args.args[0], result["job_id"])

    def test_queued_mode_never_touches_the_shared_runtime_scheduler(self):
        """Regression guard for the priority inversion fixed on 2026-07-31.

        Queued sends used to be submitted back to the shared runtime scheduler at
        CRITICAL priority. That scheduler has exactly one worker thread, and the
        dispatch call itself runs inside `finalize_scan_outputs` -- already a job
        on that worker. So the send could not start until finalize returned, i.e.
        after all of `_persist_scan_outputs`. Sends must not go near it.
        """

        with patch.dict("os.environ", {"TELEGRAM_DISPATCH_MODE": "QUEUED"}, clear=False), patch(
            "app.runtime.telegram_dispatcher.get_telegram_sender"
        ), patch(
            "app.runtime.telegram_dispatcher._append_jsonl"
        ), patch(
            "app.runtime.telegram_dispatcher.get_runtime_scheduler"
        ) as scheduler_factory:

            dispatch_telegram_message(lambda message: None, "hello", scan_id="scan")

        scheduler_factory.assert_not_called()

    def test_queued_send_is_delivered_off_the_calling_thread(self):
        """End-to-end: the real sender thread delivers without the caller blocking."""

        import threading

        sent = threading.Event()
        sending_thread = {}

        def send(message):

            sending_thread["name"] = threading.current_thread().name
            sent.set()
            return {"telegram_message_id": 1}

        with patch.dict("os.environ", {"TELEGRAM_DISPATCH_MODE": "QUEUED"}, clear=False), patch(
            "app.runtime.telegram_dispatcher._append_jsonl"
        ), patch(
            "app.runtime.telegram_dispatcher._record_dispatch_db"
        ):

            result = dispatch_telegram_message(send, "hello")

            self.assertTrue(result["queued"])
            self.assertTrue(sent.wait(timeout=5), "queued send never executed")
            self.assertTrue(drain_telegram_dispatches(timeout=5))

        self.assertNotEqual(sending_thread["name"], threading.current_thread().name)
        self.assertEqual(sending_thread["name"], "telegram-dispatcher")

    def test_direct_mode_records_failed_attempt(self):

        def send(_message):

            raise RuntimeError("boom")

        errors = []

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_DISPATCH_MODE": "DIRECT",
                "TELEGRAM_DISPATCH_RETRIES": "0",
            },
            clear=False
        ), patch(
            "app.runtime.telegram_dispatcher._append_jsonl"
        ) as append_jsonl:

            with self.assertRaises(RuntimeError):

                dispatch_telegram_message(
                    send,
                    "hello",
                    on_error=errors.append
                )

        self.assertEqual(len(errors), 1)
        self.assertEqual(append_jsonl.call_count, 2)
        self.assertEqual(append_jsonl.call_args_list[-1].args[1]["event"], "FAILED")

    def test_failed_audit_includes_telegram_response_and_context(self):

        def send(_message):

            raise TelegramDeliveryError(
                400,
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: chat not found",
                }
            )

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_DISPATCH_MODE": "DIRECT",
                "TELEGRAM_DISPATCH_RETRIES": "0",
            },
            clear=False,
        ), patch(
            "app.runtime.telegram_dispatcher._append_jsonl"
        ) as append_jsonl, patch(
            "app.runtime.telegram_dispatcher._record_dispatch_db"
        ):

            with self.assertRaises(TelegramDeliveryError):

                dispatch_telegram_message(
                    send,
                    "hello",
                    scan_id="2026-07-23_094500",
                    dispatch_metadata={
                        "metadata": {
                            "symbol": "NVDA",
                            "direction": "CALL",
                            "candidate_key": "NVDA_LONG_17",
                            "message_type": "SCANNER_ENTRY",
                            "decision": "ENTER_PAPER",
                        }
                    },
                )

        failed_audit = append_jsonl.call_args_list[-1].args[1]
        self.assertEqual(failed_audit["event"], "FAILED")
        self.assertEqual(failed_audit["scan_id"], "2026-07-23_094500")
        self.assertEqual(failed_audit["symbol"], "NVDA")
        self.assertEqual(failed_audit["candidate_key"], "NVDA_LONG_17")
        self.assertEqual(
            failed_audit["telegram_response"]["description"],
            "Bad Request: chat not found",
        )
        self.assertEqual(failed_audit["message_length"], 5)

    def test_default_mode_is_direct(self):

        with patch.dict("os.environ", {}, clear=True):

            self.assertEqual(telegram_dispatch_mode(), "DIRECT")

    def test_recover_pending_dispatches_resubmits_unsent_queue_rows(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            live_dir = Path(temp_dir)

            def live_path(filename):

                return live_dir / filename

            queue_path = live_path("telegram_dispatch_queue.jsonl")
            queue_path.write_text(
                '{"event":"QUEUED","job_id":"old","name":"send_telegram_alert","scan_id":"scan","message":"hello"}\n',
                encoding="utf-8"
            )
            live_path("telegram_dispatch_audit.jsonl").write_text("", encoding="utf-8")

            with patch.dict(
                "os.environ",
                {"TELEGRAM_DISPATCH_MODE": "QUEUED"},
                clear=False
            ), patch(
                "app.runtime.telegram_dispatcher.live_path",
                side_effect=live_path
            ), patch(
                "app.runtime.telegram_dispatcher.get_telegram_sender"
            ) as sender_factory:

                recovered = recover_pending_telegram_dispatches(lambda message: None)

        self.assertEqual(recovered[0]["original_job_id"], "old")
        self.assertTrue(recovered[0]["result"]["queued"])
        self.assertNotEqual(recovered[0]["result"]["job_id"], "old")
        sender_factory.return_value.submit.assert_called_once()


if __name__ == "__main__":

    unittest.main()