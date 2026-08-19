import os
import unittest
from unittest.mock import patch

import pandas as pd

from app.analytics.trade_ranker import (
    CONTRACT_SCORE_BUDGET,
    DEFAULT_LEVERAGE_WEIGHT,
    premium_pct_of_notional,
    rank_candidates,
)


class TradeRankerTests(unittest.TestCase):

    def test_ranker_prioritizes_high_quality_candidate(self):

        ranked = rank_candidates(pd.DataFrame([
            {
                "Symbol": "NFLX",
                "Setup %": 94,
                "Entry Timing Score": 92,
                "V2 Trend Health Status": "STRONG",
                "Option Quality Score": 90,
                "RS Rank Score": 4,
                "Option Liquidity Passed": True,
            },
            {
                "Symbol": "AMZN",
                "Setup %": 74,
                "Entry Timing Score": 60,
                "V2 Trend Health Status": "WEAKENING",
                "Option Quality Score": 70,
                "RS Rank Score": 0,
                "Option Spread %": 8,
            },
        ]))

        nflx = ranked.loc[ranked["Symbol"].eq("NFLX")].iloc[0]
        amzn = ranked.loc[ranked["Symbol"].eq("AMZN")].iloc[0]
        self.assertEqual(nflx["Candidate Rank"], 1)
        self.assertGreater(
            nflx["Trade Quality Score"],
            amzn["Trade Quality Score"]
        )
        self.assertIn("setup=94", nflx["Rank Reason"])


class LeveragePreferenceTests(unittest.TestCase):
    """Premium as a percentage of notional, which R is blind to.

    2026-08-03: ORCL and SMCI both went the right way. ORCL booked +2.41R and
    +26.4% of premium against a contract costing 2.6% of notional; SMCI booked
    +1.0R and +5.1% against one costing 9.5%.
    """

    def setUp(self):
        # Production sets RANK_LEVERAGE_WEIGHT=0, which switches this preference
        # off entirely -- `trade_ranker.py:189` notes that zero reproduces the
        # original 0.15:0.10 proportion. These cases are about what the weight
        # *does*, so they pin it to the default instead of reading whatever is
        # deployed. Before `.env` was synced to Render on 2026-08-19 they passed
        # only because the local file omitted the variable.
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("RANK_LEVERAGE_WEIGHT", None)

    def _pair(self):
        """The two trades of 2026-08-03, with everything but leverage equal."""

        shared = {
            "Setup %": 85,
            "Entry Timing Score": 80,
            "V2 Trend Health Status": "HEALTHY",
            "Option Quality Score": 95,
            "RS Rank Score": 2,
            "Option Liquidity Passed": True,
        }

        return pd.DataFrame([
            {"Symbol": "ORCL", "Candidate Entry Price": 137.38,
             "Option Mid Price": 3.70, **shared},
            {"Symbol": "SMCI", "Candidate Entry Price": 28.62,
             "Option Mid Price": 2.645, **shared},
        ])

    def test_the_cheaper_contract_outranks_the_richer_one(self):

        ranked = rank_candidates(self._pair())
        by_symbol = ranked.set_index("Symbol")

        self.assertEqual(by_symbol.loc["ORCL", "Candidate Rank"], 1)
        self.assertGreater(
            by_symbol.loc["ORCL", "Trade Quality Score"],
            by_symbol.loc["SMCI", "Trade Quality Score"],
        )

    def test_the_premium_ratio_is_published(self):

        ranked = rank_candidates(self._pair()).set_index("Symbol")

        self.assertAlmostEqual(
            ranked.loc["ORCL", "Option Premium % of Notional"], 2.69, places=2)
        self.assertAlmostEqual(
            ranked.loc["SMCI", "Option Premium % of Notional"], 9.24, places=2)

    def test_a_zero_weight_reproduces_the_previous_score_exactly(self):
        """The dial has to be a true no-op, or it is not a safe way back."""

        frame = self._pair()

        with patch.dict("os.environ", {"RANK_LEVERAGE_WEIGHT": "0"}):
            without = rank_candidates(frame)

        # Recomputed by hand on the old weights: setup .25, timing .20, trend .20,
        # option .15, relative strength .10, liquidity .10.
        expected = round(
            85 * 0.25 + 80 * 0.20 + 75 * 0.20 + 95 * 0.15 + 70 * 0.10 + 100 * 0.10,
            2,
        )

        for score in without["Trade Quality Score"]:
            self.assertEqual(score, expected)

    def test_the_weights_still_sum_to_one(self):

        for weight in (0.0, 0.05, DEFAULT_LEVERAGE_WEIGHT, CONTRACT_SCORE_BUDGET):
            with patch.dict("os.environ", {"RANK_LEVERAGE_WEIGHT": str(weight)}):
                # A row scoring 100 on every component must score 100 overall.
                ranked = rank_candidates(pd.DataFrame([{
                    "Symbol": "NVDA",
                    "Setup %": 100,
                    "Entry Timing Score": 100,
                    "V2 Trend Health Status": "STRONG",
                    "Option Quality Score": 100,
                    "RS Rank Score": 5,
                    "Option Liquidity Passed": True,
                    "Candidate Entry Price": 100.0,
                    "Option Mid Price": 2.0,
                }]))

                self.assertAlmostEqual(
                    ranked.iloc[0]["Trade Quality Score"], 100.0, places=2,
                    msg=f"weight={weight}")

    def test_an_unpriced_contract_is_neutral_not_penalised(self):
        """A quote-feed gap must not look like an expensive contract."""

        ranked = rank_candidates(pd.DataFrame([{
            "Symbol": "NVDA",
            "Setup %": 85,
            "Entry Timing Score": 80,
            "V2 Trend Health Status": "HEALTHY",
            "Option Quality Score": 95,
            "RS Rank Score": 2,
            "Option Liquidity Passed": True,
            "Candidate Entry Price": 207.55,
            "Option Mid Price": None,
        }]))

        self.assertTrue(pd.isna(ranked.iloc[0]["Option Premium % of Notional"]))

        # Scored at the neutral 50, not at 0. setup 85 x .25 + timing 80 x .20 +
        # trend 75 x .20 + rs 70 x .10 + option 95 x .09 + liquidity 100 x .06 +
        # leverage 50 x .10.
        self.assertAlmostEqual(
            ranked.iloc[0]["Trade Quality Score"], 78.80, places=2)

    def test_premium_ratio_needs_both_legs(self):

        self.assertIsNone(premium_pct_of_notional({"Option Mid Price": 2.0}))
        self.assertIsNone(premium_pct_of_notional({"Candidate Entry Price": 100.0}))
        self.assertIsNone(premium_pct_of_notional(
            {"Option Mid Price": 0, "Candidate Entry Price": 100.0}))


if __name__ == "__main__":

    unittest.main()