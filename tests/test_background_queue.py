import unittest

from app.background.background_queue import (
    get_background_metrics,
    run_background,
    wait_for_background_tasks,
)


class BackgroundQueueTests(unittest.TestCase):

    def test_runs_tasks_in_order_after_task_failure(self):

        results = []

        def failing_task():

            results.append("before failure")
            raise RuntimeError("expected test failure")

        run_background(results.append, "first")
        run_background(failing_task)
        run_background(results.append, "last")

        wait_for_background_tasks()

        self.assertEqual(
            results,
            [
                "first",
                "before failure",
                "last"
            ]
        )
        metrics = get_background_metrics()

        self.assertGreaterEqual(metrics["completed_jobs"], 2)
        self.assertGreaterEqual(metrics["failed_jobs"], 1)
        self.assertEqual(metrics["queue_depth"], 0)
        self.assertIsNotNone(metrics["average_job_time_sec"])


if __name__ == "__main__":

    unittest.main()
