"""A stop that cannot clear the contract's own spread is not a tradeable stop.

The stop is placed on the underlying; the position is an option. Those are the
same trade only if the move to the stop is large enough to cover the round trip.
PLTR on 2026-07-30 entered at 122.06 with a stop at 121.90 -- 0.13% of price,
smaller than one 5-minute candle -- against option round trips of 2.1%-8.0%.

Enforcing since 2026-07-31 at a 1.0 multiple. This module can now reject trades,
so the fail-open paths matter as much as the arithmetic: an unpriced contract is
a data gap, not evidence of a bad stop, and must never block.
"""

import unittest
from unittest.mock import patch

from app.risk.stop_viability import (
    DEFAULT_MIN_STOP_SPREAD_MULTIPLE,
    enforce_stop_viability,
    evaluate_stop_viability,
)


class ArithmeticTests(unittest.TestCase):

    def test_option_move_is_delta_scaled_not_the_raw_underlying_move(self):
        """The comparison is option-move-vs-option-spread, both as % of premium."""

        # 1.00 underlying stop distance, delta 0.50 -> 0.50 of premium on a 2.50
        # contract = 20% of premium, against a 4% round trip = 5.0x.
        result = evaluate_stop_viability(
            entry_price=100.0, stop_price=99.0,
            option_price=2.50, delta=0.50, spread_pct=4.0,
        )

        self.assertAlmostEqual(result["option_move_at_stop"], 0.50, places=4)
        self.assertAlmostEqual(result["move_pct_of_premium"], 20.0, places=2)
        self.assertAlmostEqual(result["spread_multiple"], 5.0, places=2)
        self.assertTrue(result["viable"])

    def test_a_stop_inside_the_spread_is_rejected(self):
        """The PLTR shape: a stop far too tight for the contract."""

        result = evaluate_stop_viability(
            entry_price=122.06, stop_price=121.90,
            option_price=2.00, delta=0.50, spread_pct=8.0,
        )

        self.assertFalse(result["viable"])
        self.assertEqual(result["reason"], "STOP_INSIDE_OPTION_SPREAD")
        self.assertLess(result["spread_multiple"], 1.0)

    def test_short_side_delta_sign_does_not_matter(self):

        put = evaluate_stop_viability(100.0, 101.0, 2.50, -0.50, 4.0)
        call = evaluate_stop_viability(100.0, 99.0, 2.50, 0.50, 4.0)

        self.assertEqual(put["spread_multiple"], call["spread_multiple"])

    def test_the_boundary_is_inclusive(self):
        """Exactly covering the round trip passes; a hair under does not."""

        at = evaluate_stop_viability(100.0, 99.0, 2.50, 0.50, 20.0, min_multiple=1.0)
        under = evaluate_stop_viability(100.0, 99.0, 2.50, 0.50, 20.1, min_multiple=1.0)

        self.assertTrue(at["viable"])
        self.assertFalse(under["viable"])


class FailOpenTests(unittest.TestCase):
    """Missing inputs are a data gap. `viable` must be None, never False."""

    def test_unknown_inputs_never_report_a_bad_stop(self):
        cases = {
            "NO_STOP_PRICE": (100.0, None, 2.50, 0.50, 4.0),
            "NO_OPTION_PRICE": (100.0, 99.0, None, 0.50, 4.0),
            "NO_OPTION_DELTA": (100.0, 99.0, 2.50, None, 4.0),
            "NO_OPTION_SPREAD": (100.0, 99.0, 2.50, 0.50, None),
        }

        for expected_reason, args in cases.items():
            result = evaluate_stop_viability(*args)
            self.assertIsNone(result["viable"], expected_reason)
            self.assertEqual(result["reason"], expected_reason)

    def test_zero_delta_is_treated_as_unknown(self):

        self.assertIsNone(evaluate_stop_viability(100.0, 99.0, 2.50, 0.0, 4.0)["viable"])

    def test_a_stop_sitting_on_entry_is_a_real_failure_not_a_gap(self):
        """Zero risk is knowable and wrong, so this one does reject."""

        result = evaluate_stop_viability(100.0, 100.0, 2.50, 0.50, 4.0)

        self.assertFalse(result["viable"])
        self.assertEqual(result["reason"], "STOP_AT_ENTRY")

    def test_a_zero_spread_quote_passes(self):

        result = evaluate_stop_viability(100.0, 99.0, 2.50, 0.50, 0.0)

        self.assertTrue(result["viable"])
        self.assertEqual(result["reason"], "NO_SPREAD_COST")

    def test_multiple_of_zero_disables_the_check(self):

        result = evaluate_stop_viability(100.0, 99.99, 2.50, 0.50, 90.0, min_multiple=0)

        self.assertTrue(result["viable"])
        self.assertEqual(result["reason"], "CHECK_DISABLED")


