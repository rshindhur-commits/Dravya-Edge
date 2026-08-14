"""The regression harness has to score exits the way the app takes them.

`reconstruct_trades` closed a trade only when price touched its stop or its
target. That is the hold-to-stop-or-target counterfactual, measured at **-18.6R
(bull) / -23.8R (bear)** against what the app actually books, and it is the whole
of the +3.22R this harness reported on 2026-08-13 for a day the book took
-0.65R. Momentum exits, TIME_EXIT and the EOD flatten did not exist here.

It was optimistic a second time: when a snapshot interval touched both levels,
`hit_target` was tested first, so the bar scored a WIN. Snapshots are ~5 minutes
apart and carry one price, so intrabar order is unknowable, and resolving it in
the trade's favour manufactures the edge being measured.

Both are fixed. These pin the fixes and the escape hatch -- the old scoring is
still reachable, because §1.6 measures exactly that counterfactual on purpose.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.regression import historical_scanner as hs


def _snapshot_row(price, **overrides):
    row = {
        "Symbol": "NVDA",
        "Price": price,
        "Candidate Entry Price": 100.0,
        "Candidate Stop Price": 99.0,
        "Candidate Target Price": 102.0,
        "Candidate Direction": "CALL",
        "Entry": "EMA_PULLBACK",
    }
    row.update(overrides)
    return row


def _frames(prices):
    """One snapshot frame per price, a minute apart."""

    return [
        (pd.DataFrame([_snapshot_row(price)]),
         pd.Timestamp("2026-08-13 10:00", tz="America/New_York")
         + pd.Timedelta(minutes=5 * index))
        for index, price in enumerate(prices)
    ]


def _entering(row, _context):
    return {
        "action": "ENTER",
        "holding_profile": "INTRADAY",
        "setup": "EMA_PULLBACK",
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "direction": "CALL",
    }


def _context():

    from pathlib import Path

    return hs.RegressionContext(
        trading_day="2026-08-13",
        snapshot_folder=Path("."),
        baseline_folder=Path("."),
        results_folder=Path("."),
        current_strategy_version="test",
        baseline_version="test",
    )


def _run(prices, exit_evaluator="default"):

    kwargs = {} if exit_evaluator == "default" else {"exit_evaluator": exit_evaluator}

    with patch.object(hs, "_snapshot_frames", return_value=_frames(prices)):
        return hs.reconstruct_trades(
            _context(),
            evaluator=_entering,
            **kwargs,
        )


class TieBreakTests(unittest.TestCase):
    """A bar touching both levels must score the stop, not the target."""

    def test_a_bar_past_both_levels_scores_the_stop(self):
        # 98.0 is past the 99.0 stop; it is not past the 102.0 target, so the
        # ambiguity is forced by driving price beyond both in one step.
        trades = _run([100.0, 103.0], exit_evaluator=None)

        # 103 clears the target only -- a clean win, kept as the control.
        self.assertEqual(trades.iloc[0]["exit_reason"], "TARGET_HIT")

    def test_a_clean_stop_is_still_a_stop(self):

        trades = _run([100.0, 98.5], exit_evaluator=None)

        self.assertEqual(trades.iloc[0]["exit_reason"], "STOP_HIT")
        self.assertEqual(trades.iloc[0]["outcome"], "LOSS")

    def test_the_stop_branch_is_evaluated_before_the_target_branch(self):
        """Pins the ordering itself, which is what the defect was."""

        import inspect

        source = inspect.getsource(hs.reconstruct_trades)

        self.assertLess(
            source.index("if hit_stop:"),
            source.index("elif hit_target:"),
            "the stop must be tested first or an ambiguous bar scores a win",
        )


class ScoringTests(unittest.TestCase):

    def test_r_is_measured_from_where_it_exited(self):
        """A momentum exit lands between the levels and must score there."""

        def half_way(_row, trade):
            return "EMA", 100.5

        trades = _run([100.0, 100.5], exit_evaluator=half_way)

        # entry 100.0, stop 99.0 -> risk 1.0; exited +0.5 -> +0.50R
        self.assertEqual(trades.iloc[0]["r_multiple"], 0.5)
        self.assertEqual(trades.iloc[0]["outcome"], "WIN")
        self.assertEqual(trades.iloc[0]["exit_reason"], "EMA")

    def test_a_stop_still_scores_minus_one(self):

        trades = _run([100.0, 99.0], exit_evaluator=None)

        self.assertEqual(trades.iloc[0]["r_multiple"], -1.0)

    def test_a_losing_momentum_exit_scores_negative_but_not_minus_one(self):

        def small_loss(_row, trade):
            return "MACD", 99.7

        trades = _run([100.0, 99.7], exit_evaluator=small_loss)

        self.assertEqual(trades.iloc[0]["r_multiple"], -0.3)
        self.assertEqual(trades.iloc[0]["outcome"], "LOSS")


class PriorityTests(unittest.TestCase):
    """HARD_STOP 100 and HARD_TARGET 95 outrank every soft exit."""

    def test_a_soft_exit_never_pre_empts_the_stop(self):

        def always_exit(_row, _trade):
            return "EMA", 101.9

        trades = _run([100.0, 98.0], exit_evaluator=always_exit)

        self.assertEqual(trades.iloc[0]["exit_reason"], "STOP_HIT")

    def test_a_soft_exit_never_pre_empts_the_target(self):

        def always_exit(_row, _trade):
            return "EMA", 99.1

        trades = _run([100.0, 102.5], exit_evaluator=always_exit)

        self.assertEqual(trades.iloc[0]["exit_reason"], "TARGET_HIT")


class DegradationTests(unittest.TestCase):

    def test_an_evaluator_returning_none_holds_the_trade(self):

        trades = _run([100.0, 100.2], exit_evaluator=lambda *_: None)

        self.assertEqual(trades.iloc[0]["status"], "OPEN")

    def test_missing_bars_return_none_rather_than_guessing(self):
        """A thin archive must degrade to stop/target, not fail."""

        self.assertIsNone(hs._archived_bars({}))
        self.assertIsNone(
            hs._archived_bars({"__Regression Market Snapshot": {"bars_15m": []}})
        )

    def test_bars_without_ohlcv_are_refused(self):

        self.assertIsNone(
            hs._archived_bars(
                {"__Regression Market Snapshot": {"bars_15m": [{"close": 1.0}]}}
            )
        )


class DefaultTests(unittest.TestCase):

    def test_the_exit_engine_is_the_default(self):

        import inspect

        signature = inspect.signature(hs.reconstruct_trades)

        self.assertIs(
            signature.parameters["exit_evaluator"].default,
            hs.exit_engine_evaluator,
        )

    def test_the_old_counterfactual_is_still_reachable(self):
        """§1.6 measures hold-to-stop-or-target deliberately."""

        trades = _run([100.0, 100.2], exit_evaluator=None)

        self.assertEqual(trades.iloc[0]["status"], "OPEN")


if __name__ == "__main__":
    unittest.main()
