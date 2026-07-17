import unittest

import pandas as pd

from app.analytics import engine_health


class EngineHealthTests(unittest.TestCase):

    def test_build_engine_health_tolerates_nan_integer_metrics(self):

        original_read_csv = engine_health._read_csv
        original_load_latest_engine_health = engine_health.load_latest_engine_health
        original_load_latest_stage_profile = engine_health.load_latest_stage_profile

        try:

            engine_health._read_csv = lambda path: pd.DataFrame()
            engine_health.load_latest_stage_profile = lambda report_date: pd.DataFrame()
            engine_health.load_latest_engine_health = lambda report_date: {
                "runtime": float("nan"),
                "workers": float("nan"),
                "requests": float("nan"),
                "polygon_requests": float("nan"),
                "polygon_cache_hits": float("nan"),
                "polygon_cache_misses": float("nan"),
                "average_api_time": float("nan"),
                "average_cache_read_time": float("nan"),
                "background_pending_jobs": float("nan"),
                "background_completed_jobs": float("nan"),
                "background_failed_jobs": float("nan"),
                "background_queue_depth": float("nan"),
                "background_longest_job_time": float("nan"),
                "background_longest_job_name": float("nan"),
                "background_average_job_time": float("nan"),
                "exceptions": float("nan"),
                "average_symbol_runtime": float("nan"),
                "symbols_completed": float("nan"),
                "symbols_failed": float("nan"),
                "health_score": float("nan"),
            }

            health = engine_health.build_engine_health("2099-01-01")

        finally:

            engine_health._read_csv = original_read_csv
            engine_health.load_latest_engine_health = original_load_latest_engine_health
            engine_health.load_latest_stage_profile = original_load_latest_stage_profile

        self.assertEqual(health.cache_hits, 0)
        self.assertEqual(health.cache_misses, 0)
        self.assertEqual(health.background_pending_jobs, 0)
        self.assertEqual(health.exceptions, 0)
        self.assertEqual(health.symbols_completed, 0)
        self.assertEqual(health.symbols_failed, 0)
        self.assertEqual(health.health_score, 100)


if __name__ == "__main__":

    unittest.main()