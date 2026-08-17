"""Cash and efficiency figures on the Validation page.

Both blocks were dark before 2026-08-16 and for different reasons, so the tests
guard the two failure shapes rather than the arithmetic.

The premium block was gated on `option_entry_ask`, which the repository never
lifted out of the JSONB payload, so `priced_trades` was structurally zero and the
whole cash-terms row was absent for 17 measurable trades. The efficiency block
read `trend_capture` and `left_on_table` from that same payload, where nothing has
ever written them -- they live in `trade_exit_analysis`.

The cash figures exist because a percentage average is silent about size. The
window that prompted them read -6.7% average premium P&L across 17 trades, and in
cash was -$143 total against a single -$207 position: every other trade combined
made +$64.
"""

import unittest

import pandas as pd

from datetime import date

from app.analytics.config_timeline import _changes
from app.analytics.performance_statistics import (
    build_performance_statistics,
    exit_reason_summary,
    trade_efficiency_summary,
)


def _priced(symbol, net, dollars):
    return {
        "symbol": symbol,
        "r_multiple": 0.1,
        "option_entry_ask": 2.0,
        "option_pnl_pct_net": net,
        "option_pl_dollars": dollars,
    }


class CashTests(unittest.TestCase):

    def _frame(self):
        """The shape of the real window: one disaster, a profitable remainder."""

        return pd.DataFrame([
            _priced("SMCI", -99.5, -207.0),
            _priced("CRWD", 5.71, 20.0),
            _priced("NFLX", 4.0, 14.0),
            _priced("TSLA", 7.5, 30.0),
        ])

    def test_cash_totals_are_reported(self):

        stats = build_performance_statistics(self._frame())

        self.assertEqual(stats["priced_dollar_trades"], 4)
        self.assertEqual(stats["total_option_pl_dollars"], -143.0)
        self.assertEqual(stats["average_option_pl_dollars"], -35.75)

    def test_the_dominant_loss_is_isolated_and_named(self):
        """The point of the panel: -$143 and +$64 are opposite conclusions."""

        stats = build_performance_statistics(self._frame())

        self.assertEqual(stats["worst_trade_symbol"], "SMCI")
        self.assertEqual(stats["worst_trade_dollars"], -207.0)
        self.assertEqual(stats["total_ex_worst_dollars"], 64.0)

    def test_median_sits_beside_the_mean(self):
        """A mean containing -99.5% describes no trade that was taken."""

        stats = build_performance_statistics(self._frame())

        self.assertEqual(stats["average_option_pnl_pct"], -20.57)
        self.assertEqual(stats["median_option_pnl_pct"], 4.86)

    def test_an_all_winning_window_has_no_worst_trade(self):
        """The minimum is then the smallest gain, and excluding it says nothing."""

        stats = build_performance_statistics(pd.DataFrame([
            _priced("CRWD", 5.71, 20.0),
            _priced("NFLX", 4.0, 14.0),
        ]))

        self.assertEqual(stats["total_option_pl_dollars"], 34.0)
        self.assertIsNone(stats["worst_trade_dollars"])
        self.assertIsNone(stats["worst_trade_symbol"])
        self.assertIsNone(stats["total_ex_worst_dollars"])

    def test_trades_without_a_frozen_entry_ask_contribute_nothing(self):
        """Unpriced is not break-even; see `_premium_measurable`."""

        stats = build_performance_statistics(pd.DataFrame([
            {"r_multiple": -1.0, "option_pnl_pct_net": -6.0, "option_pl_dollars": -30.0},
        ]))

        self.assertEqual(stats["priced_dollar_trades"], 0)
        self.assertIsNone(stats["total_option_pl_dollars"])
        self.assertEqual(stats["completed_trades"], 1)


