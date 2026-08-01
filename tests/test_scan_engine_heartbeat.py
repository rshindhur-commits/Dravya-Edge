"""Scan engine heartbeat: the dashboard's only view of a scanner on another host.

Once scanning moves to the Render worker, `scan_supervisor.status()` describes a
thread that does not exist in the Streamlit process. Everything here protects the
distinction between "no engine in this process" and "no engine anywhere", and the
one configuration that must never go unnoticed: two engines scanning at once.
"""

import unittest
from unittest.mock import patch

from app.runtime.scan_engine_heartbeat import (
    build_heartbeat,
    record_heartbeat,
    scan_engine_owner,
    summarize_engines,
)


class OwnerTests(unittest.TestCase):

    def test_defaults_to_dashboard(self):

        with patch.dict("os.environ", {}, clear=True):

            self.assertEqual(scan_engine_owner(), "dashboard")

    def test_env_var_moves_ownership_without_a_deploy(self):
        """The cutover switch. It has to be environment-driven because a deploy
        replaces the container, killing the in-flight scan."""

        with patch.dict("os.environ", {"SCAN_ENGINE_OWNER": "WORKER"}, clear=False):

            self.assertEqual(scan_engine_owner(), "worker")

    def test_blank_falls_back_rather_than_producing_an_empty_key(self):

        with patch.dict("os.environ", {"SCAN_ENGINE_OWNER": "   "}, clear=False):

            self.assertEqual(scan_engine_owner(), "dashboard")


class HeartbeatShapeTests(unittest.TestCase):

    def test_instance_is_keyed_on_owner_not_host(self):
        """A Render redeploy changes the hostname. Host-keyed rows would
        accumulate one per deploy and show phantom duplicate engines."""

        first = build_heartbeat("IDLE", owner="worker")

        with patch(
            "app.runtime.scan_engine_heartbeat._hostname", return_value="other-host"
        ):
            second = build_heartbeat("IDLE", owner="worker")

        self.assertEqual(first["instance_id"], second["instance_id"])
        self.assertEqual(first["instance_id"], "worker")
        self.assertNotEqual(first["hostname"], second["hostname"])

    def test_fields_pass_through_and_payload_is_isolated(self):

        heartbeat = build_heartbeat(
            "SCANNING", owner="worker", scans=4, failures=1, payload={"a": 1}
        )

        self.assertEqual(heartbeat["status"], "SCANNING")
        self.assertEqual(heartbeat["scans"], 4)
        self.assertEqual(heartbeat["failures"], 1)
        self.assertEqual(heartbeat["payload"], {"a": 1})

    def test_a_failed_write_never_propagates(self):
        """A heartbeat is telemetry. It must not be able to stop a scan."""

        with patch(
            "app.db.scan_engine_heartbeat_repository.ScanEngineHeartbeatRepository.upsert",
            side_effect=RuntimeError("db down"),
        ):
            heartbeat = record_heartbeat("IDLE", owner="worker")

        self.assertEqual(heartbeat["status"], "IDLE")


