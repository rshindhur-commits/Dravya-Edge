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
    heartbeat_to_engine_status,
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

    def test_two_scanning_engines_are_a_conflict(self):
        """The cutover failure worth shouting about: both engines scanning,
        double-opening positions that the file-based scan lock cannot serialise
        across two filesystems."""

        summary = summarize_engines([
            {"owner": "worker", "status": "SCANNING", "age_seconds": 30},
            {"owner": "dashboard", "status": "RUNNING", "age_seconds": 60},
        ])

        self.assertTrue(summary["conflict"])
        self.assertEqual(summary["live_count"], 2)
        self.assertEqual(summary["owners"], ["dashboard", "worker"])

    def test_an_engine_parked_on_the_calendar_is_not_a_conflict(self):
        """The first weekend both engines were up and the banner fired, but the
        dashboard was SLEEPING_WEEKEND with zero scans -- parked, not competing
        for the scan lock. A banner that cries wolf every weekend is one nobody
        reads on the Monday it matters."""

        summary = summarize_engines([
            {"owner": "worker", "status": "IDLE", "age_seconds": 30},
            {"owner": "dashboard", "status": "SLEEPING_WEEKEND", "age_seconds": 60},
        ])

        self.assertFalse(summary["conflict"])
        self.assertEqual(summary["owners"], ["worker"])
        self.assertEqual(summary["live_count"], 2)

    def test_a_stopped_engine_is_not_a_conflict(self):

        summary = summarize_engines([
            {"owner": "worker", "status": "IDLE", "age_seconds": 30},
            {"owner": "dashboard", "status": "STOPPED", "age_seconds": 60},
        ])

        self.assertFalse(summary["conflict"])

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
            {"owner": "worker", "status": "SCANNING", "age_seconds": 20},
            {"owner": "dashboard", "status": "RUNNING", "age_seconds": 4000},
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

            dashboard._ensure_scan_engine_started()

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



class PartialDeployResilienceTests(unittest.TestCase):
    """A missing heartbeat must degrade the panel, never the page.

    On 2026-08-01 Streamlit Cloud served a new `dashboard.py` against a stale
    `render_context.py`. The bare import raised ImportError out of
    `_render_system_status`, which runs before routing, and took the entire
    dashboard down over a status caption.
    """

    def test_system_status_survives_a_missing_heartbeat_helper(self):

        import app.dashboard as dashboard

        with patch(
            "app.ui.render_context.scan_engine_heartbeats",
            side_effect=ImportError("stale module"),
            create=True,
        ):
            self.assertEqual(dashboard._remote_engine_summary.__wrapped__(), {})

    def test_ownership_gate_survives_a_missing_heartbeat_module(self):
        """Falls back to the raw variable so the cutover switch still works."""

        import app.dashboard as dashboard

        with patch.dict(
            "os.environ", {"SCAN_ENGINE_OWNER": "worker"}, clear=False
        ), patch.dict("sys.modules", {"app.runtime.scan_engine_heartbeat": None}), patch(
            "app.runtime.scan_supervisor.ensure_started"
        ) as ensure_started, patch(
            "app.runtime.scan_supervisor.status", return_value={}
        ):

            dashboard._ensure_scan_engine_started()

        ensure_started.assert_not_called()



class OwnershipHandoverTests(unittest.TestCase):
    """Flipping SCAN_ENGINE_OWNER must actually stop this process scanning.

    On 2026-08-01 it did not. The gate skipped `ensure_started`, but the
    supervisor thread it had already started kept looping -- nothing sets
    `_stop_event` -- so the cutover looked complete while Streamlit was still a
    second scanner. Both engines then published under the same owner key, and the
    Streamlit row overwrote the Render one, hiding the conflict entirely.
    """

    def test_a_running_supervisor_is_stopped_when_ownership_moves(self):

        import app.dashboard as dashboard

        with patch.dict(
            "os.environ", {"SCAN_ENGINE_OWNER": "worker"}, clear=False
        ), patch(
            "app.runtime.scan_supervisor.status", return_value={"thread_alive": True}
        ), patch(
            "app.runtime.scan_supervisor.stop"
        ) as stop, patch(
            "app.runtime.scan_supervisor.ensure_started"
        ) as ensure_started:

            dashboard._ensure_scan_engine_started()

        stop.assert_called_once()
        ensure_started.assert_not_called()

    def test_nothing_is_stopped_when_no_engine_is_running(self):

        import app.dashboard as dashboard

        with patch.dict(
            "os.environ", {"SCAN_ENGINE_OWNER": "worker"}, clear=False
        ), patch(
            "app.runtime.scan_supervisor.status", return_value={"thread_alive": False}
        ), patch("app.runtime.scan_supervisor.stop") as stop:

            dashboard._ensure_scan_engine_started()

        stop.assert_not_called()


