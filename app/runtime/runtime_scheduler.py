from __future__ import annotations

from dataclasses import dataclass
import atexit
from queue import PriorityQueue
from threading import Lock, Thread
import time
from typing import Any, Callable

from app.runtime.runtime_jobs import RuntimeJob
from app.runtime.runtime_metrics import append_runtime_metric
from app.runtime.runtime_performance import write_runtime_state
from app.runtime.runtime_priority import Priority


_TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELED"})

# Finished records are history: `_metrics_locked` counts only QUEUED and
# RUNNING, `wait_for` waits on the same two, and `cancel_old_jobs` looks at
# QUEUED. Nothing reads a terminal record, and nothing deleted one either, so
# `_records` grew for the life of the process.
#
# Measured 2026-08-13 by replaying run_scanner() against two symbols: exactly
# +11 records a scan, all COMPLETED, never falling. On the worker that is
# 113 scans x 11 = ~1,240 live records a session, each pinning its job's
# arguments -- and the scan's finalize job is handed `df_results.copy()`, a
# frame covering the full 26-symbol watchlist. RSS climbed 241MB -> 1330MB on
# 2026-08-11 and only ever reset on deploy, which is the shape this produces.
#
# The cap bounds the history rather than removing it, because a terminal record
# is still what you read when asking why a job failed an hour ago.
_MAX_TERMINAL_RECORDS = 200


@dataclass
class _JobRecord:

    job: RuntimeJob
    submitted_at: float
    status: str = "QUEUED"
    started_at: float | None = None
    finished_at: float | None = None


