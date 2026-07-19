import importlib
import unittest
from unittest.mock import patch

import app.config.performance as performance


class PerformanceConfigTests(unittest.TestCase):

    def test_default_performance_mode(self):

        self.assertTrue(performance.PERFORMANCE_MODE["TRADING"]["lazy_imports"])
        self.assertTrue(performance.TRADING_DASHBOARD_STATE_ONLY)
        self.assertEqual(performance.TRADING_CACHE_TTL, 5)
        self.assertEqual(performance.VALIDATION_CACHE_TTL, 60)
        self.assertEqual(performance.DEVELOPER_CACHE_TTL, 120)

    def test_env_overrides_cache_ttls(self):

        with patch.dict(
            "os.environ",
            {
                "PERFORMANCE_TRADING_CACHE_TTL": "3",
                "PERFORMANCE_VALIDATION_CACHE_TTL": "90",
                "PERFORMANCE_DEVELOPER_CACHE_TTL": "180",
                "PERFORMANCE_DASHBOARD_STATE_ONLY": "false",
            },
            clear=False,
        ):

            module = importlib.reload(performance)

        try:

            self.assertEqual(module.TRADING_CACHE_TTL, 3)
            self.assertEqual(module.VALIDATION_CACHE_TTL, 90)
            self.assertEqual(module.DEVELOPER_CACHE_TTL, 180)
            self.assertFalse(module.TRADING_DASHBOARD_STATE_ONLY)

        finally:

            importlib.reload(performance)


if __name__ == "__main__":

    unittest.main()