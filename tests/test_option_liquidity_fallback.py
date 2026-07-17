import unittest
from unittest.mock import patch

from app.main import _select_liquid_option_from_bundle


class OptionLiquidityFallbackTests(unittest.TestCase):

    def test_falls_back_from_active_to_ranked_liquid_contract(self):

        option_bundle = {
            "active": {
                "ticker": "ACTIVE",
                "type": "call",
            },
            "primary": None,
            "affordable": None,
            "short_dte": None,
            "longer_dte": None,
            "ranked": [
                {
                    "ticker": "ACTIVE",
                    "type": "call",
                },
                {
                    "ticker": "FALLBACK",
                    "type": "call",
                },
            ],
        }

        def liquidity(candidate):

            if candidate.get("ticker") == "ACTIVE":

                return {
                    "liquid": False,
                    "code": "WIDE_SPREAD",
                    "reason": "Wide bid/ask spread",
                    "spread_pct": 18,
                }

            return {
                "liquid": True,
                "code": "LIQUID",
                "reason": "Healthy liquidity",
                "spread_pct": 4,
            }

        with patch("app.main.refresh_contract_quote", side_effect=lambda contract: contract), \
            patch("app.main.add_affordability_metrics", side_effect=lambda contract, config=None: contract), \
            patch("app.main.get_affordability_config", return_value={}), \
            patch("app.main.contract_matches_direction", return_value=True), \
            patch("app.main.evaluate_option_liquidity", side_effect=liquidity):

            selected, selected_liquidity, attempts = _select_liquid_option_from_bundle(
                option_bundle,
                "CALL"
            )

        self.assertEqual(selected["ticker"], "FALLBACK")
        self.assertTrue(selected_liquidity["liquid"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["code"], "WIDE_SPREAD")


if __name__ == "__main__":

    unittest.main()