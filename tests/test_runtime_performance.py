import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app.runtime.runtime_jobs import RuntimeJob
from app.runtime.runtime_performance import (
    append_runtime_performance,
    build_runtime_performance_summary,
    write_runtime_state,
)
from app.runtime.runtime_scheduler import Priority, RuntimeScheduler


class RuntimePerformanceTests(unittest.TestCase):

    def test_append_runtime_performance_writes_csv(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            data_dir = Path(temp_dir)

            with patch(
                "app.runtime.runtime_performance.DATA_DIR",
                data_dir
            ):

                row = append_runtime_performance(
                    category="scanner",
                    stage="decision_engine",
                    seconds=0.1234,
                    metadata={"symbol": "NVDA"}
                )

            output = data_dir / "runtime_performance.csv"
            self.assertTrue(output.exists())
            self.assertIn("decision_engine", output.read_text(encoding="utf-8"))
            self.assertEqual(row["category"], "scanner")

    def test_write_runtime_state_writes_json(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            output_path = Path(temp_dir) / "runtime_state.json"

            with patch(
                "app.runtime.runtime_performance.live_path",
                return_value=output_path
            ):

                state = write_runtime_state({
                    "scanner_running": False,
                    "critical_jobs": 1,
                })

            self.assertTrue(output_path.exists())
            self.assertEqual(state["critical_jobs"], 1)

    def test_build_runtime_performance_summary(self):

        import pandas as pd

        summary = build_runtime_performance_summary(
            performance_df=pd.DataFrame([
                {
                    "category": "dashboard",
                    "stage": "page_render",
                    "page": "Trading",
                    "seconds": 0.5,
                },
                {
                    "category": "dashboard",
                    "stage": "page_render",
                    "page": "Trading",
                    "seconds": 1.0,
                },
            ]),
            metrics_df=pd.DataFrame([
                {
                    "job_name": "persist_scan_outputs",
                    "queue_runtime": 2.0,
                }
            ])
        )

        self.assertEqual(summary["performance_rows"], 2)
        self.assertEqual(summary["metrics_rows"], 1)
        self.assertEqual(summary["average_seconds_by_stage"][0]["average_seconds"], 0.75)
        self.assertEqual(summary["average_runtime_by_job"][0]["average_runtime"], 2.0)

    def test_priority_order_values(self):

        self.assertLess(Priority.CRITICAL, Priority.HIGH)
        self.assertLess(Priority.HIGH, Priority.NORMAL)
        self.assertLess(Priority.NORMAL, Priority.LOW)

    def test_runtime_scheduler_executes_submitted_job(self):

        scheduler = RuntimeScheduler()
        done = Event()
        results = []

        def task():

            results.append("ran")
            done.set()

        with patch("app.runtime.runtime_scheduler.write_runtime_state"), patch(
            "app.runtime.runtime_scheduler.append_runtime_metric"
        ):

            scheduler.submit(RuntimeJob(
                name="test_task",
                priority=Priority.HIGH,
                func=task,
            ))

            self.assertTrue(done.wait(2))

        self.assertEqual(results, ["ran"])

    def test_runtime_scheduler_waits_for_submitted_job(self):

        scheduler = RuntimeScheduler()
        results = []

        def task():

            results.append("done")

        with patch("app.runtime.runtime_scheduler.write_runtime_state"), patch(
            "app.runtime.runtime_scheduler.append_runtime_metric"
        ) as append_metric:

            scheduler.submit(RuntimeJob(
                name="waited_task",
                priority=Priority.NORMAL,
                func=task,
            ))
            scheduler.wait_for_jobs()

        self.assertEqual(results, ["done"])
        append_metric.assert_called_once()

    def test_runtime_scheduler_cancels_old_queued_jobs(self):

        scheduler = RuntimeScheduler()
        old_job = RuntimeJob(
            name="old_report",
            priority=Priority.LOW,
            func=lambda: None,
            scan_id="old_scan",
        )

        with scheduler._lock:

            scheduler._records[old_job.job_id] = type(
                "Record",
                (),
                {
                    "job": old_job,
                    "submitted_at": time.perf_counter(),
                    "status": "QUEUED",
                    "started_at": None,
                    "finished_at": None,
                }
            )()

        with patch("app.runtime.runtime_scheduler.write_runtime_state"):

            canceled = scheduler.cancel_old_jobs("new_scan")

        self.assertEqual(canceled, 1)
        self.assertEqual(scheduler._records[old_job.job_id].status, "CANCELED")

    def test_runtime_scheduler_tracks_scanner_running(self):

        scheduler = RuntimeScheduler()

        with patch("app.runtime.runtime_scheduler.write_runtime_state"):

            scheduler.set_scanner_running(True)
            self.assertTrue(scheduler.metrics()["scanner_running"])
            scheduler.set_scanner_running(False)
            self.assertFalse(scheduler.metrics()["scanner_running"])


if __name__ == "__main__":

    unittest.main()