import unittest

from app.options.option_affordability import add_affordability_metrics


SMALL_ACCOUNT_CONFIG = {
    "mode": "HARD",
    "profile_name": "SMALL_ACCOUNT",
    "daily_start_capital": 1000.0,
    "option_stop_loss_pct": 0.20,
    "max_risk_per_trade_pct": 0.12,
    "min_contract_cost": 100.0,
    "preferred_max_contract_cost": 500.0,
    "max_contract_cost": 650.0,
    "min_affordable_delta": 0.25,
}


class OptionAffordabilityTests(unittest.TestCase):

    def test_risk_based_cap_overrides_static_max_contract_cost(self):
        contract = add_affordability_metrics(
            {
                "mid_price": 6.50,
                "delta": 0.40,
            },
            config=SMALL_ACCOUNT_CONFIG,
        )

        self.assertEqual(contract["contract_cost"], 650.0)
        self.assertEqual(contract["risk_at_stop"], 130.0)
        self.assertEqual(contract["max_allowed_risk"], 120.0)
        self.assertEqual(contract["risk_based_max_contract_cost"], 600.0)
        self.assertEqual(contract["max_allowed_contract_cost"], 600.0)
        self.assertFalse(contract["affordable"])
        self.assertEqual(contract["affordability_status"], "OPTION_TOO_EXPENSIVE")

    def test_contract_at_risk_based_cap_is_affordable(self):
        contract = add_affordability_metrics(
            {
                "mid_price": 6.00,
                "delta": 0.40,
            },
            config=SMALL_ACCOUNT_CONFIG,
        )

        self.assertEqual(contract["contract_cost"], 600.0)
        self.assertEqual(contract["risk_at_stop"], 120.0)
        self.assertEqual(contract["max_allowed_contract_cost"], 600.0)
        self.assertTrue(contract["affordable"])
        self.assertEqual(contract["affordability_status"], "AFFORDABLE")

    def test_preferred_affordable_cannot_exceed_risk_cap(self):
        config = {
            **SMALL_ACCOUNT_CONFIG,
            "preferred_max_contract_cost": 700.0,
        }
        contract = add_affordability_metrics(
            {
                "mid_price": 6.50,
                "delta": 0.40,
            },
            config=config,
        )

        self.assertFalse(contract["preferred_affordable"])
        self.assertFalse(contract["affordable"])
        self.assertEqual(contract["affordability_status"], "OPTION_TOO_EXPENSIVE")


if __name__ == "__main__":
    unittest.main()
