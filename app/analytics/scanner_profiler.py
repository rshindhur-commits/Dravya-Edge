from __future__ import annotations

from contextlib import contextmanager
import time

import pandas as pd

from app.storage.daily_paths import DATA_DIR, daily_path


class StageTimer:

    def __init__(self):

        self.timings = {}

    def record(self, name, seconds):

        self.timings[name] = self.timings.get(name, 0.0) + seconds

    @contextmanager
    def stage(self, name):

        start = time.perf_counter()

        try:

            yield

        finally:

            self.timings[name] = self.timings.get(name, 0.0) + (
                time.perf_counter() - start
            )

    def as_rows(self, trading_day, scan_id, observed_at=None):

        observed_at = observed_at or pd.Timestamp.utcnow().isoformat()

        return [
            {
                "trading_day": trading_day,
                "scan_id": scan_id,
                "observed_at": observed_at,
                "stage": stage,
                "seconds": round(seconds, 4),
            }
            for stage, seconds in self.timings.items()
        ]


def append_scanner_stage_profile(trading_day, scan_id, timer: StageTimer, observed_at=None):

    rows = timer.as_rows(
        trading_day=trading_day,
        scan_id=scan_id,
        observed_at=observed_at,
    )

    if not rows:

        return None

    paths = [
        daily_path(trading_day, "scanner_stage_profile.csv"),
        DATA_DIR / "scanner_stage_profile.csv",
    ]

    for path in paths:

        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        pd.DataFrame(rows).to_csv(
            path,
            mode="a",
            header=write_header,
            index=False,
        )

    return {
        "rows": len(rows),
        "path": str(paths[0]),
    }


def load_latest_stage_profile(report_date):

    path = daily_path(report_date, "scanner_stage_profile.csv")

    if not path.exists() or path.stat().st_size == 0:

        path = DATA_DIR / "scanner_stage_profile.csv"

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        profile = pd.read_csv(path)

    except Exception:

        return pd.DataFrame()

    if profile.empty:

        return profile

    if "trading_day" in profile.columns:

        daily_profile = profile[profile["trading_day"].astype(str).eq(str(report_date))].copy()

        if not daily_profile.empty:

            profile = daily_profile

    if "scan_id" not in profile.columns:

        return profile

    latest_scan_id = profile["scan_id"].dropna().astype(str).iloc[-1]

    return profile[profile["scan_id"].astype(str).eq(latest_scan_id)].copy()
