"""The Validation panel for watchlist 2.6.

Rendered with a stubbed `st`, following the pattern in test_operator_console.
What matters here is not layout but that the three states are distinguishable:
nothing measured yet, measured and fine, measured and wrong. The first is the
easiest to get wrong -- an empty panel reads as "nothing to see", when it
actually means "no data exists yet".
"""

import unittest
from unittest.mock import patch

import app.dashboard as dashboard


class _Captured:
    """What the panel emitted, by streamlit call."""

    def __init__(self):
        self.markdown = []
        self.info = []
        self.warning = []
        self.dataframe = []
        self.caption = []


def _render(calibration):

    captured = _Captured()
    fake = type("St", (), {
        "markdown": staticmethod(lambda text, **_k: captured.markdown.append(text)),
        "info": staticmethod(lambda text, **_k: captured.info.append(text)),
        "warning": staticmethod(lambda text, **_k: captured.warning.append(text)),
        "dataframe": staticmethod(lambda frame, **_k: captured.dataframe.append(frame)),
        "caption": staticmethod(lambda text, **_k: captured.caption.append(text)),
    })

    with patch.object(dashboard, "st", fake), patch.object(
        dashboard, "_render_compact_card_grid", lambda cards: None
    ):
        dashboard._render_spread_calibration(calibration)

    return captured


class SpreadCalibrationPanelTests(unittest.TestCase):

    def test_no_data_says_so_rather_than_rendering_nothing(self):

        for empty in (None, {}, {"measurable_trades": 0}):

            captured = _render(empty)

            self.assertTrue(captured.info, f"no explanation rendered for {empty!r}")
            self.assertIn("eb56f75", captured.info[0])
            self.assertFalse(captured.warning)

    def test_clean_measurements_render_without_a_warning(self):

        captured = _render({
            "measurable_trades": 3,
            "high_score_wide_spread_count": 0,
            "quality_vs_cost_gap": 0.4,
            "rows": [{"symbol": "NVDA", "option_quality_score": 95}],
        })

        self.assertFalse(captured.info)
        self.assertFalse(captured.warning)
        self.assertEqual(len(captured.dataframe), 1)

    def test_two_flagged_trades_trip_the_watchlist_threshold(self):

        captured = _render({
            "measurable_trades": 4,
            "high_score_wide_spread_count": 2,
            "quality_vs_cost_gap": 0.5,
            "rows": [{"symbol": "NVDA"}],
        })

        self.assertTrue(any("2.6" in text for text in captured.warning))

    def test_one_flagged_trade_does_not_trip_it(self):
        """One is an anecdote; the threshold is deliberately two."""

        captured = _render({
            "measurable_trades": 4,
            "high_score_wide_spread_count": 1,
            "quality_vs_cost_gap": 0.5,
            "rows": [],
        })

        self.assertFalse(captured.warning)

    def test_widening_spreads_are_reported_as_a_distinct_problem(self):
        """A large entry-to-realised gap is not a scoring failure -- the spread
        moved while the position was held, which no entry-time score could catch."""

        captured = _render({
            "measurable_trades": 3,
            "high_score_wide_spread_count": 0,
            "quality_vs_cost_gap": 4.1,
            "rows": [],
        })

        self.assertTrue(any("widening" in text for text in captured.warning))


if __name__ == "__main__":

    unittest.main()
