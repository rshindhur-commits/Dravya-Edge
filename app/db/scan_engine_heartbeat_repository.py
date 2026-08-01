import json

from app.db.repository_base import BestEffortRepository


class ScanEngineHeartbeatRepository(BestEffortRepository):
    """Who is scanning, when they last scanned, and whether they are still alive.

    The dashboard's only way to answer those questions once the scanner lives in a
    separate process on a separate host. See migration 026.
    """

    def upsert(self, heartbeat):
        heartbeat = heartbeat or {}

        return self._execute(
            """
            INSERT INTO scan_engine_heartbeat (
                instance_id, owner, hostname, status, session,
                last_scan_id, last_scan_at, last_duration_sec, next_due_at,
                interval_seconds, scans, failures, last_error, payload, updated_at
            ) VALUES (
                :instance_id, :owner, :hostname, :status, :session,
                :last_scan_id, :last_scan_at, :last_duration_sec, :next_due_at,
                :interval_seconds, :scans, :failures, :last_error,
                CAST(:payload AS JSONB), NOW()
            ) ON CONFLICT (instance_id) DO UPDATE SET
                owner = EXCLUDED.owner,
                hostname = EXCLUDED.hostname,
                status = EXCLUDED.status,
                session = EXCLUDED.session,
                -- COALESCE only on the three "what already happened" fields.
                -- Not every heartbeat carries them: a SCANNING or STOPPED beat
                -- reports status and counts, and plain assignment would erase
                -- the last completed scan, leaving the dashboard showing an
                -- engine that has apparently never scanned.
                --
                -- Deliberately NOT applied to last_error: a clean scan passes
                -- None precisely to clear the previous failure, and preserving
                -- it would leave a stale error on screen forever.
                last_scan_id = COALESCE(EXCLUDED.last_scan_id, scan_engine_heartbeat.last_scan_id),
                last_scan_at = COALESCE(EXCLUDED.last_scan_at, scan_engine_heartbeat.last_scan_at),
                last_duration_sec = COALESCE(EXCLUDED.last_duration_sec, scan_engine_heartbeat.last_duration_sec),
                next_due_at = EXCLUDED.next_due_at,
                interval_seconds = EXCLUDED.interval_seconds,
                scans = EXCLUDED.scans,
                failures = EXCLUDED.failures,
                last_error = EXCLUDED.last_error,
                payload = EXCLUDED.payload,
                updated_at = NOW()
            """,
            {
                "instance_id": heartbeat.get("instance_id"),
                "owner": heartbeat.get("owner"),
                "hostname": heartbeat.get("hostname"),
                "status": heartbeat.get("status"),
                "session": heartbeat.get("session"),
                "last_scan_id": heartbeat.get("last_scan_id"),
                "last_scan_at": heartbeat.get("last_scan_at"),
                "last_duration_sec": heartbeat.get("last_duration_sec"),
                "next_due_at": heartbeat.get("next_due_at"),
                "interval_seconds": heartbeat.get("interval_seconds"),
                "scans": heartbeat.get("scans") or 0,
                "failures": heartbeat.get("failures") or 0,
                "last_error": heartbeat.get("last_error"),
                "payload": json.dumps(heartbeat.get("payload") or {}, default=str),
            },
        )

    def insert(self, heartbeat):
        return self.upsert(heartbeat)

    def get(self, *_args, **_kwargs):
        return None

    def fetch_recent(self, within_seconds=1800):
        """Engines that have reported inside the window, freshest first.

        Anything older is not "stopped" -- it is *not reporting*, which is a
        different claim. The caller decides what to say about it; this only
        reports what the table knows.
        """

        return self._fetch(
            """
            SELECT instance_id, owner, hostname, status, session,
                   last_scan_id, last_scan_at, last_duration_sec, next_due_at,
                   interval_seconds, scans, failures, last_error, updated_at,
                   EXTRACT(EPOCH FROM (NOW() - updated_at)) AS age_seconds
            FROM scan_engine_heartbeat
            WHERE updated_at >= NOW() - make_interval(secs => :within_seconds)
            ORDER BY updated_at DESC
            """,
            {"within_seconds": int(within_seconds)},
        )

    def fetch_all(self):
        return self._fetch(
            """
            SELECT instance_id, owner, hostname, status, session,
                   last_scan_id, last_scan_at, next_due_at, scans, failures,
                   last_error, updated_at,
                   EXTRACT(EPOCH FROM (NOW() - updated_at)) AS age_seconds
            FROM scan_engine_heartbeat
            ORDER BY updated_at DESC
            """
        )