class EngineIdentityTests(unittest.TestCase):
    """Identity is what a module is; SCAN_ENGINE_OWNER is who should scan.

    Conflating them let one host publish under the other's key and overwrite its
    row, which is exactly how the real Render worker vanished from the panel.
    """

    def test_the_supervisor_always_publishes_as_dashboard(self):

        from app.runtime import scan_supervisor

        with patch.dict(
            "os.environ", {"SCAN_ENGINE_OWNER": "worker"}, clear=False
        ), patch(
            "app.runtime.scan_engine_heartbeat.record_heartbeat"
        ) as record:

            scan_supervisor._publish_heartbeat({"status": "RUNNING"})

        self.assertEqual(record.call_args.kwargs["owner"], "dashboard")

    def test_the_worker_always_publishes_as_worker(self):

        from app.runtime import scan_loop

        with patch.dict(
            "os.environ", {"SCAN_ENGINE_OWNER": "dashboard"}, clear=False
        ), patch(
            "app.runtime.scan_engine_heartbeat.record_heartbeat"
        ) as record:

            scan_loop._publish_heartbeat("SCANNING")

        self.assertEqual(record.call_args.kwargs["owner"], "worker")



class EngineStatusFallbackTests(unittest.TestCase):
    """Every consumer of engine health must know about the worker, not just one.

    On 2026-08-01 the sidebar correctly reported the Render worker while the
    Operator Console two feet away sat on ENGINE DOWN with 0/0 scans and told the
    operator to restart Streamlit -- advice that would not have helped and would
    have wiped container state. Fixed at `engine_status()` rather than per panel.
    """

    def test_a_live_worker_is_reported_when_no_thread_runs_here(self):

        from app.ui import render_context

        with patch(
            "app.runtime.scan_supervisor.status",
            return_value={"thread_alive": False, "scans": 0},
        ), patch.object(
            render_context,
            "scan_engine_heartbeats",
            return_value={"live": [{"owner": "worker", "status": "IDLE", "scans": 7}]},
        ):
            engine = render_context.engine_status()

        self.assertTrue(engine["running"])
        self.assertFalse(engine["thread_alive"])
        self.assertEqual(engine["owner"], "worker")
        self.assertEqual(engine["scans"], 7)

    def test_a_local_thread_still_wins(self):

        from app.ui import render_context

        with patch(
            "app.runtime.scan_supervisor.status",
            return_value={"thread_alive": True, "scans": 3},
        ):
            engine = render_context.engine_status()

        self.assertTrue(engine["running"])
        self.assertEqual(engine["owner"], "dashboard")

    def test_nothing_reporting_anywhere_is_genuinely_down(self):

        from app.ui import render_context

        with patch(
            "app.runtime.scan_supervisor.status", return_value={"thread_alive": False}
        ), patch.object(
            render_context, "scan_engine_heartbeats", return_value={"live": []}
        ):
            engine = render_context.engine_status()

        self.assertFalse(engine["running"])

    def test_an_unreachable_database_does_not_claim_an_engine(self):
        """Failing open here would report a scanner that may not exist."""

        from app.ui import render_context

        with patch(
            "app.runtime.scan_supervisor.status", return_value={"thread_alive": False}
        ), patch.object(
            render_context,
            "scan_engine_heartbeats",
            side_effect=RuntimeError("neon down"),
        ):
            engine = render_context.engine_status()

        self.assertFalse(engine["running"])



