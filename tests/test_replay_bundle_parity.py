"""Guard the two places that must agree about live's selection order.

``app/backtesting/contract_selector.py`` reproduces the bundle walk that
``app/main.py`` performs, and cannot import it -- main.py is the Streamlit
entrypoint. That leaves the order duplicated, so it is asserted here rather
than trusted: if someone adds a slot to ``_iter_option_bundle_candidates``,
this fails instead of the replay quietly selecting a contract live would not.
"""

import unittest

from app.backtesting.contract_selector import (
    LONGER_DTE_WINDOW,
    SHORT_DTE_WINDOW,
    BUNDLE_ORDER,
    _iter_bundle_candidates,
)
from app.main import _iter_option_bundle_candidates

BUNDLE = {
    "active": {"ticker": "ACTIVE"},
    "primary": {"ticker": "PRIMARY"},
    "affordable": {"ticker": "AFFORDABLE"},
    "short_dte": {"ticker": "SHORT"},
    "longer_dte": {"ticker": "LONGER"},
    "ranked": [
        {"ticker": "R1"},
        {"ticker": "R2"},
    ],
}


class BundleOrderParityTests(unittest.TestCase):

    def test_replay_walks_the_bundle_exactly_as_live_does(self):

        self.assertEqual(
            list(_iter_bundle_candidates(BUNDLE)),
            list(_iter_option_bundle_candidates(BUNDLE)),
        )

    def test_the_order_is_the_documented_one(self):
        """Both agreeing on the wrong order would still be wrong."""

        self.assertEqual(
            [source for source, _ in _iter_bundle_candidates(BUNDLE)],
            BUNDLE_ORDER + ["ranked #1", "ranked #2"],
        )

    def test_an_empty_bundle_yields_no_contracts(self):

        self.assertEqual(
            [c for _, c in _iter_bundle_candidates({}) if c],
            [],
        )

    def test_alternate_dte_windows_match_live(self):
        """The windows are literals in ``recommend_live_option_bundle``."""

        import inspect

        from app.options import options_recommender

        source = inspect.getsource(
            options_recommender.recommend_live_option_bundle
        )

        for low, high in [SHORT_DTE_WINDOW, LONGER_DTE_WINDOW]:

            self.assertIn(
                f"{low},\n            {high},",
                source,
                f"live no longer picks a {low}-{high} DTE alternate",
            )


if __name__ == "__main__":

    unittest.main()
