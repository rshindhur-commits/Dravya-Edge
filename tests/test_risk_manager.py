import os
import unittest
from unittest.mock import patch

import pandas as pd

from app.risk.risk_manager import calculate_risk


class RiskManagerTests(unittest.TestCase):

    def test_ema_pullback_does_not_force_full_atr_stop_floor(self):

        df = pd.DataFrame([
            {
                "High": 101.25,
                "Low": 99.55,
                "Close": 100.00,
                "ATR": 2.00,
                "EMA9": 99.80,
                "VWAP": 99.40,
                "ROLLING_RESISTANCE": 104.00,
                "ROLLING_SUPPORT": 98.00,
                "PREV_HIGH": 103.00,
                "PREV_LOW": 98.50,
            }
        ] * 20)

        result = calculate_risk(
            df=df,
            analysis={
                "signal": "BULLISH",
                "market_regime": "TRENDING_BULL"
            },
            entry_setup={
                "entry_type": "EMA_PULLBACK",
                "entry_quality": "HIGH",
                "avoid_chasing": False
            }
        )

        self.assertTrue(result["trade_allowed"])
        self.assertEqual(result["stop_loss"], 99.25)
        self.assertGreaterEqual(result["risk_reward"], 3.0)
        self.assertFalse(
            any(
                reason.startswith("ATR floor adjusted stop")
                for reason in result["reasons"]
            )
        )


if __name__ == "__main__":

    unittest.main()


class StopFloorAtrCapTests(unittest.TestCase):
    """The 0.50% price floor is a proxy for a cost that lives in the option spread.

    `stop_viability` enforces the real term -- the specific contract's round-trip
    spread -- and says of this one: "a 0.50% stop is ample on a penny-wide
    contract and still unwinnable on one quoted 8% wide, and a single price-term
    floor cannot tell those apart."

    On a low-volatility underlying the proxy inverts. Measured 2026-08-19..21,
    the 0.50% floor expressed in ATR: SPY 4.08, QQQ 2.31, against NFLX 1.43 and
    PLTR 0.78 -- the widest the app actually trades. SPY and QQQ produced 3,300+
    entry signals each over ten days and passed the Risk stage **zero** times,
    with 958 RR readings apiece maxing at 0.81 and 1.64 against a 2.0 gate.
    """

    @staticmethod
    def _frame(price, atr):
        """A bar set whose structure stop is tighter than the price floor."""

        row = {
            "High": price * 1.004,
            "Low": price * 0.998,
            "Close": price,
            "ATR": atr,
            "EMA9": price * 0.999,
            "VWAP": price * 0.998,
            "ROLLING_RESISTANCE": price * 1.02,
            "ROLLING_SUPPORT": price * 0.985,
            "PREV_HIGH": price * 1.03,
            "PREV_LOW": price * 0.98,
        }
        return pd.DataFrame([row] * 20)

    def _stop_distance(self, price, atr, cap):
        with patch.dict(os.environ, {"MIN_STOP_DISTANCE_ATR_CAP": cap}, clear=False):
            result = calculate_risk(
                self._frame(price, atr),
                {"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
                {"entry_type": "EMA_PULLBACK", "entry_quality": "HIGH",
                 "avoid_chasing": False},
            )
        entry, stop = result.get("entry_price"), result.get("stop_loss")
        return None if (entry is None or stop is None) else abs(entry - stop)

    # SPY-like: 0.50% of price is many multiples of ATR.
    LOW_VOL = (765.0, 0.70)
    # PLTR-like: 0.50% of price sits comfortably under one ATR.
    NORMAL = (180.0, 1.08)

    def test_the_floor_stops_dictating_the_stop_on_a_low_volatility_underlying(self):
        price, atr = self.LOW_VOL
        uncapped = self._stop_distance(price, atr, cap="0")
        capped = self._stop_distance(price, atr, cap="2.0")

        # Uncapped, the price floor is the whole stop: 0.50% of 765 is 3.825,
        # wider than anything structure offered, so it overrode structure. The
        # tolerance is the stop's own 2-decimal rounding, not slack.
        self.assertLess(abs(uncapped - price * 0.005), 0.011)

        # Capped, the floor no longer reaches, and structure decides instead --
        # which is why the result is not simply atr * 2.0. The cap bounds the
        # floor; it does not impose a stop of its own.
        self.assertLess(capped, uncapped)
        self.assertGreater(abs(capped - price * 0.005), 0.011)

    def test_a_normally_volatile_underlying_is_untouched(self):
        """The cap sits above every symbol the app currently trades."""

        self.assertAlmostEqual(
            self._stop_distance(*self.NORMAL, cap="0"),
            self._stop_distance(*self.NORMAL, cap="2.0"),
            places=9,
        )

    def test_zero_restores_the_unbounded_price_floor(self):
        self.assertAlmostEqual(
            self._stop_distance(*self.LOW_VOL, cap="0"),
            self._stop_distance(*self.LOW_VOL, cap="0.0"),
            places=9,
        )

    def test_the_cap_can_only_narrow_a_stop_never_widen_one(self):
        """It bounds a floor. A floor that binds less cannot move a stop out."""

        for price, atr in (self.LOW_VOL, self.NORMAL, (37.0, 0.084)):
            uncapped = self._stop_distance(price, atr, cap="0")
            capped = self._stop_distance(price, atr, cap="2.0")
            self.assertLessEqual(capped, uncapped + 1e-9, f"{price}/{atr} widened")

    def test_the_default_is_two_atr(self):
        """Pinned: the value is what keeps the current book untouched."""

        from app.config.settings import get_float_env

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MIN_STOP_DISTANCE_ATR_CAP", None)
            self.assertEqual(get_float_env("MIN_STOP_DISTANCE_ATR_CAP", 2.0), 2.0)

