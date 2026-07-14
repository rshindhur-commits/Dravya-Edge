from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


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


def _top_candidates(rows, limit=10):

    candidates = []

    for row in rows:

        readiness = _safe_float(_row_get(row, "ENTRY_READINESS"), 0) or 0
        rr = _safe_float(_row_get(row, "Candidate RR", "Risk Reward", "RR"), 0) or 0
        score = _safe_float(_row_get(row, "15m Score"), 0) or 0
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
                "sort_score": readiness + min(rr, 3) * 8 + min(abs(score), 100) * 0.1,
            }
        )

    candidates = sorted(candidates, key=lambda item: item["sort_score"], reverse=True)
    for item in candidates:

        item.pop("sort_score", None)
    return candidates[:limit]


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


def _best_by_direction(candidates, direction):

    for candidate in candidates:

        if candidate.get("direction") == direction:

            return candidate

    return None


def build_dashboard_state(df: pd.DataFrame, generated_at: str | None = None, scanner_status: str = "LIVE") -> dict[str, Any]:

    rows = df.to_dict("records") if df is not None and not df.empty else []
    generated_at = generated_at or datetime.now().isoformat(timespec="seconds")
    scan_id = _clean(_row_get(rows[0], "scan_id", "Scan ID")) if rows else None
    candidates = _top_candidates(rows)
    best_call = _best_by_direction(candidates, "CALL")
    best_put = _best_by_direction(candidates, "PUT")
    summary = _summary(rows)
    blockers = _top_blockers(rows)
    best = best_put or best_call

    return {
        "generated_at": generated_at,
        "scan_id": scan_id,
        "data_version": scan_id,
        "scanner": scanner_status,
        "decision_engine": "v4",
        "telegram": "CONFIGURED",
        "market_bias": _market_bias(rows),
        "best_call": best_call,
        "best_put": best_put,
        "reason": best.get("blocked") if best else "NO_CANDIDATES",
        "summary": summary,
        "top_candidates": candidates,
        "blockers": blockers,
        "missed_opportunities": [candidate for candidate in candidates if candidate.get("action") not in {"ENTER", "ENTER_PAPER", "OPENED"}][:5],
    }


def write_dashboard_state(df: pd.DataFrame, paths: list[Path], generated_at: str | None = None) -> dict[str, Any]:

    state = build_dashboard_state(df, generated_at=generated_at)
    payload = json.dumps(state, indent=2, default=str)

    for path in paths:

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")

    return state