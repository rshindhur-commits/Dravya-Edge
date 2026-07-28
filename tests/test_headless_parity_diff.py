"""S2.4 -- tests for tools/headless_parity_diff.py.

Uses synthetic state dicts, not real Neon/paper data -- this validates the
diffing mechanism itself, which is a prerequisite for the actual multi-session
parallel run (a real elapsed-time procedure this test suite cannot fabricate;
see docs/specs/S2.4-parallel-run-procedure.md).
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from headless_parity_diff import diff_states, format_report, is_clean, load_state


CLOSED_TRADE = {
    "symbol": "NVDA",
    "direction": "CALL",
    "status": "CLOSED",
    "entry_price": 196.13,
    "stop_loss": 195.42,
    "take_profit": 197.77,
    "option_ticker": "O:NVDA260821C00205000",
    "close_price": 197.77,
    "exit_reason": "HARD_TARGET",
    "r_multiple": 2.31,
    "outcome": "TARGET_HIT",
    "holding_profile": "INTRADAY",
    "closed_at_et": "2026-07-27T15:30:00-04:00",
}


def _trade(**overrides):
    trade = dict(CLOSED_TRADE)
    trade.update(overrides)
    return trade


class TestZeroDiff(unittest.TestCase):

    def test_identical_states_produce_a_clean_report(self):
        live = {"NVDA_1": _trade()}
        shadow = {"NVDA_1": _trade()}

        report = diff_states(live, shadow)

        self.assertTrue(is_clean(report))
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["only_in_live"], [])
        self.assertEqual(report["only_in_shadow"], [])
        self.assertEqual(report["mismatched"], {})

    def test_empty_states_are_clean(self):
        self.assertTrue(is_clean(diff_states({}, {})))

    def test_close_timestamps_within_tolerance_do_not_count_as_a_diff(self):
        live = {"NVDA_1": _trade(closed_at_et="2026-07-27T15:30:00-04:00")}
        shadow = {"NVDA_1": _trade(closed_at_et="2026-07-27T15:31:30-04:00")}

        self.assertTrue(is_clean(diff_states(live, shadow)))


class TestEachDivergenceCategoryIsCaught(unittest.TestCase):

    def test_trade_only_in_live_is_reported(self):
        live = {"NVDA_1": _trade()}
        shadow = {}

        report = diff_states(live, shadow)

        self.assertFalse(is_clean(report))
        self.assertEqual(report["only_in_live"], ["NVDA_1"])
        self.assertEqual(report["only_in_shadow"], [])

    def test_trade_only_in_shadow_is_reported(self):
        live = {}
        shadow = {"NVDA_1": _trade()}

        report = diff_states(live, shadow)

        self.assertFalse(is_clean(report))
        self.assertEqual(report["only_in_shadow"], ["NVDA_1"])

    def test_mismatched_r_multiple_is_reported(self):
        live = {"NVDA_1": _trade(r_multiple=2.31)}
        shadow = {"NVDA_1": _trade(r_multiple=1.17)}

        report = diff_states(live, shadow)

        self.assertFalse(is_clean(report))
        fields = [m[0] for m in report["mismatched"]["NVDA_1"]]
        self.assertIn("r_multiple", fields)

    def test_mismatched_exit_reason_is_reported(self):
        live = {"NVDA_1": _trade(exit_reason="HARD_TARGET")}
        shadow = {"NVDA_1": _trade(exit_reason="EMA")}

        report = diff_states(live, shadow)

        fields = [m[0] for m in report["mismatched"]["NVDA_1"]]
        self.assertIn("exit_reason", fields)

    def test_close_timestamp_gap_beyond_tolerance_is_reported(self):
        live = {"NVDA_1": _trade(closed_at_et="2026-07-27T15:30:00-04:00")}
        shadow = {"NVDA_1": _trade(closed_at_et="2026-07-27T15:45:00-04:00")}

        report = diff_states(live, shadow)

        self.assertFalse(is_clean(report))
        fields = [m[0] for m in report["mismatched"]["NVDA_1"]]
        self.assertTrue(any("closed_at" in f for f in fields))

    def test_multiple_mismatched_fields_all_reported_together(self):
        live = {"NVDA_1": _trade(r_multiple=2.31, outcome="TARGET_HIT")}
        shadow = {"NVDA_1": _trade(r_multiple=1.17, outcome="WIN")}

        report = diff_states(live, shadow)

        fields = [m[0] for m in report["mismatched"]["NVDA_1"]]
        self.assertIn("r_multiple", fields)
        self.assertIn("outcome", fields)

    def test_a_matching_trade_alongside_a_mismatched_one_reports_only_the_bad_one(self):
        live = {
            "NVDA_1": _trade(),
            "AMD_1": _trade(symbol="AMD", r_multiple=0.5),
        }
        shadow = {
            "NVDA_1": _trade(),
            "AMD_1": _trade(symbol="AMD", r_multiple=-0.2),
        }

        report = diff_states(live, shadow)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(list(report["mismatched"].keys()), ["AMD_1"])


class TestReportFormattingAndExitCode(unittest.TestCase):

    def test_format_report_mentions_every_category(self):
        live = {"A": _trade(), "B": _trade(symbol="X")}
        shadow = {"B": _trade(symbol="X", r_multiple=9.9), "C": _trade(symbol="Y")}

        report = diff_states(live, shadow)
        text = format_report(report)

        self.assertIn("only_in_live", text)
        self.assertIn("only_in_shadow", text)
        self.assertIn("A", text)
        self.assertIn("C", text)
        self.assertIn("r_multiple", text)

    def test_load_state_missing_file_returns_empty_dict_not_an_error(self):
        self.assertEqual(load_state("/nonexistent/path/state.json"), {})

    def test_load_state_reads_real_json(self):
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
            json.dump({"NVDA_1": CLOSED_TRADE}, handle)
            path = handle.name

        try:
            state = load_state(path)
            self.assertIn("NVDA_1", state)
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