class DefaultsTests(unittest.TestCase):

    def test_enforcement_is_on_by_default(self):
        """.env is gitignored, so the code default is what runs on Streamlit."""

        with patch("app.risk.stop_viability.get_bool_env", side_effect=lambda n, d: d):
            self.assertTrue(enforce_stop_viability())

    def test_default_multiple_is_the_measured_break(self):

        self.assertEqual(DEFAULT_MIN_STOP_SPREAD_MULTIPLE, 1.0)


class RowGateTests(unittest.TestCase):
    """_add_stop_viability is what actually costs a trade."""

    def _row(self, action="ENTER_PAPER"):
        return {
            "Action Status": action,
            "Candidate Entry Price": 122.06,
            "Candidate Stop Price": 121.90,
            "Option Mid Price": 2.00,
            "Option Delta": 0.50,
            "Option Spread %": 8.0,
        }

    def _apply(self, row, enforcing):
        from app import main

        with patch.object(main, "enforce_stop_viability", return_value=enforcing):
            return main._add_stop_viability(row)

    def test_enforcing_downgrades_the_row_to_avoid(self):

        result = self._apply(self._row(), enforcing=True)

        self.assertEqual(result["Action Status"], "AVOID")
        self.assertEqual(result["Blocked By"], "STOP_INSIDE_OPTION_SPREAD")
        self.assertIn("round-trip spread", result["Action Reason"])

    def test_observe_only_records_without_blocking(self):

        result = self._apply(self._row(), enforcing=False)

        self.assertEqual(result["Action Status"], "ENTER_PAPER")
        self.assertTrue(result["STOP_VIABILITY_WOULD_BLOCK"])

    def test_a_viable_stop_is_untouched(self):

        row = self._row()
        row["Option Spread %"] = 1.0

        result = self._apply(row, enforcing=True)

        self.assertEqual(result["Action Status"], "ENTER_PAPER")

    def test_a_data_gap_never_blocks_even_when_enforcing(self):
        """The failure mode that would quietly stop all trading on a feed hiccup."""

        row = self._row()
        row["Option Delta"] = None

        result = self._apply(row, enforcing=True)

        self.assertEqual(result["Action Status"], "ENTER_PAPER")

    def test_a_row_already_rejected_keeps_its_own_reason(self):
        """This reason is less specific than whatever rejected it first."""

        result = self._apply(self._row(action="STALE_STOCK_DATA"), enforcing=True)

        self.assertEqual(result["Action Status"], "STALE_STOCK_DATA")
        self.assertIsNone(result.get("Blocked By"))

    def test_the_verdict_is_recorded_either_way(self):

        for enforcing in (True, False):
            result = self._apply(self._row(), enforcing=enforcing)
            self.assertEqual(result["STOP_VIABILITY"], "STOP_INSIDE_OPTION_SPREAD")
            self.assertIsNotNone(result["STOP_SPREAD_MULTIPLE"])
            self.assertEqual(result["STOP_VIABILITY_ENFORCED"], enforcing)


if __name__ == "__main__":
    unittest.main()
