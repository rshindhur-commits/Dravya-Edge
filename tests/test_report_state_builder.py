import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ui.cache.report_state_builder import (
    build_report_state_payload,
    write_report_state,
)


class ReportStateBuilderTests(unittest.TestCase):

    def test_builds_ready_report_state_payload(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            report = root / "daily_validation_report.html"
            scanner = root / "scanner_output_close.csv"
            report.write_text("<html></html>", encoding="utf-8")
            scanner.write_text("Symbol\nNVDA\n", encoding="utf-8")

            payload = build_report_state_payload(
                "2026-07-18",
                daily_report_path=report,
                root_report_path=root / "missing.html",
                scanner_path=scanner,
                scan_id="scan",
            )

        self.assertIn(payload["status"], {"READY", "STALE"})
        self.assertEqual(payload["metadata"]["scan_id"], "scan")
        self.assertTrue(payload["daily_report"]["exists"])
        self.assertEqual(payload["scan_id"], "scan")

    def test_write_report_state_writes_live_and_daily_json(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            live = root / "live_report_state.json"
            daily = root / "daily_report_state.json"

            with patch(
                "app.ui.cache.report_state_builder.live_path",
                return_value=live
            ), patch(
                "app.ui.cache.report_state_builder.daily_path",
                return_value=daily
            ):

                payload = write_report_state(
                    "2026-07-18",
                    scan_id="scan"
                )

            self.assertTrue(live.exists())
            self.assertTrue(daily.exists())
            self.assertEqual(payload["scan_id"], "scan")
            self.assertEqual(payload["metadata"]["scan_id"], "scan")


if __name__ == "__main__":

    unittest.main()