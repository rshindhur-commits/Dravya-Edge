from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.runtime.scan_generation import atomic_write_json, metadata_from_generation
from app.storage.daily_paths import daily_path, live_path, ROOT_DIR


def _file_info(path: Path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return {
                "exists": False,
                "path": str(path),
                "size_bytes": 0,
                "mtime": None,
            }

        stat = path.stat()

        return {
            "exists": True,
            "path": str(path),
            "size_bytes": int(stat.st_size),
            "mtime": datetime.fromtimestamp(
                stat.st_mtime,
                timezone.utc
            ).isoformat(),
        }

    except Exception:

        return {
            "exists": False,
            "path": str(path),
            "size_bytes": 0,
            "mtime": None,
        }


def build_report_state_payload(
    report_date: str,
    daily_report_path: Path | None = None,
    root_report_path: Path | None = None,
    scanner_path: Path | None = None,
    generated_at: str | None = None,
    scan_id: str | None = None,
    generation=None,
) -> dict[str, Any]:

    daily_report_path = daily_report_path or daily_path(
        report_date,
        "daily_validation_report.html"
    )
    root_report_path = root_report_path or (
        ROOT_DIR / "reports" / f"daily_validation_{report_date}.html"
    )
    scanner_path = scanner_path or daily_path(
        report_date,
        "scanner_output_close.csv"
    )
    daily_info = _file_info(daily_report_path)
    root_info = _file_info(root_report_path)
    scanner_info = _file_info(scanner_path)

    if daily_info["exists"] or root_info["exists"]:

        report_mtime = daily_info.get("mtime") or root_info.get("mtime")
        status = "READY"

        if scanner_info.get("mtime") and report_mtime and scanner_info["mtime"] > report_mtime:

            status = "STALE"

    else:

        status = "MISSING"

    return {
        "metadata": metadata_from_generation(generation, scan_id=scan_id) or {
            "scan_id": scan_id,
            "generation": scan_id,
            "schema": 1,
            "created_at": generated_at or datetime.now(timezone.utc).isoformat(),
        },
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "trading_day": report_date,
        "scan_id": scan_id,
        "status": status,
        "daily_report": daily_info,
        "root_report": root_info,
        "scanner_snapshot": scanner_info,
        "errors": [] if status != "MISSING" else ["Daily validation report has not been generated."],
    }


def write_report_state(report_date: str, scan_id: str | None = None, generation=None):

    payload = build_report_state_payload(
        report_date,
        scan_id=scan_id,
        generation=generation,
    )
    paths = [
        live_path("report_state.json"),
        daily_path(report_date, "report_state.json"),
    ]

    for path in paths:

        atomic_write_json(path, payload)

    return payload