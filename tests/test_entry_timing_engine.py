import unittest

from app.analytics.entry_timing_engine import evaluate_entry_timing


class EntryTimingEngineTests(unittest.TestCase):

    def test_efficient_first_pullback_is_excellent(self):

        result = evaluate_entry_timing({
            "entry_efficiency_score": 92,
            "trend_age_bars": 2,
            "pullback_number": 1,
            "bars_since_breakout": 1,
            "ema9_extension_atr": 0.15,
            "vwap_extension_atr": 0.25,
        })

        self.assertGreater(result["entry_timing_score"], 80)
        self.assertEqual(result["entry_timing_grade"], "EXCELLENT")
        self.assertIn("FIRST_PULLBACK", result["entry_timing_reason"])

    def test_extended_mature_entry_is_late(self):

        result = evaluate_entry_timing({
            "entry_efficiency_score": 40,
            "trend_age_bars": 12,
            "pullback_number": 3,
            "bars_since_breakout": 9,
            "ema9_extension_atr": 1.8,
            "vwap_extension_atr": 2.1,
        })

        self.assertLess(result["entry_timing_score"], 55)
        self.assertEqual(result["entry_timing_grade"], "LATE_ENTRY")
        self.assertIn("MATURE_TREND", result["entry_timing_reason"])


if __name__ == "__main__":

    unittest.main()