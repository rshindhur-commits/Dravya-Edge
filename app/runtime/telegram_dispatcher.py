from __future__ import annotations

from datetime import datetime, timezone
from queue import Queue
from threading import Lock, Thread
from uuid import uuid4
import atexit
import json
import os
import time

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


class _TelegramSender:
    """Dedicated single-thread worker for outbound Telegram sends.

    Deliberately *not* the shared runtime scheduler. `_dispatch_telegram_entry_alerts`
    runs inside `finalize_scan_outputs`, which is itself a job on the scheduler's one
    worker thread. A send submitted back to that scheduler therefore could not start
    until finalize returned -- i.e. after the whole of `_persist_scan_outputs`, which
    on 2026-07-31 measured ~5.5s of candidate-evidence, learning-engine and Excel
    writes. CRITICAL priority bought nothing against a worker blocked by the very job
    that queued the work. Owning a thread here removes that inversion: an alert goes
    out while persistence is still running.

    Still single-threaded, on purpose. Telegram rate-limits per chat at roughly one
    message per second, so sends must stay serialized; the latency win comes from the
    pooled connection in `telegram_alerts`, not from parallelism.
    """

    def __init__(self):

        self._queue = Queue()
        self._thread = None
        self._lock = Lock()

    def _ensure_worker(self):

        if self._thread is not None and self._thread.is_alive():

            return

        with self._lock:

            if self._thread is not None and self._thread.is_alive():

                return

            self._thread = Thread(
                target=self._worker,
                daemon=True,
                name="telegram-dispatcher",
            )
            self._thread.start()

    def submit(self, job_id, func):

        self._ensure_worker()
        self._queue.put((job_id, func))

        return job_id

    def _worker(self):

        while True:

            job_id, func = self._queue.get()

            try:

                func()

            except Exception as exc:

                # execute_send has already written its own FAILED audit row and
                # exhausted retries by this point; nothing above can catch it.
                print(f"[TELEGRAM DISPATCH ERROR] {job_id}: {exc}")

            finally:

                self._queue.task_done()

    def drain(self, timeout=None):
        """Block until queued sends finish. For shutdown and tests."""

        if timeout is None:

            self._queue.join()
            return True

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:

            if self._queue.unfinished_tasks == 0:

                return True

            time.sleep(0.01)

        return self._queue.unfinished_tasks == 0


_telegram_sender = None
_telegram_sender_lock = Lock()


def get_telegram_sender():

    global _telegram_sender

    with _telegram_sender_lock:

        if _telegram_sender is None:

            _telegram_sender = _TelegramSender()

        return _telegram_sender


def drain_telegram_dispatches(timeout=None):
    """Wait for in-flight queued sends. No-op if nothing was ever dispatched."""

    if _telegram_sender is None:

        return True

    return _telegram_sender.drain(timeout)


# The sender thread is a daemon, so without this an interpreter exit between
# hand-off and delivery would drop the alert until the next startup recovery.
# The shared scheduler this replaced drained the same way (its own atexit hook).
# Bounded, so a wedged send cannot hang shutdown.
atexit.register(lambda: drain_telegram_dispatches(timeout=15))


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


def _audit_record(
    event,
    name,
    scan_id,
    attempt=None,
    job_id=None,
    error=None,
    dispatch_metadata=None,
    latency_ms=None,
    result=None
):

    metadata = (dispatch_metadata or {}).get(
        "metadata",
        dispatch_metadata or {}
    )
    telegram_response = (
        getattr(error, "telegram_response", None)
        if error
        else (result or {}).get("telegram_response")
        if isinstance(result, dict)
        else None
    )

    return {
        "event": event,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "scan_id": scan_id,
        "job_id": job_id,
        "attempt": attempt,
        "symbol": metadata.get("symbol"),
        "direction": metadata.get("direction"),
        "candidate_key": metadata.get("candidate_key"),
        "message_type": metadata.get("message_type") or metadata.get("event_type"),
        "decision": metadata.get("decision"),
        "policy": metadata.get("policy"),
        "parse_mode": metadata.get("parse_mode", "HTML"),
        "message_length": metadata.get("message_length"),
        "latency_ms": latency_ms,
        "telegram_response": telegram_response,
        "error": str(error) if error else None,
    }


