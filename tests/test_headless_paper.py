"""S2.2 tests.

These NEVER call the real `run_auto_paper_entries`/`run_auto_paper_exits` or
touch real market data / Telegram -- everything that could have a live side
effect is mocked. `TELEGRAM_ALERTS_ENABLED=true` in this repo's `.env`, so a
live call is never an acceptable way to test this module.
"""
import os
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from app.runtime.auto_paper_controls import resolve_auto_paper_controls
from app.runtime.headless_paper import build_automation_frame, main, run_cycle


class TestControlsResolverMatchesOldDashboardLiterals(unittest.TestCase):

    """Pins the exact fallback values that used to be hardcoded inline in
    dashboard.py::_auto_refresh_defaults (S2.1 §3.1). If these ever drift from
    what the dashboard resolves, the two callers have silently diverged.
    """

    def test_resolves_documented_defaults_when_no_settings_file_exists(self):
        controls = resolve_auto_paper_controls({})

        self.assertEqual(controls, {
            "auto_paper_enabled": True,
            "max_daily": 3,
            "min_setup": 70.0,
            "min_rr": 1.8,
            "direction": "Both",
            "auto_exit_enabled": True,
            "eod_close_enabled": False,
            "restore_multiday_positions": True,
            "profit_r": 1.0,
        })

    def test_saved_settings_override_every_default(self):
        saved = {
            "auto_paper_enabled": False,
            "auto_paper_max_daily": 5,
            "auto_paper_min_setup": 80,
            "auto_paper_min_rr": 2.2,
            "auto_paper_direction": "Calls",
            "auto_paper_exit_enabled": False,
            "auto_paper_eod_close_enabled": True,
            "restore_multiday_positions": False,
            "auto_paper_profit_r": 1.5,
        }

        controls = resolve_auto_paper_controls(saved)

        self.assertFalse(controls["auto_paper_enabled"])
        self.assertEqual(controls["max_daily"], 5)
        self.assertEqual(controls["direction"], "Calls")
        self.assertTrue(controls["eod_close_enabled"])
        self.assertFalse(controls["restore_multiday_positions"])
        self.assertEqual(controls["profit_r"], 1.5)

    def test_string_booleans_from_json_are_coerced(self):
        controls = resolve_auto_paper_controls({
            "auto_paper_eod_close_enabled": "true",
            "restore_multiday_positions": "false",
        })

        self.assertTrue(controls["eod_close_enabled"])
        self.assertFalse(controls["restore_multiday_positions"])

    def test_env_var_used_only_when_settings_file_omits_the_key(self):
        with patch.dict(os.environ, {"AUTO_PAPER_ENABLED": "false"}):
            self.assertFalse(resolve_auto_paper_controls({})["auto_paper_enabled"])
            self.assertTrue(
                resolve_auto_paper_controls({"auto_paper_enabled": True})["auto_paper_enabled"]
            )


class TestBuildAutomationFrame(unittest.TestCase):

    def test_empty_scanner_output_returns_empty_frame_without_calling_enrichments(self):
        with patch("app.dashboard._load_scanner_output", return_value=pd.DataFrame()), \
             patch("app.dashboard._sync_suggested_trades") as sync, \
             patch("app.dashboard._add_paper_trade_opened") as opened, \
             patch("app.dashboard._add_real_trade_readiness") as ready, \
             patch("app.dashboard._enrich_with_suggestion_lifecycle") as lifecycle:

            result = build_automation_frame()

            self.assertTrue(result.empty)
            sync.assert_not_called()
            opened.assert_not_called()
            ready.assert_not_called()
            lifecycle.assert_not_called()

    def test_non_empty_frame_runs_all_four_enrichments_in_order(self):
        raw = pd.DataFrame({"Symbol": ["QQQ"]})
        calls = []

        def mark(name):
            def _inner(df):
                calls.append(name)
                return df
            return _inner

        with patch("app.dashboard._load_scanner_output", return_value=raw), \
             patch("app.dashboard._sync_suggested_trades", side_effect=mark("sync")), \
             patch("app.dashboard._add_paper_trade_opened", side_effect=mark("opened")), \
             patch("app.dashboard._add_real_trade_readiness", side_effect=mark("readiness")), \
             patch("app.dashboard._enrich_with_suggestion_lifecycle", side_effect=mark("lifecycle")):

            result = build_automation_frame()

            self.assertEqual(calls, ["sync", "opened", "readiness", "lifecycle"])
            self.assertEqual(list(result["Symbol"]), ["QQQ"])