class EfficiencyTests(unittest.TestCase):

    def test_capture_reports_the_count_it_was_measured_on(self):
        """Capture comes from the post-market review, MFE from the trade itself.

        Reporting the mean alone put a figure measured on 17 trades under a panel
        headed 29.
        """

        summary = trade_efficiency_summary(pd.DataFrame([
            {"trend_capture": 50.0, "left_on_table": 0.4, "mfe_r": 0.5},
            {"trend_capture": None, "left_on_table": None, "mfe_r": 0.7},
            {"trend_capture": None, "left_on_table": None, "mfe_r": 0.3},
        ]))

        self.assertEqual(summary["trades"], 3)
        self.assertEqual(summary["trend_capture_trades"], 1)
        self.assertEqual(summary["mfe_r_trades"], 3)

    def test_median_capture_survives_an_unbounded_tail(self):
        """Capture is bounded at +100 and has no floor, so its mean is hostage."""

        summary = trade_efficiency_summary(pd.DataFrame([
            {"trend_capture": -880.0},
            {"trend_capture": 0.0},
            {"trend_capture": 50.0},
        ]))

        self.assertEqual(summary["trend_capture"], -276.667)
        self.assertEqual(summary["trend_capture_median"], 0.0)

    def test_a_frame_without_review_columns_reports_none_not_zero(self):

        summary = trade_efficiency_summary(pd.DataFrame([{"mfe_r": 0.5}]))

        self.assertEqual(summary["trades"], 1)
        self.assertIsNone(summary["trend_capture"])
        self.assertEqual(summary["trend_capture_trades"], 0)


class RConcentrationTests(unittest.TestCase):

    def test_the_worst_trade_is_isolated_from_the_r_total(self):
        """-21.78R total and +1.89R without one trade support opposite calls."""

        stats = build_performance_statistics(pd.DataFrame([
            {"symbol": "SMCI", "r_multiple": -23.67},
            {"symbol": "CRWD", "r_multiple": 0.79},
            {"symbol": "TSLA", "r_multiple": 1.10},
        ]))

        self.assertEqual(stats["worst_r"], -23.67)
        self.assertEqual(stats["worst_r_symbol"], "SMCI")
        self.assertEqual(stats["total_r_ex_worst"], 1.89)

    def test_an_all_winning_window_has_no_worst_trade(self):

        stats = build_performance_statistics(pd.DataFrame([
            {"symbol": "CRWD", "r_multiple": 0.79},
            {"symbol": "TSLA", "r_multiple": 1.10},
        ]))

        self.assertIsNone(stats["worst_r"])
        self.assertIsNone(stats["total_r_ex_worst"])


class ExitReasonTests(unittest.TestCase):

    def _frame(self):
        return pd.DataFrame([
            {"symbol": "SMCI", "exit_reason": "Hard stop hit (short)", "r_multiple": -23.67,
             "option_entry_ask": 2.08, "option_pnl_pct_net": -99.5, "option_pl_dollars": -207.0},
            {"symbol": "CRWD", "exit_reason": "Profit target reached (long)", "r_multiple": 2.38,
             "option_entry_ask": 3.5, "option_pnl_pct_net": 19.9, "option_pl_dollars": 280.0},
            {"symbol": "NFLX", "exit_reason": "EMA9 invalidation (long)", "r_multiple": -0.21,
             "option_entry_ask": 1.31, "option_pnl_pct_net": -5.1, "option_pl_dollars": -54.0},
            # Recorded, but with no premium marks to price it.
            {"symbol": "PLTR", "exit_reason": "EMA9 invalidation (long)", "r_multiple": -0.30},
        ])

    def test_worst_cash_comes_first(self):
        """The question is which rule to look at, not which fires most."""

        rows = exit_reason_summary(self._frame())

        self.assertEqual(
            [row["exit_reason"] for row in rows],
            [
                "Hard stop hit (short)",
                "EMA9 invalidation (long)",
                "Profit target reached (long)",
            ],
        )

    def test_unpriced_trades_are_counted_but_not_valued(self):
        """Folding them in as zero would understate the reason."""

        rows = {row["exit_reason"]: row for row in exit_reason_summary(self._frame())}
        ema9 = rows["EMA9 invalidation (long)"]

        self.assertEqual(ema9["trades"], 2)
        self.assertEqual(ema9["priced"], 1)
        self.assertEqual(ema9["total_dollars"], -54.0)
        self.assertEqual(ema9["avg_r"], -0.26)

    def test_a_reason_with_no_priced_trade_sorts_last(self):
        """An absence of measurement is not evidence of a cheap rule."""

        rows = exit_reason_summary(pd.DataFrame([
            {"symbol": "AAPL", "exit_reason": "Unmeasured", "r_multiple": 0.5},
            {"symbol": "SMCI", "exit_reason": "Costly", "r_multiple": -1.0,
             "option_entry_ask": 2.0, "option_pnl_pct_net": -50.0, "option_pl_dollars": -100.0},
        ]))

        self.assertEqual([row["exit_reason"] for row in rows], ["Costly", "Unmeasured"])
        self.assertIsNone(rows[1]["total_dollars"])

    def test_a_frame_without_the_column_yields_nothing(self):

        self.assertEqual(exit_reason_summary(pd.DataFrame([{"r_multiple": 1.0}])), [])


