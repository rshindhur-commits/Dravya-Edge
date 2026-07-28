"""Characterization tests — S0.3.

These pin CURRENT behaviour of the decision-critical surfaces so a later
refactor cannot change it silently. They are deliberately assertive about exact
numbers.

Read this before changing an expectation:

- A failure here means behaviour changed. That is a finding, not a broken test.
  Decide whether the change was intended, then update the pin in the same commit
  that changes the behaviour.
- These tests document what the code DOES, not what it SHOULD do. Where current
  behaviour looks wrong, it is pinned anyway and annotated with `NOTE:`.
- Settings are patched explicitly rather than read from `.env`, so results do not
  depend on the developer's local configuration.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

import pandas as pd

from app.config.settings import settings as live_settings
from app.exit.exit_engine import evaluate_exit
from app.gates.entry_gate import price_geometry_error, validate_price_geometry
from app.options.options_filter import evaluate_option_liquidity
from app.risk.position_sizing import calculate_position_size
from app.risk.risk_manager import calculate_risk


def _risk_frame(**overrides):
    """20 identical bars; calculate_risk reads iloc[-1] plus shift(1).tail(5)."""

    bar = {
        "High": 101.25,
        "Low": 99.55,
        "Close": 100.00,
        "ATR": 2.00,
        "EMA9": 99.80,
        "EMA20": 99.50,
        "VWAP": 99.40,
        "ROLLING_RESISTANCE": 104.00,
        "ROLLING_SUPPORT": 98.00,
        "PREV_HIGH": 103.00,
        "PREV_LOW": 98.50,
    }
    bar.update(overrides)

    return pd.DataFrame([bar] * 20)


def _exit_frame(**overrides):
    """Bar shaped for a healthy long trade; override to trigger specific exits."""

    bar = {
        "Open": 100.0,
        "High": 103.0,
        "Low": 99.5,
        "Close": 102.0,
        "ATR": 2.0,
        "EMA9": 101.0,
        "EMA20": 100.0,
        "EMA9_SLOPE": 0.5,
        "VWAP": 100.5,
        "MACD": 1.0,
        "MACD_SIGNAL": 0.5,
        "RSI": 60.0,
        "REL_VOLUME": 1.5,
        "HIGHER_HIGH": True,
        "HIGHER_LOW": True,
        "LOWER_HIGH": False,
        "LOWER_LOW": False,
        "FAILED_BREAKOUT": False,
    }
    bar.update(overrides)

    return pd.DataFrame([bar] * 10)


LONG_RISK_SETUP = {
    "entry_price": 100.0,
    "stop_loss": 98.0,
    "take_profit": 106.0,
}


class RiskManagerCharacterizationTests(unittest.TestCase):

    def test_ema_pullback_long_exact_geometry(self):

        result = calculate_risk(
            _risk_frame(),
            {"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
            {"entry_type": "EMA_PULLBACK", "entry_quality": "HIGH"},
        )

        self.assertEqual(result["entry_price"], 100.0)
        self.assertEqual(result["stop_loss"], 99.25)
        self.assertEqual(result["take_profit"], 104.2)
        self.assertEqual(result["risk_reward"], 5.6)
        self.assertTrue(result["trade_allowed"])

    def test_breakout_long_uses_recent_low_and_resistance(self):

        result = calculate_risk(
            _risk_frame(),
            {"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
            {"entry_type": "BREAKOUT", "entry_quality": "HIGH"},
        )

        # stop = min(recent_low, entry - 1.5*ATR); target = max(resistance+0.4, entry+3.5*ATR)
        self.assertEqual(result["stop_loss"], 97.0)
        self.assertEqual(result["take_profit"], 107.0)
        self.assertEqual(result["risk_reward"], 2.33)
        # NOTE: stop distance is 3.0% of entry, far above the 0.95% TRENDING cap,
        # so a textbook breakout on this fixture is rejected on stop width.
        self.assertFalse(result["trade_allowed"])
        self.assertIn(
            "Stop too wide: 3.0%",
            result["reasons"],
        )

    def test_ema_rejection_short_exact_geometry(self):

        result = calculate_risk(
            _risk_frame(),
            {"signal": "BEARISH", "market_regime": "TRENDING_BEAR"},
            {"entry_type": "EMA_REJECTION_SHORT", "entry_quality": "HIGH"},
        )

        # Structural stop = max(High+0.15*ATR, EMA9+0.10*ATR) = 101.55, but the
        # full-ATR floor widens it to entry + 2.00 and cuts RR from 2.32 to 1.80.
        self.assertEqual(result["stop_loss"], 102.0)
        self.assertEqual(result["take_profit"], 96.4)
        self.assertEqual(result["risk_reward"], 1.8)
        self.assertIn(
            "ATR floor adjusted stop: original_stop=101.55 adjusted_stop=102.0 "
            "rr_before=2.32 rr_after=1.8",
            result["reasons"],
        )

    def test_vwap_rejection_short_exact_geometry(self):

        result = calculate_risk(
            _risk_frame(),
            {"signal": "BEARISH", "market_regime": "TRENDING_BEAR"},
            {"entry_type": "VWAP_REJECTION", "entry_quality": "HIGH"},
        )

        # Structural stop = max(VWAP+0.15*ATR, High+0.10*ATR) = 101.45, again
        # widened to the full-ATR floor.
        self.assertEqual(result["stop_loss"], 102.0)
        self.assertEqual(result["take_profit"], 96.0)
        self.assertEqual(result["risk_reward"], 2.0)

    def test_regime_changes_atr_multipliers(self):

        setup = {"entry_type": "BREAKOUT", "entry_quality": "HIGH"}
        stops = {}

        for regime in [
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
            "CHOPPY",
            "TRENDING_BULL",
        ]:

            stops[regime] = calculate_risk(
                _risk_frame(),
                {"signal": "BULLISH", "market_regime": regime},
                setup,
            )["stop_loss"]

        # BREAKOUT stop = min(recent_low=99.55, entry - mult*ATR), then the
        # full-ATR floor pins anything closer than 2.00 to entry - 2.00.
        self.assertEqual(stops["HIGH_VOLATILITY"], 96.4)   # 1.8*ATR
        self.assertEqual(stops["LOW_VOLATILITY"], 98.0)    # 0.6*ATR -> floored
        self.assertEqual(stops["CHOPPY"], 98.0)            # 1.0*ATR, exactly at floor
        self.assertEqual(stops["TRENDING_BULL"], 97.0)     # 1.5*ATR

    def test_stop_width_gate_rejects_normal_atr_setups(self):
        """NOTE: this is the most consequential pin in this file.

        `max_stop_distance_pct` is 0.75% (base), 0.95% (trending), 1.15%
        (high-vol), 0.50% (low-vol) of entry price. On a $100 name with a $2.00
        ATR — 2% ATR, ordinary for this watchlist — every setup family below
        produces a stop wider than its cap and is rejected on width alone,
        regardless of RR.

        EMA_PULLBACK is the sole survivor, and only because its 0.25*ATR floor
        keeps the stop at 0.75%.
        """

        rejected = {}

        for entry_type, signal, regime in [
            ("BREAKOUT", "BULLISH", "TRENDING_BULL"),
            ("EMA_REJECTION_SHORT", "BEARISH", "TRENDING_BEAR"),
            ("VWAP_REJECTION", "BEARISH", "TRENDING_BEAR"),
        ]:

            result = calculate_risk(
                _risk_frame(),
                {"signal": signal, "market_regime": regime},
                {"entry_type": entry_type, "entry_quality": "HIGH"},
            )
            rejected[entry_type] = (
                result["trade_allowed"],
                [r for r in result["reasons"] if r.startswith("Stop too wide")],
            )

        for entry_type, (allowed, width_reasons) in rejected.items():

            self.assertFalse(allowed, entry_type)
            self.assertEqual(len(width_reasons), 1, entry_type)

        pullback = calculate_risk(
            _risk_frame(),
            {"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
            {"entry_type": "EMA_PULLBACK", "entry_quality": "HIGH"},
        )

        self.assertTrue(pullback["trade_allowed"])

    def test_neutral_signal_short_circuits(self):

        result = calculate_risk(
            _risk_frame(),
            {"signal": "NEUTRAL", "market_regime": "RANGE_BOUND"},
            {"entry_type": "BREAKOUT"},
        )

        self.assertFalse(result["trade_allowed"])
        self.assertEqual(result["risk_reward"], 0)
        self.assertIsNone(result["entry_price"])
        self.assertEqual(result["reasons"], ["Neutral trend environment"])

    def test_missing_entry_setup_short_circuits(self):

        result = calculate_risk(
            _risk_frame(),
            {"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
            None,
        )

        self.assertFalse(result["trade_allowed"])
        self.assertEqual(result["reasons"], ["No valid entry setup"])

    def test_ema_pullback_quarter_atr_stop_floor_applies(self):

        # Low/EMA9 sit close enough to entry that the raw stop is inside the floor.
        result = calculate_risk(
            _risk_frame(Low=99.95, EMA9=99.98),
            {"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
            {"entry_type": "EMA_PULLBACK", "entry_quality": "HIGH"},
        )

        # floor = 0.25 * ATR = 0.5 -> stop pinned to entry - 0.5
        self.assertEqual(result["stop_loss"], 99.5)
        self.assertTrue(
            any(
                reason.startswith("ATR floor adjusted stop")
                for reason in result["reasons"]
            )
        )


class ExitPrecedenceCharacterizationTests(unittest.TestCase):

    def test_hard_stop_outranks_every_soft_exit(self):

        result = evaluate_exit(
            _exit_frame(
                Low=97.0,          # hard stop
                Close=99.0,
                EMA9=100.0,
                EMA9_SLOPE=-0.5,   # EMA exit
                VWAP=100.5,        # VWAP exit
                MACD=0.1,
                MACD_SIGNAL=0.5,   # MACD exit
                FAILED_BREAKOUT=True,
            ),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
        )

        self.assertTrue(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "HARD_STOP")
        self.assertEqual(result["exit_reason"], "Hard stop hit (long)")
        self.assertEqual(result["trade_action"], "EXIT")
        self.assertGreater(len(result["secondary_exits"]), 0)

    def test_hard_target_selected_for_long(self):

        result = evaluate_exit(
            _exit_frame(High=107.0, Close=106.5),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
        )

        self.assertTrue(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "HARD_TARGET")

    def test_ema_outranks_vwap_when_both_fire(self):

        result = evaluate_exit(
            _exit_frame(
                Close=99.0,
                Low=98.5,
                EMA9=100.0,
                EMA9_SLOPE=-0.5,
                VWAP=100.5,
                MACD=1.0,
                MACD_SIGNAL=0.5,
            ),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT", "bars_in_trade": 10},
            {"bars_in_trade": 10},
        )

        self.assertTrue(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "EMA")
        self.assertIn("VWAP invalidation (long)", result["secondary_exits"])

    def test_vwap_selected_when_ema_intact(self):

        result = evaluate_exit(
            _exit_frame(
                Close=100.2,
                Low=99.9,
                EMA9=100.0,
                EMA9_SLOPE=0.5,
                VWAP=100.5,
            ),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
            {"bars_in_trade": 10},
        )

        self.assertTrue(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "VWAP")

    def test_failed_breakout_exit(self):

        result = evaluate_exit(
            _exit_frame(FAILED_BREAKOUT=True),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
            {"bars_in_trade": 10},
        )

        self.assertTrue(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "FAILED_BREAKOUT")

    def test_time_exit_requires_24_bars_and_low_progress(self):

        # Every higher-priority rule must be kept quiet: price above VWAP and
        # EMA9, MACD aligned, no failed breakout, stop/target untouched.
        stagnant = _exit_frame(
            Close=100.2,
            High=100.5,
            Low=99.9,
            EMA9=100.0,
            EMA9_SLOPE=0.1,
            VWAP=100.0,
        )

        early = evaluate_exit(
            stagnant,
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
            {"bars_in_trade": 10},
        )
        late = evaluate_exit(
            stagnant,
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
            {"bars_in_trade": 25},
        )

        self.assertNotEqual(early["exit_rule"], "TIME_EXIT")
        self.assertTrue(late["exit_signal"])
        self.assertEqual(late["exit_rule"], "TIME_EXIT")
        self.assertEqual(late["bars_in_trade"], 26)

    def test_early_weak_exit_guard_holds_trade(self):

        # bars_in_trade <= 3, |rr| < 0.25, weak reason, trend still valid
        result = evaluate_exit(
            _exit_frame(
                Close=100.1,
                High=100.3,
                Low=99.9,
                EMA9=100.2,
                EMA9_SLOPE=-0.1,
                VWAP=100.0,
            ),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
            {"bars_in_trade": 0},
        )

        self.assertFalse(result["exit_signal"])
        self.assertEqual(result["exit_reason"], "Hold")
        self.assertEqual(
            result["adjustment_reason"],
            "Early weak exit guarded; trend intact",
        )

    def test_rr_progress_and_stop_management_ladder(self):

        result = evaluate_exit(
            _exit_frame(Close=103.0, High=103.5, Low=102.0),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
            {"bars_in_trade": 5},
        )

        # rr = (103 - 100) / (100 - 98) = 1.5 -> breakeven stop + partial profit
        self.assertEqual(result["rr_progress"], 1.5)
        self.assertEqual(result["updated_stop"], 100.0)
        self.assertTrue(result["partial_profit_taken"])
        self.assertEqual(result["trade_action"], "PARTIAL_PROFIT")

    def test_hold_returns_hold_rule_and_no_exit_stage(self):

        result = evaluate_exit(
            _exit_frame(),
            {"signal": "BULLISH"},
            LONG_RISK_SETUP,
            {"entry_type": "BREAKOUT"},
            {"bars_in_trade": 5},
        )

        self.assertFalse(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "HOLD")
        self.assertIsNone(result["exit_stage"])
        self.assertEqual(result["exit_reason"], "Hold")


class PriceGeometryCharacterizationTests(unittest.TestCase):

    def test_valid_call_geometry(self):

        self.assertIsNone(price_geometry_error("CALL", 100, 98, 104))
        self.assertTrue(validate_price_geometry("CALL", 100, 98, 104))

    def test_invalid_call_geometry_is_named(self):

        self.assertEqual(
            price_geometry_error("CALL", 100, 104, 98),
            "INVALID_PRICE_GEOMETRY_CALL_REQUIRES_STOP_LT_ENTRY_LT_TARGET",
        )

    def test_valid_put_geometry(self):

        self.assertIsNone(price_geometry_error("PUT", 100, 104, 96))

    def test_invalid_put_geometry_is_named(self):

        self.assertEqual(
            price_geometry_error("PUT", 100, 96, 104),
            "INVALID_PRICE_GEOMETRY_PUT_REQUIRES_TARGET_LT_ENTRY_LT_STOP",
        )

    def test_missing_prices_reported_separately_from_invalid(self):

        self.assertEqual(
            price_geometry_error("CALL", None, 98, 104),
            "MISSING_PRICE_GEOMETRY",
        )

    def test_unknown_direction_is_not_an_error(self):

        # NOTE: a non-CALL/PUT direction short-circuits to None, so geometry is
        # NOT enforced for rows whose direction is missing or unrecognised.
        self.assertIsNone(price_geometry_error("NONE", 100, 104, 98))
        self.assertIsNone(price_geometry_error(None, 100, 104, 98))

    def test_equal_prices_are_rejected_for_call(self):

        self.assertIsNotNone(price_geometry_error("CALL", 100, 100, 104))
        self.assertIsNotNone(price_geometry_error("CALL", 100, 98, 100))


def _liquid_option(**overrides):

    option = {
        "bid": 2.30,
        "ask": 2.40,
        "open_interest": 5000,
        "volume": 2500,
        "quote_status": "OK",
        "quote_freshness": "LIVE_QUOTE",
        "quote_timeframe": "REALTIME",
        "expiration_bucket": "SWING",
        "option_quality_score": 90,
        "delta": 0.45,
        "dte": 21,
    }
    option.update(overrides)

    return option


AFFORDABLE_ENV = {
    "OPTION_AFFORDABILITY_MODE": "OFF",
    "OPTION_CAPITAL_PROFILE": "BEST_QUALITY",
}


class OptionLiquidityCharacterizationTests(unittest.TestCase):
    """Settings are patched so results do not depend on the local .env."""

    def setUp(self):

        # RuntimeSettings is a frozen dataclass, so build a substitute instance
        # rather than patching attributes on the live one.
        pinned = replace(
            live_settings,
            option_require_bid_ask=True,
            option_require_fresh_quote=True,
            option_min_open_interest=500,
            option_min_volume=100,
            option_max_spread_pct=10,
            option_min_quality_score=65,
            option_allow_0dte=False,
            option_allow_1dte=False,
        )
        settings_patch = patch(
            "app.options.options_filter.settings",
            pinned,
        )
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

        env_patch = patch.dict("os.environ", AFFORDABLE_ENV, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_clean_contract_passes(self):

        result = evaluate_option_liquidity(_liquid_option())

        self.assertTrue(result["liquid"])
        self.assertEqual(result["code"], "LIQUID")

    def test_missing_bid_ask_rejected_first(self):

        result = evaluate_option_liquidity(
            _liquid_option(bid=0, ask=0, open_interest=1, volume=1)
        )

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "MISSING_BID_ASK")

    def test_delayed_quote_timeframe_rejected(self):

        result = evaluate_option_liquidity(
            _liquid_option(quote_timeframe="DELAYED")
        )

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "DELAYED_QUOTE")

    def test_crossed_market_rejected(self):

        result = evaluate_option_liquidity(
            _liquid_option(bid=2.50, ask=2.40)
        )

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "CROSSED_MARKET")

    def test_low_open_interest_rejected(self):

        result = evaluate_option_liquidity(
            _liquid_option(open_interest=100)
        )

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "LOW_OPEN_INTEREST")

    def test_low_volume_rejected(self):

        result = evaluate_option_liquidity(
            _liquid_option(volume=10)
        )

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "LOW_VOLUME")

    def test_wide_spread_rejected(self):

        result = evaluate_option_liquidity(
            _liquid_option(bid=2.00, ask=2.60)
        )

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "WIDE_SPREAD")

    def test_low_quality_score_rejected(self):

        result = evaluate_option_liquidity(
            _liquid_option(option_quality_score=40)
        )

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "LOW_OPTION_QUALITY")

    def test_open_interest_checked_before_volume(self):

        result = evaluate_option_liquidity(
            _liquid_option(open_interest=100, volume=10)
        )

        self.assertEqual(result["code"], "LOW_OPEN_INTEREST")


class PositionSizingCharacterizationTests(unittest.TestCase):

    def test_baseline_sizing_numbers(self):

        result = calculate_position_size(
            account_size=2000,
            risk_percent=10,
            entry_price=100,
            stop_loss=98,
            option_price=2.50,
            max_contracts=10,
        )

        # max_risk = 200; option risk/contract = 2.50*0.20*100 = 50 -> 4 contracts
        self.assertEqual(result["max_risk"], 200)
        self.assertEqual(result["contracts"], 4)
        self.assertEqual(result["estimated_loss"], 200.0)
        self.assertEqual(result["estimated_profit"], 250.0)
        self.assertEqual(result["aggressiveness"], "Moderate")

    def test_decimal_risk_percent_is_scaled_to_percent(self):

        decimal_form = calculate_position_size(
            account_size=2000, risk_percent=0.10, entry_price=100,
            stop_loss=98, option_price=2.50, max_contracts=10,
        )
        percent_form = calculate_position_size(
            account_size=2000, risk_percent=10, entry_price=100,
            stop_loss=98, option_price=2.50, max_contracts=10,
        )

        # NOTE: risk_percent <= 1 is multiplied by 100, so 1.0 means 1%
        # but 0.99 means 99%. The boundary is a latent foot-gun.
        self.assertEqual(decimal_form["max_risk"], percent_form["max_risk"])

    def test_contracts_floor_at_one_even_when_unaffordable(self):

        result = calculate_position_size(
            account_size=100,
            risk_percent=1,
            entry_price=100,
            stop_loss=98,
            option_price=50.0,
            max_contracts=10,
        )

        # NOTE: sizing never returns 0 contracts; affordability is enforced
        # elsewhere (options_filter), not here.
        self.assertEqual(result["contracts"], 1)
        self.assertEqual(result["estimated_loss"], 1000.0)

    def test_max_contracts_clamp_applies(self):

        result = calculate_position_size(
            account_size=100000,
            risk_percent=10,
            entry_price=100,
            stop_loss=98,
            option_price=2.50,
            max_contracts=1,
        )

        self.assertEqual(result["contracts"], 1)
        self.assertEqual(result["aggressiveness"], "Conservative")

    def test_projection_drives_estimated_profit(self):

        result = calculate_position_size(
            account_size=2000,
            risk_percent=10,
            entry_price=100,
            stop_loss=98,
            option_price=2.50,
            max_contracts=10,
            projection={"projected_option_gain": 80},
        )

        self.assertEqual(result["estimated_profit"], 800.0)

    def test_invalid_input_returns_zeroed_result(self):

        result = calculate_position_size(
            account_size=2000,
            risk_percent=10,
            entry_price=100,
            stop_loss=98,
            option_price=None,
            max_contracts=1,
        )

        self.assertEqual(result["contracts"], 0)
        self.assertEqual(result["max_risk"], 0)
        self.assertEqual(result["aggressiveness"], "N/A")

    def test_aggressive_label_at_five_contracts(self):

        result = calculate_position_size(
            account_size=10000,
            risk_percent=10,
            entry_price=100,
            stop_loss=98,
            option_price=2.50,
            max_contracts=50,
        )

        self.assertEqual(result["contracts"], 20)
        self.assertEqual(result["aggressiveness"], "Aggressive")


if __name__ == "__main__":

    unittest.main()