class TestRunCycleReplicatesDashboardOrderingAndRerunParity(unittest.TestCase):

    """S2.1 §4.1/§4.2: exits run before entries, and a non-empty close list
    ends the pass without evaluating entries -- exactly the `st.rerun()`
    control-flow break in dashboard.py:9399-9412.
    """

    def _patched(self, df, closed, opened=None):
        return patch.multiple(
            "app.runtime.headless_paper",
            build_automation_frame=MagicMock(return_value=df),
        ), patch(
            "app.runtime.paper_automation.run_auto_paper_exits", return_value=closed
        ), patch(
            "app.runtime.paper_automation.run_auto_paper_entries", return_value=opened or []
        )

    def test_empty_frame_short_circuits_before_any_automation_call(self):
        with patch("app.runtime.headless_paper.build_automation_frame", return_value=pd.DataFrame()), \
             patch("app.runtime.paper_automation.run_auto_paper_exits") as exits, \
             patch("app.runtime.paper_automation.run_auto_paper_entries") as entries:

            result = run_cycle(controls={"auto_paper_enabled": True})

            self.assertEqual(result, {"opened": [], "closed": [], "skipped_entries": False})
            exits.assert_not_called()
            entries.assert_not_called()

    def test_a_close_skips_entries_this_cycle_rerun_parity(self):
        df = pd.DataFrame({"Symbol": ["NVDA"]})

        with patch("app.runtime.headless_paper.build_automation_frame", return_value=df), \
             patch("app.runtime.paper_automation.run_auto_paper_exits", return_value=["NVDA"]), \
             patch("app.runtime.paper_automation.run_auto_paper_entries") as entries:

            result = run_cycle(controls={"auto_paper_enabled": True})

            self.assertEqual(result["closed"], ["NVDA"])
            self.assertEqual(result["opened"], [])
            self.assertTrue(result["skipped_entries"])
            entries.assert_not_called()

    def test_no_closes_lets_entries_evaluate(self):
        df = pd.DataFrame({"Symbol": ["NVDA"]})

        with patch("app.runtime.headless_paper.build_automation_frame", return_value=df), \
             patch("app.runtime.paper_automation.run_auto_paper_exits", return_value=[]), \
             patch("app.runtime.paper_automation.run_auto_paper_entries", return_value=["AMD"]) as entries:

            result = run_cycle(controls={"auto_paper_enabled": True})

            self.assertEqual(result["closed"], [])
            self.assertEqual(result["opened"], ["AMD"])
            self.assertFalse(result["skipped_entries"])
            entries.assert_called_once()

    def test_exits_are_always_called_before_entries_are_even_considered(self):
        df = pd.DataFrame({"Symbol": ["NVDA"]})
        order = []

        with patch("app.runtime.headless_paper.build_automation_frame", return_value=df), \
             patch(
                 "app.runtime.paper_automation.run_auto_paper_exits",
                 side_effect=lambda *a, **k: order.append("exits") or []
             ), \
             patch(
                 "app.runtime.paper_automation.run_auto_paper_entries",
                 side_effect=lambda *a, **k: order.append("entries") or []
             ):

            run_cycle(controls={"auto_paper_enabled": True})

            self.assertEqual(order, ["exits", "entries"])

    def test_no_controls_argument_resolves_them_itself(self):
        df = pd.DataFrame()

        with patch("app.runtime.headless_paper.build_automation_frame", return_value=df), \
             patch("app.runtime.headless_paper.resolve_auto_paper_controls") as resolver:

            resolver.return_value = {"auto_paper_enabled": True}
            run_cycle()

            resolver.assert_called_once()