class SummaryTests(unittest.TestCase):

    def test_two_live_engines_are_a_conflict(self):
        """The cutover failure worth shouting about: both engines scanning,
        double-opening positions that the file-based scan lock cannot serialise
        across two filesystems."""

        summary = summarize_engines([
            {"owner": "worker", "age_seconds": 30},
            {"owner": "dashboard", "age_seconds": 60},
        ])

        self.assertTrue(summary["conflict"])
        self.assertEqual(summary["live_count"], 2)
        self.assertEqual(summary["owners"], ["dashboard", "worker"])

    def test_one_live_engine_is_not_a_conflict(self):

        summary = summarize_engines([{"owner": "worker", "age_seconds": 30}])

        self.assertFalse(summary["conflict"])
        self.assertEqual(summary["live_count"], 1)

    def test_a_stale_engine_is_not_counted_as_live(self):
        """Stale means 'not reporting', which is a different claim from
        'stopped'. It must not keep a dead engine looking alive."""

        summary = summarize_engines(
            [{"owner": "worker", "age_seconds": 5000}], stale_after_seconds=900
        )

        self.assertEqual(summary["live_count"], 0)
        self.assertFalse(summary["conflict"])
        self.assertEqual(len(summary["stale"]), 1)

    def test_an_old_engine_plus_a_live_one_is_not_a_conflict(self):
        """A redeploy leaves the previous owner's row behind. Counting it would
        raise a false double-scan alarm on every cutover."""

        summary = summarize_engines([
            {"owner": "worker", "age_seconds": 20},
            {"owner": "dashboard", "age_seconds": 4000},
        ], stale_after_seconds=900)

        self.assertFalse(summary["conflict"])
        self.assertEqual(summary["owners"], ["worker"])

    def test_no_rows_is_safe(self):

        for empty in (None, []):

            summary = summarize_engines(empty)

            self.assertEqual(summary["live_count"], 0)
            self.assertFalse(summary["conflict"])


class DashboardOwnershipGateTests(unittest.TestCase):

    def test_dashboard_does_not_start_an_engine_it_does_not_own(self):

        import app.dashboard as dashboard

        with patch.dict(
            "os.environ", {"SCAN_ENGINE_OWNER": "worker"}, clear=False
        ), patch(
            "app.runtime.scan_supervisor.ensure_started"
        ) as ensure_started, patch(
            "app.runtime.scan_supervisor.status", return_value={"status": "IDLE"}
        ):

            dashboard._ensure_scan_engine_started(None)

        ensure_started.assert_not_called()


class UpsertSemanticsTests(unittest.TestCase):
    """A partial heartbeat must not erase what the row already knows.

    `SCANNING` and `STOPPED` beats carry status and counts but no completed scan,
    so plain assignment left the dashboard showing an engine that had apparently
    never run. `last_error` is deliberately excluded from that protection: a clean
    scan passes None precisely to clear the previous failure.

    Exercised against a real database rather than by grepping the SQL: this
    session already replaced one source-inspection test that broke on a refactor
    while nothing regressed. Skips when no database is reachable.
    """

    INSTANCE = "pytest-heartbeat"

    def setUp(self):
        from app.db.scan_engine_heartbeat_repository import ScanEngineHeartbeatRepository

        self.repository = ScanEngineHeartbeatRepository()

        if not self.repository.upsert({
            "instance_id": self.INSTANCE,
            "owner": self.INSTANCE,
            "status": "IDLE",
            "last_scan_at": "2026-08-01T10:11:56-04:00",
            "last_duration_sec": 2.6,
            "scans": 2,
            "last_error": "boom",
        }):
            self.skipTest("no database available")

    def tearDown(self):
        try:
            from sqlalchemy import text

            from app.db.connection import get_engine

            with get_engine().begin() as connection:
                connection.execute(
                    text("DELETE FROM scan_engine_heartbeat WHERE instance_id = :i"),
                    {"i": self.INSTANCE},
                )
        except Exception:
            pass

    def _row(self):
        rows = [
            row for row in self.repository.fetch_all()
            if row["instance_id"] == self.INSTANCE
        ]
        self.assertTrue(rows, "heartbeat row disappeared")

        return rows[0]

    def test_a_partial_beat_preserves_the_last_completed_scan(self):

        self.repository.upsert({
            "instance_id": self.INSTANCE,
            "owner": self.INSTANCE,
            "status": "STOPPED",
            "scans": 2,
        })
        row = self._row()

        self.assertEqual(row["status"], "STOPPED")
        self.assertIsNotNone(row["last_scan_at"])

    def test_a_clean_scan_clears_the_previous_error(self):

        self.repository.upsert({
            "instance_id": self.INSTANCE,
            "owner": self.INSTANCE,
            "status": "IDLE",
            "scans": 3,
            "last_error": None,
        })

        self.assertIsNone(self._row()["last_error"])


if __name__ == "__main__":

    unittest.main()
