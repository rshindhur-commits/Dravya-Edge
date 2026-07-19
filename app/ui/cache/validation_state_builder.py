from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.analytics.trend_capture import summarize_trend_capture
from app.runtime.scan_generation import atomic_write_json, metadata_from_generation
from app.storage.daily_paths import daily_path, live_path


def _read_csv(path: Path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_csv(path)

    except Exception:

        return pd.DataFrame()


def _safe_number(value, default=None):

    try:

        if value is None or pd.isna(value):

            return default

        return round(float(value), 4)

    except Exception:

        return default


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


def _paper_summary(paper_events):

    if paper_events is None or paper_events.empty:

        return {
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "total_r": 0,
            "average_r": None,
        }

    events = paper_events.copy()
    event_type = events.get("event_type", pd.Series(dtype=object)).astype(str).str.upper()
    closed = events[
        event_type.isin(["AUTO_EXIT", "MANUAL_CLOSE", "CLOSE", "CLOSED", "EXIT"])
    ].copy()

    if closed.empty:

        closed = events[events.get("status", pd.Series(dtype=object)).astype(str).str.upper().eq("CLOSED")].copy()

    r_values = pd.to_numeric(
        closed.get("r_multiple", pd.Series(dtype=object)),
        errors="coerce"
    ).dropna()
    wins = r_values[r_values > 0]
    losses = r_values[r_values < 0]

    return {
        "closed_trades": int(len(closed)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / len(r_values) * 100, 1) if len(r_values) else None,
        "total_r": round(float(r_values.sum()), 2) if len(r_values) else 0,
        "average_r": round(float(r_values.mean()), 2) if len(r_values) else None,
    }


def _scanner_kpis(scanner):

    if scanner is None or scanner.empty:

        return {
            "rows": 0,
            "enter_paper": 0,
            "review": 0,
            "wait": 0,
            "avoid": 0,
        }

    action = scanner.get("Action Status", pd.Series(dtype=object)).astype(str).str.upper()

    return {
        "rows": int(len(scanner)),
        "enter_paper": int(action.eq("ENTER_PAPER").sum()),
        "review": int(action.eq("REVIEW_TV_CHART").sum()),
        "wait": int(action.eq("WAIT").sum()),
        "avoid": int(action.eq("AVOID").sum()),
    }


def _recommendations(trend_summary, paper_summary, scanner_kpis):

    recommendations = []

    if trend_summary.get("engineering_recommendation"):

        recommendations.append(trend_summary["engineering_recommendation"])

    if paper_summary.get("closed_trades", 0) == 0 and scanner_kpis.get("enter_paper", 0) > 0:

        recommendations.append({
            "priority": "MEDIUM",
            "reason": "Paper entries exist but no closed paper trades are available yet.",
            "recommendation": "Wait for closes before changing strategy thresholds."
        })

    if not recommendations:

        recommendations.append({
            "priority": "LOW",
            "reason": "Validation cache did not find urgent blockers.",
            "recommendation": "Continue paper validation."
        })

    return recommendations


def build_validation_state_payload(
    report_date: str,
    scanner: pd.DataFrame | None = None,
    paper_events: pd.DataFrame | None = None,
    trend_capture: pd.DataFrame | None = None,
    generated_at: str | None = None,
    scan_id: str | None = None,
    generation=None,
) -> dict[str, Any]:

    scanner = scanner if scanner is not None else pd.DataFrame()
    paper_events = paper_events if paper_events is not None else pd.DataFrame()
    trend_capture = trend_capture if trend_capture is not None else pd.DataFrame()
    trend_summary = summarize_trend_capture(trend_capture)
    paper = _paper_summary(paper_events)
    scanner_kpis = _scanner_kpis(scanner)

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
        "kpis": {
            "scanner": scanner_kpis,
            "paper": paper,
            "trend_capture": {
                "average_capture": trend_summary.get("average_capture"),
                "median_capture": trend_summary.get("median_capture"),
                "average_mfe": trend_summary.get("average_mfe"),
                "average_mae": trend_summary.get("average_mae"),
                "average_left_on_table": trend_summary.get("average_left_on_table"),
                "average_trend_health": trend_summary.get("average_trend_health"),
                "average_delay_gain": trend_summary.get("average_delay_gain"),
                "trade_efficiency_score": trend_summary.get("trade_efficiency_score"),
            },
        },
        "trend_capture": {
            "exit_verdict_distribution": _json_records(
                trend_summary.get("exit_verdict_distribution")
            ),
            "by_setup": _json_records(trend_summary.get("by_setup")),
            "by_regime": _json_records(trend_summary.get("by_regime")),
            "by_exit_reason": _json_records(trend_summary.get("by_exit_reason")),
        },
        "recommendations": _recommendations(
            trend_summary,
            paper,
            scanner_kpis
        ),
    }


def write_validation_state(report_date: str, scan_id: str | None = None, generation=None):

    scanner = _read_csv(daily_path(report_date, "scanner_output_close.csv"))
    paper_events = _read_csv(daily_path(report_date, "paper_trade_events.csv"))
    trend_capture = _read_csv(daily_path(report_date, "trend_capture_analysis.csv"))
    payload = build_validation_state_payload(
        report_date,
        scanner=scanner,
        paper_events=paper_events,
        trend_capture=trend_capture,
        scan_id=scan_id,
        generation=generation,
    )
    paths = [
        live_path("validation_state.json"),
        daily_path(report_date, "validation_state.json"),
    ]

    for path in paths:

        atomic_write_json(path, payload)

    return payload