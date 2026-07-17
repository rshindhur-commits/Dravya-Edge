import unittest

from app.gates.entry_gate import (
    EntryGateConfig,
    build_entry_gate_diagnostics
)


def _row(setup):

    return {
        "Action Status": "ENTER_PAPER",
        "Candidate Direction": "CALL",
        "Candidate Entry Price": 100,
        "Candidate Stop Price": 98,
        "Candidate Target Price": 106,
        "Setup %": setup,
        "Candidate RR": 3.0,
        "Option Quality Score": 90,
        "Option Quote Freshness": "LIVE_QUOTE",
        "Option Spread %": 2.5,
        "Affordable": True,
    }


class EntryGateDiagnosticsTests(unittest.TestCase):

    def test_records_setup_threshold_pass(self):

        diagnostics = build_entry_gate_diagnostics(
            _row(71),
            EntryGateConfig(min_setup_percent=70.0),
            mode="paper"
        )

        self.assertEqual(diagnostics["setup"], 71)
        self.assertEqual(diagnostics["min_setup"], 70.0)
        self.assertEqual(diagnostics["result"], "PASS")
        self.assertIsNone(diagnostics["failure"])

    def test_records_setup_threshold_failure(self):

        diagnostics = build_entry_gate_diagnostics(
            _row(68),
            EntryGateConfig(min_setup_percent=70.0),
            mode="paper"
        )

        self.assertEqual(diagnostics["setup"], 68)
        self.assertEqual(diagnostics["min_setup"], 70.0)
        self.assertEqual(diagnostics["result"], "FAIL")
        self.assertEqual(diagnostics["failure"], "SETUP_BELOW_THRESHOLD")


if __name__ == "__main__":

    unittest.main()