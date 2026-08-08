from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.runtime.scan_generation import atomic_write_json, metadata_from_generation
from app.storage.daily_paths import DAILY_DIR, daily_path, live_path, ROOT_DIR


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


_VALIDATION_STATE_CACHE: dict[str, tuple[tuple[int, int], Any]] = {}


def _daily_validation_states(limit=20):
    """The most recent days' validation states, parsed once each.

    Three builders want this list, and it was being read once for each of them
    -- twenty files of half a megabyte of JSON turned into Python objects and
    thrown away, three times over, on every scan of the session. It was the
    largest single thing the worker did with memory.

    A finished day's validation state never changes, so entries are held
    against (mtime, size) and only today's file is re-read. The cache is
    rebuilt from the paths still in range each call, so a worker that runs for
    months does not keep one more state for every day it has survived.
    """
    states = []
    fresh = {}

    try:

        paths = sorted(
            DAILY_DIR.glob("*/validation_state.json"), reverse=True
        )[:limit]

    except Exception:

        return states

    for path in paths:

        try:

            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = _VALIDATION_STATE_CACHE.get(str(path))

            if cached is not None and cached[0] == stamp:

                state = cached[1]

            else:

                state = json.loads(path.read_text(encoding="utf-8"))

        except Exception:

            continue

        fresh[str(path)] = (stamp, state)
        states.append(state)

    _VALIDATION_STATE_CACHE.clear()
    _VALIDATION_STATE_CACHE.update(fresh)

    return states


def _records(frame):

    if frame is None or frame.empty:

        return []

    return json.loads(frame.to_json(orient="records", date_format="iso"))


def build_historical_trade_efficiency(validation_states):

    daily_rows = []
    setup_rows = []
    regime_rows = []
    exit_rows = []

    for state in validation_states or []:

        efficiency = state.get("trade_efficiency") or {}
        summary = efficiency.get("summary") or {}
        paper = (state.get("kpis") or {}).get("paper") or {}
        daily_rows.append({
            "Trading Day": state.get("trading_day"),
            "Capture": summary.get("average_capture"),
            "TES": summary.get("average_tes"),
            "Average R": summary.get("average_r"),
            "Win Rate": paper.get("win_rate"),
        })
        charts = efficiency.get("charts") or {}

        for key, target in [
            ("capture_by_setup", setup_rows),
            ("capture_by_regime", regime_rows),
            ("exit_verdict", exit_rows),
        ]:

            for row in charts.get(key) or []:

                target.append(row)

    daily = pd.DataFrame(daily_rows)

    if not daily.empty:

        daily["Trading Day"] = pd.to_datetime(daily["Trading Day"], errors="coerce")
        daily = daily.sort_values("Trading Day").tail(20)
        for column in ["Capture", "TES", "Average R", "Win Rate"]:

            daily[column] = pd.to_numeric(daily[column], errors="coerce")

        daily["Weekday"] = daily["Trading Day"].dt.day_name()
        daily["Rolling Average Capture"] = daily["Capture"].rolling(5, min_periods=1).mean().round(2)

    def aggregate(rows, label):

        frame = pd.DataFrame(rows)

        if frame.empty:

            return []

        value = "Average Trend Capture %" if "Average Trend Capture %" in frame.columns else "Count"
        return _records(frame.groupby(label, dropna=True)[value].mean().round(2).reset_index()) if label in frame.columns else []

    weekly = pd.DataFrame()
    monthly = pd.DataFrame()
    weekday = pd.DataFrame()

    if not daily.empty:

        weekly = daily.set_index("Trading Day").resample("W-FRI")[["Capture", "TES", "Average R", "Win Rate"]].mean().round(2).reset_index()
        monthly = daily.set_index("Trading Day").resample("ME")[["Capture", "TES", "Average R", "Win Rate"]].mean().round(2).reset_index()
        weekday = daily.groupby("Weekday", dropna=True)["Capture"].mean().round(2).reset_index()

    return {
        "daily": _records(daily),
        "weekly": _records(weekly),
        "monthly": _records(monthly),
        "weekday": _records(weekday),
        "setup": aggregate(setup_rows, "Setup"),
        "regime": aggregate(regime_rows, "Market Regime"),
        "exit": aggregate(exit_rows, "Exit Verdict"),
    }