def _record_dispatch_db(
    event,
    scan_id,
    dispatch_metadata=None,
    error=None,
    result=None,
    attempt=None,
    latency_ms=None
):
    """Best-effort scheduler-only promotion of the dispatcher audit event."""
    try:
        from app.db.telegram_dispatch_repository import TelegramDispatchRepository

        metadata = (dispatch_metadata or {}).get("metadata") or dispatch_metadata or {}
        status = "ATTEMPTED" if event == "ATTEMPT" else "DELIVERED" if event == "SENT" else "FAILED"
        get_runtime_scheduler().submit_normal(
            TelegramDispatchRepository().insert,
            {
                "scan_id": scan_id,
                "trade_id": metadata.get("trade_id"),
                "symbol": metadata.get("symbol"),
                "direction": metadata.get("direction"),
                "candidate_key": metadata.get("candidate_key"),
                "message_type": metadata.get("event_type") or metadata.get("message_type") or "UNKNOWN",
                "decision": metadata.get("decision") or "ELIGIBLE",
                "policy": metadata.get("policy"),
                "parse_mode": metadata.get("parse_mode", "HTML"),
                "message_length": metadata.get("message_length"),
                "telegram_response": getattr(error, "telegram_response", None) if error else (result or {}).get("telegram_response") if isinstance(result, dict) else None,
                "attempt": attempt,
                "latency_ms": latency_ms,
                "attempted": event in {"ATTEMPT", "SENT", "FAILED"},
                "delivered": event == "SENT",
                "status": status,
                "failure_reason": str(error) if error else None,
                "telegram_message_id": (result or {}).get("telegram_message_id") if isinstance(result, dict) else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        print(f"[TELEGRAM DISPATCH DB WARNING] {exc}")


def dispatch_telegram_message(
    send_func,
    message,
    name="telegram_send",
    scan_id=None,
    after_success=None,
    on_error=None,
    dispatch_metadata=None,
    job_id=None,
):
    """`job_id` re-sends an existing queue row under its own identity.

    Recovery replays a queued row by calling back into here, and without this it
    arrived as a brand new job: a second queue row was written, and success was
    audited against the *new* id. The original never appeared in the audit as
    SENT, so it stayed pending and replayed on every startup, adding another row
    each time. Passing the original id makes the send settle the row it came
    from, so recovery converges instead of accumulating.
    """

    job_id_holder = {"job_id": job_id}
    dispatch_metadata = dict(dispatch_metadata or {})
    metadata = dict(dispatch_metadata.get("metadata") or {})
    metadata.setdefault("message_length", len(str(message or "")))
    metadata.setdefault("parse_mode", "HTML")
    dispatch_metadata["metadata"] = metadata

    def execute_send():

        attempts = 0
        max_attempts = int(os.getenv("TELEGRAM_DISPATCH_RETRIES", "2")) + 1

        while True:

            try:

                attempt_started = time.perf_counter()

                _append_jsonl(
                    TELEGRAM_AUDIT_FILE,
                    _audit_record(
                        "ATTEMPT",
                        name,
                        scan_id,
                        attempt=attempts + 1,
                        job_id=job_id_holder.get("job_id"),
                        dispatch_metadata=dispatch_metadata
                    )
                )
                _record_dispatch_db(
                    "ATTEMPT",
                    scan_id,
                    dispatch_metadata,
                    attempt=attempts + 1
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
                        job_id=job_id_holder.get("job_id"),
                        dispatch_metadata=dispatch_metadata,
                        latency_ms=round(
                            (time.perf_counter() - attempt_started) * 1000,
                            2
                        ),
                        result=result
                    )
                )
                _record_dispatch_db(
                    "SENT",
                    scan_id,
                    dispatch_metadata,
                    result=result,
                    attempt=attempts + 1,
                    latency_ms=round(
                        (time.perf_counter() - attempt_started) * 1000,
                        2
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
                            error=exc,
                            dispatch_metadata=dispatch_metadata,
                            latency_ms=round(
                                (time.perf_counter() - attempt_started) * 1000,
                                2
                            )
                        )
                    )
                    _record_dispatch_db(
                        "FAILED",
                        scan_id,
                        dispatch_metadata,
                        error=exc,
                        attempt=attempts,
                        latency_ms=round(
                            (time.perf_counter() - attempt_started) * 1000,
                            2
                        )
                    )

                    raise

                time.sleep(0.25 * attempts)

    if telegram_dispatch_mode() == "QUEUED":

        replaying = job_id is not None
        job_id = job_id or str(uuid4())
        job_id_holder["job_id"] = job_id

        # The durable queue row is written before the send is handed off, so a
        # crash between here and delivery is recoverable by
        # `recover_pending_telegram_dispatches`. A replay skips the write: the
        # row it is replaying is already in the file, and appending a copy is
        # what made the queue grow by one on every restart.
        if not replaying:

            _append_jsonl(
                TELEGRAM_QUEUE_FILE,
                _queue_record(job_id, name, scan_id, message, dispatch_metadata)
            )

        get_telegram_sender().submit(job_id, execute_send)

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
            dispatch_metadata=row.get("dispatch_metadata") or {},
            # Under its own id, so a successful send audits SENT against this row
            # and the next startup skips it.
            job_id=row.get("job_id"),
        )
        recovered.append({
            "original_job_id": row.get("job_id"),
            "result": result,
        })

        if limit is not None and len(recovered) >= limit:

            break

    return recovered