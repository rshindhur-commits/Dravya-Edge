"""S2.5 -- tests for tools/check_strategy_version_approved.py, the CI gate
that converts the validation freeze into an actual build failure.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_strategy_version_approved as gate


class TestLoadApprovedVersions(unittest.TestCase):

    def test_missing_file_returns_empty_list(self):
        with patch.object(gate, "APPROVAL_FILE", Path("/nonexistent/approved.json")):
            self.assertEqual(gate.load_approved_versions(), [])

    def test_reads_the_approved_list_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approved.json"
            path.write_text(json.dumps({
                "approved": [
                    {"strategy_version": "aaa111", "approved_at": "2026-01-01", "note": "x"},
                    {"strategy_version": "bbb222", "approved_at": "2026-01-02", "note": "y"},
                ]
            }))

            with patch.object(gate, "APPROVAL_FILE", path):
                self.assertEqual(gate.load_approved_versions(), ["aaa111", "bbb222"])

    def test_empty_approved_list_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approved.json"
            path.write_text(json.dumps({"approved": []}))

            with patch.object(gate, "APPROVAL_FILE", path):
                self.assertEqual(gate.load_approved_versions(), [])


class TestMainExitCode(unittest.TestCase):

    def test_exits_zero_when_current_version_is_approved(self):
        with patch.object(
            gate, "strategy_version_manifest",
            return_value={"strategy_version": "current123", "logic_files": []}
        ), patch.object(gate, "load_approved_versions", return_value=["current123"]):

            with self.assertRaises(SystemExit) as ctx:
                gate.main()

            self.assertEqual(ctx.exception.code, 0)

    def test_exits_nonzero_when_current_version_is_not_approved(self):
        with patch.object(
            gate, "strategy_version_manifest",
            return_value={"strategy_version": "unapproved999", "logic_files": ["app/x.py"]}
        ), patch.object(gate, "load_approved_versions", return_value=["current123"]):

            with self.assertRaises(SystemExit) as ctx:
                gate.main()

            self.assertEqual(ctx.exception.code, 1)

    def test_the_real_checked_in_approval_file_currently_approves_the_real_current_version(self):
        # This is the actual state of the repo right now: the seeded
        # baseline in app/versioning/approved_strategy_versions.json must
        # match what compute_strategy_version() returns for the code as
        # committed. If this ever fails, either the baseline is stale or
        # someone changed V1 logic without going through the gate.
        with self.assertRaises(SystemExit) as ctx:
            gate.main()

        self.assertEqual(
            ctx.exception.code, 0,
            "the real approved_strategy_versions.json is out of date -- "
            "add the current strategy_version to it"
        )


if __name__ == "__main__":
    unittest.main()
