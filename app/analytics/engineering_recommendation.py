from __future__ import annotations

import pandas as pd


def build_engineering_recommendation(scorecard):

    coverage = scorecard.coverage
    attribution = scorecard.loss_attribution
    paper = scorecard.paper or {}
    signals = scorecard.signals or {}
    recommendations = []

    coverage_score = coverage.coverage_score if coverage else None
    entry_rate = coverage.entry_rate if coverage else None
    win_rate = paper.get("win_rate")

    option_rejection_rate = 0

    if attribution is not None and not attribution.empty and "reason" in attribution.columns:

        reasons = attribution["reason"].astype(str).str.upper()
        option_rejection_rate = round((reasons.eq("OPTION").mean() or 0) * 100, 1)

    if coverage_score is not None and coverage_score < 70:

        recommendations.append("Investigate scanner coverage")

    if coverage_score is not None and coverage_score > 90 and entry_rate is not None and entry_rate < 40:

        recommendations.append("Investigate entry delay")

    if entry_rate is not None and entry_rate > 80 and win_rate is not None and win_rate < 45:

        recommendations.append("Investigate exits")

    if option_rejection_rate > 40:

        recommendations.append("Investigate option spread/filtering")

    if not recommendations:

        recommendations.append("No strategy change")

    return {
        "recommendations": recommendations,
        "primary": recommendations[0],
        "option_rejection_rate": option_rejection_rate,
        "coverage_score": coverage_score,
        "entry_rate": entry_rate,
        "win_rate": win_rate,
        "signals": signals.get("signals", 0),
    }
