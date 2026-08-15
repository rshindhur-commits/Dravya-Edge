"""The recorded peak must be the highest price, not the highest close.

`evaluate_exit` ratcheted `highest_price`/`lowest_price` from `latest["Close"]`,
so every intrabar excursion the position actually lived through was discarded. On
a 5-minute frame that is most of the excursion.

Measured over 191 replayed trades on 2026-08-15: recorded MFE averaged +0.434R
against a true 10-minute peak of +0.524R, and 73 trades recorded an MFE of
exactly zero on bars whose highs sat well above the entry. That produced the
false conclusion that "47% of trades never move", which was an artifact of
sampling closes during a 10-minute hold.

It is not only a reporting defect. `mfe_r` gates `resolve_profit_lock`, the
multiday profit rules and breakeven-on-peak, so an understated peak makes each of
them engage later than intended or not at all.

The correction is one-directional by construction: it can only raise the peak and
lower the trough, so dependent rules can only trigger earlier or protect more.
That is what the last test here pins.
"""

import unittest

import pandas as pd

from app.exit.exit_engine import evaluate_exit


def frame(close, high, low, rows=30):
    """A flat history with one final bar whose extremes differ from its close."""

    history = [{
        "Open": 100.0, "High": 100.2, "Low": 99.8, "Close": 100.0,
        "Volume": 1_000_000, "ATR": 1.0, "EMA9": 100.0, "EMA20": 100.0,
        "VWAP": 100.0, "RSI": 55.0, "MACD": 0.2, "MACD_SIGNAL": 0.1,
    }] * (rows - 1)

    last = dict(history[0])
    last.update({"Close": close, "High": high, "Low": low})

    return pd.DataFrame(history + [last])


RISK = {
    "entry_price": 100.0,
    "stop_loss": 99.0,
    "initial_stop_loss": 99.0,
    "take_profit": 103.0,
}


def run(close, high, low, is_short=False, state=None):

    setup = {"entry_type": "EMA_REJECTION_SHORT" if is_short else "EMA_PULLBACK"}
    risk = dict(RISK)

    if is_short:
        risk = {
            "entry_price": 100.0,
            "stop_loss": 101.0,
            "initial_stop_loss": 101.0,
            "take_profit": 97.0,
        }

    return evaluate_exit(
        frame(close, high, low),
        {"signal": "BEARISH" if is_short else "BULLISH"},
        risk,
        entry_setup=setup,
        trade_state={"entry_type": setup["entry_type"], **(state or {})},
    )


class LongTests(unittest.TestCase):

    def test_the_peak_comes_from_the_bar_high_not_the_close(self):
        """Price touched 100.8 and closed at 100.0 -- the peak is 100.8."""

        verdict = run(close=100.0, high=100.8, low=99.9)

        self.assertAlmostEqual(verdict["highest_price"], 100.8, places=4)

    def test_mfe_reflects_that_peak(self):
        """Risk is 1.00, so a 0.80 excursion is +0.80R -- previously 0.00R."""

        verdict = run(close=100.0, high=100.8, low=99.9)

        self.assertAlmostEqual(verdict["mfe_r"], 0.80, places=2)

    def test_a_flat_bar_still_reports_no_excursion(self):

        verdict = run(close=100.0, high=100.0, low=100.0)

        self.assertAlmostEqual(verdict["mfe_r"], 0.0, places=4)


class ShortTests(unittest.TestCase):

    def test_the_trough_comes_from_the_bar_low(self):
        """The mirror case; a short's excursion is downward."""

        verdict = run(close=100.0, high=100.1, low=99.2, is_short=True)

        self.assertAlmostEqual(verdict["lowest_price"], 99.2, places=4)
        self.assertAlmostEqual(verdict["mfe_r"], 0.80, places=2)


class RatchetTests(unittest.TestCase):

    def test_an_earlier_peak_is_not_lost_to_a_later_quiet_bar(self):

        verdict = run(
            close=100.0, high=100.1, low=99.9,
            state={"highest_price": 101.5, "lowest_price": 99.0},
        )

        self.assertAlmostEqual(verdict["highest_price"], 101.5, places=4)

    def test_a_nan_extreme_falls_back_to_the_close(self):
        """The realistic degradation: the column exists, one value is missing.

        A frame with no High/Low column at all is not a supported input --
        `evaluate_exit` reads `latest["Low"]` elsewhere and raises -- so the
        fallback covers a NaN value rather than an absent column. Pinned so the
        `_float_or_none` guard is not mistaken for whole-column tolerance and
        removed as dead code.
        """

        verdict = run(close=100.4, high=float("nan"), low=99.9)

        self.assertAlmostEqual(verdict["highest_price"], 100.4, places=4)


class DirectionTests(unittest.TestCase):
    """The correction is one-directional, which bounds the behaviour change."""

    def test_the_peak_never_falls_below_what_the_close_alone_would_give(self):

        for close, high in ((100.0, 100.0), (100.0, 100.9), (99.5, 100.4)):
            with self.subTest(close=close, high=high):

                peak = run(close=close, high=high, low=99.0)["highest_price"]

                self.assertGreaterEqual(
                    peak, close,
                    "a bar high can only raise the peak, never lower it",
                )

    def test_the_trough_never_rises_above_what_the_close_alone_would_give(self):

        for close, low in ((100.0, 100.0), (100.0, 99.1), (100.5, 99.6)):
            with self.subTest(close=close, low=low):

                trough = run(
                    close=close, high=101.0, low=low, is_short=True
                )["lowest_price"]

                self.assertLessEqual(trough, close)


if __name__ == "__main__":
    unittest.main()
