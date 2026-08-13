"""The scheduler must not keep finished jobs alive.

`_records` had no eviction: submit() inserted, cancel_old_jobs() flipped a
status, the worker set COMPLETED, and nothing ever deleted. Every finished
record kept `job.args`, and run_scanner() hands its finalize job a copy of the
full results DataFrame.

Measured on 2026-08-13 by replaying run_scanner() over two symbols: exactly +11
records a scan, all COMPLETED, never falling. The worker runs 26 symbols on a
2-minute cadence, which is the 241MB -> 1330MB climb seen on 2026-08-11 and the
reason only a deploy ever brought it back down.

These cover the two halves of the fix separately, because they fail in
different ways: releasing the payload frees the memory, and capping the dict
stops both the record count and the O(n) metrics walk from growing without
bound.
"""

import gc
import unittest
from threading import Event
from unittest.mock import patch

from app.runtime.runtime_jobs import RuntimeJob
from app.runtime import runtime_scheduler as scheduler_module
from app.runtime.runtime_scheduler import Priority, RuntimeScheduler


class _Payload:
    """Stands in for the results DataFrame: identifiable and weak-referenceable."""


def _drain(scheduler, timeout=5.0):
    scheduler.wait_for_jobs()
    scheduler.wait_for(Priority.NORMAL, timeout=timeout)


class PayloadReleaseTests(unittest.TestCase):

    def test_a_finished_job_stops_referencing_its_arguments(self):
        """The record may survive; the 26-symbol frame it was handed must not."""

        scheduler = RuntimeScheduler()
        done = Event()
        payload = _Payload()

        def work(_arg):
            done.set()

        job = RuntimeJob(
            name="finalize_scan_outputs",
            priority=Priority.HIGH,
            func=work,
            args=(payload,),
            cancelable=False,
        )

        scheduler.submit(job)
        self.assertTrue(done.wait(timeout=5), "job never ran")
        _drain(scheduler)

        record = scheduler._records.get(job.job_id)
        self.assertIsNotNone(record, "history should survive the release")
        self.assertEqual(record.status, "COMPLETED")
        self.assertEqual(record.job.args, (), "arguments are still pinned")
        self.assertEqual(record.job.kwargs, {})

    def test_a_cancelled_job_also_releases(self):
        """cancel_old_jobs fires every scan, so this is a common path."""

        scheduler = RuntimeScheduler()
        payload = _Payload()

        job = RuntimeJob(
            name="stale_snapshot",
            priority=Priority.LOW,
            func=lambda _arg: None,
            args=(payload,),
            cancelable=True,
            scan_id="scan-1",
        )

        # Submit without starting the worker, so it is still QUEUED when
        # the next scan cancels it.
        with patch.object(scheduler, "_start_worker"):
            scheduler.submit(job)

        self.assertEqual(scheduler.cancel_old_jobs("scan-2"), 1)

        scheduler._start_worker()
        _drain(scheduler)

        record = scheduler._records.get(job.job_id)

        if record is not None:
            self.assertEqual(record.job.args, ())


class RecordCapTests(unittest.TestCase):

    def test_finished_records_are_capped(self):
        """Without this the dict grows for the life of the process."""

        scheduler = RuntimeScheduler()

        with patch.object(scheduler_module, "_MAX_TERMINAL_RECORDS", 5):

            for i in range(20):
                scheduler.submit(
                    RuntimeJob(
                        name=f"job-{i}",
                        priority=Priority.NORMAL,
                        func=lambda: None,
                        cancelable=False,
                    )
                )

            _drain(scheduler)

        self.assertLessEqual(
            len(scheduler._records), 5,
            "terminal records grew past the cap"
        )

    def test_queued_and_running_records_are_never_evicted(self):
        """Eviction must not lose a job the scheduler still has to run."""

        scheduler = RuntimeScheduler()
        release = Event()
        started = Event()

        def blocker():
            started.set()
            release.wait(timeout=5)

        with patch.object(scheduler_module, "_MAX_TERMINAL_RECORDS", 1):

            blocking = RuntimeJob(
                name="blocker",
                priority=Priority.CRITICAL,
                func=blocker,
                cancelable=False,
            )
            scheduler.submit(blocking)
            self.assertTrue(started.wait(timeout=5))

            queued = [
                RuntimeJob(
                    name=f"queued-{i}",
                    priority=Priority.LOW,
                    func=lambda: None,
                    cancelable=False,
                )
                for i in range(10)
            ]

            for job in queued:
                scheduler.submit(job)

            # Everything except the blocker is QUEUED behind it. None of them
            # may be evicted, however far over the cap the dict is.
            still_present = [
                j.job_id for j in queued if j.job_id in scheduler._records
            ]
            self.assertEqual(
                len(still_present), len(queued),
                "a queued job was evicted and would never run"
            )

            release.set()
            _drain(scheduler)

    def test_metrics_still_report_pending_work(self):
        """The counts read only QUEUED/RUNNING, so pruning must not move them."""

        scheduler = RuntimeScheduler()

        for i in range(6):
            scheduler.submit(
                RuntimeJob(
                    name=f"job-{i}",
                    priority=Priority.NORMAL,
                    func=lambda: None,
                    cancelable=False,
                )
            )

        _drain(scheduler)
        metrics = scheduler.metrics()

        self.assertEqual(metrics["normal_jobs"], 0)
        self.assertEqual(metrics["running_jobs"], 0)


if __name__ == "__main__":
    unittest.main()
