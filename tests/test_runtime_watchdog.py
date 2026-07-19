import unittest

from app.runtime.runtime_watchdog import evaluate_runtime_health


class RuntimeWatchdogTests(unittest.TestCase):

    def test_healthy_runtime(self):

        health = evaluate_runtime_health(runtime_state={})

        self.assertTrue(health["healthy"])
        self.assertEqual(health["errors"], [])

    def test_queue_overflow_warning(self):

        health = evaluate_runtime_health(runtime_state={"low_jobs": 51})

        self.assertTrue(health["warnings"])

    def test_worker_dead_error(self):

        health = evaluate_runtime_health(runtime_state={}, worker_alive=False)

        self.assertFalse(health["healthy"])

    def test_scanner_timeout(self):

        health = evaluate_runtime_health(
            runtime_state={
                "scanner_running": True,
                "updated_at_utc": "2020-01-01T00:00:00+00:00",
            }
        )

        self.assertFalse(health["healthy"])

    def test_dashboard_stale(self):

        health = evaluate_runtime_health(
            dashboard_state={"generated_at": "2020-01-01T00:00:00+00:00"}
        )

        self.assertFalse(health["healthy"])


if __name__ == "__main__":

    unittest.main()