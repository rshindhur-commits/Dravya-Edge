"""Cross-process mutual exclusion for scans.

Only one scan may run at a time. Paper state mutation is read-modify-write on
`paper_trade_state.json` with no cross-process lock, so two concurrent scans can
lose each other's updates.

The lock used to live inside `app/dashboard.py` and only guarded the dashboard's
own `Run scanner now` / auto-refresh path. `run_scanner()` itself took no lock, so
a terminal scan or the new `app.runtime.scan_loop` could run straight through a
dashboard-triggered scan. Now `run_scanner()` holds this lock, which makes every
entry point -- loop, dashboard button, auto-refresh, bare `python -m app.main` --
mutually exclusive.

Reentrant within a *thread* so a caller that already holds it does not deadlock
against `run_scanner()`. Reentrancy is deliberately thread-local, not
process-global: `app.runtime.scan_supervisor` runs the scan loop on a daemon
thread inside the Streamlit process, and a process-global depth counter would let
any other thread in that process piggyback on the held lock and start a second
concurrent scan -- exactly the interleaving this lock exists to prevent.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.storage.daily_paths import live_path

ET = ZoneInfo("America/New_York")
LOCK_FILENAME = "scanner_run.lock"

# A scan that has held the lock longer than this is treated as dead and reclaimed,
# so a crashed process cannot block scanning forever.
STALE_LOCK_MINUTES = 15

_local = threading.local()


def _depth():
    return getattr(_local, "depth", 0)


def lock_path():
    return live_path(LOCK_FILENAME)


def lock_age_minutes():
    path = lock_path()

    if not path.exists():
        return None

    try:
        age = datetime.now(ET) - datetime.fromtimestamp(path.stat().st_mtime, ET)
    except Exception:
        return None

    return age.total_seconds() / 60


def is_locked():
    return lock_path().exists()


def acquire(stale_minutes=STALE_LOCK_MINUTES):
    """Take the scan lock. Returns True if this call now owns it."""

    if _depth() > 0:
        _local.depth = _depth() + 1
        return True

    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    except FileExistsError:
        age = lock_age_minutes()

        if age is not None and age > stale_minutes:
            print(
                f"[SCAN LOCK] reclaiming a stale lock held for {age:.1f} minutes "
                f"(limit {stale_minutes})"
            )
            try:
                path.unlink()
            except Exception:
                return False
            return acquire(stale_minutes)

        return False

    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(f"{os.getpid()} {datetime.now(ET).isoformat()}")

    _local.depth = 1
    return True


def release():

    if _depth() > 1:
        _local.depth = _depth() - 1
        return

    _local.depth = 0

    try:
        lock_path().unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[SCAN LOCK WARNING] could not release: {exc}")


class scan_lock:
    """Context manager. `acquired` is False when another scan already holds it."""

    def __init__(self, stale_minutes=STALE_LOCK_MINUTES):
        self.stale_minutes = stale_minutes
        self.acquired = False

    def __enter__(self):
        self.acquired = acquire(self.stale_minutes)
        return self

    def __exit__(self, *_exc):
        if self.acquired:
            release()
        return False
