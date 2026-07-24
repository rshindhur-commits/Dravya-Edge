import unittest
import pandas as pd
from app.analytics.performance_statistics import build_performance_statistics

class PerformanceStatisticsTests(unittest.TestCase):
    def test_builds_completed_trade_statistics(self):
        result = build_performance_statistics(pd.DataFrame({"r_multiple": [1.5, -0.5, 1.0]}))
        self.assertEqual(result["completed_trades"], 3)
        self.assertEqual(result["win_rate"], 66.7)
        self.assertEqual(result["profit_factor"], 5.0)