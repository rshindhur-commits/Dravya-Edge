import os
import unittest
from unittest.mock import patch

from app.config.settings import get_settings
from app.risk.position_sizing import calculate_position_size


class PositionSizingTests(unittest.TestCase):

    def test_settings_default_to_daily_option_capital_and_risk_pct(self):
        clear_keys = [
            "ACCOUNT_SIZE",
            "RISK_PERCENT",
        ]
        overrides = {
            "DAILY_START_CAPITAL": "2000",
            "OPTION_MAX_RISK_PER_TRADE_PCT": "0.10",
            "MAX_CONTRACTS_PER_TRADE": "1",
        }

        original_values = {
            key: os.environ.get(key)
            for key in clear_keys
        }

        for key in clear_keys:

            os.environ.pop(key, None)

        with patch.dict(os.environ, overrides, clear=False):
            settings = get_settings()

        for key, value in original_values.items():

            if value is None:

                os.environ.pop(key, None)

            else:

                os.environ[key] = value

        self.assertEqual(settings.account_size, 2000)
        self.assertEqual(settings.risk_percent, 10)
        self.assertEqual(settings.max_contracts_per_trade, 1)

    def test_position_sizing_accepts_decimal_risk_percent(self):
        result = calculate_position_size(
            account_size=2000,
            risk_percent=0.10,
            entry_price=100,
            stop_loss=98,
            option_price=5,
            max_contracts=5,
        )

        self.assertEqual(result["max_risk"], 200)

    def test_position_sizing_respects_max_contracts_per_trade(self):
        result = calculate_position_size(
            account_size=2000,
            risk_percent=10,
            entry_price=100,
            stop_loss=98,
            option_price=1,
            max_contracts=1,
        )

        self.assertEqual(result["contracts"], 1)

    def test_position_sizing_uses_configured_option_stop_loss_pct(self):
        result = calculate_position_size(
            account_size=2000,
            risk_percent=10,
            entry_price=100,
            stop_loss=98,
            option_price=5,
            max_contracts=1,
            option_stop_loss_pct=0.20,
        )

        self.assertEqual(result["estimated_loss"], 100.0)


if __name__ == "__main__":
    unittest.main()
