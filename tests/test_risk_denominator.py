"""R must be measured against the risk frozen at entry.

Regression cover for the 2026-07-29 NVDA trade: the exit engine moved the stop to
breakeven at +1R, and because rr_progress derived risk from the *current* stop,
`abs(entry - stop)` became 0. rr_progress and mfe_r collapsed to 0 permanently,
which silently disabled partial profit, the ATR trailing stop, and multiday
profit protection -- all gated on R thresholds. The trade gave back a +1.4R peak
and closed flat with r_multiple NULL and trend capture -36%.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.exit.exit_engine import (
    _bars_since_entry,
    _calculate_rr_progress,
    evaluate_exit,
    resolve_risk_per_share,
)
from app.state.paper_trade_manager import (
    _backfill_initial_stop,
    _initial_risk_per_share,
    _paper_trade_result,
)


class ResolveRiskPerShareTests(unittest.TestCase):

    def test_prefers_the_initial_stop(self):

        self.assertAlmostEqual(
            resolve_risk_per_share(193.32, 191.27, 193.32), 2.05, places=2
        )

    def test_falls_back_to_current_stop_for_legacy_trades(self):

        self.assertAlmostEqual(
            resolve_risk_per_share(193.32, None, 191.27), 2.05, places=2
        )

    def test_breakeven_initial_stop_does_not_yield_zero_when_current_is_usable(self):

        self.assertAlmostEqual(
            resolve_risk_per_share(100.0, 100.0, 98.0), 2.0, places=2
        )

    def test_returns_zero_only_when_nothing_is_usable(self):

        self.assertEqual(resolve_risk_per_share(100.0, 100.0, 100.0), 0.0)
        self.assertEqual(resolve_risk_per_share(None, None, None), 0.0)


class RrProgressTests(unittest.TestCase):

    def test_breakeven_stop_no_longer_collapses_r(self):
        """The exact 2026-07-29 NVDA state: stop moved onto entry."""

        collapsed = _calculate_rr_progress(
            current_price=196.09, entry_price=193.32,
            stop_loss=193.32, is_short=False,
        )
        self.assertEqual(collapsed, 0)

        preserved = _calculate_rr_progress(
            current_price=196.09, entry_price=193.32,
            stop_loss=193.32, is_short=False,
            risk_per_share=2.05,
        )
        self.assertAlmostEqual(preserved, 1.35, places=2)

    def test_short_side_uses_frozen_risk(self):

        progress = _calculate_rr_progress(
            current_price=98.0, entry_price=100.0,
            stop_loss=100.0, is_short=True,
            risk_per_share=2.0,
        )
        self.assertAlmostEqual(progress, 1.0, places=2)


def _frame(close, high=None, low=None):
    high = high if high is not None else close
    low = low if low is not None else close
    index = pd.date_range("2026-07-29 14:00", periods=30, freq="15min", tz="America/New_York")
    return pd.DataFrame(
        {
            "Open": [close] * 30,
            "High": [high] * 30,
            "Low": [low] * 30,
            "Close": [close] * 30,
            "Volume": [1_000_000] * 30,
            "ATR": [1.0] * 30,
            "EMA9": [close] * 30,
            "EMA20": [close] * 30,
            "VWAP": [close] * 30,
            "MACD": [0.1] * 30,
            "RSI": [55.0] * 30,
            "Relative Volume": [1.2] * 30,
        },
        index=index,
    )


class EvaluateExitRiskTests(unittest.TestCase):
    """End-to-end: a trade whose stop already sits on entry still measures R."""

    def _evaluate(self, risk_setup, trade_state):
        with patch("app.exit.exit_engine.evaluate_live_trend_health",
                   return_value={"status": "HEALTHY", "score": 70}), \
             patch("app.exit.exit_engine.evaluate_exit_confidence",
                   return_value={"exit_confidence_score": 20, "health_label": "HEALTHY"}):
            return evaluate_exit(
                _frame(196.0, high=196.2, low=195.8),
                {"signal": "BULLISH", "score": 7},
                risk_setup,
                {"entry_type": "BREAKOUT"},
                trade_state=trade_state,
            )

    def test_frozen_risk_keeps_r_alive_after_breakeven_move(self):

        result = self._evaluate(
            {
                "entry_price": 193.32,
                "stop_loss": 193.32,          # already moved to breakeven
                "initial_stop_loss": 191.27,  # frozen entry risk
                "take_profit": 197.81,
            },
            {"entry_type": "BREAKOUT", "highest_price": 196.09,
             "holding_profile": "MULTIDAY", "bars_in_trade": 10},
        )

        self.assertGreater(result["rr_progress"], 1.0)
        self.assertGreater(result["mfe_r"], 1.0)

    def test_without_the_frozen_stop_r_is_dead(self):
        """Documents the old behaviour so the fix cannot silently regress."""

        result = self._evaluate(
            {
                "entry_price": 193.32,
                "stop_loss": 193.32,
                "initial_stop_loss": 193.32,
                "take_profit": 197.81,
            },
            {"entry_type": "BREAKOUT", "highest_price": 196.09,
             "holding_profile": "MULTIDAY", "bars_in_trade": 10},
        )

        self.assertEqual(result["rr_progress"], 0)
        self.assertEqual(result["mfe_r"], 0)


class PaperTradeInitialStopTests(unittest.TestCase):

    def test_initial_risk_per_share(self):

        self.assertAlmostEqual(_initial_risk_per_share(193.32, 191.27), 2.05, places=2)
        self.assertIsNone(_initial_risk_per_share(100.0, 100.0))
        self.assertIsNone(_initial_risk_per_share(None, 1.0))

    def test_backfill_adopts_current_stop_once(self):

        trade = {"entry_price": 100.0, "stop_loss": 98.0}
        _backfill_initial_stop(trade)

        self.assertEqual(trade["initial_stop_loss"], 98.0)
        self.assertAlmostEqual(trade["initial_risk_per_share"], 2.0, places=2)

    def test_backfill_never_overwrites_an_existing_initial_stop(self):

        trade = {"entry_price": 100.0, "stop_loss": 100.0, "initial_stop_loss": 98.0,
                 "initial_risk_per_share": 2.0}
        _backfill_initial_stop(trade)

        self.assertEqual(trade["initial_stop_loss"], 98.0)


class BarsSinceEntryTests(unittest.TestCase):
    """Bars elapsed must come from the frame, not from how often we scanned.

    `bars_in_trade` was incremented once per evaluate_exit call, so at the 300s
    REGULAR cadence a 15m bar counted three times and every bar-denominated
    threshold fired 3x early -- the 24-bar time exit at 2h instead of 6h, the
    MULTIDAY momentum leash at 30m instead of 90m.
    """

    def _frame_from(self, start, periods):
        index = pd.date_range(start, periods=periods, freq="15min", tz="America/New_York")
        return pd.DataFrame({"Close": [100.0] * periods}, index=index)

    def test_counts_bars_not_scans(self):
        """Four 15m bars since entry reads 4, however many times we evaluated."""

        frame = self._frame_from("2026-07-29 14:00", 8)
        trade = {"opened_at_et": "2026-07-29T15:00:00-04:00", "bars_in_trade": 40}

        self.assertEqual(_bars_since_entry(frame, trade, fallback=41), 4)

    def test_first_evaluation_after_entry_still_reads_one(self):
        """Nothing shifts at the start of a trade."""

        frame = self._frame_from("2026-07-29 14:00", 5)
        trade = {"opened_at_et": "2026-07-29T15:00:00-04:00"}

        self.assertEqual(_bars_since_entry(frame, trade, fallback=1), 1)

    def test_entry_after_the_last_bar_is_still_bar_one(self):

        frame = self._frame_from("2026-07-29 14:00", 4)
        trade = {"opened_at_et": "2026-07-29T18:00:00-04:00"}

        self.assertEqual(_bars_since_entry(frame, trade, fallback=1), 1)

    def test_accepts_the_naive_et_opened_at_format(self):

        frame = self._frame_from("2026-07-29 14:00", 8)
        trade = {"opened_at": "2026-07-29 15:00:00"}

        self.assertEqual(_bars_since_entry(frame, trade, fallback=99), 4)

    def test_falls_back_when_the_entry_timestamp_is_unusable(self):

        frame = self._frame_from("2026-07-29 14:00", 8)

        self.assertEqual(_bars_since_entry(frame, {}, fallback=7), 7)
        self.assertEqual(_bars_since_entry(frame, None, fallback=7), 7)
        self.assertEqual(
            _bars_since_entry(frame, {"opened_at": "not a timestamp"}, fallback=7), 7
        )

    def test_falls_back_on_an_empty_frame(self):

        trade = {"opened_at_et": "2026-07-29T15:00:00-04:00"}

        self.assertEqual(_bars_since_entry(pd.DataFrame(), trade, fallback=3), 3)


class ClosedTradeRMultipleTests(unittest.TestCase):
    """The close-out path must use the same frozen denominator as the exit engine.

    `resolve_risk_per_share` was applied to rr_progress and mfe_r but not to the
    R booked at close, so a trade could be managed correctly and still be
    *recorded* against the moved stop. That biases the record against winners:
    reaching +1R is what moves the stop, so every trade that worked reported no R.
    """

    def test_breakeven_stop_no_longer_voids_r_at_close(self):
        """The 2026-07-29 NVDA close: entry and stop both 193.32."""

        result = _paper_trade_result(
            {
                "entry_price": 193.32,
                "stop_loss": 193.32,          # moved to breakeven during the trade
                "initial_stop_loss": 191.27,  # frozen entry risk
                "take_profit": 197.81,
                "direction": "CALL",
            },
            192.23,
        )

        # -1.09 against a frozen 2.05 risk.
        self.assertAlmostEqual(result["r_multiple"], -0.53, places=2)
        self.assertEqual(result["outcome"], "LOSS")

    def test_stop_trailed_into_profit_still_measures_r(self):
        """A trailed long stop sits *above* entry; the signed form went negative."""

        result = _paper_trade_result(
            {
                "entry_price": 100.0,
                "stop_loss": 103.0,           # trailed past entry
                "initial_stop_loss": 98.0,
                "take_profit": 110.0,
                "direction": "CALL",
            },
            104.0,
        )

        self.assertAlmostEqual(result["r_multiple"], 2.0, places=2)

    def test_short_side_uses_the_frozen_stop(self):

        result = _paper_trade_result(
            {
                "entry_price": 100.0,
                "stop_loss": 100.0,           # breakeven on a short
                "initial_stop_loss": 102.0,
                "take_profit": 94.0,
                "direction": "PUT",
            },
            98.0,
        )

        self.assertAlmostEqual(result["r_multiple"], 1.0, places=2)

    def test_legacy_trade_without_initial_stop_falls_back(self):
        """Trades opened before `initial_stop_loss` existed still report R."""

        result = _paper_trade_result(
            {
                "entry_price": 100.0,
                "stop_loss": 98.0,
                "take_profit": 106.0,
                "direction": "CALL",
            },
            102.0,
        )

        self.assertAlmostEqual(result["r_multiple"], 1.0, places=2)

    def test_no_usable_risk_reports_no_r_rather_than_a_wrong_one(self):

        result = _paper_trade_result(
            {
                "entry_price": 100.0,
                "stop_loss": 100.0,
                "initial_stop_loss": 100.0,
                "take_profit": 106.0,
                "direction": "CALL",
            },
            102.0,
        )

        self.assertIsNone(result["r_multiple"])
        self.assertAlmostEqual(result["pnl_pct"], 2.0, places=2)


if __name__ == "__main__":
    unittest.main()
