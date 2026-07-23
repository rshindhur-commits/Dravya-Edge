from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd

from app.analytics.decision_waterfall import (
    build_decision_waterfall,
    build_v1_v2_waterfall_comparison,
    summarize_blocking_stages,
)
from app.runtime.scan_generation import metadata_from_generation


def _clean(value):

    if value is None:

        return None

    if isinstance(value, float):

        if math.isnan(value) or math.isinf(value):

            return None

        return round(value, 4)

    try:

        if pd.isna(value):

            return None

    except Exception:

        pass

    if isinstance(value, (str, int, bool)):

        return value

    try:

        return round(float(value), 4)

    except Exception:

        return str(value)


def _row_get(row, *names, default=None):

    for name in names:

        try:

            value = row.get(name)

        except Exception:

            value = None

        if value is None:

            continue

        text = str(value).strip()

        if text.lower() in {"", "nan", "none"}:

            continue

        return value

    return default


def _safe_float(value, default=None):

    try:

        if value is None:

            return default

        numeric = float(value)

        if math.isnan(numeric) or math.isinf(numeric):

            return default

        return numeric

    except Exception:

        return default


def _direction(row):

    direction = str(_row_get(row, "Candidate Direction", "Intended Option Direction", default="") or "").upper()

    if direction in {"CALL", "PUT"}:

        return direction

    signal = str(_row_get(row, "Final Signal", default="") or "").upper()

    if "BEAR" in signal:

        return "PUT"

    if "BULL" in signal:

        return "CALL"

    return "NONE"


def _blocker(row):

    return _clean(
        _row_get(
            row,
            "ENTRY_GATE_FAILURE_STAGE",
            "Blocked By",
            "Option Rejection Reason",
            "Realtime Block Reason",
            "Action Reason",
            default="Unknown",
        )
    )


def _needs(row):

    stage = str(_row_get(row, "ENTRY_GATE_FAILURE_STAGE", default="") or "").upper()
    rr = _safe_float(_row_get(row, "Candidate RR", "Risk Reward", "RR"), None)

    if stage == "RISK" or (rr is not None and rr < 2.0):

        return f"RR >= 2.0 (actual {round(rr, 2) if rr is not None else 'N/A'})"

    failed = _row_get(row, "FAILED_ENTRY_CONDITIONS")

    if failed:

        return str(failed).split(",")[0].strip()

    reason = _row_get(row, "Next Condition", "Action Reason")

    return _clean(reason) or "Clean trigger"


def _option_label(row):

    quality = _safe_float(_row_get(row, "Option Quality Score"), None)
    freshness = str(_row_get(row, "Option Quote Freshness", default="") or "")

    if quality is None:

        return freshness or "N/A"

    if quality >= 80:

        return "Good"

    if quality >= 65:

        return "OK"

    return "Weak"


def _telegram_status(row):
    sent = str(_row_get(row, "Telegram Sent", default="")).strip().lower()

    if sent in {"true", "1", "yes"}:

        return "SENT"

    reason = _row_get(
        row,
        "Telegram Block Reason",
        "Telegram Eligibility",
        "Telegram Error Reason",
    )

    return f"NOT SENT: {reason}" if reason else "NOT SENT"


def _action_priority(action):
    return {
        "ENTER": 40,
        "ENTER_PAPER": 35,
        "REVIEW_TV_CHART": 20,
        "WAIT": 10,
    }.get(str(action or "").upper(), 0)


def _top_candidates(rows, limit=10):

    candidates = []

    for row in rows:

        readiness = _safe_float(_row_get(row, "ENTRY_READINESS"), 0) or 0
        rr = _safe_float(_row_get(row, "Candidate RR", "Risk Reward", "RR"), 0) or 0
        score = _safe_float(_row_get(row, "15m Score"), 0) or 0
        trade_quality_score = _safe_float(
            _row_get(row, "Trade Quality Score"),
            None
        )
        candidate_rank = _safe_float(
            _row_get(row, "Candidate Rank"),
            None
        )
        action = str(_row_get(row, "Action Status", default="") or "")
        candidates.append(
            {
                "symbol": _clean(_row_get(row, "Symbol")),
                "direction": _direction(row),
                "setup": _clean(_row_get(row, "ENTRY_SETUP_CANDIDATE", "Entry")),
                "readiness": round(readiness, 1),
                "setup_score": _clean(_row_get(row, "Setup %", "15m Score")),
                "rr": round(rr, 2) if rr else None,
                "option": _option_label(row),
                "blocked": _blocker(row),
                "needs": _needs(row),
                "next_trigger": _clean(_row_get(row, "Next Condition", "Candidate Trigger", "Entry Trigger")),
                "action": action,
                "entry_price": _clean(_row_get(row, "Candidate Entry Price", "Entry Price", "Price")),
                "stop_price": _clean(_row_get(row, "Candidate Stop Price", "Stop Loss")),
                "target_price": _clean(_row_get(row, "Candidate Target Price", "Take Profit")),
                "trend_health": _clean(_row_get(row, "Trend Health State", "V2 Trend Health Status")),
                "option_ticker": _clean(_row_get(row, "Option Ticker", "Recommended Option")),
                "option_quality": _clean(_row_get(row, "Option Quality Score")),
                "entry_timing_score": _clean(
                    _row_get(row, "Entry Timing Score")
                ),
                "entry_timing_grade": _clean(
                    _row_get(row, "Entry Timing Grade")
                ),
                "trade_quality_score": _clean(trade_quality_score),
                "candidate_rank": _clean(candidate_rank),
                "rank_reason": _clean(_row_get(row, "Rank Reason")),
                "telegram": _telegram_status(row),
                "sort_score": (
                    trade_quality_score
                    if trade_quality_score is not None
                    else _action_priority(action)
                    + readiness
                    + min(rr, 3) * 8
                    + min(abs(score), 100) * 0.1
                ),
            }
        )

    candidates = sorted(
        candidates,
        key=lambda item: (
            item["candidate_rank"] is None,
            item["candidate_rank"]
            if item["candidate_rank"] is not None
            else -item["sort_score"],
        )
    )
    for item in candidates:

        item.pop("sort_score", None)
    return candidates[:limit]


