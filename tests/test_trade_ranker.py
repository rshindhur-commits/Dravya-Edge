import unittest

import pandas as pd

from app.analytics.trade_ranker import rank_candidates


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


if __name__ == "__main__":

    unittest.main()