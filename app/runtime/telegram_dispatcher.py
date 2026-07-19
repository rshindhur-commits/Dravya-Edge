from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time

from app.runtime.runtime_jobs import RuntimeJob
from app.runtime.runtime_priority import Priority
from app.runtime.runtime_scheduler import get_runtime_scheduler
from app.storage.daily_paths import live_path


TELEGRAM_QUEUE_FILE = "telegram_dispatch_queue.jsonl"
TELEGRAM_AUDIT_FILE = "telegram_dispatch_audit.jsonl"


def telegram_dispatch_mode():

    return str(
        os.getenv("TELEGRAM_DISPATCH_MODE", "DIRECT")
    ).strip().upper()


def _append_jsonl(filename, payload):

    path = live_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:

        handle.write(json.dumps(payload, default=str) + "\n")

    return path


def _read_jsonl(filename):

    path = live_path(filename)

    if not path.exists() or path.stat().st_size == 0:

        return []

    rows = []

    try:

        with path.open("r", encoding="utf-8") as handle:

            for line in handle:

                try:

                    rows.append(json.loads(line))

                except Exception:

                    continue

    except Exception:

        return []

    return rows


def _queue_record(job_id, name, scan_id, message, dispatch_metadata=None):

    return {
        "event": "QUEUED",
        "queued_at_utc": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "name": name,
        "scan_id": scan_id,
        "message": message,
        "message_chars": len(str(message or "")),
        "dispatch_metadata": dispatch_metadata or {},
    }


def _audit_record(event, name, scan_id, attempt=None, job_id=None, error=None):

    return {
        "event": event,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "scan_id": scan_id,
        "job_id": job_id,
        "attempt": attempt,
        "error": str(error) if error else None,
    }


def dispatch_telegram_message(
    send_func,
    message,
    name="telegram_send",
    scan_id=None,
    after_success=None,
    on_error=None,
    dispatch_metadata=None,
):

    job_id_holder = {"job_id": None}

    def execute_send():

        attempts = 0
        max_attempts = int(os.getenv("TELEGRAM_DISPATCH_RETRIES", "2")) + 1

        while True:

            try:

                _append_jsonl(
                    TELEGRAM_AUDIT_FILE,
                    _audit_record(
                        "ATTEMPT",
                        name,
                        scan_id,
                        attempt=attempts + 1,
                        job_id=job_id_holder.get("job_id")
                    )
                )

                result = send_func(message)

                if after_success:

                    after_success(result)

                _append_jsonl(
                    TELEGRAM_AUDIT_FILE,
                    _audit_record(
                        "SENT",
                        name,
                        scan_id,
                        attempt=attempts + 1,
                        job_id=job_id_holder.get("job_id")
                    )
                )

                return result

            except Exception as exc:

                attempts += 1

                if attempts >= max_attempts:

                    if on_error:

                        on_error(exc)

                    _append_jsonl(
                        TELEGRAM_AUDIT_FILE,
                        _audit_record(
                            "FAILED",
                            name,
                            scan_id,
                            attempt=attempts,
                            job_id=job_id_holder.get("job_id"),
                            error=exc
                        )
                    )

                    raise

                time.sleep(0.25 * attempts)

    if telegram_dispatch_mode() == "QUEUED":

        scheduler = get_runtime_scheduler()
        job = RuntimeJob(
            name=name,
            priority=Priority.CRITICAL,
            func=execute_send,
            cancelable=False,
            scan_id=scan_id,
        )
        job_id_holder["job_id"] = job.job_id
        job_id = scheduler.submit_critical(job)
        _append_jsonl(
            TELEGRAM_QUEUE_FILE,
            _queue_record(job_id, name, scan_id, message, dispatch_metadata)
        )

        return {
            "queued": True,
            "job_id": job_id,
        }

    return execute_send()


def recover_pending_telegram_dispatches(send_func, limit=None):

    queue_rows = _read_jsonl(TELEGRAM_QUEUE_FILE)
    audit_rows = _read_jsonl(TELEGRAM_AUDIT_FILE)
    sent_job_ids = {
        row.get("job_id")
        for row in audit_rows
        if row.get("event") == "SENT" and row.get("job_id")
    }
    recovered = []

    for row in queue_rows:

        if row.get("event") != "QUEUED":

            continue

        if row.get("job_id") in sent_job_ids:

            continue

        if not row.get("message"):

            continue

        result = dispatch_telegram_message(
            send_func,
            row.get("message"),
            name=row.get("name") or "recovered_telegram_send",
            scan_id=row.get("scan_id"),
            dispatch_metadata=row.get("dispatch_metadata") or {}
        )
        recovered.append({
            "original_job_id": row.get("job_id"),
            "result": result,
        })

        if limit is not None and len(recovered) >= limit:

            break

    return recovered