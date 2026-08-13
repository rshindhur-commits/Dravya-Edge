import os
import unittest
from unittest.mock import patch

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

    def test_a_setup_below_the_bar_no_longer_refuses_the_trade(self):
        """The score does not predict outcomes, so it stopped blocking.

        Measured over 244 resolved candidates on 2026-08-12 the bands run
        inverted -- <50 wins 33.8%, 70+ wins 21.3% -- with median RR flat across
        them, so target distance does not explain it. The cost was concrete: SPCX
        was refused at setup 68 against a bar of 81 and its contract returned
        +22.26%.

        The threshold is still recorded. Removing the refusal must not remove the
        ability to measure the bar, or the next person cannot check this.
        """

        diagnostics = build_entry_gate_diagnostics(
            _row(68),
            EntryGateConfig(min_setup_percent=70.0),
            mode="paper"
        )

        self.assertEqual(diagnostics["setup"], 68)
        self.assertEqual(diagnostics["min_setup"], 70.0, "the bar stays visible")
        self.assertEqual(diagnostics["result"], "PASS")
        self.assertIsNone(diagnostics["failure"])

    def test_the_setup_gate_still_blocks_when_switched_back_on(self):
        """SETUP_GATE_ENABLED=true restores the old refusal exactly."""

        with patch.dict(os.environ, {"SETUP_GATE_ENABLED": "true"}):

            diagnostics = build_entry_gate_diagnostics(
                _row(68),
                EntryGateConfig(min_setup_percent=70.0),
                mode="paper"
            )

        self.assertEqual(diagnostics["result"], "FAIL")
        self.assertEqual(diagnostics["failure"], "SETUP_BELOW_THRESHOLD")


if __name__ == "__main__":

    unittest.main()