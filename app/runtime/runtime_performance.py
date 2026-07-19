from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import json
import time

import pandas as pd

from app.storage.daily_paths import DATA_DIR, daily_path, live_path


RUNTIME_PERFORMANCE_COLUMNS = [
    "observed_at_utc",
    "trading_day",
    "scan_id",
    "category",
    "stage",
    "page",
    "seconds",
    "metadata",
]


def _write_csv_row(path, row):

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=RUNTIME_PERFORMANCE_COLUMNS
        )

        if write_header:

            writer.writeheader()

        writer.writerow({
            column: row.get(column)
            for column in RUNTIME_PERFORMANCE_COLUMNS
        })


def append_runtime_performance(
    category,
    stage,
    seconds,
    trading_day=None,
    scan_id=None,
    page=None,
    metadata=None,
):

    row = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "trading_day": trading_day,
        "scan_id": scan_id,
        "category": category,
        "stage": stage,
        "page": page,
        "seconds": round(float(seconds or 0), 4),
        "metadata": json.dumps(metadata or {}, default=str),
    }
    paths = [
        DATA_DIR / "runtime_performance.csv",
    ]

    if trading_day:

        paths.append(
            daily_path(trading_day, "runtime_performance.csv")
        )

    for path in paths:

        try:

            _write_csv_row(path, row)

        except Exception as exc:

            print(f"[RUNTIME PERFORMANCE WARNING] {exc}")

    try:

        write_runtime_performance_summary()

    except Exception as exc:

        print(f"[RUNTIME PERFORMANCE SUMMARY WARNING] {exc}")

    return row


@contextmanager
def measure_runtime(
    category,
    stage,
    trading_day=None,
    scan_id=None,
    page=None,
    metadata=None,
):

    start = time.perf_counter()

    try:

        yield

    finally:

        append_runtime_performance(
            category=category,
            stage=stage,
            seconds=time.perf_counter() - start,
            trading_day=trading_day,
            scan_id=scan_id,
            page=page,
            metadata=metadata,
        )


def write_runtime_state(state):

    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        **(state or {}),
    }
    path = live_path("runtime_state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8"
    )
    return payload


def _read_csv(path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_csv(path)

    except Exception:

        return pd.DataFrame()


def _records(df, limit=25):

    if df is None or df.empty:

        return []

    output = df.tail(limit).iloc[::-1].copy()

    return output.where(pd.notna(output), None).to_dict("records")


def build_runtime_performance_summary(performance_df=None, metrics_df=None):

    performance_df = performance_df if performance_df is not None else _read_csv(DATA_DIR / "runtime_performance.csv")
    metrics_df = metrics_df if metrics_df is not None else _read_csv(DATA_DIR / "runtime_metrics.csv")
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "performance_rows": int(len(performance_df)) if performance_df is not None else 0,
        "metrics_rows": int(len(metrics_df)) if metrics_df is not None else 0,
        "recent_timings": _records(performance_df),
        "recent_jobs": _records(metrics_df),
        "average_seconds_by_stage": [],
        "average_runtime_by_job": [],
    }

    if performance_df is not None and not performance_df.empty and "seconds" in performance_df.columns:

        perf = performance_df.copy()
        perf["seconds"] = pd.to_numeric(perf["seconds"], errors="coerce")
        group_columns = [column for column in ["category", "stage", "page"] if column in perf.columns]

        if group_columns:

            summary["average_seconds_by_stage"] = (
                perf.groupby(group_columns, dropna=False)["seconds"]
                .mean()
                .round(4)
                .reset_index(name="average_seconds")
                .sort_values("average_seconds", ascending=False)
                .head(20)
                .where(lambda frame: pd.notna(frame), None)
                .to_dict("records")
            )

    if metrics_df is not None and not metrics_df.empty and "queue_runtime" in metrics_df.columns:

        metrics = metrics_df.copy()
        metrics["queue_runtime"] = pd.to_numeric(metrics["queue_runtime"], errors="coerce")

        if "job_name" in metrics.columns:

            summary["average_runtime_by_job"] = (
                metrics.groupby("job_name", dropna=False)["queue_runtime"]
                .mean()
                .round(4)
                .reset_index(name="average_runtime")
                .sort_values("average_runtime", ascending=False)
                .head(20)
                .where(lambda frame: pd.notna(frame), None)
                .to_dict("records")
            )

    return summary


def write_runtime_performance_summary():

    payload = build_runtime_performance_summary()
    path = live_path("runtime_performance_summary.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8"
    )
    return payload