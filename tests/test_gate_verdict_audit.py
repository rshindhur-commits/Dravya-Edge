"""Every scanner entry recommendation must carry a recorded execution verdict.

On 2026-07-29 three PUT candidates reached ENTER_PAPER, never opened, and left no
row in auto_paper_decisions.csv. run_auto_paper_entries() accounts for every
actionable row on every return path, but it is only reachable from scan
finalization -- so when it does not run, nothing is recorded and the miss cannot
be diagnosed afterwards.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.runtime.paper_automation import (
    NO_GATE_VERDICT,
    audit_unrecorded_entry_recommendations,
)


def _row(symbol, action="ENTER_PAPER", **extra):
    base = {"Symbol": symbol, "Action Status": action}
    base.update(extra)
    return base


class GateVerdictAuditTests(unittest.TestCase):

    def test_records_a_verdict_when_the_gate_never_ran(self):

        df = pd.DataFrame([_row("NVDA"), _row("NFLX")])

        with patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision"
        ) as record:

            unrecorded = audit_unrecorded_entry_recommendations(df, {})

        self.assertEqual(unrecorded, ["NVDA", "NFLX"])
        self.assertEqual(record.call_count, 2)
        self.assertEqual(record.call_args.args[1], "SKIPPED")
        self.assertEqual(record.call_args.args[2], NO_GATE_VERDICT)

        self.assertEqual(list(df["Execution Outcome"]), ["SKIPPED", "SKIPPED"])
        self.assertEqual(list(df["Execution Reason"]), [NO_GATE_VERDICT] * 2)
        self.assertEqual(list(df["Trade Status"]), ["NOT_CREATED"] * 2)

    def test_leaves_rows_the_gate_already_judged_alone(self):

        df = pd.DataFrame([
            _row("NVDA", **{"Execution Outcome": "OPENED"}),
            _row("NFLX", **{"Execution Outcome": "BLOCKED"}),
        ])

        with patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision"
        ) as record:

            unrecorded = audit_unrecorded_entry_recommendations(df, {})

        self.assertEqual(unrecorded, [])
        record.assert_not_called()
        self.assertEqual(list(df["Execution Outcome"]), ["OPENED", "BLOCKED"])

    def test_audits_only_the_rows_without_a_verdict(self):

        df = pd.DataFrame([
            _row("NVDA", **{"Execution Outcome": "OPENED"}),
            _row("NFLX", **{"Execution Outcome": None}),
            _row("ORCL", **{"Execution Outcome": "   "}),
        ])

        with patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision"
        ):
            unrecorded = audit_unrecorded_entry_recommendations(df, {})

        self.assertEqual(unrecorded, ["NFLX", "ORCL"])

    def test_ignores_non_actionable_rows(self):

        df = pd.DataFrame([_row("NVDA", action="WAIT"), _row("NFLX", action="AVOID")])

        with patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision"
        ) as record:

            self.assertEqual(audit_unrecorded_entry_recommendations(df, {}), [])

        record.assert_not_called()

    def test_tolerates_empty_and_malformed_input(self):

        self.assertEqual(audit_unrecorded_entry_recommendations(None, {}), [])
        self.assertEqual(audit_unrecorded_entry_recommendations(pd.DataFrame(), {}), [])
        self.assertEqual(
            audit_unrecorded_entry_recommendations(pd.DataFrame([{"Symbol": "NVDA"}]), {}),
            [],
        )

    def test_a_recording_failure_does_not_stop_the_audit(self):

        df = pd.DataFrame([_row("NVDA"), _row("NFLX")])

        with patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision",
            side_effect=RuntimeError("db down"),
        ):
            unrecorded = audit_unrecorded_entry_recommendations(df, {})

        self.assertEqual(unrecorded, ["NVDA", "NFLX"])
        self.assertEqual(list(df["Execution Reason"]), [NO_GATE_VERDICT] * 2)


if __name__ == "__main__":
    unittest.main()
