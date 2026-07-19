import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.runtime.runtime_priority import Priority
from app.runtime.shutdown_manager import ShutdownManager


class _Scheduler:

    def __init__(self, completed=True):

        self.completed = completed

    def wait_for(self, priority, timeout=None):

        return self.completed and priority == Priority.CRITICAL

    def metrics(self):

        return {
            "critical_jobs": 0 if self.completed else 1,
            "high_jobs": 0,
            "normal_jobs": 0,
            "low_jobs": 0,
        }


class ShutdownManagerTests(unittest.TestCase):

    def test_shutdown_writes_summary(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            path = Path(temp_dir) / "runtime_shutdown.json"

            manager = ShutdownManager(scheduler=_Scheduler(completed=True))

            with patch("app.runtime.shutdown_manager.live_path", return_value=path):

                payload = manager.shutdown()

        self.assertTrue(payload["critical_completed"])
        self.assertTrue(payload["db_flushed"])
        self.assertTrue(payload["telegram_flushed"])

    def test_shutdown_timeout_handling(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            path = Path(temp_dir) / "runtime_shutdown.json"
            manager = ShutdownManager(scheduler=_Scheduler(completed=False))

            with patch("app.runtime.shutdown_manager.live_path", return_value=path):
                payload = manager.shutdown()

        self.assertFalse(payload["critical_completed"])
        self.assertEqual(payload["queue_remaining"], 1)


if __name__ == "__main__":

    unittest.main()