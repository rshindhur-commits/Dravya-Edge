from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.runtime.scan_generation import atomic_write_json, metadata_from_generation
from app.storage.daily_paths import daily_path, live_path


def _read_csv(path: Path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_csv(path)

    except Exception:

        return pd.DataFrame()


def _file_mtime(path: Path):

    try:

        if not path.exists():

            return None

        return datetime.fromtimestamp(
            path.stat().st_mtime,
            timezone.utc
        ).isoformat()

    except Exception:

        return None


def _json_records(df, limit=None):

    if df is None or df.empty:

        return []

    records = df.head(limit).to_dict("records") if limit else df.to_dict("records")

    return [
        {
            key: (
                None
                if pd.isna(value)
                else value
            )
            for key, value in record.items()
        }
        for record in records
    ]


def _blocker_summary(summary_df, replay_df):

    source = summary_df if summary_df is not None and not summary_df.empty else replay_df

    if source is None or source.empty:

        return []

    column = None

    for candidate in ["Gate Failure Stage", "ENTRY_GATE_FAILURE_STAGE"]:

        if candidate in source.columns:

            column = candidate
            break

    if not column:

        return []

    blockers = (
        source[column]
        .fillna("Unknown")
        .astype(str)
        .value_counts()
        .reset_index()
    )
    blockers.columns = ["blocker", "count"]

    return _json_records(blockers)


def _top_misses(summary_df, limit=10):

    if summary_df is None or summary_df.empty:

        return []

    output = summary_df.copy()

    if "Readiness" in output.columns:

        output["Readiness"] = pd.to_numeric(
            output["Readiness"],
            errors="coerce"
        )
        output = output.sort_values(
            by="Readiness",
            ascending=False,
            na_position="last"
        )

    columns = [
        column for column in [
            "Symbol",
            "Closest Setup",
            "Readiness",
            "First Failed Rule",
            "Recommendation",
            "Trade Block Details",
            "Final Decision",
            "Gate Failure Stage",
        ]
        if column in output.columns
    ]

    return _json_records(output[columns], limit=limit) if columns else _json_records(output, limit=limit)


def build_replay_state_payload(
    report_date: str,
    scanner_df: pd.DataFrame | None = None,
    replay_df: pd.DataFrame | None = None,
    summary_df: pd.DataFrame | None = None,
    scanner_mtime=None,
    replay_mtime=None,
    generated_at: str | None = None,
    scan_id: str | None = None,
    generation=None,
) -> dict[str, Any]:

    scanner_df = scanner_df if scanner_df is not None else pd.DataFrame()
    replay_df = replay_df if replay_df is not None else pd.DataFrame()
    summary_df = summary_df if summary_df is not None else pd.DataFrame()
    scanner_rows = int(len(scanner_df))
    replay_rows = int(len(replay_df) if not replay_df.empty else len(summary_df))
    coverage_pct = round(replay_rows / scanner_rows * 100, 2) if scanner_rows else 0
    missing_indicators = 0

    if "FAILED_ENTRY_CONDITIONS" in replay_df.columns:

        missing_indicators = int(
            replay_df["FAILED_ENTRY_CONDITIONS"]
            .astype(str)
            .str.contains("Missing replay indicators", na=False)
            .sum()
        )

    if replay_rows <= 0:

        status = "MISSING"

    elif scanner_mtime and replay_mtime and str(scanner_mtime) > str(replay_mtime):

        status = "STALE"

    else:

        status = "READY"

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
        "scanner_rows": scanner_rows,
        "replay_rows": replay_rows,
        "coverage_pct": coverage_pct,
        "missing_indicators": missing_indicators,
        "partial_replay": missing_indicators,
        "scanner_mtime": scanner_mtime,
        "replay_mtime": replay_mtime,
        "blockers": _blocker_summary(summary_df, replay_df),
        "top_misses": _top_misses(summary_df),
        "replay_summary": _json_records(summary_df, limit=50),
        "errors": [] if replay_rows else ["Replay output has not been generated."],
    }


def write_replay_state(report_date: str, scan_id: str | None = None, generation=None):

    scanner_path = daily_path(report_date, "scanner_output_close.csv")
    replay_path = daily_path(report_date, "offline_replay.csv")
    summary_path = daily_path(report_date, "offline_replay_summary.csv")
    payload = build_replay_state_payload(
        report_date,
        scanner_df=_read_csv(scanner_path),
        replay_df=_read_csv(replay_path),
        summary_df=_read_csv(summary_path),
        scanner_mtime=_file_mtime(scanner_path),
        replay_mtime=_file_mtime(summary_path) or _file_mtime(replay_path),
        scan_id=scan_id,
        generation=generation,
    )
    paths = [
        live_path("replay_state.json"),
        daily_path(report_date, "replay_state.json"),
    ]

    for path in paths:

        atomic_write_json(path, payload)

    return payload