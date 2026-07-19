from __future__ import annotations

from datetime import datetime, timezone
import atexit
import json

from app.runtime.runtime_priority import Priority
from app.runtime.runtime_scheduler import get_runtime_scheduler
from app.storage.daily_paths import live_path


class ShutdownManager:

    def __init__(self, scheduler=None):

        self.scheduler = scheduler or get_runtime_scheduler()
        self._ran = False

    def shutdown(self):

        if self._ran:

            return None

        self._ran = True
        critical_completed = True

        try:

            critical_completed = self.scheduler.wait_for(Priority.CRITICAL, timeout=15)

        except Exception:

            critical_completed = False

        metrics = self.scheduler.metrics()
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "critical_completed": bool(critical_completed),
            "db_flushed": True,
            "telegram_flushed": bool(critical_completed),
            "queue_remaining": sum(int(metrics.get(key, 0) or 0) for key in ["critical_jobs", "high_jobs", "normal_jobs", "low_jobs"]),
        }
        path = live_path("runtime_shutdown.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return payload


shutdown_manager = ShutdownManager()
atexit.register(shutdown_manager.shutdown)