def _open_trades(rows):
    open_trades = []

    for row in rows:
        action = str(_row_get(row, "Trade Action", default="") or "").upper()
        entry = str(_row_get(row, "Entry", default="") or "").upper()

        if action not in {"HOLD", "PARTIAL_PROFIT"} and entry != "ACTIVE_TRADE":

            continue

        open_trades.append({
            "symbol": _clean(_row_get(row, "Symbol")),
            "r_progress": _clean(_row_get(row, "RR Progress")),
            "trend_health": _clean(_row_get(row, "V2 Trend Health Status", "Trend Health State")),
            "action": action or "HOLD",
            "setup": _clean(_row_get(row, "Entry")),
        })

    return open_trades


def _market_bias(rows):

    bullish = sum(1 for row in rows if "BULL" in str(_row_get(row, "Final Signal", default="")).upper())
    bearish = sum(1 for row in rows if "BEAR" in str(_row_get(row, "Final Signal", default="")).upper())

    if bearish > bullish:

        return "BEARISH"
    if bullish > bearish:

        return "BULLISH"
    return "MIXED"


def _top_blockers(rows):

    counts: dict[str, int] = {}

    for row in rows:

        blocker = str(_blocker(row) or "Unknown")
        counts[blocker] = counts.get(blocker, 0) + 1

    return [
        {"blocker": key, "count": value}
        for key, value in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


def _summary(rows):

    entered = sum(1 for row in rows if str(_row_get(row, "Action Status", default="")).upper() in {"ENTER", "ENTER_PAPER", "OPENED"})
    entry = sum(1 for row in rows if str(_row_get(row, "Entry", default="")).upper() not in {"", "NAN", "NONE", "NO_ENTRY", "NO_SETUP"})
    option = sum(1 for row in rows if _row_get(row, "Option Ticker"))
    bearish = sum(1 for row in rows if _direction(row) == "PUT")
    bullish = sum(1 for row in rows if _direction(row) == "CALL")

    return {
        "scanned": len(rows),
        "bullish": bullish,
        "bearish": bearish,
        "entry": entry,
        "option": option,
        "trades": entered,
    }


def build_today_performance_summary(paper_events=None, trend_capture=None):

    paper_events = paper_events if paper_events is not None else pd.DataFrame()
    trend_capture = trend_capture if trend_capture is not None else pd.DataFrame()
    events = paper_events.copy()
    event_type = events.get("event_type", pd.Series(dtype=object)).astype(str).str.upper()
    closed = events[event_type.isin(["AUTO_EXIT", "MANUAL_CLOSE", "CLOSE", "CLOSED", "EXIT"])]

    if closed.empty:

        closed = events[events.get("status", pd.Series(dtype=object)).astype(str).str.upper().eq("CLOSED")]

    r_values = pd.to_numeric(closed.get("r_multiple", pd.Series(dtype=object)), errors="coerce").dropna()
    capture = pd.to_numeric(trend_capture.get("Trend Capture %", pd.Series(dtype=object)), errors="coerce").dropna()
    tes = pd.to_numeric(trend_capture.get("Trade Efficiency Score", pd.Series(dtype=object)), errors="coerce").dropna()
    left_on_table = pd.to_numeric(trend_capture.get("Left On Table", pd.Series(dtype=object)), errors="coerce").dropna()
    verdicts = trend_capture.get("Exit Verdict", pd.Series(dtype=object)).astype(str).str.upper()
    completed_at = pd.Series(dtype=object)

    for column in ["closed_at_et", "closed_at_utc", "closed_at", "event_timestamp", "timestamp"]:

        if column in closed.columns:

            completed_at = pd.to_datetime(closed[column], errors="coerce", utc=True).dropna()

            if not completed_at.empty:

                break

    last_completed_trade = completed_at.max().isoformat() if not completed_at.empty else None

    return {
        "completed_trades": int(len(closed)),
        "last_completed_trade": last_completed_trade,
        "winning_trades": int((r_values > 0).sum()),
        "losing_trades": int((r_values < 0).sum()),
        "win_rate": round(float((r_values > 0).mean() * 100), 1) if not r_values.empty else None,
        "average_r": round(float(r_values.mean()), 2) if not r_values.empty else None,
        "average_trend_capture": round(float(capture.mean()), 1) if not capture.empty else None,
        "best_capture": round(float(capture.max()), 1) if not capture.empty else None,
        "average_tes": round(float(tes.mean()), 1) if not tes.empty else None,
        "excellent_exits": int(verdicts.eq("EXCELLENT_EXIT").sum()),
        "exit_too_early": int(verdicts.eq("EXIT_TOO_EARLY").sum()),
        "left_on_table": round(float(left_on_table.mean()), 1) if not left_on_table.empty else None,
    }


def _best_by_direction(candidates, direction):

    for candidate in candidates:

        if candidate.get("direction") == direction:

            return candidate

    return None


def _scan_generation_metadata(rows, generated_at, scan_id):

    source_timestamps = []

    for row in rows:

        timestamp = _clean(
            _row_get(
                row,
                "generated_at",
                "Generated At",
                "scan_generated_at",
                "Scan Generated At",
                "Timestamp",
                "timestamp",
            )
        )

        if timestamp is not None:

            source_timestamps.append(timestamp)

    unique_timestamps = sorted({str(timestamp) for timestamp in source_timestamps})

    return {
        "scan_id": scan_id,
        "generated_at": generated_at,
        "row_count": len(rows),
        "data_version": scan_id or generated_at,
        "source_timestamps": unique_timestamps[:10],
    }


def _write_json_atomic(path: Path, payload: str):

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")

    try:

        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(path)

    finally:

        try:

            if temp_path.exists():

                temp_path.unlink()

        except Exception:

            pass


def build_dashboard_state(
    df: pd.DataFrame,
    generated_at: str | None = None,
    scanner_status: str = "LIVE",
    scanner_health: dict[str, Any] | None = None,
    telegram_summary: dict[str, Any] | None = None,
    today_performance: dict[str, Any] | None = None,
    generation=None,
) -> dict[str, Any]:

    rows = df.to_dict("records") if df is not None and not df.empty else []
    generated_at = generated_at or datetime.now().isoformat(timespec="seconds")
    scan_id = _clean(_row_get(rows[0], "scan_id", "Scan ID")) if rows else None
    candidates = _top_candidates(rows)
    best_call = _best_by_direction(candidates, "CALL")
    best_put = _best_by_direction(candidates, "PUT")
    summary = _summary(rows)
    blockers = _top_blockers(rows)
    decision_waterfalls = [
        build_decision_waterfall(
            row,
            scan_id=str(_row_get(row, "scan_id", "Scan ID", default="")),
        )
        for row in rows
    ]
    v1_v2_decision_waterfalls = [
        build_v1_v2_waterfall_comparison(
            row,
            scan_id=str(_row_get(row, "scan_id", "Scan ID", default="")),
        )
        for row in rows
    ]
    best = best_put or best_call
    scan_generation = _scan_generation_metadata(rows, generated_at, scan_id)
    metadata = metadata_from_generation(generation, scan_id=scan_id) or {
        "scan_id": scan_id,
        "generation": scan_generation["data_version"],
        "schema": 1,
        "created_at": generated_at,
    }

    return {
        "metadata": metadata,
        "generated_at": generated_at,
        "scan_id": scan_id,
        "data_version": scan_generation["data_version"],
        "scan_generation": scan_generation,
        "scanner": scanner_status,
        "decision_engine": "v4",
        "telegram": "CONFIGURED",
        "telegram_summary": telegram_summary or {},
        "scanner_health": scanner_health or {},
        "market_bias": _market_bias(rows),
        "best_call": best_call,
        "best_put": best_put,
        "reason": best.get("blocked") if best else "NO_CANDIDATES",
        "summary": summary,
        "today_performance": today_performance or build_today_performance_summary(),
        "top_candidates": candidates,
        "decision_center": {
            "best_call": best_call,
            "best_put": best_put,
            "ranked_opportunities": candidates[:5],
        },
        "open_trades": _open_trades(rows),
        "blockers": blockers,
        "decision_waterfalls": decision_waterfalls,
        "v1_v2_decision_waterfalls": v1_v2_decision_waterfalls,
        "blocking_stage_summary": summarize_blocking_stages(
            decision_waterfalls
        ),
        "missed_opportunities": [candidate for candidate in candidates if candidate.get("action") not in {"ENTER", "ENTER_PAPER", "OPENED"}][:5],
    }


def write_dashboard_state(
    df: pd.DataFrame,
    paths: list[Path],
    generated_at: str | None = None,
    scanner_health: dict[str, Any] | None = None,
    telegram_summary: dict[str, Any] | None = None,
    today_performance: dict[str, Any] | None = None,
    generation=None,
) -> dict[str, Any]:

    state = build_dashboard_state(
        df,
        generated_at=generated_at,
        scanner_health=scanner_health,
        telegram_summary=telegram_summary,
        today_performance=today_performance,
        generation=generation,
    )
    payload = json.dumps(state, indent=2, default=str)

    for path in paths:

        _write_json_atomic(path, payload)

    return state