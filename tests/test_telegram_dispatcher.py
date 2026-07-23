import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.alerts.telegram_alerts import TelegramDeliveryError
from app.runtime.telegram_dispatcher import (
    dispatch_telegram_message,
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

    def test_queued_mode_submits_critical_job(self):

        with patch.dict("os.environ", {"TELEGRAM_DISPATCH_MODE": "QUEUED"}, clear=False), patch(
            "app.runtime.telegram_dispatcher.get_runtime_scheduler"
        ) as scheduler_factory, patch(
            "app.runtime.telegram_dispatcher._append_jsonl"
        ) as append_jsonl:

            scheduler = scheduler_factory.return_value
            scheduler.submit_critical.return_value = "job-id"
            result = dispatch_telegram_message(
                lambda message: None,
                "hello",
                scan_id="scan",
                dispatch_metadata={"alert_key": "abc"}
            )

        self.assertEqual(result["job_id"], "job-id")
        self.assertTrue(result["queued"])
        scheduler.submit_critical.assert_called_once()
        job = scheduler.submit_critical.call_args.args[0]
        self.assertEqual(job.priority.name, "CRITICAL")
        self.assertFalse(job.cancelable)
        self.assertEqual(job.scan_id, "scan")
        append_jsonl.assert_called_once()
        self.assertEqual(append_jsonl.call_args.args[0], "telegram_dispatch_queue.jsonl")
        self.assertEqual(append_jsonl.call_args.args[1]["message"], "hello")
        self.assertEqual(append_jsonl.call_args.args[1]["dispatch_metadata"]["alert_key"], "abc")

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
                "app.runtime.telegram_dispatcher.get_runtime_scheduler"
            ) as scheduler_factory:

                scheduler_factory.return_value.submit_critical.return_value = "new-job"
                recovered = recover_pending_telegram_dispatches(lambda message: None)

        self.assertEqual(recovered[0]["original_job_id"], "old")
        self.assertEqual(recovered[0]["result"]["job_id"], "new-job")


if __name__ == "__main__":

    unittest.main()