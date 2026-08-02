"""Tell the operator when the machine is broken, without telling subscribers.

Everything else in this system is pull. On 2026-08-01 a container ran for hours
unable to reach Postgres -- re-sending a weekly summary, reporting zero trades
over a week with seven -- and nothing said a word. It was noticed because the
messages arrived, not because anything reported the fault. A postmortem you have
to remember to open is strictly worse than a message that arrives.

**Never the subscriber channel.** Operator alerts go to `TELEGRAM_OPERATOR_CHAT_ID`
and nowhere else; with that unset the whole module is inert. Defaulting to the
broadcast chat would mean one bad deploy narrating its own failure to every
subscriber, which is a worse outcome than no alerting at all.

Only transitions are sent. A fault that persists for six hours is one message,
not one per scan -- an operator channel that repeats itself is one nobody reads
on the day it matters.
"""

from __future__ import annotations

import os
import threading


OPERATOR_CHAT_ENV = "TELEGRAM_OPERATOR_CHAT_ID"

_state_lock = threading.Lock()
_last_state = {}


def operator_chat_id():

    return str(os.getenv(OPERATOR_CHAT_ENV, "")).strip()


def operator_alerts_enabled():
    """Inert unless an operator chat is configured. See the module docstring."""

    return bool(operator_chat_id())


def reset_operator_alert_state():
    """For tests, and for a process that wants to re-announce on startup."""

    with _state_lock:
        _last_state.clear()


def notify_operator(key, message, healthy=False, force=False):
    """Report a fault, or its recovery, once per transition.

    `key` names the condition -- `database`, `scan_failures` -- not the incident.
    `healthy=True` marks it resolved, which is what makes the next fault on the
    same key send again.

    Returns a dict rather than raising: this is called from the scan loop, and a
    monitoring failure must never be able to stop a scan.
    """

    if not operator_alerts_enabled():

        return {"sent": False, "reason": "OPERATOR_CHAT_NOT_CONFIGURED"}

    with _state_lock:

        previous = _last_state.get(key)
        unchanged = previous is not None and previous == bool(healthy)

        if unchanged and not force:

            return {"sent": False, "reason": "NO_CHANGE", "key": key}

        # Recovery for a key that never reported a fault is not news.
        if previous is None and healthy and not force:

            _last_state[key] = True

            return {"sent": False, "reason": "ALREADY_HEALTHY", "key": key}

        _last_state[key] = bool(healthy)

    prefix = "✅ RECOVERED" if healthy else "🚨 OPERATOR ALERT"

    try:
        _send_to_operator(f"<b>{prefix}</b>\n{message}")

        return {"sent": True, "key": key, "healthy": bool(healthy)}

    except Exception as exc:

        # The state is already recorded, so a failed send does not re-fire on the
        # next scan. That is deliberate: an unreachable Telegram during an
        # incident should not become its own repeating incident.
        print(f"[OPERATOR ALERT WARNING] {key}: {exc}")

        return {"sent": False, "reason": "SEND_FAILED", "key": key}


def _send_to_operator(message):
    """Direct, not queued. An operator alert about a broken machine must not sit
    behind that machine's own dispatch queue."""

    import requests

    from app.alerts.telegram_alerts import get_telegram_credentials, get_telegram_session

    token, _subscriber_chat = get_telegram_credentials()

    if not token:

        raise ValueError("Telegram bot token not configured")

    response = get_telegram_session().post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": operator_chat_id(),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    response.raise_for_status()

    return response.json()
