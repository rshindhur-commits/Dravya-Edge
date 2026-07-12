import atexit
import logging
import time
from queue import Queue
from threading import Lock, Thread
from typing import Any, Callable


logger = logging.getLogger(__name__)

_background_queue: Queue[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = Queue()
_worker_started = False
_worker_lock = Lock()
_metrics_lock = Lock()
_metrics = {
    "queued_jobs": 0,
    "completed_jobs": 0,
    "failed_jobs": 0,
    "job_time_total_sec": 0.0,
    "job_time_count": 0,
    "longest_job_time_sec": 0.0,
    "longest_job_name": None,
}


def _record_queued_job() -> None:
    with _metrics_lock:
        _metrics["queued_jobs"] += 1


def _record_finished_job(name: str, elapsed_seconds: float, failed: bool) -> None:
    with _metrics_lock:
        if failed:
            _metrics["failed_jobs"] += 1
        else:
            _metrics["completed_jobs"] += 1
        _metrics["job_time_total_sec"] += elapsed_seconds
        _metrics["job_time_count"] += 1

        if elapsed_seconds > (_metrics["longest_job_time_sec"] or 0):
            _metrics["longest_job_time_sec"] = elapsed_seconds
            _metrics["longest_job_name"] = name


def get_background_metrics() -> dict[str, Any]:
    with _metrics_lock:
        snapshot = dict(_metrics)

    pending_jobs = max(
        snapshot["queued_jobs"]
        - snapshot["completed_jobs"]
        - snapshot["failed_jobs"],
        0,
    )
    snapshot["pending_jobs"] = pending_jobs
    snapshot["queue_depth"] = _background_queue.qsize()
    snapshot["average_job_time_sec"] = (
        round(snapshot["job_time_total_sec"] / snapshot["job_time_count"], 4)
        if snapshot["job_time_count"] > 0
        else None
    )
    snapshot["longest_job_time_sec"] = round(
        snapshot["longest_job_time_sec"],
        4,
    )
    return snapshot


def background_worker() -> None:
    while True:
        func, args, kwargs = _background_queue.get()
        started_at = time.perf_counter()
        failed = False
        job_name = getattr(func, "__name__", str(func))

        try:
            func(*args, **kwargs)
        except Exception:
            failed = True
            logger.exception("Background task failed")
        finally:
            _record_finished_job(
                job_name,
                time.perf_counter() - started_at,
                failed,
            )
            _background_queue.task_done()


def start_worker() -> None:
    global _worker_started

    if _worker_started:
        return

    with _worker_lock:
        if _worker_started:
            return

        Thread(
            target=background_worker,
            daemon=True,
            name="scanner-background-persistence",
        ).start()

        _worker_started = True


def run_background(func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    start_worker()
    _record_queued_job()

    _background_queue.put((func, args, kwargs))


def wait_for_background_tasks() -> None:
    _background_queue.join()


atexit.register(wait_for_background_tasks)
