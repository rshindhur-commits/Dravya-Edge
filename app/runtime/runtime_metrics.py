from __future__ import annotations

from datetime import datetime, timezone
import csv

from app.storage.daily_paths import DATA_DIR, daily_path


RUNTIME_METRICS_COLUMNS = [
    "observed_at_utc",
    "scan_id",
    "job_id",
    "job_name",
    "priority",
    "queue_wait",
    "queue_runtime",
    "total_runtime",
    "status",
]


def append_runtime_metric(
    job,
    queue_wait=None,
    queue_runtime=None,
    total_runtime=None,
    status="COMPLETED",
    trading_day=None,
):

    row = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scan_id": getattr(job, "scan_id", None),
        "job_id": getattr(job, "job_id", None),
        "job_name": getattr(job, "name", None),
        "priority": getattr(getattr(job, "priority", None), "name", getattr(job, "priority", None)),
        "queue_wait": round(float(queue_wait or 0), 4),
        "queue_runtime": round(float(queue_runtime or 0), 4),
        "total_runtime": round(float(total_runtime or 0), 4),
        "status": status,
    }
    paths = [DATA_DIR / "runtime_metrics.csv"]

    if trading_day:

        paths.append(daily_path(trading_day, "runtime_metrics.csv"))

    for path in paths:

        try:

            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists() or path.stat().st_size == 0

            with path.open("a", newline="", encoding="utf-8") as handle:

                writer = csv.DictWriter(handle, fieldnames=RUNTIME_METRICS_COLUMNS)

                if write_header:

                    writer.writeheader()

                writer.writerow(row)

        except Exception as exc:

            print(f"[RUNTIME METRICS WARNING] {exc}")

    return row