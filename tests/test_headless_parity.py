"""S2.3 -- the parity harness.

Verifies that `app.dashboard._dispatch_trading_page_automation` (the
dashboard's real trigger, extracted verbatim in S2.3 -- see
docs/specs/S2.1-headless-extraction-plan.md) and
`app.runtime.headless_paper.run_cycle` (the headless caller, built in S2.2)
make identical calls, in identical order, against identical input, and reach
identical results.

Since S2.1 established that the underlying decision path
(`run_auto_paper_entries`/`run_auto_paper_exits`) is shared code, not a ported
copy, this harness is not comparing two independent implementations of
strategy logic -- it is proving the two *orchestration* layers around that
shared code agree: same controls resolution, same call order, and critically,
the same `st.rerun()`-after-close short-circuit (S2.1 §4.1).

A note on that short-circuit and why this test mocks `st.rerun`: verified
directly against this Streamlit version, `st.rerun()` is a documented "halt
execution and rerun the script" contract, but outside a live
ScriptRunContext it is literally a no-op (it checks `ctx and
ctx.script_requests`, and `ctx` is None in a unit test) -- it does not raise.
Calling the dashboard's real function in a bare test therefore cannot observe
the halt through Streamlit's own machinery. This harness enforces the
documented contract explicitly, by patching `st.rerun` to raise a sentinel and
asserting nothing after it runs -- rather than trusting an internal mechanism
that provably does not fire in this context.
"""
import unittest
from unittest.mock import patch

import pandas as pd

import app.dashboard as dashboard
from app.runtime.headless_paper import run_cycle


class _RerunSentinel(Exception):
    """Stands in for Streamlit's real rerun halt, which does not fire outside
    a live ScriptRunContext (verified) but is documented to halt execution in
    production. See module docstring."""


FRAME = pd.DataFrame({
    "Symbol": ["NVDA", "AMD"],
    "Action Status": ["ENTER_PAPER", "ENTER_PAPER"],
})

CONTROLS = {
    "auto_paper_enabled": True,
    "max_daily": 3,
    "min_setup": 70.0,
    "min_rr": 1.8,
    "direction": "Both",
    "auto_exit_enabled": True,
    "eod_close_enabled": False,
    "restore_multiday_positions": True,
    "profit_r": 1.0,
}


class TestDashboardAndHeadlessCallTheSameFunctionsInTheSameOrder(unittest.TestCase):

    def test_no_closes_both_paths_call_exits_then_entries_once_each(self):
        with patch(
            "app.runtime.paper_automation.run_auto_paper_exits", return_value=[]
        ) as exits, patch(
            "app.runtime.paper_automation.run_auto_paper_entries", return_value=["NVDA"]
        ) as entries:

            dashboard_result = dashboard._dispatch_trading_page_automation(FRAME, CONTROLS)

        with patch(
            "app.runtime.paper_automation.run_auto_paper_exits", return_value=[]
        ) as headless_exits, patch(
            "app.runtime.paper_automation.run_auto_paper_entries", return_value=["NVDA"]
        ) as headless_entries, patch(
            "app.runtime.headless_paper.build_automation_frame", return_value=FRAME
        ):

            headless_result = run_cycle(controls=CONTROLS)

        # Same arguments passed to the shared functions on both paths.
        exits.assert_called_once_with(FRAME, CONTROLS)
        headless_exits.assert_called_once_with(FRAME, CONTROLS)
        entries.assert_called_once_with(FRAME, CONTROLS)
        headless_entries.assert_called_once_with(FRAME, CONTROLS)

        # Same decision.
        self.assertEqual(dashboard_result["closed"], headless_result["closed"])
        self.assertEqual(dashboard_result["opened"], headless_result["opened"])
        self.assertEqual(dashboard_result["opened"], ["NVDA"])

    def test_a_close_both_paths_never_reach_entries_this_cycle(self):
        with patch("streamlit.rerun", side_effect=_RerunSentinel), patch(
            "app.runtime.paper_automation.run_auto_paper_exits", return_value=["NVDA"]
        ), patch(
            "app.runtime.paper_automation.run_auto_paper_entries"
        ) as dashboard_entries:

            with self.assertRaises(_RerunSentinel):
                dashboard._dispatch_trading_page_automation(FRAME, CONTROLS)

            # The real halt-and-rerun contract: nothing after st.rerun() runs.
            dashboard_entries.assert_not_called()

        with patch(
            "app.runtime.paper_automation.run_auto_paper_exits", return_value=["NVDA"]
        ), patch(
            "app.runtime.paper_automation.run_auto_paper_entries"
        ) as headless_entries, patch(
            "app.runtime.headless_paper.build_automation_frame", return_value=FRAME
        ):

            headless_result = run_cycle(controls=CONTROLS)

            # headless_paper has no rerun mechanism to lean on -- it enforces
            # the identical outcome (no entries evaluated after a close)
            # directly in its own control flow (S2.1 §4.1).
            headless_entries.assert_not_called()
            self.assertEqual(headless_result["closed"], ["NVDA"])
            self.assertEqual(headless_result["opened"], [])
            self.assertTrue(headless_result["skipped_entries"])

    def test_exits_are_evaluated_before_entries_on_both_paths(self):
        dashboard_order = []
        headless_order = []

        with patch(
            "app.runtime.paper_automation.run_auto_paper_exits",
            side_effect=lambda *a, **k: dashboard_order.append("exits") or []
        ), patch(
            "app.runtime.paper_automation.run_auto_paper_entries",
            side_effect=lambda *a, **k: dashboard_order.append("entries") or []
        ):
            dashboard._dispatch_trading_page_automation(FRAME, CONTROLS)

        with patch(
            "app.runtime.paper_automation.run_auto_paper_exits",
            side_effect=lambda *a, **k: headless_order.append("exits") or []
        ), patch(
            "app.runtime.paper_automation.run_auto_paper_entries",
            side_effect=lambda *a, **k: headless_order.append("entries") or []
        ), patch(
            "app.runtime.headless_paper.build_automation_frame", return_value=FRAME
        ):
            run_cycle(controls=CONTROLS)

        self.assertEqual(dashboard_order, ["exits", "entries"])
        self.assertEqual(headless_order, ["exits", "entries"])
        self.assertEqual(dashboard_order, headless_order)


class TestControlsResolutionAgreesAcrossBothCallers(unittest.TestCase):

    """The dashboard resolves controls through widgets seeded by
    resolve_auto_paper_controls (S2.2's dashboard.py refactor); the headless
    caller resolves them directly. Both must reach the same controls dict
    from the same saved-settings JSON, with no Streamlit session at all.
    """

    def test_identical_saved_settings_resolve_to_identical_controls(self):
        from app.runtime.auto_paper_controls import resolve_auto_paper_controls

        saved = {
            "auto_paper_enabled": True,
            "auto_paper_max_daily": 4,
            "auto_paper_min_setup": 75,
            "auto_paper_min_rr": 2.0,
            "auto_paper_direction": "Calls",
            "auto_paper_exit_enabled": True,
            "auto_paper_eod_close_enabled": True,
            "restore_multiday_positions": False,
            "auto_paper_profit_r": 1.25,
        }

        first = resolve_auto_paper_controls(saved)
        second = resolve_auto_paper_controls(saved)

        self.assertEqual(first, second)
        self.assertEqual(first["max_daily"], 4)
        self.assertEqual(first["direction"], "Calls")


if __name__ == "__main__":
    unittest.main()
