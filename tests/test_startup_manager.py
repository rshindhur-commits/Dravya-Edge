import unittest
from unittest.mock import patch

from app.runtime.startup_manager import startup


class StartupManagerTests(unittest.TestCase):

    def test_startup_writes_health_without_recovery_by_default(self):

        with patch("app.runtime.startup_manager.get_live_dir"), patch(
            "app.runtime.startup_manager.write_runtime_health",
            return_value={"healthy": True}
        ), patch(
            "app.runtime.startup_manager.recover_pending_telegram_dispatches"
        ) as recover:

            result = startup()

        self.assertTrue(result["live_dir_ready"])
        self.assertEqual(result["recovered_telegram"], [])
        recover.assert_not_called()

    def test_startup_recovers_telegram_when_enabled(self):

        with patch.dict("os.environ", {"TELEGRAM_RECOVER_ON_STARTUP": "true"}, clear=False), patch(
            "app.runtime.startup_manager.get_live_dir"
        ), patch(
            "app.runtime.startup_manager.write_runtime_health",
            return_value={"healthy": True}
        ), patch(
            "app.runtime.startup_manager.recover_pending_telegram_dispatches",
            return_value=[{"result": "queued"}]
        ):

            result = startup()

        self.assertEqual(result["recovered_telegram"], [{"result": "queued"}])


if __name__ == "__main__":

    unittest.main()