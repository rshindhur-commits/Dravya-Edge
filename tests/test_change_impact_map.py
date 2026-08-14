"""The claims in docs/CHANGE_IMPACT_MAP.md, made executable.

A map of "if you change X, Y moves" is worth having only while it is true, and
the way such a document dies is silently: someone fixes one of the traps it
warns about, the warning becomes wrong, and the next reader is misled by the
thing that was supposed to protect them.

So each test here pins one load-bearing claim. Several pin behaviour that is
undesirable -- the hardcoded auto-paper constants, the regression evaluator that
never calls the exit engine. That is deliberate. When someone repairs one, the
test fails, and the failure is the reminder to update the map in the same commit.

Every test names the section of the map it defends.
"""

import inspect
import unittest


class FrozenSettingsTests(unittest.TestCase):
    """Map 0.1 -- `settings.*` is frozen at import; `get_float_env` is live."""

    def test_settings_is_built_once_at_import(self):

        from app.config import settings as settings_module

        self.assertTrue(
            hasattr(settings_module, "settings"),
            "the module-level singleton is what makes this a trap",
        )

        # A frozen dataclass instance, not a callable or a proxy that re-reads.
        import dataclasses

        self.assertTrue(dataclasses.is_dataclass(settings_module.settings))
        self.assertTrue(
            settings_module.RuntimeSettings.__dataclass_params__.frozen,
            "if this ever becomes mutable the 'frozen at import' warning is wrong",
        )

    def test_no_refresh_hook_exists(self):
        """The absence of this is the whole point of the warning."""

        from app.config import settings as settings_module

        for name in ("refresh_settings", "reload_settings", "invalidate_settings"):
            self.assertFalse(
                hasattr(settings_module, name),
                f"{name} exists now -- map section 0.1 needs rewriting",
            )

    def test_the_entry_gate_reads_the_spread_ceiling_live(self):
        """The other half of the asymmetry: this one is re-read per call."""

        from app.gates import entry_gate

        source = inspect.getsource(entry_gate.scanner_entry_gate_config)
        self.assertIn("get_float_env", source)
        self.assertIn("OPTION_MAX_SPREAD_PCT", source)


class AutoPaperGateTests(unittest.TestCase):
    """Map 0.3 and 0.4 -- the auto-paper path has its own, partly hardcoded, bars."""

    def test_its_option_bars_are_constants_not_environment(self):

        from app.runtime import paper_automation_support as support

        self.assertEqual(support.DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT, 6.0)
        self.assertEqual(support.DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY, 65.0)

        # If these ever start reading the environment, OPTION_MAX_SPREAD_PCT
        # would reach this gate directly and map section 0.3 becomes wrong.
        source = inspect.getsource(support._auto_paper_entry_reason)
        self.assertIn("DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT", source)
        self.assertIn("DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY", source)

    def test_the_scanner_rr_bar_sits_above_the_auto_paper_one(self):
        """So raising AUTO_PAPER_MIN_RR alone changes nothing."""

        from app.gates.entry_gate import scanner_entry_gate_config
        from app.runtime.paper_automation_support import DEFAULT_AUTO_PAPER_MIN_RR

        self.assertGreater(
            scanner_entry_gate_config().min_rr,
            DEFAULT_AUTO_PAPER_MIN_RR,
            "if these ever cross, which gate binds changes and map 0.4 is wrong",
        )

    def test_the_entry_window_is_0945_to_1530(self):

        from datetime import time

        from app.runtime import paper_automation_support as support

        self.assertEqual(support.AUTO_PAPER_ENTRY_START, time(9, 45))
        self.assertEqual(support.AUTO_PAPER_ENTRY_END, time(15, 30))
        self.assertEqual(support.AUTO_PAPER_EOD_CLOSE, time(15, 55))


class ShortCircuitTests(unittest.TestCase):
    """Map 0.2 -- a failure count is 'failed here first', never 'would fail here'."""

    def test_the_liquidity_filter_checks_open_interest_before_spread(self):
        """The ordering that made volume and OI look like the binding constraint."""

        from app.options import options_filter

        source = inspect.getsource(options_filter._evaluate_option_liquidity)

        order = [
            source.index('"LOW_OPEN_INTEREST"'),
            source.index('"LOW_VOLUME"'),
            source.index('"WIDE_SPREAD"'),
            source.index('"LOW_OPTION_QUALITY"'),
        ]

        self.assertEqual(order, sorted(order), "map section 3's table is ordered")

    def test_the_entry_gate_checks_spread_last(self):
        """So SPREAD_TOO_WIDE undercounts what a tighter ceiling would remove."""

        from app.gates import entry_gate

        source = inspect.getsource(entry_gate.evaluate_entry_gate)

        self.assertLess(
            source.index('"RR_BELOW_THRESHOLD"'),
            source.index('"SPREAD_TOO_WIDE"'),
        )
        self.assertLess(
            source.index('"OPTION_QUALITY_BELOW_THRESHOLD"'),
            source.index('"SPREAD_TOO_WIDE"'),
        )


