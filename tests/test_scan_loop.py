"""The scanner must schedule itself rather than depend on a browser tab.

`python -m app.main` is single-shot, so before this the only thing driving repeated
scans was Streamlit auto-refresh. On 2026-07-29 that produced 32 archived scans with
an 86-minute blind hole (11:09-12:35 ET), about 38% of a 5-minute cadence.
"""

import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.runtime import scan_loop

ET = ZoneInfo("America/New_York")


class IntervalTests(unittest.TestCase):

    def test_regular_session_is_denser_than_after_hours(self):

        self.assertLess(
            scan_loop.interval_for_session("REGULAR"),
            scan_loop.interval_for_session("AFTERHOURS"),
        )

    def test_opening_range_is_the_densest(self):

        intervals = {
            name: scan_loop.interval_for_session(name)
            for name in scan_loop.SESSION_INTERVALS
        }
        self.assertEqual(min(intervals, key=intervals.get), "OPENING_RANGE")

    def test_premarket_is_sparse_because_it_cannot_price_a_contract(self):
        """Premarket scans priced 0 contracts on 2026-08-11 and carried chain
        evidence on 4 of 728 rows. Options do not trade before 09:30 and the
        entry window opens at 09:45, so a dense premarket cadence buys nothing
        and holds the database awake to buy it."""

        self.assertGreaterEqual(scan_loop.interval_for_session("PREMARKET"), 1800)

    def test_a_sparse_session_outsleeps_the_database_idle_timer(self):
        """Neon suspends compute after 300s idle and a scan writes for ~5s.

        Any session claiming to be sparse has to leave a gap wider than that or
        it pays for continuous uptime while doing nothing.
        """

        neon_idle_timeout = 300
        burst = 5

        for session in ("PREMARKET", "AFTERHOURS", "CLOSED"):

            with self.subTest(session=session):
                gap = scan_loop.interval_for_session(session) - burst
                self.assertGreater(gap, neon_idle_timeout)

    def test_the_post_close_tail_is_guarded_by_the_regular_interval(self):
        """The archive scan survives AFTERHOURS being widened for the DB bill.

        One scan has to land after the bell to write the closing archive. It is
        scheduled by the iteration before it, which is still REGULAR at 15:59 --
        so the tail must outlast the REGULAR interval, and AFTERHOURS may be any
        width. Pinned because the tail's own docstring used to justify itself
        against AFTERHOURS, which made widening that value look unsafe.
        """

        from app.runtime.market_calendar import after_close_tail_minutes

        self.assertGreater(
            after_close_tail_minutes() * 60,
            scan_loop.interval_for_session("REGULAR"),
        )

    def test_the_idle_windows_do_not_hold_the_database_awake(self):
        """AFTERHOURS and CLOSED never scan; every pass writes a heartbeat.

        Each one wakes Neon for the full 300s suspend timer, so their cost is
        set by how often they fire, not by what they do. Measured 2026-08-13:
        2.7 of 9.65 compute-hours a day came from these two windows alone.
        """

        for session in ("AFTERHOURS", "CLOSED"):
            with self.subTest(session=session):
                self.assertGreaterEqual(
                    scan_loop.interval_for_session(session), 1800,
                    "a window that only heartbeats must not fire twice an hour"
                )

    def test_override_wins_and_is_floored(self):

        self.assertEqual(scan_loop.interval_for_session("REGULAR", 60), 60)
        self.assertEqual(scan_loop.interval_for_session("REGULAR", 1), 30)

    def test_unknown_session_falls_back_to_the_default(self):

        self.assertEqual(
            scan_loop.interval_for_session("NOT_A_SESSION"),
            scan_loop.DEFAULT_INTERVAL,
        )

    def test_session_is_read_from_the_scanner(self):

        session = scan_loop.current_session(datetime(2026, 7, 30, 10, 30, tzinfo=ET))
        self.assertEqual(session, "REGULAR")


