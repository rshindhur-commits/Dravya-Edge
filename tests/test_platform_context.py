import unittest
from app.analytics.market_regime import evaluate_market_regime
from app.analytics.trade_lifecycle import evaluate_trade_lifecycle

class PlatformContextTests(unittest.TestCase):
    def test_regime_and_lifecycle_adapters(self):
        self.assertEqual(evaluate_market_regime("TRENDING_BULL", 2)["state"], "STRONG_BULL")
        self.assertEqual(evaluate_trade_lifecycle({"entry_price": 100, "mfe_r": 1.2})["phase"], "EXPANSION")