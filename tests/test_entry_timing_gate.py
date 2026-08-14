"""The entry-timing score runs inverted, so it can now refuse a trade.

`evaluate_entry_timing` describes itself as observational and was never checked
against outcomes. Measured over 122 resolved candidates on 2026-08-13:

    score   0-55   n=52   EV -0.099   median RR 1.80
    score  55-70   n=46   EV -0.251   median RR 2.00
    score 70-101   n=24   EV -0.521   median RR 2.00

Median RR is flat across the bands, so it is not target distance. It survives
inside both RR bands, in both directions, and the GOOD grade's bootstrap CI
[-1.000, -0.116] excludes zero. Dropping the five best outcomes moves the >=70
band from -0.521 to -1.000 -- it strengthens without its outliers.

Off by default: 122 candidates justifies a switch, not a hard-coded rule. These
cover both states, because a gate nobody can turn off is how the setup score
went unmeasured for weeks.
"""

import os
import unittest
from unittest.mock import patch

from app.gates.entry_gate import (
    EntryGateConfig,
    build_entry_gate_diagnostics,
    evaluate_entry_gate,
)


def _row(timing_score):

    return {
        "Action Status": "ENTER_PAPER",
        "Candidate Direction": "CALL",
        "Candidate Entry Price": 100,
        "Candidate Stop Price": 98,
        "Candidate Target Price": 106,
        "Setup %": 75,
        "Candidate RR": 3.0,
        "Option Quality Score": 90,
        "Option Quote Freshness": "LIVE_QUOTE",
        "Option Spread %": 1.5,
        "Affordable": True,
        "Entry Timing Score": timing_score,
    }


CONFIG = EntryGateConfig(min_setup_percent=70.0)


class TimingGateOffTests(unittest.TestCase):

    def test_a_high_timing_score_passes_while_the_gate_is_off(self):
        """Default must change nothing until the switch is thrown."""

        allowed, reason = evaluate_entry_gate(_row(95), CONFIG, mode="paper")

        self.assertTrue(allowed, f"refused with the gate off: {reason}")


class TimingGateOnTests(unittest.TestCase):

    def setUp(self):
        self._env = patch.dict(
            os.environ, {"ENTRY_TIMING_GATE_ENABLED": "true"}
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_a_score_at_the_threshold_is_refused(self):
        """PLTR scored 83.66 on 2026-08-13 and closed -0.44R."""

        allowed, reason = evaluate_entry_gate(_row(83.66), CONFIG, mode="paper")

        self.assertFalse(allowed)
        self.assertEqual(reason, "ENTRY_TIMING_TOO_EARLY")

    def test_the_boundary_is_inclusive(self):

        allowed, _ = evaluate_entry_gate(_row(70.0), CONFIG, mode="paper")
        self.assertFalse(allowed, "70 is inside the losing band")

    def test_a_low_timing_score_still_passes(self):
        """The gate refuses early entries, not every entry."""

        allowed, reason = evaluate_entry_gate(_row(40), CONFIG, mode="paper")

        self.assertTrue(allowed, f"refused a low-score candidate: {reason}")

    def test_a_missing_score_never_refuses(self):
        """Absent telemetry must not silently stop trading."""

        for missing in (None, "", "nan", "none"):
            row = _row(0)
            row["Entry Timing Score"] = missing

            allowed, reason = evaluate_entry_gate(row, CONFIG, mode="paper")

            self.assertTrue(
                allowed, f"{missing!r} refused the trade: {reason}"
            )

    def test_the_threshold_is_tunable(self):

        with patch.dict(os.environ, {"ENTRY_TIMING_MAX_SCORE": "90"}):

            allowed, _ = evaluate_entry_gate(_row(83.66), CONFIG, mode="paper")
            self.assertTrue(allowed, "83.66 should pass a bar of 90")

            allowed, reason = evaluate_entry_gate(_row(92), CONFIG, mode="paper")
            self.assertFalse(allowed)
            self.assertEqual(reason, "ENTRY_TIMING_TOO_EARLY")

    def test_diagnostics_report_the_same_verdict(self):
        """The two enforcement sites must not disagree."""

        diagnostics = build_entry_gate_diagnostics(
            _row(83.66), CONFIG, mode="paper"
        )

        self.assertEqual(diagnostics["result"], "FAIL")
        self.assertEqual(diagnostics["failure"], "ENTRY_TIMING_TOO_EARLY")


if __name__ == "__main__":
    unittest.main()