class ConfigTimelineTests(unittest.TestCase):

    def _daily(self):
        return [
            (date(2026, 8, 12), {"option_max_spread_pct": 2.0, "max_daily_entries": 5}),
            (date(2026, 8, 13), {"option_max_spread_pct": 2.0, "max_daily_entries": 5}),
            (date(2026, 8, 14), {"option_max_spread_pct": 3.0, "max_daily_entries": 5}),
        ]

    def test_a_change_inside_the_window_is_reported(self):

        changes = _changes(self._daily(), date(2026, 8, 13))

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["day"], "2026-08-14")
        self.assertEqual(changes[0]["setting"], "Option spread ceiling")
        self.assertEqual((changes[0]["from"], changes[0]["to"]), (2.0, 3.0))

    def test_days_before_the_window_are_baseline_not_news(self):
        """They are read to establish what the window started from."""

        changes = _changes(self._daily(), date(2026, 8, 15))

        self.assertEqual(changes, [])

    def test_unwatched_keys_are_ignored(self):
        """Listing everything would bury the levers that move which trades exist."""

        changes = _changes(
            [
                (date(2026, 8, 13), {"some_debug_flag": False}),
                (date(2026, 8, 14), {"some_debug_flag": True}),
            ],
            date(2026, 8, 13),
        )

        self.assertEqual(changes, [])

    def test_a_key_appearing_for_the_first_time_is_not_a_change(self):
        """A snapshot that gained a field did not move the lever."""

        changes = _changes(
            [
                (date(2026, 8, 13), {}),
                (date(2026, 8, 14), {"option_max_spread_pct": 3.0}),
            ],
            date(2026, 8, 13),
        )

        self.assertEqual(changes, [])


class FreshnessTests(unittest.TestCase):
    """A redraw is not a refresh, and the page has to say which it is reporting."""

    class _St:
        def __init__(self):
            self.captions = []

        def caption(self, text, **_kwargs):
            self.captions.append(text)

    def test_the_read_time_is_reported_not_the_draw_time(self):

        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from app.ui.pages.validation import _render_freshness

        fetched = datetime.now(ZoneInfo("America/New_York")) - timedelta(minutes=12)
        st = self._St()

        _render_freshness(st, fetched.isoformat(), 900)

        self.assertIn(f"{fetched:%H:%M:%S}", st.captions[0])
        self.assertIn("12 min ago", st.captions[0])
        self.assertIn("cached for 15 minutes", st.captions[0])

    def test_an_unparseable_stamp_says_so_rather_than_guessing(self):

        from app.ui.pages.validation import _render_freshness

        st = self._St()

        _render_freshness(st, "not a timestamp", 900)

        self.assertEqual(st.captions, ["Data age unknown."])


if __name__ == "__main__":
    unittest.main()
