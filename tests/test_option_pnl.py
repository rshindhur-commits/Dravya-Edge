import math
import unittest
from datetime import datetime, timedelta

from app.economics.option_pnl import (
    MARKET_TZ,
    SOURCE_ESTIMATE,
    black_scholes_price,
    implied_volatility,
    option_pnl_estimated,
    parse_occ_ticker,
    reprice_option,
    time_to_expiry_years,
)
from app.economics.trade_costs import DEGRADED, OK, UNAVAILABLE, CostModel


# Pinned to the parameters the S1.2 fixtures were computed under, so a change
# to CostModel's defaults cannot silently move the recorded numbers.
MODEL = CostModel(tick_size=0.05, stop_exit_spread_multiplier=1.5)

ENTRY_AT = datetime(2026, 7, 27, 16, 0, tzinfo=MARKET_TZ)

SPOT = 196.1337
STOP = 195.424912
TARGET = 197.769364

RATE = 0.04


def trade(ticker, bid, ask, stop, contracts=1):
    return {
        "option_ticker": ticker,
        "option_bid": bid,
        "option_ask": ask,
        "entry_price": SPOT,
        "stop_loss": stop,
        "contracts": contracts,
        "opened_at": ENTRY_AT,
        "risk_free_rate": RATE,
    }


FX_WINNER = trade("O:NVDA260821C00205000", 4.95, 5.05, STOP)
FX_LOSER = trade("O:NVDA260821P00190000", 5.45, 5.55, 196.842488)
FX_STOPOUT = trade("O:NVDA260814C00205000", 3.85, 4.00, STOP)


class TestContractParsing(unittest.TestCase):

    def test_op_01_parses_a_standard_occ_ticker(self):
        parsed = parse_occ_ticker("O:NVDA260821C00205000")

        self.assertEqual(parsed["status"], OK)
        self.assertEqual(parsed["underlying"], "NVDA")
        self.assertEqual(parsed["option_type"], "CALL")
        self.assertAlmostEqual(parsed["strike"], 205.0, places=6)
        self.assertEqual(parsed["expiry"].isoformat(), "2026-08-21")

    def test_op_02_parses_fractional_strike_and_put(self):
        parsed = parse_occ_ticker("O:NVDA260821P00192500")

        self.assertEqual(parsed["option_type"], "PUT")
        self.assertAlmostEqual(parsed["strike"], 192.5, places=6)

    def test_op_03_malformed_ticker_is_unavailable_without_raising(self):
        for bad in [None, "", "NOTATICKER", "O:NVDA26082XC00205000", "O:NVDA269921C00205000"]:
            parsed = parse_occ_ticker(bad)

            self.assertEqual(parsed["status"], UNAVAILABLE)
            self.assertIsNone(parsed["strike"])


