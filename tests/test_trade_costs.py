import unittest

from app.economics.trade_costs import (
    DEGRADED,
    OK,
    UNAVAILABLE,
    CostModel,
    entry_cost,
    exit_proceeds,
    fill_price,
    is_stop_exit,
    resolve_tick_size,
    round_to_tick,
    round_trip_costs,
)


MODEL = CostModel(tick_size=0.05)


def quote(bid, ask):
    return {"bid": bid, "ask": ask}


class TestFillModel(unittest.TestCase):

    def test_tc_01_zero_spread_leaves_only_commission_and_fees(self):
        costs = round_trip_costs(quote(5.00, 5.00), quote(5.00, 5.00), 1, MODEL, "HARD_TARGET")

        self.assertEqual(costs["status"], OK)
        self.assertAlmostEqual(costs["spread_component"], 0.0, places=9)
        self.assertAlmostEqual(costs["commission_component"], 1.30, places=6)
        self.assertGreater(costs["fee_component"], 0.0)

    def test_tc_02_ten_percent_spread_costs_sixty_dollars_round_trip(self):
        costs = round_trip_costs(quote(5.70, 6.30), quote(5.70, 6.30), 1, MODEL, "HARD_TARGET")

        self.assertAlmostEqual(costs["spread_component"], 60.0, places=6)

    def test_tc_03_zero_aggression_reproduces_mid_fills(self):
        model = CostModel(tick_size=0.05, entry_fill_aggression=0.0, exit_fill_aggression=0.0)
        costs = round_trip_costs(quote(5.70, 6.30), quote(5.70, 6.30), 1, model, "HARD_TARGET")

        self.assertAlmostEqual(costs["spread_component"], 0.0, places=9)

    def test_tc_04_half_aggression_crosses_half_the_spread(self):
        model = CostModel(tick_size=0.01, entry_fill_aggression=0.5, exit_fill_aggression=0.5)
        costs = round_trip_costs(quote(5.70, 6.30), quote(5.70, 6.30), 1, model, "HARD_TARGET")

        self.assertAlmostEqual(costs["spread_component"], 30.0, places=6)

    def test_tc_05_stop_multiplier_applies_only_to_stop_exits(self):
        stopped = round_trip_costs(quote(3.85, 4.00), quote(3.85, 4.00), 1, MODEL, "HARD_STOP")
        target = round_trip_costs(quote(3.85, 4.00), quote(3.85, 4.00), 1, MODEL, "HARD_TARGET")

        self.assertEqual(stopped["exit_spread_multiplier"], 1.5)
        self.assertEqual(target["exit_spread_multiplier"], 1.0)
        self.assertGreater(stopped["spread_component"], target["spread_component"])

    def test_tc_05b_unclassified_exit_reason_degrades_and_does_not_widen(self):
        costs = round_trip_costs(quote(3.85, 4.00), quote(3.85, 4.00), 1, MODEL, None)

        self.assertEqual(costs["status"], DEGRADED)
        self.assertIn("EXIT_REASON_UNCLASSIFIED", costs["reason"])
        self.assertEqual(costs["exit_spread_multiplier"], 1.0)

    def test_tc_06_friction_scales_linearly_with_contracts(self):
        one = round_trip_costs(quote(5.70, 6.30), quote(5.70, 6.30), 1, MODEL, "HARD_TARGET")
        three = round_trip_costs(quote(5.70, 6.30), quote(5.70, 6.30), 3, MODEL, "HARD_TARGET")

        self.assertAlmostEqual(three["total_friction"], one["total_friction"] * 3, places=6)

    def test_tc_07_crossed_market_is_unavailable_not_negative(self):
        result = fill_price(None, 6.30, 5.70, "BUY", 1.0, 0.05)

        self.assertEqual(result["status"], UNAVAILABLE)
        self.assertEqual(result["reason"], "CROSSED_MARKET")
        self.assertIsNone(result["fill"])

    def test_tc_08_sell_side_fees_absent_on_the_buy_leg(self):
        buy = entry_cost(5.00, 1, MODEL)
        sell = exit_proceeds(5.00, 1, MODEL)

        self.assertNotIn("finra_taf", buy["breakdown"])
        self.assertNotIn("sec_fee", buy["breakdown"])
        self.assertGreater(sell["breakdown"]["finra_taf"], 0.0)
        self.assertGreater(sell["breakdown"]["sec_fee"], 0.0)

    def test_tc_09_rounding_is_always_away_from_the_trader(self):
        buy = fill_price(5.02, 5.00, 5.04, "BUY", 1.0, 0.05)
        sell = fill_price(5.02, 5.00, 5.04, "SELL", 1.0, 0.05)

        self.assertAlmostEqual(buy["fill"], 5.05, places=6)
        self.assertAlmostEqual(sell["fill"], 5.00, places=6)

    def test_tc_10_tick_tier_switches_at_three_dollars(self):
        cheap = resolve_tick_size(2.50, CostModel())
        rich = resolve_tick_size(3.50, CostModel())

        self.assertAlmostEqual(cheap["tick_size"], 0.01, places=6)
        self.assertAlmostEqual(rich["tick_size"], 0.05, places=6)
        self.assertEqual(cheap["status"], DEGRADED)
        self.assertEqual(cheap["reason"], "TICK_SIZE_INFERRED")

    def test_tc_10b_explicit_tick_size_is_not_degraded(self):
        resolved = resolve_tick_size(2.50, MODEL)

        self.assertEqual(resolved["status"], OK)
        self.assertAlmostEqual(resolved["tick_size"], 0.05, places=6)

    def test_tc_11_non_positive_contracts_are_unavailable(self):
        for contracts in [0, -1]:
            self.assertEqual(entry_cost(5.00, contracts, MODEL)["status"], UNAVAILABLE)
            self.assertEqual(exit_proceeds(5.00, contracts, MODEL)["status"], UNAVAILABLE)

    def test_tc_12_commission_percentage_cap_binds(self):
        model = CostModel(
            tick_size=0.05,
            commission_per_contract=5.00,
            commission_max_pct_of_premium=0.001,
        )
        leg = entry_cost(5.00, 1, model)

        self.assertAlmostEqual(leg["commission"], 0.50, places=6)

    def test_missing_quote_is_unavailable_and_never_zero(self):
        result = fill_price(None, None, None, "BUY", 1.0, 0.05)

        self.assertEqual(result["status"], UNAVAILABLE)
        self.assertIsNone(result["fill"])
        self.assertIsNone(result["slippage_per_share"])

    def test_round_to_tick_is_exact_on_tick_boundaries(self):
        self.assertAlmostEqual(round_to_tick(5.05, 0.05, True), 5.05, places=9)
        self.assertAlmostEqual(round_to_tick(5.05, 0.05, False), 5.05, places=9)

    def test_is_stop_exit_classification(self):
        self.assertTrue(is_stop_exit("HARD_STOP"))
        self.assertFalse(is_stop_exit("HARD_TARGET"))
        self.assertIsNone(is_stop_exit(None))


if __name__ == "__main__":
    unittest.main()
