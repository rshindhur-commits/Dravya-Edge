from __future__ import annotations

import json
import os
import hashlib
import time
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from app.db.connection import get_engine
from app.db.persistence import _json_safe, _safe_execute
from app.storage.daily_paths import live_path
from app.utils.json_store import load_json_file, save_json_file


class ScannerSnapshotRepository:
    def enabled(self):
        return str(os.getenv("REGRESSION_SNAPSHOT_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}

    def batch_insert(self, trading_day, scan_id, scan_timestamp, scanner_version, data_version, records, market_snapshots=None):
        if not self.enabled():
            return {"enabled": False, "queued": 0, "completed": 0, "dropped": 0, "failures": 0}
        market_snapshots = market_snapshots or {}
        statement = text("""
            INSERT INTO scanner_snapshot (
                trading_day, scan_id, scan_timestamp, scanner_version, data_version,
                symbol, market_payload, decision_payload, payload, payload_hash
            ) VALUES (
                CAST(:trading_day AS DATE), :scan_id, :scan_timestamp,
                :scanner_version, :data_version, :symbol, :market_payload, :decision_payload, :payload, :payload_hash
            )
            ON CONFLICT (scan_id, symbol) WHERE symbol IS NOT NULL DO NOTHING
        """).bindparams(
            bindparam("market_payload", type_=JSONB),
            bindparam("decision_payload", type_=JSONB),
            bindparam("payload", type_=JSONB),
        )
        candidates = []
        for record in records or []:
            symbol = str(record.get("Symbol") or record.get("symbol") or "")
            if not symbol:
                continue
            decision = _json_safe(record)
            payload_hash = hashlib.sha256(
                json.dumps(decision, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            candidates.append({
                "trading_day": trading_day,
                "scan_id": scan_id,
                "scan_timestamp": scan_timestamp,
                "scanner_version": scanner_version,
                "data_version": data_version,
                "symbol": symbol,
                "market_payload": _json_safe(market_snapshots.get(symbol) or {}),
                "decision_payload": decision,
                "payload": decision,
                "payload_hash": payload_hash,
            })
        if not candidates:
            return {"enabled": True, "queued": 0, "completed": 0, "dropped": 0, "failures": 0}
        started = time.perf_counter()
        try:
            from app.db.persistence import db_writes_enabled
            if not db_writes_enabled():
                return self._record_metrics(len(candidates), 0, 0, 0, started)
            from app.db.connection import get_engine
            with get_engine().begin() as connection:
                previous = {
                    row["symbol"]: row["payload_hash"]
                    for row in connection.execute(text("""
                        SELECT DISTINCT ON (symbol) symbol, payload_hash
                        FROM scanner_snapshot
                        WHERE symbol = ANY(:symbols)
                        ORDER BY symbol, scan_timestamp DESC
                    """), {"symbols": [row["symbol"] for row in candidates]}).mappings()
                }
                rows = [
                    row for row in candidates
                    if previous.get(row["symbol"]) != row["payload_hash"]
                ]
                if not rows:
                    return self._record_metrics(len(candidates), 0, len(candidates), 0, started)
                connection.execute(statement, rows)
            return self._record_metrics(len(candidates), len(rows), len(candidates) - len(rows), 0, started)
        except Exception:
            return self._record_metrics(len(candidates), 0, 0, 1, started)

    def _record_metrics(self, queued, completed, dropped, failures, started):
        path = live_path("regression_snapshot_metrics.json")
        metrics = load_json_file(str(path), {
            "queued": 0, "completed": 0, "dropped": 0, "failures": 0, "average_persist_ms": 0.0,
            "persist_attempts": 0,
        })
        elapsed_ms = (time.perf_counter() - started) * 1000
        attempts = int(metrics.get("persist_attempts", 0)) + 1
        prior_average = float(metrics.get("average_persist_ms", 0.0))
        metrics.update({
            "enabled": True,
            "queued": int(metrics.get("queued", 0)) + queued,
            "completed": int(metrics.get("completed", 0)) + completed,
            "dropped": int(metrics.get("dropped", 0)) + dropped,
            "failures": int(metrics.get("failures", 0)) + failures,
            "persist_attempts": attempts,
            "average_persist_ms": round(((prior_average * (attempts - 1)) + elapsed_ms) / attempts, 2),
            "last_persist_ms": round(elapsed_ms, 2),
        })
        save_json_file(str(path), metrics)
        return metrics

    def load_day(self, trading_day):
        # A swallowed failure here is indistinguishable from an empty archive,
        # which is how an unconfigured DATABASE_URL surfaced to the operator as
        # "No scanner snapshots" for a day holding 702 rows. Read failures are
        # still non-fatal -- HSR falls back to the local snapshot folder -- but
        # they must say so.
        try:
            with get_engine().connect() as connection:
                rows = connection.execute(text("""
                          SELECT scan_id, scan_timestamp, scanner_version, data_version, symbol,
                              market_payload, decision_payload, payload
                    FROM scanner_snapshot
                    WHERE trading_day = CAST(:trading_day AS DATE)
                    ORDER BY scan_timestamp, scan_id
                """), {"trading_day": trading_day}).mappings().all()
            return [dict(row) for row in rows]
        except Exception as exc:
            print(f"[SCANNER SNAPSHOT WARNING] load_day({trading_day}) failed: {exc}")
            return []

    def archive_status(self, trading_day):
        try:
            with get_engine().connect() as connection:
                row = connection.execute(text("""
                    SELECT COUNT(*) AS rows, COUNT(DISTINCT scan_id) AS scans
                    FROM scanner_snapshot
                    WHERE trading_day = CAST(:trading_day AS DATE)
                """), {"trading_day": trading_day}).mappings().one()
            return {"available": bool(row["rows"]), "rows": int(row["rows"]), "scans": int(row["scans"])}
        except Exception as exc:
            print(f"[SCANNER SNAPSHOT WARNING] archive_status({trading_day}) failed: {exc}")
            return {"available": False, "rows": 0, "scans": 0, "error": str(exc)}


class RegressionBaselineRepository:
    def freeze(self, trading_day, baseline_version, payload):
        statement = text("""
            INSERT INTO scanner_regression_baseline (trading_day, baseline_version, payload)
            VALUES (CAST(:trading_day AS DATE), :baseline_version, :payload)
            ON CONFLICT (trading_day) DO NOTHING
        """).bindparams(bindparam("payload", type_=JSONB))
        return _safe_execute(statement, {
            "trading_day": trading_day,
            "baseline_version": baseline_version,
            "payload": _json_safe(payload or []),
        })

    def load(self, trading_day):
        try:
            with get_engine().connect() as connection:
                row = connection.execute(text("""
                    SELECT baseline_version, payload, frozen_at
                    FROM scanner_regression_baseline
                    WHERE trading_day = CAST(:trading_day AS DATE)
                """), {"trading_day": trading_day}).mappings().first()
            return dict(row) if row else None
        except Exception:
            return None


class RegressionRunRepository:
    def record(self, trading_day, strategy_version, summary, comparison, git_commit=None):
        run_id = str(uuid4())
        statement = text("""
            INSERT INTO regression_run (run_id, trading_day, strategy_version, git_commit, status, summary, completed_at)
            VALUES (:run_id, CAST(:trading_day AS DATE), :strategy_version, :git_commit, 'COMPLETED', :summary, now())
        """).bindparams(bindparam("summary", type_=JSONB))
        if not _safe_execute(statement, {
            "run_id": run_id, "trading_day": trading_day, "strategy_version": strategy_version,
            "git_commit": git_commit, "summary": _json_safe(summary),
        }):
            return None
        result_rows = []
        for classification, entries in (comparison or {}).items():
            for entry in entries:
                result_rows.append({
                    "run_id": run_id,
                    "trade_key": entry if isinstance(entry, str) else entry.get("trade_key"),
                    "classification": classification.upper(),
                    "baseline_r": None if isinstance(entry, str) else entry.get("old_r"),
                    "current_r": None if isinstance(entry, str) else entry.get("new_r"),
                    "payload": _json_safe(entry if isinstance(entry, dict) else {"trade_key": entry}),
                })
        if result_rows:
            result_statement = text("""
                INSERT INTO regression_result (run_id, trade_key, classification, baseline_r, current_r, payload)
                VALUES (:run_id, :trade_key, :classification, :baseline_r, :current_r, :payload)
            """).bindparams(bindparam("payload", type_=JSONB))
            try:
                from app.db.connection import get_engine
                with get_engine().begin() as connection:
                    connection.execute(result_statement, result_rows)
            except Exception:
                return None
        return run_id

    def list_recent(self, limit=20):
        try:
            with get_engine().connect() as connection:
                rows = connection.execute(text("""
                    SELECT run_id, trading_day, strategy_version, status, summary, started_at, completed_at
                    FROM regression_run
                    ORDER BY started_at DESC
                    LIMIT :limit
                """), {"limit": limit}).mappings().all()
            return [dict(row) for row in rows]
        except Exception:
            return []