class TestBlackScholes(unittest.TestCase):

    def test_op_04_put_call_parity_holds(self):
        years = 25 / 365
        call = black_scholes_price(SPOT, 205.0, years, 0.41, RATE, "CALL")
        put = black_scholes_price(SPOT, 205.0, years, 0.41, RATE, "PUT")

        parity = SPOT - 205.0 * math.exp(-RATE * years)

        self.assertAlmostEqual(call["price"] - put["price"], parity, places=9)

    def test_op_05_deep_itm_near_expiry_approaches_intrinsic(self):
        priced = black_scholes_price(300.0, 205.0, 1e-5, 0.41, RATE, "CALL")

        self.assertAlmostEqual(priced["price"], 95.0, places=3)
        self.assertAlmostEqual(priced["delta"], 1.0, places=3)

    def test_op_06_far_otm_is_positive_and_near_zero(self):
        priced = black_scholes_price(100.0, 205.0, 25 / 365, 0.41, RATE, "CALL")

        self.assertGreaterEqual(priced["price"], 0.0)
        self.assertLess(priced["price"], 0.01)

    def test_op_07_implied_vol_round_trips(self):
        years = 25 / 365
        priced = black_scholes_price(SPOT, 205.0, years, 0.41, RATE, "CALL")
        inverted = implied_volatility(priced["price"], SPOT, 205.0, years, RATE, "CALL")

        self.assertEqual(inverted["status"], OK)
        self.assertTrue(inverted["converged"])
        self.assertAlmostEqual(inverted["iv"], 0.41, places=6)

    def test_op_08_price_below_intrinsic_is_unavailable(self):
        inverted = implied_volatility(1.0, 300.0, 205.0, 25 / 365, RATE, "CALL")

        self.assertEqual(inverted["status"], UNAVAILABLE)
        self.assertEqual(inverted["reason"], "PRICE_BELOW_INTRINSIC")
        self.assertIsNone(inverted["iv"])

    def test_op_09_unreachable_price_terminates_deterministically(self):
        inverted = implied_volatility(195.0, SPOT, 205.0, 25 / 365, RATE, "CALL")

        self.assertEqual(inverted["status"], UNAVAILABLE)
        self.assertEqual(inverted["reason"], "PRICE_ABOVE_MODEL_RANGE")
        self.assertLessEqual(inverted["iterations"], 200)

    def test_op_10_zero_time_returns_intrinsic_and_degrades(self):
        priced = black_scholes_price(SPOT, 195.0, 0.0, 0.41, RATE, "CALL")

        self.assertEqual(priced["status"], DEGRADED)
        self.assertEqual(priced["reason"], "TIME_TO_EXPIRY_BELOW_FLOOR")
        self.assertAlmostEqual(priced["price"], SPOT - 195.0, places=6)

    def test_op_11_expiry_before_as_of_is_unavailable(self):
        repriced = reprice_option(
            "O:NVDA260821C00205000",
            SPOT,
            datetime(2026, 9, 1, 10, 0, tzinfo=MARKET_TZ),
            0.41,
            RATE,
        )

        self.assertEqual(repriced["status"], UNAVAILABLE)
        self.assertEqual(repriced["reason"], "EXPIRED_BEFORE_AS_OF")
        self.assertIsNone(repriced["price"])

    def test_invalid_volatility_is_unavailable(self):
        self.assertEqual(
            black_scholes_price(SPOT, 205.0, 25 / 365, 0.0, RATE, "CALL")["status"],
            UNAVAILABLE,
        )

    def test_op_16_theta_over_an_intraday_hold_is_negligible_at_21_dte(self):
        opened = black_scholes_price(SPOT, 200.0, 21 / 365, 0.41, RATE, "CALL")
        held = black_scholes_price(SPOT, 200.0, (21 - 2 / 24) / 365, 0.41, RATE, "CALL")

        decay = opened["price"] - held["price"]

        self.assertGreater(decay, 0.0)
        self.assertLess(decay / opened["extrinsic"], 0.005)


class TestFixtures(unittest.TestCase):

    """Asserts the S1.2 fixture numbers recorded in docs/specs/S1.1-trade-economics.md."""

    def test_fx_winner(self):
        result = option_pnl_estimated(
            FX_WINNER, TARGET, ENTRY_AT + timedelta(hours=2), MODEL, "HARD_TARGET"
        )

        self.assertEqual(result["status"], OK)
        self.assertAlmostEqual(result["entry_iv"], 0.407499, places=5)
        self.assertAlmostEqual(result["entry_fill"], 5.05, places=6)
        self.assertAlmostEqual(result["premium_at_exit_est"], 5.612911, places=4)
        self.assertAlmostEqual(result["exit_fill"], 5.55, places=6)
        self.assertAlmostEqual(result["premium_at_stop_est"], 4.729714, places=4)
        self.assertAlmostEqual(result["stop_fill"], 4.65, places=6)
        self.assertAlmostEqual(result["risk_dollars_net"], 41.409417, places=3)
        self.assertAlmostEqual(result["r_multiple_gross"], 2.267635, places=3)
        self.assertAlmostEqual(result["r_multiple_net"], 1.173358, places=3)

    def test_fx_loser(self):
        result = option_pnl_estimated(
            FX_LOSER, 196.460833, ENTRY_AT + timedelta(hours=3), MODEL, "EMA"
        )

        self.assertEqual(result["status"], OK)
        self.assertAlmostEqual(result["entry_iv"], 0.418091, places=5)
        self.assertAlmostEqual(result["entry_fill"], 5.55, places=6)
        self.assertAlmostEqual(result["premium_at_exit_est"], 5.365670, places=4)
        self.assertAlmostEqual(result["exit_fill"], 5.30, places=6)
        self.assertAlmostEqual(result["premium_at_stop_est"], 5.233429, places=4)
        self.assertAlmostEqual(result["risk_dollars_net"], 41.410807, places=3)
        self.assertAlmostEqual(result["r_multiple_gross"], -0.503919, places=3)
        self.assertAlmostEqual(result["r_multiple_net"], -0.637786, places=3)

    def test_fx_stopout_returns_exactly_minus_one_r(self):
        result = option_pnl_estimated(
            FX_STOPOUT, STOP, ENTRY_AT + timedelta(hours=1.5), MODEL, "HARD_STOP"
        )

        self.assertEqual(result["status"], OK)
        self.assertAlmostEqual(result["entry_iv"], 0.417038, places=5)
        self.assertAlmostEqual(result["entry_fill"], 4.00, places=6)
        self.assertAlmostEqual(result["exit_fill"], 3.55, places=6)
        self.assertAlmostEqual(result["risk_dollars_net"], 46.406359, places=3)
        self.assertAlmostEqual(result["r_multiple_gross"], -1.0, places=9)
        self.assertAlmostEqual(result["r_multiple_net"], -1.0, places=9)

    def test_stopout_identity_holds_for_every_fixture_contract(self):
        for fixture in [FX_WINNER, FX_STOPOUT]:
            result = option_pnl_estimated(
                fixture,
                fixture["stop_loss"],
                ENTRY_AT + timedelta(hours=2),
                MODEL,
                "HARD_STOP",
            )

            self.assertAlmostEqual(result["r_multiple_net"], -1.0, places=9)