class TestMainLoopRespectsMarketHoursAndTheEnabledFlag(unittest.TestCase):

    def test_skips_run_cycle_outside_market_hours(self):
        with patch("app.runtime.headless_paper.is_market_hours", return_value=False), \
             patch("app.runtime.headless_paper.run_cycle") as cycle, \
             patch("time.sleep", side_effect=StopIteration):

            with self.assertRaises(StopIteration):
                main(poll_seconds=0)

            cycle.assert_not_called()

    def test_skips_run_cycle_when_auto_paper_disabled(self):
        with patch("app.runtime.headless_paper.is_market_hours", return_value=True), \
             patch(
                 "app.runtime.headless_paper.resolve_auto_paper_controls",
                 return_value={"auto_paper_enabled": False},
             ), \
             patch("app.runtime.headless_paper.run_cycle") as cycle, \
             patch("time.sleep", side_effect=StopIteration):

            with self.assertRaises(StopIteration):
                main(poll_seconds=0)

            cycle.assert_not_called()

    def test_runs_cycle_when_market_open_and_enabled(self):
        with patch("app.runtime.headless_paper.is_market_hours", return_value=True), \
             patch(
                 "app.runtime.headless_paper.resolve_auto_paper_controls",
                 return_value={"auto_paper_enabled": True},
             ), \
             patch(
                 "app.runtime.headless_paper.run_cycle",
                 return_value={"opened": [], "closed": [], "skipped_entries": False},
             ) as cycle, \
             patch("time.sleep", side_effect=StopIteration):

            with self.assertRaises(StopIteration):
                main(poll_seconds=0)

            cycle.assert_called_once()

    def test_an_exception_in_one_cycle_does_not_kill_the_loop(self):
        calls = {"n": 0}

        def sleep_side_effect(_seconds):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise StopIteration

        with patch("app.runtime.headless_paper.is_market_hours", return_value=True), \
             patch(
                 "app.runtime.headless_paper.resolve_auto_paper_controls",
                 return_value={"auto_paper_enabled": True},
             ), \
             patch("app.runtime.headless_paper.run_cycle", side_effect=RuntimeError("boom")), \
             patch("time.sleep", side_effect=sleep_side_effect):

            with self.assertRaises(StopIteration):
                main(poll_seconds=0)

            self.assertEqual(calls["n"], 2)


class TestShadowStateFileOverride(unittest.TestCase):

    """S2.1 §7: a prerequisite for the S2.4 parallel run is that the headless
    caller can write to a separate state file so it never collides with the
    dashboard's live paper_trade_state.json.
    """

    def test_env_override_changes_the_state_file_path(self):
        import importlib

        import app.state.paper_trade_manager as ptm

        original = ptm.PAPER_TRADE_STATE_FILE

        try:
            with patch.dict(os.environ, {"PAPER_TRADE_STATE_FILE_OVERRIDE": "/tmp/shadow_state.json"}):
                importlib.reload(ptm)

                self.assertEqual(ptm.PAPER_TRADE_STATE_FILE, "/tmp/shadow_state.json")

        finally:
            importlib.reload(ptm)
            self.assertEqual(ptm.PAPER_TRADE_STATE_FILE, original)

    def test_default_path_is_unchanged_when_override_is_unset(self):
        import importlib

        import app.state.paper_trade_manager as ptm

        os.environ.pop("PAPER_TRADE_STATE_FILE_OVERRIDE", None)
        importlib.reload(ptm)

        self.assertTrue(ptm.PAPER_TRADE_STATE_FILE.replace("\\", "/").endswith(
            "app/state/paper_trade_state.json"
        ))


if __name__ == "__main__":
    unittest.main()