def build_historical_v2_learning(limit=20):
    frames = []

    for directory in sorted(DAILY_DIR.glob("*"), reverse=True)[:limit]:
        path = directory / "v2_learning_dataset.csv"
        try:
            if path.exists() and path.stat().st_size:
                frames.append(pd.read_csv(path))
        except Exception:
            continue

    if not frames:
        return {"daily": [], "exit_phase": []}

    learning = pd.concat(frames, ignore_index=True, sort=False)
    if "trading_day" not in learning.columns:
        return {"daily": [], "exit_phase": []}
    if (
        "engine_version" in learning.columns
        and learning["engine_version"].notna().any()
    ):
        learning = learning[
            learning["engine_version"].astype(str).str.lower().eq("v2")
        ].copy()
    if learning.empty:
        return {"daily": [], "exit_phase": []}

    numeric_columns = [
        "trend_age", "entry_efficiency_score", "trend_capture_pct", "tes",
    ]
    for column in numeric_columns:
        learning[column] = pd.to_numeric(
            learning.get(column, pd.Series(index=learning.index, dtype=float)),
            errors="coerce",
        )

    daily = learning.groupby("trading_day", dropna=True)[numeric_columns].mean().round(2).reset_index()
    daily = daily.rename(columns={
        "trading_day": "Trading Day",
        "trend_age": "Trend Age",
        "entry_efficiency_score": "Entry Efficiency",
        "trend_capture_pct": "Trend Capture %",
        "tes": "TES",
    }).sort_values("Trading Day")
    phase_counts = learning.get(
        "exit_phase",
        pd.Series("UNKNOWN", index=learning.index),
    ).fillna("UNKNOWN").value_counts().reset_index()
    phase_counts.columns = ["Exit Phase", "Count"]
    return {
        "daily": _records(daily),
        "exit_phase": _records(phase_counts),
    }


def build_historical_observational_analytics(validation_states):

    records = []

    for state in validation_states or []:

        analytics = state.get("observational_analytics") or {}
        timing = analytics.get("entry_timing") or {}
        ranking = pd.DataFrame(analytics.get("trade_ranking") or [])
        quality = pd.to_numeric(
            ranking.get("Trade Quality Score", pd.Series(dtype=float)),
            errors="coerce",
        )
        rank = pd.to_numeric(
            ranking.get("Candidate Rank", pd.Series(dtype=float)),
            errors="coerce",
        )
        records.append({
            "Trading Day": state.get("trading_day"),
            "Average Entry Timing": timing.get("average_score"),
            "Late Entries": len(timing.get("late_entries") or []),
            "Average TQS": round(float(quality.mean()), 2)
            if quality.notna().any()
            else None,
            "Average Rank": round(float(rank.mean()), 2)
            if rank.notna().any()
            else None,
        })

    return {"daily": _records(pd.DataFrame(records))}


def build_historical_blocking_trends(validation_states):

    rows = []
    dominant = []

    for state in validation_states or []:

        analytics = state.get("observational_analytics") or {}
        summary = analytics.get("blocking_stage_summary") or {}
        stages = summary.get("stages") or []

        for stage in stages:

            rows.append({
                "Trading Day": state.get("trading_day"),
                "Stage": stage.get("stage"),
                "Count": stage.get("count"),
                "Percentage": stage.get("percentage"),
            })

        if stages:

            dominant.append({
                "Trading Day": state.get("trading_day"),
                "Dominant Blocking Stage": stages[0].get("stage"),
                "Count": stages[0].get("count"),
                "Percentage": stages[0].get("percentage"),
            })

    return {
        "daily": _records(pd.DataFrame(rows)),
        "dominant_daily": _records(pd.DataFrame(dominant)),
    }

def build_report_state_payload(
    report_date: str,
    daily_report_path: Path | None = None,
    root_report_path: Path | None = None,
    scanner_path: Path | None = None,
    generated_at: str | None = None,
    scan_id: str | None = None,
    generation=None,
    validation_states=None,
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
    # Read once and hand the same list to all three builders.
    if validation_states is None:

        validation_states = _daily_validation_states()

    historical_trade_efficiency = build_historical_trade_efficiency(validation_states)
    historical_v2_learning = build_historical_v2_learning()
    historical_observational_analytics = (
        build_historical_observational_analytics(validation_states)
    )
    historical_blocking_trends = build_historical_blocking_trends(validation_states)

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
        "historical_trade_efficiency": historical_trade_efficiency,
        "historical_v2_learning": historical_v2_learning,
        "historical_observational_analytics": historical_observational_analytics,
        "historical_blocking_trends": historical_blocking_trends,
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