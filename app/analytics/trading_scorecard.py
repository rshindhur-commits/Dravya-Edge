from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from app.analytics.engine_health import build_engine_health
from app.analytics.engineering_recommendation import build_engineering_recommendation
from app.analytics.entry_delay import build_entry_delay
from app.analytics.loss_attribution import build_loss_attribution
from app.analytics.market_coverage import build_market_coverage, load_daily_inputs
from app.analytics.market_leaderboard import build_market_leaderboard
from app.analytics.opportunity_funnel import build_opportunity_funnel
from app.analytics.strategy_journal import append_strategy_journal


@dataclass
class TradingScorecard:

    date: str
    market_regime: str = "UNKNOWN"
    coverage: object | None = None
    expectancy: dict = field(default_factory=dict)
    paper: dict = field(default_factory=dict)
    signals: dict = field(default_factory=dict)
    exits: dict = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    entry_delay: dict = field(default_factory=dict)
    loss_attribution: pd.DataFrame = field(default_factory=pd.DataFrame)
    coverage_detail: pd.DataFrame = field(default_factory=pd.DataFrame)
    candidate_strength: pd.DataFrame = field(default_factory=pd.DataFrame)
    opportunity_funnel: object | None = None
    opportunity_funnel_rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    engine_health: object | None = None
    market_leaderboard: pd.DataFrame = field(default_factory=pd.DataFrame)
    engineering_recommendation: dict = field(default_factory=dict)
    strategy_journal_row: dict = field(default_factory=dict)


def _safe_numeric(series):

    return pd.to_numeric(series, errors="coerce")


def _mode(series, default="UNKNOWN"):

    if series is None or series.empty:

        return default

    values = series.dropna().astype(str)

    if values.empty:

        return default

    return values.value_counts().index[0]


def _paper_summary(paper_events):

    if paper_events is None or paper_events.empty:

        return {
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "average_winner_r": None,
            "average_loser_r": None,
            "expectancy_r": None,
        }

    r_values = _safe_numeric(paper_events.get("r_multiple", pd.Series(dtype=object))).dropna()

    if r_values.empty:

        return {
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "average_winner_r": None,
            "average_loser_r": None,
            "expectancy_r": None,
        }

    wins = r_values[r_values > 0]
    losses = r_values[r_values < 0]

    return {
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / len(r_values) * 100, 1),
        "average_winner_r": round(float(wins.mean()), 2) if not wins.empty else None,
        "average_loser_r": round(float(losses.mean()), 2) if not losses.empty else None,
        "expectancy_r": round(float(r_values.mean()), 2),
    }


def _signal_summary(audit):

    if audit is None or audit.empty:

        return {"signals": 0, "paper": 0, "real": 0}

    action = audit.get("action", pd.Series(dtype=object)).astype(str).str.upper()

    return {
        "signals": int(action.isin(["ENTER", "ENTER_PAPER", "REVIEW_TV_CHART", "OPENED"]).sum()),
        "paper": int(action.isin(["ENTER_PAPER", "OPENED"]).sum()),
        "real": int(action.eq("REAL").sum()),
    }


def _exit_summary(paper_events):

    if paper_events is None or paper_events.empty:

        return {"early_exits": 0, "late_exits": 0, "perfect_exits": 0}

    reason = paper_events.get("exit_reason", pd.Series(dtype=object)).astype(str).str.lower()
    r_values = _safe_numeric(paper_events.get("r_multiple", pd.Series(dtype=object)))

    return {
        "early_exits": int(reason.str.contains("early|weak", regex=True).sum()),
        "late_exits": int(reason.str.contains("late|stagnation|near-close", regex=True).sum()),
        "perfect_exits": int((r_values >= 1).sum()),
    }


def _candidate_strength(audit):

    if audit is None or audit.empty:

        return pd.DataFrame()

    required = {"symbol", "candidate_best_score", "candidate_score_delta"}

    if not required.issubset(set(audit.columns)):

        return pd.DataFrame()

    rows = audit.copy()
    rows["candidate_best_score"] = _safe_numeric(rows["candidate_best_score"])
    rows["candidate_score_delta"] = _safe_numeric(rows["candidate_score_delta"])
    rows = rows[rows["candidate_best_score"].notna()].copy()

    if rows.empty:

        return pd.DataFrame()

    rows = rows.sort_index().groupby("symbol").tail(1).copy()
    rows["trend"] = rows["candidate_score_delta"].map(
        lambda value: "up" if value > 0 else "down" if value < 0 else "flat"
    )

    return rows[[
        "symbol",
        "score",
        "candidate_best_score",
        "candidate_score_delta",
        "trend",
    ]].rename(columns={
        "score": "current_score",
        "candidate_best_score": "best_score",
        "candidate_score_delta": "score_delta",
    })


def _recommendations(scorecard: TradingScorecard):

    recommendations = []
    coverage = scorecard.coverage

    if coverage and coverage.coverage_score is not None:

        if coverage.coverage_score < 70:

            recommendations.append("Review scanner coverage before changing sizing.")

        elif coverage.missed > 0:

            recommendations.append("Inspect missed movers by attribution before strategy changes.")

    if scorecard.entry_delay.get("average_minutes") and scorecard.entry_delay["average_minutes"] > 10:

        recommendations.append("Average entry delay is high; review persistence and entry timing.")

    if not recommendations:

        recommendations.append("No strategy change. Keep collecting validation samples.")

    return recommendations


def build_trading_scorecard(report_date: str):

    coverage, coverage_detail, inputs = build_market_coverage(report_date)
    funnel, funnel_rows = build_opportunity_funnel(report_date)
    delay_df, delay_summary = build_entry_delay(report_date)
    attribution = build_loss_attribution(report_date)
    engine_health = build_engine_health(report_date)
    leaderboard = build_market_leaderboard(report_date)
    audit = inputs.get("audit", pd.DataFrame())
    scanner = inputs.get("scanner", pd.DataFrame())
    paper_events = inputs.get("paper_events", pd.DataFrame())
    scorecard = TradingScorecard(
        date=report_date,
        market_regime=_mode(scanner.get("Market Regime", pd.Series(dtype=object))),
        coverage=coverage,
        expectancy=_paper_summary(paper_events),
        paper=_paper_summary(paper_events),
        signals=_signal_summary(audit),
        exits=_exit_summary(paper_events),
        entry_delay=delay_summary,
        loss_attribution=attribution,
        coverage_detail=coverage_detail,
        candidate_strength=_candidate_strength(audit),
        opportunity_funnel=funnel,
        opportunity_funnel_rows=funnel_rows,
        engine_health=engine_health,
        market_leaderboard=leaderboard,
    )
    scorecard.engineering_recommendation = build_engineering_recommendation(scorecard)
    scorecard.recommendations = scorecard.engineering_recommendation.get(
        "recommendations",
        _recommendations(scorecard)
    )
    scorecard.strategy_journal_row = append_strategy_journal(scorecard)

    return scorecard
