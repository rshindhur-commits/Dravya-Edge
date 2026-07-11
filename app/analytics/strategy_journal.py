from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.storage.daily_paths import DATA_DIR, daily_path


def _safe(value):

    if value is None:

        return None

    try:

        if pd.isna(value):

            return None

    except Exception:

        pass

    return value


def build_strategy_journal_row(scorecard):

    attribution = scorecard.loss_attribution
    largest_miss = None

    if attribution is not None and not attribution.empty:

        rows = attribution.copy()
        rows["move_pct"] = pd.to_numeric(rows.get("move_pct"), errors="coerce")
        rows = rows.sort_values("move_pct", key=lambda series: series.abs(), ascending=False)

        if not rows.empty:

            largest_miss = rows.iloc[0].get("symbol")

    recommendations = scorecard.engineering_recommendation.get("recommendations", []) if hasattr(scorecard, "engineering_recommendation") else scorecard.recommendations
    coverage = scorecard.coverage
    paper = scorecard.paper or {}

    confidence_inputs = [
        coverage.coverage_score if coverage else None,
        paper.get("win_rate"),
        100 + (paper.get("expectancy_r") or 0) * 25 if paper.get("expectancy_r") is not None else None,
    ]
    confidence_values = [value for value in confidence_inputs if value is not None]
    confidence = round(sum(confidence_values) / len(confidence_values), 1) if confidence_values else None

    return {
        "date": scorecard.date,
        "market_regime": scorecard.market_regime,
        "coverage": _safe(coverage.coverage_score if coverage else None),
        "win_rate": _safe(paper.get("win_rate")),
        "expectancy": _safe(paper.get("expectancy_r")),
        "average_r": _safe(paper.get("expectancy_r")),
        "best_trade": _safe(paper.get("average_winner_r")),
        "worst_trade": _safe(paper.get("average_loser_r")),
        "largest_miss": largest_miss,
        "largest_false_positive": None,
        "code_changes": "measurement_update",
        "recommendation": " | ".join(recommendations),
        "confidence": confidence,
    }


def append_strategy_journal(scorecard):

    row = build_strategy_journal_row(scorecard)
    paths = [
        daily_path(scorecard.date, "strategy_journal.csv"),
        DATA_DIR / "strategy_journal.csv",
    ]

    for path in paths:

        path.parent.mkdir(parents=True, exist_ok=True)
        existing = pd.read_csv(path) if path.exists() and path.stat().st_size > 0 else pd.DataFrame()
        updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True, sort=False)

        if "date" in updated.columns:

            updated = updated.drop_duplicates("date", keep="last")

        updated.to_csv(path, index=False)

    return row
