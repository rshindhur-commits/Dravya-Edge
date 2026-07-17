import atexit
import logging
from queue import Queue
from threading import Lock, Thread
from typing import Any, Callable


logger = logging.getLogger(__name__)

_background_queue: Queue[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = Queue()
_worker_started = False
_worker_lock = Lock()


def background_worker() -> None:
    while True:
        func, args, kwargs = _background_queue.get()

        try:
            func(*args, **kwargs)
        except Exception:
            logger.exception("Background task failed")
        finally:
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

    _background_queue.put((func, args, kwargs))


def wait_for_background_tasks() -> None:
    _background_queue.join()


atexit.register(wait_for_background_tasks)