class SessionBoundaryWaitTests(unittest.TestCase):
    """A sleep must not carry the worker through a session that scans faster.

    On 2026-08-17 a premarket scan finished at 09:22:50, took PREMARKET's 1800s,
    and the next scan ran at 09:52:50 -- past the open, past the whole
    OPENING_RANGE window, and seven minutes into the 09:45 entry window. The
    week before it landed at 09:54, 09:33, 09:43, 09:36, 09:36 and 09:37, decided
    only by where the last premarket scan fell.
    """

    def _wait_at(self, when, session):
        """`_bounded_wait` reads the clock itself, so the boundary lookup is
        pinned to a fixed instant rather than the test's wall time."""

        with patch.object(
            scan_loop,
            "seconds_to_next_session_change",
            side_effect=lambda now=None, horizon=None: _real_boundary(when, horizon),
        ):
            return scan_loop._bounded_wait(scan_loop.interval_for_session(session))

    def test_premarket_sleep_stops_at_the_opening_bell(self):

        seconds, upcoming = _real_boundary(datetime(2026, 8, 17, 9, 22, 50, tzinfo=ET))
        self.assertEqual(upcoming, "OPENING_RANGE")
        # 09:22:50 -> 09:30:00 exactly, not 09:30:59 and not 1800s later.
        self.assertEqual(seconds, 430)

    def test_the_closed_to_premarket_boundary_is_also_caught(self):

        seconds, upcoming = _real_boundary(datetime(2026, 8, 17, 3, 50, 0, tzinfo=ET))
        self.assertEqual(upcoming, "PREMARKET")
        self.assertEqual(seconds, 600)

    def test_a_tightening_boundary_shortens_the_sleep(self):

        self.assertEqual(
            self._wait_at(datetime(2026, 8, 17, 9, 22, 50, tzinfo=ET), "PREMARKET"),
            430,
        )

    def test_the_post_close_archive_scan_is_left_alone(self):
        """`idle_reason` guarantees one scan after the bell by keeping the tail
        wider than the REGULAR interval, so a 15:59 scan schedules ~16:04. That
        scan writes the closing archive. Clamping to the 16:00 boundary would pull
        it to 16:00:00 and archive a close the provider may not have settled --
        and waking early into a *slower* session buys nothing anyway."""

        self.assertEqual(
            self._wait_at(datetime(2026, 8, 17, 15, 59, 0, tzinfo=ET), "REGULAR"),
            scan_loop.interval_for_session("REGULAR"),
        )

    def test_a_widening_boundary_never_shortens_the_sleep(self):

        for when, session in (
            (datetime(2026, 8, 17, 9, 44, 30, tzinfo=ET), "OPENING_RANGE"),
            (datetime(2026, 8, 17, 16, 5, 0, tzinfo=ET), "AFTERHOURS"),
        ):
            with self.subTest(session=session):
                self.assertEqual(
                    self._wait_at(when, session),
                    scan_loop.interval_for_session(session),
                )

    def test_mid_session_cadence_is_untouched(self):

        self.assertEqual(
            self._wait_at(datetime(2026, 8, 17, 11, 0, 0, tzinfo=ET), "REGULAR"),
            scan_loop.interval_for_session("REGULAR"),
        )


# Bound at import, before any test patches the module attribute -- otherwise the
# helper would call whatever the patch installed and recurse.
_REAL_SECONDS_TO_NEXT_SESSION_CHANGE = scan_loop.seconds_to_next_session_change


def _real_boundary(when, horizon=None):

    return _REAL_SECONDS_TO_NEXT_SESSION_CHANGE(when, horizon)


class LoopTests(unittest.TestCase):

    def setUp(self):
        scan_loop._stopping = False
        self.addCleanup(setattr, scan_loop, "_stopping", False)

        # Ownership pinned to `worker`, for the same reason skip_closed is pinned
        # below: the loop consults the real environment, and `.env` carries
        # SCAN_ENGINE_OWNER=dashboard. Left ambient, every test here parks in
        # STANDBY and spins forever against a patched-out _sleep. These tests are
        # about loop mechanics, so both switches are stated rather than inherited.
        owner = patch.dict("os.environ", {"SCAN_ENGINE_OWNER": "worker"}, clear=False)
        owner.start()
        self.addCleanup(owner.stop)

    def test_loop_runs_the_requested_number_of_scans(self):

        # skip_closed pinned off. It defaults True and consults the real market
        # calendar, so leaving it implicit makes this test pass Monday to Friday
        # and hang all weekend: no scan runs, max_scans is never reached, and
        # with _sleep patched out the loop spins. A test whose result depends on
        # the day it runs is not a test.
        with patch("app.main.run_scanner") as run_scanner, \
             patch("app.runtime.scan_loop._sleep"), \
             patch("app.runtime.scan_loop.signal.signal"):

            result = scan_loop.run_scan_loop(
                interval_override=30, max_scans=3, skip_closed=False
            )

        self.assertEqual(run_scanner.call_count, 3)
        self.assertEqual(result, {"scans": 3, "failures": 0})

    def test_a_failing_scan_does_not_stop_the_loop(self):
        """A bad cycle must not end the session's coverage."""

        with patch("app.main.run_scanner",
                   side_effect=[RuntimeError("polygon down"), None, None]) as run_scanner, \
             patch("app.runtime.scan_loop._sleep"), \
             patch("app.runtime.scan_loop.signal.signal"):

            result = scan_loop.run_scan_loop(
                interval_override=30, max_scans=3, skip_closed=False
            )

        self.assertEqual(run_scanner.call_count, 3)
        self.assertEqual(result["scans"], 3)
        self.assertEqual(result["failures"], 1)

    def test_skip_closed_does_not_scan_while_closed(self):

        with patch("app.main.run_scanner") as run_scanner, \
             patch("app.runtime.scan_loop.current_session", return_value="CLOSED"), \
             patch("app.runtime.scan_loop._sleep",
                   side_effect=lambda _s: setattr(scan_loop, "_stopping", True)), \
             patch("app.runtime.scan_loop.signal.signal"):

            scan_loop.run_scan_loop(skip_closed=True)

        run_scanner.assert_not_called()

    def test_stop_signal_ends_the_loop(self):

        scan_loop._request_stop(15, None)

        with patch("app.main.run_scanner") as run_scanner, \
             patch("app.runtime.scan_loop.signal.signal"):

            result = scan_loop.run_scan_loop(interval_override=30)

        run_scanner.assert_not_called()
        self.assertEqual(result["scans"], 0)



