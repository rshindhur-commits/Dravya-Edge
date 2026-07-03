import unittest
import pandas as pd

from app.gates.entry_gate import (
    EntryGateConfig,
    evaluate_entry_gate,
    price_geometry_error,
    validate_price_geometry
)
from app.risk.risk_manager import calculate_risk


def _base_row(direction, entry, stop, target):

    return {
        "Action Status": "REVIEW_TV_CHART",
        "Candidate Direction": direction,
        "Candidate Entry Price": entry,
        "Candidate Stop Price": stop,
        "Candidate Target Price": target,
        "Setup %": 90,
        "Candidate RR": 2.2,
        "Option Quality Score": 90,
        "Option Quote Freshness": "LIVE_QUOTE",
        "Option Spread %": 2.5,
        "Affordable": True,
    }


class PriceGeometryTests(unittest.TestCase):

    def test_put_price_geometry_must_have_target_below_entry_and_stop_above_entry(self):

        self.assertFalse(
            validate_price_geometry(
                "PUT",
                entry=197.66,
                stop=195.69,
                target=202.03
            )
        )

    def test_valid_put_price_geometry(self):

        self.assertTrue(
            validate_price_geometry(
                "PUT",
                entry=197.66,
                stop=199.50,
                target=193.50
            )
        )

    def test_valid_call_price_geometry(self):

        self.assertTrue(
            validate_price_geometry(
                "CALL",
                entry=244.29,
                stop=242.18,
                target=248.99
            )
        )

    def test_entry_gate_blocks_invalid_put_price_geometry(self):

        allowed, reason = evaluate_entry_gate(
            _base_row(
                "PUT",
                entry=197.66,
                stop=195.69,
                target=202.03
            ),
            EntryGateConfig(),
            mode="paper"
        )

        self.assertFalse(allowed)
        self.assertEqual(
            reason,
            "INVALID_PRICE_GEOMETRY"
        )

    def test_price_geometry_error_explains_invalid_put(self):

        error = price_geometry_error(
            _base_row(
                "PUT",
                entry=197.66,
                stop=195.69,
                target=202.03
            )
        )

        self.assertEqual(
            error,
            "INVALID_PRICE_GEOMETRY_PUT_REQUIRES_TARGET_LT_ENTRY_LT_STOP"
        )

    def test_bearish_active_trade_risk_rejects_bullish_geometry(self):

        df = pd.DataFrame([
            {
                "High": 199.0,
                "Low": 195.0,
                "Close": 197.66,
                "ATR": 1.25,
                "VWAP": 196.50,
            }
        ] * 20)
        result = calculate_risk(
            df=df,
            analysis={
                "signal": "BEARISH",
                "market_regime": "TRENDING_BEAR"
            },
            entry_setup={
                "entry_type": "ACTIVE_TRADE",
                "entry_quality": "HIGH",
                "avoid_chasing": False
            }
        )

        self.assertFalse(result["trade_allowed"])
        self.assertIn(
            "INVALID_PRICE_GEOMETRY",
            result["reasons"]
        )


if __name__ == "__main__":

    unittest.main()