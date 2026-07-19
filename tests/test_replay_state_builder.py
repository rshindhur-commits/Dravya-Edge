import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.ui.cache.replay_state_builder import (
    build_replay_state_payload,
    write_replay_state,
)


class ReplayStateBuilderTests(unittest.TestCase):

    def test_builds_ready_replay_state_payload(self):

        payload = build_replay_state_payload(
            "2026-07-18",
            scanner_df=pd.DataFrame([
                {"Symbol": "NVDA"},
                {"Symbol": "MSFT"},
            ]),
            replay_df=pd.DataFrame([
                {"Symbol": "NVDA", "ENTRY_GATE_FAILURE_STAGE": "Risk"},
                {"Symbol": "MSFT", "ENTRY_GATE_FAILURE_STAGE": "Entry"},
            ]),
            summary_df=pd.DataFrame([
                {
                    "Symbol": "NVDA",
                    "Closest Setup": "EMA_PULLBACK",
                    "Readiness": 82,
                    "Gate Failure Stage": "Risk",
                    "First Failed Rule": "RR",
                }
            ]),
            scanner_mtime="2026-07-18T10:00:00+00:00",
            replay_mtime="2026-07-18T10:01:00+00:00",
            scan_id="scan",
        )

        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["metadata"]["scan_id"], "scan")
        self.assertEqual(payload["coverage_pct"], 100)
        self.assertEqual(payload["blockers"][0]["blocker"], "Risk")
        self.assertEqual(payload["top_misses"][0]["Symbol"], "NVDA")

    def test_write_replay_state_writes_live_and_daily_json(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            live = root / "live_replay_state.json"
            daily = root / "daily_replay_state.json"

            with patch(
                "app.ui.cache.replay_state_builder._read_csv",
                return_value=pd.DataFrame()
            ), patch(
                "app.ui.cache.replay_state_builder._file_mtime",
                return_value=None
            ), patch(
                "app.ui.cache.replay_state_builder.live_path",
                return_value=live
            ), patch(
                "app.ui.cache.replay_state_builder.daily_path",
                return_value=daily
            ):

                payload = write_replay_state(
                    "2026-07-18",
                    scan_id="scan"
                )

            self.assertTrue(live.exists())
            self.assertTrue(daily.exists())
            self.assertEqual(payload["status"], "MISSING")
            self.assertEqual(payload["metadata"]["scan_id"], "scan")


if __name__ == "__main__":

    unittest.main()