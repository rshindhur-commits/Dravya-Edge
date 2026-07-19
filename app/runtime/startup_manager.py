from __future__ import annotations

import os

from app.alerts.telegram_alerts import _send_telegram_alert_direct
from app.runtime.telegram_dispatcher import recover_pending_telegram_dispatches
from app.runtime.runtime_watchdog import write_runtime_health
from app.storage.daily_paths import get_live_dir


def startup():

    get_live_dir()
    recovered_telegram = []

    if str(os.getenv("TELEGRAM_RECOVER_ON_STARTUP", "false")).strip().lower() in {"1", "true", "yes", "on"}:

        recovered_telegram = recover_pending_telegram_dispatches(_send_telegram_alert_direct)

    health = write_runtime_health()

    return {
        "live_dir_ready": True,
        "recovered_telegram": recovered_telegram,
        "runtime_health": health,
    }