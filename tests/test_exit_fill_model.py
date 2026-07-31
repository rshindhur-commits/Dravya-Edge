"""A level-triggered exit fills at its level, not at the last print.

Every exit was booked at the latest 5m close whatever rule fired, so a stop
detected on the 15m bar's High/Low was filled wherever price sat up to a scan
interval later. SPCX on 2026-07-20 had a 0.58 risk, a stop at 125.38 and a fill
at 124.87 -- an intended -1R booked as -1.88R, with the overshoot invisible
because it was folded into the fill price.
"""

import unittest

from app.exit.exit_engine import resolve_exit_fill


class HardStopFillTests(unittest.TestCase):

    def test_long_stop_never_fills_better_than_the_stop(self):
        """Price back above the stop by the time we looked: still filled at it."""

        fill, slippage = resolve_exit_fill(
            "HARD_STOP", is_short=False, market_price=126.10,
            stop_loss=125.38, take_profit=130.47,
        )

        self.assertAlmostEqual(fill, 125.38, places=2)
        # The level was honoured, so nothing was lost to slippage.
        self.assertEqual(slippage, 0.0)

    def test_long_stop_fills_worse_when_price_ran_through(self):
        """The SPCX case: the market is already past the stop."""

        fill, slippage = resolve_exit_fill(
            "HARD_STOP", is_short=False, market_price=124.87,
            stop_loss=125.38, take_profit=130.47,
        )

        self.assertAlmostEqual(fill, 124.87, places=2)
        self.assertAlmostEqual(slippage, 0.51, places=2)

    def test_short_stop_is_mirrored(self):

        through, slip_through = resolve_exit_fill(
            "HARD_STOP", is_short=True, market_price=126.20,
            stop_loss=125.38, take_profit=121.00,
        )
        self.assertAlmostEqual(through, 126.20, places=2)
        self.assertAlmostEqual(slip_through, 0.82, places=2)

        recovered, slip_recovered = resolve_exit_fill(
            "HARD_STOP", is_short=True, market_price=124.00,
            stop_loss=125.38, take_profit=121.00,
        )
        self.assertAlmostEqual(recovered, 125.38, places=2)
        self.assertEqual(slip_recovered, 0.0)

    def test_slippage_is_never_negative(self):
        """Positive is always adverse; a favourable gap is not a credit."""

        for is_short, market in ((False, 126.10), (True, 124.00)):
            _, slippage = resolve_exit_fill(
                "HARD_STOP", is_short=is_short, market_price=market,
                stop_loss=125.38, take_profit=130.47,
            )
            self.assertGreaterEqual(slippage, 0.0)


class HardTargetFillTests(unittest.TestCase):

    def test_long_target_fills_at_the_limit_not_the_close(self):
        """The rule only fires once the bar traded through the target."""

        fill, _ = resolve_exit_fill(
            "HARD_TARGET", is_short=False, market_price=131.20,
            stop_loss=125.38, take_profit=130.47,
        )

        self.assertAlmostEqual(fill, 130.47, places=2)

    def test_long_target_is_not_penalised_by_a_pullback_close(self):

        fill, _ = resolve_exit_fill(
            "HARD_TARGET", is_short=False, market_price=129.90,
            stop_loss=125.38, take_profit=130.47,
        )

        self.assertAlmostEqual(fill, 130.47, places=2)

    def test_short_target_fills_at_the_limit(self):

        fill, _ = resolve_exit_fill(
            "HARD_TARGET", is_short=True, market_price=120.10,
            stop_loss=125.38, take_profit=121.00,
        )

        self.assertAlmostEqual(fill, 121.00, places=2)


class MarketExitFillTests(unittest.TestCase):
    """Discretionary exits have no resting level; the close is the honest fill."""

    def test_soft_exits_fill_at_the_market(self):

        for rule in ("EMA", "VWAP", "MACD", "FAILED_BREAKOUT",
                     "TIME_EXIT", "NEAR_CLOSE", "PROFIT_PROTECTION"):

            fill, slippage = resolve_exit_fill(
                rule, is_short=False, market_price=124.87,
                stop_loss=125.38, take_profit=130.47,
            )

            self.assertAlmostEqual(fill, 124.87, places=2, msg=rule)
            self.assertEqual(slippage, 0.0, msg=rule)


class DegradedInputTests(unittest.TestCase):

    def test_missing_market_price_yields_nothing(self):

        self.assertEqual(
            resolve_exit_fill("HARD_STOP", False, None, 125.38, 130.47),
            (None, None),
        )

    def test_missing_level_falls_back_to_the_market(self):

        fill, slippage = resolve_exit_fill("HARD_STOP", False, 124.87, None, 130.47)

        self.assertAlmostEqual(fill, 124.87, places=2)
        self.assertEqual(slippage, 0.0)

    def test_unparseable_level_falls_back_to_the_market(self):

        fill, slippage = resolve_exit_fill("HARD_STOP", False, 124.87, "n/a", 130.47)

        self.assertAlmostEqual(fill, 124.87, places=2)
        self.assertEqual(slippage, 0.0)


class RegressionTests(unittest.TestCase):

    def test_spcx_intended_loss_is_no_longer_overstated(self):
        """entry 125.96, stop 125.38, risk 0.58 -- booked at -1.88R."""

        entry, stop, risk = 125.96, 125.38, 0.58

        booked_r = (124.87 - entry) / risk
        self.assertAlmostEqual(booked_r, -1.88, places=2)

        fill, slippage = resolve_exit_fill("HARD_STOP", False, 124.87, stop, 130.47)
        modelled_r = (fill - entry) / risk

        # The fill itself is unchanged -- the market did print 124.87 -- but the
        # overshoot is now recorded rather than silently inflating the loss.
        self.assertAlmostEqual(modelled_r, -1.88, places=2)
        self.assertAlmostEqual(slippage, 0.51, places=2)
        # 0.88R of the 1.88R loss was slippage, not the trade being wrong.
        self.assertAlmostEqual((slippage / risk), 0.88, places=2)

    def test_a_stop_that_held_now_books_exactly_one_r(self):
        """The other half: no longer credited a fill better than a stop can get."""

        entry, stop, risk = 125.96, 125.38, 0.58

        fill, _ = resolve_exit_fill("HARD_STOP", False, 125.60, stop, 130.47)

        self.assertAlmostEqual((fill - entry) / risk, -1.0, places=2)


if __name__ == "__main__":
    unittest.main()