class StalenessWindowTests(unittest.TestCase):
    """Silence means different things at different cadences.

    The heartbeat lands once per cycle: 300s in the regular session, 900s after
    hours, 1800s when the market is shut. Against a flat 900s threshold a healthy
    weekend worker reads as dead -- exactly the ambiguity the heartbeat exists to
    remove.
    """

    def test_a_slow_cadence_engine_is_not_stale_between_beats(self):

        summary = summarize_engines([
            {"owner": "worker", "status": "SLEEPING_WEEKEND",
             "interval_seconds": 1800, "age_seconds": 1750},
        ])

        self.assertEqual(summary["live_count"], 1)
        self.assertEqual(summary["stale"], [])

    def test_two_missed_beats_is_stale(self):
        """One missed beat is tolerated; two means something is wrong."""

        summary = summarize_engines([
            {"owner": "worker", "status": "SLEEPING_WEEKEND",
             "interval_seconds": 900, "age_seconds": 2000},
        ])

        self.assertEqual(summary["live_count"], 0)
        self.assertEqual(len(summary["stale"]), 1)

    def test_a_fast_cadence_engine_keeps_the_floor(self):
        """A 300s cadence must not shrink the window below the default."""

        summary = summarize_engines([
            {"owner": "worker", "status": "IDLE",
             "interval_seconds": 300, "age_seconds": 800},
        ])

        self.assertEqual(summary["live_count"], 1)

    def test_a_missing_interval_falls_back_to_the_floor(self):

        summary = summarize_engines([
            {"owner": "worker", "status": "IDLE", "age_seconds": 1000},
        ])

        self.assertEqual(summary["live_count"], 0)



class DeadStatusTests(unittest.TestCase):
    """A STOPPED heartbeat is a process announcing its own exit.

    `running` was hardcoded True for any fresh row, so the Operator Console
    painted "ENGINE STOPPED · worker" in green while the Render container was
    genuinely down for half an hour. Asleep is alive; stopped is not.
    """

    def test_stopped_is_not_running(self):

        self.assertFalse(
            heartbeat_to_engine_status({"owner": "worker", "status": "STOPPED"})["running"]
        )

    def test_every_other_reported_status_is_running(self):

        for status in ("SCANNING", "IDLE", "FAILED", "SLEEPING_WEEKEND",
                       "SLEEPING_HOLIDAY", "SLEEPING_AFTER_CLOSE", "STANDBY"):

            self.assertTrue(
                heartbeat_to_engine_status({"owner": "worker", "status": status})["running"],
                f"{status} should count as a live process",
            )

    def test_an_absent_status_does_not_claim_death(self):
        """Unknown is not the same as stopped; only an explicit exit is."""

        self.assertTrue(heartbeat_to_engine_status({"owner": "worker"})["running"])


if __name__ == "__main__":

    unittest.main()


def test_engine_status_times_are_converted_to_et():
    """Callers slice these strings and paste " ET" after them, so an unconverted
    UTC value is not unlabelled -- it is labelled wrongly by four hours. The
    conversion lived in the sidebar only, and `Next due 01:10:32 ET` reached the
    dashboard for a beat that was actually due at 21:10."""

    from datetime import datetime, timezone

    engine = heartbeat_to_engine_status({
        "status": "SLEEPING_WEEKEND",
        "last_scan_at": datetime(2026, 8, 1, 16, 3, 26, tzinfo=timezone.utc),
        "next_due_at": datetime(2026, 8, 2, 1, 10, 32, tzinfo=timezone.utc),
    })

    assert engine["last_completed_at"][11:19] == "12:03:26"
    assert engine["next_due_at"][11:19] == "21:10:32"


def test_naive_timestamps_are_read_as_utc_not_as_local():
    """Postgres is the only writer here and it stores UTC. Treating a naive value
    as local time would shift it by the reader's offset, which differs between a
    developer laptop and the Streamlit container."""

    from datetime import datetime

    engine = heartbeat_to_engine_status(
        {"status": "IDLE", "last_scan_at": datetime(2026, 8, 1, 16, 3, 26)})

    assert engine["last_completed_at"][11:19] == "12:03:26"


def test_missing_timestamps_stay_none():

    engine = heartbeat_to_engine_status({"status": "IDLE"})

    assert engine["last_completed_at"] is None
    assert engine["next_due_at"] is None