class RegressionEvaluatorTests(unittest.TestCase):
    """Map section 7 -- how regression scores an exit.

    Both defects this class used to pin were repaired on 2026-08-14 as Phase A
    task 3, which failed these assertions and is the contract this file exists
    for: neither repair could land without the map being corrected alongside it.
    Behaviour is covered by tests/test_regression_exit_engine.py; the structural
    claims stay here.
    """

    def test_the_live_exit_engine_is_the_default(self):

        from app.regression import historical_scanner

        signature = inspect.signature(historical_scanner.reconstruct_trades)

        self.assertIs(
            signature.parameters["exit_evaluator"].default,
            historical_scanner.exit_engine_evaluator,
        )
        self.assertIn(
            "evaluate_exit",
            inspect.getsource(historical_scanner.exit_engine_evaluator),
        )

    def test_a_bar_touching_both_levels_is_scored_a_stop(self):
        """Was the reverse. Intrabar order is unknowable at ~5m sampling."""

        from app.regression import historical_scanner

        source = inspect.getsource(historical_scanner.reconstruct_trades)

        self.assertLess(source.index("if hit_stop:"), source.index("elif hit_target:"))

    def test_the_old_counterfactual_is_still_reachable(self):
        """§1.6 measures hold-to-stop-or-target deliberately."""

        from app.regression.historical_scanner import reconstruct_trades

        signature = inspect.signature(reconstruct_trades)

        self.assertIn("evaluator", signature.parameters)
        self.assertIn("exit_evaluator", signature.parameters)


class DuplicatedLimitTests(unittest.TestCase):
    """Map 0.5 -- cooldown and per-symbol caps are implemented twice."""

    def test_both_copies_still_exist(self):

        from pathlib import Path

        root = Path(__file__).resolve().parents[1]

        worker = (root / "app" / "runtime" / "paper_automation_support.py").read_text(
            encoding="utf-8", errors="ignore"
        )
        dashboard = (root / "app" / "dashboard.py").read_text(
            encoding="utf-8", errors="ignore"
        )

        for text in (worker, dashboard):
            self.assertIn("AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES", text)
            self.assertIn("MAX_TRADES_PER_SYMBOL_PER_DAY", text)


class AvoidChasingTests(unittest.TestCase):
    """Map 1a -- the hard block that decides which candidates can exist.

    A candidate more than 1.2% from its EMA9, or 1.5% from VWAP, is refused
    outright rather than down-weighted.

    This class previously asserted the thresholds were hardcoded. They were made
    configurable on 2026-08-14 as Phase A's first task, which failed that
    assertion and is exactly the contract this file is meant to enforce: the
    repair could not land without the map being updated alongside it. The
    behavioural guarantees now live in tests/test_avoid_chasing.py; what remains
    here is the map's structural claim.
    """

    def test_it_is_a_hard_refusal_not_a_score_penalty(self):

        from app.risk import risk_manager

        source = inspect.getsource(risk_manager.calculate_risk)

        self.assertIn("avoid_chasing", source)
        self.assertIn("Avoid chasing extended move", source)

    def test_the_thresholds_are_configurable_and_default_unchanged(self):

        from app.strategies import entry_engine

        self.assertEqual(entry_engine.DEFAULT_MAX_VWAP_DISTANCE_PCT, 1.5)
        self.assertEqual(entry_engine.DEFAULT_MAX_EMA_DISTANCE_PCT, 1.2)
        self.assertEqual(entry_engine.max_vwap_distance_pct(), 1.5)
        self.assertEqual(entry_engine.max_ema_distance_pct(), 1.2)

    def test_the_refusal_has_its_own_switch(self):
        """Separate from the thresholds: one lifts the block, the other widens it."""

        from app.risk.risk_manager import avoid_chasing_blocks

        self.assertTrue(avoid_chasing_blocks(), "must stay on by default")

    def test_it_applies_to_both_directions(self):
        """It was long-broken for shorts; abs() is what fixed it."""

        from app.strategies import entry_engine

        source = inspect.getsource(entry_engine.detect_entry)

        self.assertIn("abs(vwap_distance)", source)
        self.assertIn("abs(ema_distance)", source)