class RuntimeScheduler:

    def __init__(self):

        self._queue = PriorityQueue()
        self._lock = Lock()
        self._records: dict[str, _JobRecord] = {}
        self._sequence = 0
        self._worker_started = False
        self._scanner_running = False

    def _start_worker(self):

        if self._worker_started:

            return

        with self._lock:

            if self._worker_started:

                return

            Thread(
                target=self._worker,
                daemon=True,
                name="runtime-priority-scheduler",
            ).start()
            self._worker_started = True

    def submit(self, job: RuntimeJob):

        self._start_worker()

        with self._lock:

            self._sequence += 1
            self._records[job.job_id] = _JobRecord(
                job=job,
                submitted_at=time.perf_counter(),
            )
            self._queue.put((int(job.priority), self._sequence, job.job_id))
            self._write_state_locked()

        return job.job_id

    def submit_critical(self, job_or_func, *args, **kwargs):

        return self.submit(self._coerce_job(Priority.CRITICAL, job_or_func, *args, **kwargs))

    def submit_high(self, job_or_func, *args, **kwargs):

        return self.submit(self._coerce_job(Priority.HIGH, job_or_func, *args, **kwargs))

    def submit_normal(self, job_or_func, *args, **kwargs):

        return self.submit(self._coerce_job(Priority.NORMAL, job_or_func, *args, **kwargs))

    def submit_low(self, job_or_func, *args, **kwargs):

        return self.submit(self._coerce_job(Priority.LOW, job_or_func, *args, **kwargs))

    def cancel_old_jobs(self, scan_id):

        canceled = 0

        with self._lock:

            for record in self._records.values():

                job = record.job

                if (
                    record.status == "QUEUED"
                    and job.cancelable
                    and job.priority != Priority.CRITICAL
                    and job.scan_id
                    and job.scan_id != scan_id
                ):

                    record.status = "CANCELED"
                    record.finished_at = time.perf_counter()
                    canceled += 1

            self._write_state_locked()

        return canceled

    def metrics(self):

        with self._lock:

            return self._metrics_locked()

    def set_scanner_running(self, running: bool):

        with self._lock:

            self._scanner_running = bool(running)
            self._write_state_locked()

    def wait_for_jobs(self):

        self._queue.join()

    def wait_for(self, priority, timeout=None):

        priority = Priority(priority)
        start = time.perf_counter()

        while True:

            with self._lock:

                remaining = [
                    record for record in self._records.values()
                    if record.job.priority == priority
                    and record.status in {"QUEUED", "RUNNING"}
                ]

            if not remaining:

                return True

            if timeout is not None and time.perf_counter() - start >= timeout:

                return False

            time.sleep(0.05)

    def _coerce_job(self, priority: Priority, job_or_func, *args, **kwargs):

        if isinstance(job_or_func, RuntimeJob):

            job_or_func.priority = priority
            return job_or_func

        return RuntimeJob(
            name=getattr(job_or_func, "__name__", "runtime_job"),
            priority=priority,
            func=job_or_func,
            args=args,
            kwargs=kwargs,
            cancelable=(priority != Priority.CRITICAL),
        )

    def _worker(self):

        while True:

            _priority, _sequence, job_id = self._queue.get()

            try:

                record = self._records.get(job_id)

                if record is None:

                    continue

                if record.status == "CANCELED":

                    append_runtime_metric(
                        record.job,
                        queue_wait=time.perf_counter() - record.submitted_at,
                        status="CANCELED",
                    )

                    # A cancelled job never ran, so nothing has dropped its
                    # arguments. `cancel_old_jobs` fires on every scan against
                    # the previous scan's queue, so this is the common path for
                    # anything that falls behind -- not a rare one.
                    with self._lock:

                        self._release_payload(record.job)
                        self._prune_terminal_locked()

                    continue

                with self._lock:

                    record.status = "RUNNING"
                    record.started_at = time.perf_counter()
                    self._write_state_locked()

                try:

                    record.job.func(*record.job.args, **record.job.kwargs)
                    status = "COMPLETED"

                except Exception as exc:

                    print(f"[RUNTIME SCHEDULER ERROR] {record.job.name}: {exc}")
                    status = "FAILED"

                finally:

                    finished_at = time.perf_counter()

                    with self._lock:

                        record.status = status
                        record.finished_at = finished_at
                        self._release_payload(record.job)
                        self._prune_terminal_locked()
                        self._write_state_locked()

                    append_runtime_metric(
                        record.job,
                        queue_wait=(record.started_at or finished_at) - record.submitted_at,
                        queue_runtime=finished_at - (record.started_at or finished_at),
                        total_runtime=finished_at - record.submitted_at,
                        status=status,
                    )

            finally:

                self._queue.task_done()

    @staticmethod
    def _release_payload(job: RuntimeJob):
        """Drop a finished job's arguments; the record itself stays.

        This is the part that frees the memory. Waiting for the record to be
        evicted would hold a 26-symbol DataFrame for however long the cap
        allows, and the arguments are of no use once the job has run -- only
        `name`, `priority` and the timings are ever read afterwards.
        """

        job.args = ()
        job.kwargs = {}

    def _prune_terminal_locked(self):
        """Keep the newest `_MAX_TERMINAL_RECORDS` finished records.

        Bounding this matters twice over. `_metrics_locked` walks every record
        and runs on each submit and each status change, so an unbounded dict
        makes a session's bookkeeping quadratic as well as heavy.

        Caller must hold `self._lock`.
        """

        terminal = [
            (record.finished_at or record.submitted_at, job_id)
            for job_id, record in self._records.items()
            if record.status in _TERMINAL_STATUSES
        ]

        excess = len(terminal) - _MAX_TERMINAL_RECORDS

        if excess <= 0:
            return 0

        terminal.sort()

        for _finished_at, job_id in terminal[:excess]:
            self._records.pop(job_id, None)

        return excess

    def _metrics_locked(self):

        counts = {
            Priority.CRITICAL: 0,
            Priority.HIGH: 0,
            Priority.NORMAL: 0,
            Priority.LOW: 0,
        }
        running = 0

        for record in self._records.values():

            if record.status == "QUEUED":

                counts[record.job.priority] += 1

            elif record.status == "RUNNING":

                running += 1

        return {
            "scanner_running": self._scanner_running,
            "telegram_queue": counts[Priority.CRITICAL],
            "critical_jobs": counts[Priority.CRITICAL],
            "high_jobs": counts[Priority.HIGH],
            "normal_jobs": counts[Priority.NORMAL],
            "low_jobs": counts[Priority.LOW],
            "running_jobs": running,
        }

    def _write_state_locked(self):

        write_runtime_state(self._metrics_locked())


_DEFAULT_SCHEDULER = RuntimeScheduler()
atexit.register(_DEFAULT_SCHEDULER.wait_for_jobs)


def get_runtime_scheduler():

    return _DEFAULT_SCHEDULER


def run_with_priority(priority, func, *args, **kwargs):

    return _DEFAULT_SCHEDULER.submit(
        RuntimeJob(
            name=getattr(func, "__name__", "runtime_job"),
            priority=Priority(priority),
            func=func,
            args=args,
            kwargs=kwargs,
            cancelable=(Priority(priority) != Priority.CRITICAL),
        )
    )


def run_critical(func: Callable[..., Any], *args: Any, **kwargs: Any):

    return run_with_priority(Priority.CRITICAL, func, *args, **kwargs)


def run_high(func: Callable[..., Any], *args: Any, **kwargs: Any):

    return run_with_priority(Priority.HIGH, func, *args, **kwargs)


def run_normal(func: Callable[..., Any], *args: Any, **kwargs: Any):

    return run_with_priority(Priority.NORMAL, func, *args, **kwargs)


def run_low(func: Callable[..., Any], *args: Any, **kwargs: Any):

    return run_with_priority(Priority.LOW, func, *args, **kwargs)