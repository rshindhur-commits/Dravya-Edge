from __future__ import annotations

import pandas as pd


def _number(value, default=0.0):

    try:

        numeric = float(value)

        return numeric if pd.notna(numeric) else default

    except (TypeError, ValueError):

        return default


def _scale_relative_strength(value):

    return max(0, min(100, 50 + _number(value) * 10))


def _trend_health_score(row):

    value = str(
        row.get("V2 Trend Health Status")
        or row.get("Trend Health State")
        or ""
    ).upper()
    return {
        "STRONG": 100,
        "HEALTHY": 75,
        "WEAKENING": 45,
        "BROKEN": 0,
    }.get(value, 50)


def _liquidity_score(row):

    passed = str(row.get("Option Liquidity Passed") or "").lower()

    if passed in {"true", "1", "yes"}:

        return 100

    spread = _number(row.get("Option Spread %"), None)

    if spread is None:

        return 50

    return max(0, min(100, 100 - spread * 10))


def _score_row(row):

    setup = max(0, min(100, _number(
        row.get("Setup %", row.get("15m Score"))
    )))
    timing = max(0, min(100, _number(row.get("Entry Timing Score"))))
    trend = _trend_health_score(row)
    option = max(0, min(100, _number(row.get("Option Quality Score"))))
    relative_strength = _scale_relative_strength(row.get("RS Rank Score"))
    liquidity = _liquidity_score(row)
    score = round(
        setup * 0.25
        + timing * 0.20
        + trend * 0.20
        + option * 0.15
        + relative_strength * 0.10
        + liquidity * 0.10,
        2
    )
    components = {
        "setup": round(setup, 1),
        "timing": round(timing, 1),
        "trend": round(trend, 1),
        "option": round(option, 1),
        "relative_strength": round(relative_strength, 1),
        "liquidity": round(liquidity, 1),
    }
    leaders = sorted(
        components.items(),
        key=lambda item: item[1],
        reverse=True
    )[:3]
    return score, "; ".join(
        f"{name}={value:g}" for name, value in leaders
    )


def rank_candidates(frame):
    """Add observational TQS, rank, and explainable component leaders."""

    if frame is None or frame.empty:

        return frame

    ranked = frame.copy()
    scored = ranked.apply(_score_row, axis=1)
    ranked["Trade Quality Score"] = scored.map(lambda item: item[0])
    ranked["Rank Reason"] = scored.map(lambda item: item[1])
    ranked["Candidate Rank"] = (
        ranked["Trade Quality Score"]
        .rank(method="first", ascending=False)
        .astype(int)
    )
    return ranked