class SetupRegistryTests(unittest.TestCase):
    """Map section 2 -- five setups, and one of them ignores the scale."""

    def test_exactly_five_setups_can_be_emitted(self):

        from app.strategies.setup_registry import SETUP_DIRECTIONS

        self.assertEqual(
            SETUP_DIRECTIONS,
            {
                "BREAKOUT": "CALL",
                "EMA_PULLBACK": "CALL",
                "BREAKDOWN_SHORT": "PUT",
                "EMA_REJECTION_SHORT": "PUT",
                "VWAP_REJECTION": "PUT",
            },
        )

    def test_vwap_rejection_ignores_the_distance_scale(self):
        """So an ATR_DISTANCE_SCALE arm leaves this setup untouched."""

        from app.risk import risk_manager

        source = inspect.getsource(risk_manager.calculate_risk)
        start = source.index('entry_type == "VWAP_REJECTION"')
        branch = source[start:start + 600]

        self.assertNotIn(
            "_distance_scale", branch,
            "if VWAP_REJECTION starts scaling, the map's table is wrong",
        )

    def test_structure_stops_are_unreachable_in_production(self):
        """Every production caller takes the "SWING" default."""

        from app.risk import risk_manager

        source = inspect.getsource(risk_manager.calculate_risk)
        self.assertIn('stop_anchor="SWING"', source)
        self.assertIn('== "STRUCTURE"', source)


class StopGeometryTests(unittest.TestCase):
    """Map section 2 -- the scale is a weak lever because the bar dominates.

    The arithmetic itself is pinned in tests/test_distance_scale.py. This only
    checks that both knobs are still read in the place the map says, since the
    map's advice depends on them moving together.
    """

    def test_both_scale_knobs_are_read_by_the_risk_manager(self):

        from app.risk import risk_manager

        source = inspect.getsource(risk_manager.calculate_risk)

        self.assertIn("ATR_DISTANCE_SCALE", source)
        self.assertIn("MAX_STOP_DISTANCE_SCALE", source)

    def test_the_target_extension_is_capped(self):
        """Uncapped, TARGET_MIN_RR makes the RR gate check its own answer."""

        from app.risk import risk_manager

        source = inspect.getsource(risk_manager.calculate_risk)

        self.assertIn("TARGET_MIN_RR", source)
        self.assertIn("TARGET_MAX_REWARD_ATR", source)


class ExitPriorityTests(unittest.TestCase):
    """Map section 6 -- the priority table, and the momentum class."""

    def test_the_hard_levels_outrank_every_soft_exit(self):

        from app.exit.exit_engine import EXIT_PRIORITY

        hard = min(EXIT_PRIORITY["HARD_STOP"], EXIT_PRIORITY["HARD_TARGET"])
        soft = max(
            EXIT_PRIORITY[code]
            for code in ("EMA", "VWAP", "MACD", "FAILED_BREAKOUT", "TIME_EXIT")
        )

        self.assertGreater(hard, soft)

    def test_the_momentum_class_is_removable_in_one_switch(self):
        """Removing members individually only redistributes between them."""

        from app.exit import exit_engine

        source = inspect.getsource(exit_engine._momentum_exits_allowed)
        self.assertIn("EXIT_MOMENTUM_ENABLED", source)

    def test_profit_lock_only_converts_momentum_exits(self):

        from app.exit.exit_engine import PROFIT_LOCK_ELIGIBLE_EXITS

        self.assertEqual(PROFIT_LOCK_ELIGIBLE_EXITS, {"EMA", "VWAP", "MACD"})


class ReplayIndependenceTests(unittest.TestCase):
    """Map section 7 -- replay_forward never reads the live book.

    This is why the two corrupted trades of 2026-08-13 did not contaminate any
    replay result. If the tool ever starts reading paper_trades, that reasoning
    stops holding and every conclusion drawn from it needs re-examining.
    """

    def test_replay_forward_does_not_read_paper_trades(self):

        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1] / "tools" / "replay_forward.py"
        ).read_text(encoding="utf-8", errors="ignore")

        self.assertNotIn("paper_trades", source)


if __name__ == "__main__":
    unittest.main()