class TestAssemblyProperties(unittest.TestCase):

    def test_op_12_option_pnl_never_flips_sign_with_direction(self):
        winner_call = option_pnl_estimated(
            FX_WINNER, TARGET, ENTRY_AT + timedelta(hours=2), MODEL, "HARD_TARGET"
        )
        winner_put = option_pnl_estimated(
            FX_LOSER, 194.0, ENTRY_AT + timedelta(hours=2), MODEL, "HARD_TARGET"
        )

        for winner in [winner_call, winner_put]:
            self.assertGreater(winner["pnl_option_est"], 0.0)
            self.assertGreater(winner["pnl_underlying_est"], 0.0)
            self.assertGreater(winner["r_multiple_net"], 0.0)

    def test_op_13_repricing_at_entry_yields_zero_gross_pnl(self):
        result = option_pnl_estimated(FX_WINNER, SPOT, ENTRY_AT, MODEL, "HARD_TARGET")

        self.assertAlmostEqual(result["pnl_option_gross"], 0.0, places=6)

    def test_op_14_long_options_are_convex_for_the_holder(self):
        years = 25 / 365
        entry = black_scholes_price(SPOT, 205.0, years, 0.41, RATE, "CALL")
        move = SPOT - STOP

        down = black_scholes_price(STOP, 205.0, years, 0.41, RATE, "CALL")
        up = black_scholes_price(SPOT + move, 205.0, years, 0.41, RATE, "CALL")

        linear = entry["delta"] * move

        self.assertLess(entry["price"] - down["price"], linear)
        self.assertGreater(up["price"] - entry["price"], linear)

    def test_op_15_implied_stop_loss_pct_is_computed_not_the_constant(self):
        result = option_pnl_estimated(
            FX_WINNER, TARGET, ENTRY_AT + timedelta(hours=2), MODEL, "HARD_TARGET"
        )

        self.assertIsNotNone(result["implied_stop_loss_pct"])
        self.assertLess(result["implied_stop_loss_pct"], 10.0)
        self.assertNotAlmostEqual(result["implied_stop_loss_pct"], 20.0, places=1)

    def test_op_17_missing_entry_quote_returns_none_never_zero(self):
        broken = dict(FX_WINNER)
        broken["option_bid"] = None
        broken["option_ask"] = None

        result = option_pnl_estimated(
            broken, TARGET, ENTRY_AT + timedelta(hours=2), MODEL, "HARD_TARGET"
        )

        self.assertEqual(result["status"], UNAVAILABLE)
        self.assertEqual(result["reason"], "MISSING_ENTRY_QUOTE")

        for key in ["r_multiple_net", "r_multiple_gross", "pnl_option_est", "cost_total"]:
            self.assertIsNone(result[key], f"{key} must be None, never 0.0")

    def test_op_18_full_chain_on_a_real_contract_is_clean(self):
        result = option_pnl_estimated(
            FX_WINNER, TARGET, ENTRY_AT + timedelta(hours=2), MODEL, "HARD_TARGET"
        )

        self.assertEqual(result["status"], OK)
        self.assertIsNone(result["reason"])
        self.assertEqual(result["source"], SOURCE_ESTIMATE)
        self.assertEqual(result["confidence"], "MEDIUM")

    def test_crossed_entry_quote_is_unavailable(self):
        broken = dict(FX_WINNER)
        broken["option_bid"] = 5.05
        broken["option_ask"] = 4.95

        result = option_pnl_estimated(
            broken, TARGET, ENTRY_AT + timedelta(hours=2), MODEL, "HARD_TARGET"
        )

        self.assertEqual(result["status"], UNAVAILABLE)
        self.assertEqual(result["reason"], "CROSSED_MARKET")

    def test_time_to_expiry_matches_whole_days_at_market_close(self):
        parsed = parse_occ_ticker("O:NVDA260821C00205000")
        years = time_to_expiry_years(parsed["expiry"], ENTRY_AT)

        self.assertAlmostEqual(years, 25 / 365, places=9)


if __name__ == "__main__":
    unittest.main()
