from __future__ import annotations

import pandas as pd
import streamlit as st

from app.analytics.trading_scorecard import build_trading_scorecard
from app.ui.components import kpi_card


def _fmt_pct(value):

    if value is None or pd.isna(value):

        return "N/A"

    return f"{value:.1f}%"


def _fmt_num(value):

    if value is None or pd.isna(value):

        return "N/A"

    return str(value)


def _trend_symbol(value):

    text = str(value or "").lower()

    if text == "up":

        return "UP"

    if text == "down":

        return "DOWN"

    return "FLAT"


def render_market_coverage(report_date: str):

    scorecard = build_trading_scorecard(report_date)
    coverage = scorecard.coverage

    st.subheader("Trading Scorecard")
    cols = st.columns(5)
    values = [
        ("Market Regime", scorecard.market_regime),
        ("Coverage", _fmt_pct(coverage.coverage_score if coverage else None)),
        ("Signals", scorecard.signals.get("signals", 0)),
        ("Paper", scorecard.signals.get("paper", 0)),
        ("Expectancy", _fmt_num(scorecard.expectancy.get("expectancy_r"))),
    ]

    for col, (label, value) in zip(cols, values):

        with col:

            kpi_card(label, str(value))

    st.caption("Recommendations: " + " | ".join(scorecard.recommendations))

    if scorecard.strategy_journal_row:

        st.caption(
            "Strategy journal confidence: "
            + _fmt_num(scorecard.strategy_journal_row.get("confidence"))
        )

    st.subheader("Market Coverage")
    cols = st.columns(6)
    values = [
        ("Watchlist Movers", coverage.total_watchlist),
        ("Detected", f"{coverage.detected} / {_fmt_pct(coverage.detection_rate)}"),
        ("Correct Direction", f"{coverage.correct_direction} / {_fmt_pct(coverage.correct_direction_rate)}"),
        ("Paper Entries", f"{coverage.entered} / {_fmt_pct(coverage.entry_rate)}"),
        ("Profitable", f"{coverage.profitable} / {_fmt_pct(coverage.profitable_rate)}"),
        ("Missed Winners", coverage.missed),
    ]

    for col, (label, value) in zip(cols, values):

        with col:

            kpi_card(label, str(value))

    st.subheader("Opportunity Funnel")

    if scorecard.opportunity_funnel_rows.empty:

        st.info("No opportunity funnel rows yet.")

    else:

        st.dataframe(scorecard.opportunity_funnel_rows, width="stretch", hide_index=True)

    st.subheader("Engine Health")
    health = scorecard.engine_health
    cols = st.columns(8)
    values = [
        ("Health", _fmt_num(health.health_score if health else None)),
        ("Scanner", _fmt_num(health.scanner_runtime if health else None)),
        ("Workers", _fmt_num(health.worker_count if health else None)),
        ("Symbols", _fmt_num(health.symbols_completed if health else None)),
        ("Avg/Symbol", _fmt_num(health.average_symbol_time if health else None)),
        ("Fresh Quotes", _fmt_pct(health.fresh_quote_rate if health else None)),
        ("Delayed", _fmt_num(health.delayed_quotes if health else None)),
        ("Failures", _fmt_num(health.symbols_failed if health else None)),
    ]

    for col, (label, value) in zip(cols, values):

        with col:

            kpi_card(label, str(value))

    if health and health.stage_profile is not None and not health.stage_profile.empty:

        stage_profile = health.stage_profile.copy()
        stage_profile["seconds"] = pd.to_numeric(stage_profile["seconds"], errors="coerce")
        stage_profile = stage_profile.sort_values("seconds", ascending=False)
        st.dataframe(
            stage_profile[["stage", "seconds"]],
            width="stretch",
            hide_index=True
        )

    st.subheader("Market Leaderboard")

    if scorecard.market_leaderboard.empty:

        st.info("No market leaderboard rows yet.")

    else:

        st.dataframe(scorecard.market_leaderboard, width="stretch", hide_index=True)

    st.subheader("Entry Delay")
    delay = scorecard.entry_delay
    cols = st.columns(4)
    values = [
        ("Average", _fmt_num(delay.get("average_minutes"))),
        ("Median", _fmt_num(delay.get("median_minutes"))),
        ("Longest", _fmt_num(delay.get("longest_minutes"))),
        ("Best", _fmt_num(delay.get("best_minutes"))),
    ]

    for col, (label, value) in zip(cols, values):

        with col:

            kpi_card(label, str(value))

    if delay:

        distribution = pd.DataFrame([
            {"Bucket": "0-2", "Count": delay.get("bucket_0_2", 0)},
            {"Bucket": "2-5", "Count": delay.get("bucket_2_5", 0)},
            {"Bucket": "5-10", "Count": delay.get("bucket_5_10", 0)},
            {"Bucket": "10+", "Count": delay.get("bucket_10_plus", 0)},
        ])
        st.dataframe(distribution, width="stretch", hide_index=True)

    st.subheader("Candidate Strength")

    if scorecard.candidate_strength.empty:

        st.info("No persisted candidate strength rows yet.")

    else:

        display = scorecard.candidate_strength.copy()
        display["trend"] = display["trend"].map(_trend_symbol)
        st.dataframe(display, width="stretch", hide_index=True)

    st.subheader("Missed Opportunity Attribution")

    if scorecard.loss_attribution.empty:

        st.info("No missed mover attribution rows yet.")

    else:

        st.dataframe(scorecard.loss_attribution, width="stretch", hide_index=True)

    with st.expander("Market coverage detail", expanded=False):

        if scorecard.coverage_detail.empty:

            st.info("No market coverage detail rows yet.")

        else:

            st.dataframe(scorecard.coverage_detail, width="stretch", hide_index=True)
