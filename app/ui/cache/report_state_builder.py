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


def _daily_validation_states(limit=20):

    states = []

    try:

        for path in sorted(DAILY_DIR.glob("*/validation_state.json"), reverse=True)[:limit]:

            states.append(json.loads(path.read_text(encoding="utf-8")))

    except Exception:

        return states

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
    historical_trade_efficiency = build_historical_trade_efficiency(
        validation_states if validation_states is not None else _daily_validation_states()
    )

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