class OwnershipStandbyTests(unittest.TestCase):
    """The cutover switch has to work in both directions.

    Only the dashboard read SCAN_ENGINE_OWNER. Flipping it back to `dashboard` --
    the obvious move if the Render worker is down and you want Streamlit scanning
    for the open -- started the supervisor while the worker carried on, giving two
    scanners and the double-open `scan_lock` cannot prevent across hosts.
    """

    def setUp(self):
        scan_loop._stopping = False
        self.addCleanup(setattr, scan_loop, "_stopping", False)

    def _run_once(self, env):
        with patch.dict("os.environ", env, clear=False), \
             patch("app.main.run_scanner") as run_scanner, \
             patch("app.runtime.scan_loop._publish_heartbeat") as heartbeat, \
             patch("app.runtime.scan_loop._sleep",
                   side_effect=lambda _s: setattr(scan_loop, "_stopping", True)), \
             patch("app.runtime.scan_loop.signal.signal"):

            scan_loop.run_scan_loop(interval_override=30, skip_closed=False)

        return run_scanner, heartbeat

    def test_the_worker_parks_when_the_dashboard_owns_scanning(self):

        run_scanner, heartbeat = self._run_once({"SCAN_ENGINE_OWNER": "dashboard"})

        run_scanner.assert_not_called()
        # Looks for STANDBY among the statuses rather than pinning it to index 0.
        # A startup preflight now reports database state before the loop begins,
        # and this assertion broke on an addition it was never about.
        statuses = [call.args[0] for call in heartbeat.call_args_list if call.args]
        self.assertIn("STANDBY", statuses)

    def test_the_worker_scans_when_it_owns_scanning(self):

        run_scanner, _ = self._run_once({"SCAN_ENGINE_OWNER": "worker"})

        run_scanner.assert_called_once()

    def test_an_unset_variable_means_no_opinion_and_scans(self):
        """Keeps `python -m app.runtime.scan_loop` working locally, where the
        default owner is `dashboard` and would otherwise park it forever."""

        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("SCAN_ENGINE_OWNER", None)
            run_scanner, _ = self._run_once({})

        run_scanner.assert_called_once()


class RestartReportingTests(unittest.TestCase):
    """A worker that is killed leaves no trace once its successor publishes.

    The heartbeat is keyed on instance_id, so the new process overwrites the old
    row and resets `scans` to zero. A clean SIGTERM writes STOPPED on the way out
    and explains itself; an OOM kill writes nothing at all.
    """

    def _report(self, previous):
        repository = patch(
            "app.db.scan_engine_heartbeat_repository.ScanEngineHeartbeatRepository"
        )

        with repository as factory, \
             patch("app.runtime.scan_loop._notify_operator") as notify, \
             patch("app.runtime.scan_loop._publish_heartbeat") as heartbeat:

            factory.return_value.fetch_instance.return_value = previous
            result = scan_loop._report_restart()

        return result, notify, heartbeat

    def test_an_unclean_restart_is_announced(self):

        previous = {
            "status": "SCANNING", "hostname": "srv-old", "scans": 41,
            "failures": 0, "last_scan_at": "2026-08-03 20:19:35+00:00",
            "last_error": None, "age_seconds": 5040.0,
        }

        result, notify, heartbeat = self._report(previous)

        self.assertEqual(result, previous)
        notify.assert_called_once()
        self.assertIn("restarted", notify.call_args.args[1].lower())
        # 5040s is 84 minutes.
        self.assertIn("84 minutes ago", notify.call_args.args[1])

        # And recorded on the row, so it survives an undelivered alert.
        status, kwargs = heartbeat.call_args.args[0], heartbeat.call_args.kwargs
        self.assertEqual(status, "STARTING")
        self.assertEqual(kwargs["payload"]["restarted_from"]["scans"], 41)

    def test_a_clean_shutdown_is_not_an_incident(self):

        result, notify, _ = self._report({
            "status": "STOPPED", "hostname": "srv-old", "scans": 41,
            "last_error": "stopped: terminated by signal 15", "age_seconds": 60.0,
        })

        self.assertIsNone(result)
        notify.assert_not_called()

    def test_a_first_ever_start_is_not_a_restart(self):

        result, notify, _ = self._report(None)

        self.assertIsNone(result)
        notify.assert_not_called()

    def test_an_unreadable_row_makes_no_claim(self):
        """"Could not ask" must never be reported as "restarted"."""

        with patch(
            "app.db.scan_engine_heartbeat_repository.ScanEngineHeartbeatRepository",
            side_effect=RuntimeError("no database"),
        ), patch("app.runtime.scan_loop._notify_operator") as notify:

            self.assertIsNone(scan_loop._report_restart())

        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
