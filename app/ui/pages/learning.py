from __future__ import annotations

import json

from app.storage.daily_paths import live_path


def render(df=None):

    import streamlit as st

    try:
        from app.db.learning_engine_repository import LearningEngineRepository
        repository = LearningEngineRepository()
        summary = repository.get_daily_summary() or repository.latest_summary() or {}
    except Exception:
        summary = {}

    if not summary:
        path = live_path("daily_engine_summary.json")
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}

    st.subheader("Learning")

    if not summary:
        st.info("Learning metrics will appear after scanner persistence completes.")
        return

    columns = st.columns(4)
    metrics = [
        ("V2 Shadow Trades", summary.get("v2_shadow_trades")),
        ("Avg R Delta", summary.get("avg_r_delta")),
        ("Premature Exits", summary.get("premature_exits")),
        ("Avg Exit Confidence", summary.get("avg_exit_confidence")),
    ]
    for column, (label, value) in zip(columns, metrics):
        with column:
            st.metric(label, value if value is not None else "-")

    performance = summary.get("performance") or {}
    st.subheader("Performance")
    st.dataframe({"Metric": ["Completed Trades", "Win Rate", "Average R", "Profit Factor", "Max Drawdown R"], "Value": [performance.get("completed_trades"), performance.get("win_rate"), performance.get("average_r"), performance.get("profit_factor"), performance.get("max_drawdown_r")]}, width="stretch", hide_index=True)

    st.subheader("Premature Exit Research")
    st.dataframe({
        "Metric": ["Wait one bar improved", "Wait two bars improved", "Avg +1 bar move", "Avg +2 bars move"],
        "Value": [summary.get("wait_one_bar_improved"), summary.get("wait_two_bars_improved"), summary.get("avg_wait_one_bar_move"), summary.get("avg_wait_two_bars_move")],
    }, width="stretch", hide_index=True)

    st.subheader("Blocking Stages")
    st.dataframe(
        [{"Stage": stage, "Count": count} for stage, count in (summary.get("blocking_stages") or {}).items()],
        width="stretch",
        hide_index=True,
    )
    for title, key in [("Market Distribution", "market_distribution"), ("Lifecycle Distribution", "lifecycle_distribution")]:
        values = summary.get(key) or {}
        if values:
            st.markdown(f"### {title}")
            st.dataframe([{"State": state, "Count": count} for state, count in values.items()], width="stretch", hide_index=True)
    feedback = summary.get("feedback_loop") or {}
    st.subheader("Feedback Loop")
    st.metric("Refresh Success Rate (Last 50)", feedback.get("refresh_success_rate_last_50") if feedback.get("refresh_success_rate_last_50") is not None else "-")
    for title, key in [("TQS Outcome Calibration", "tqs_calibration"), ("Rule ROI", "rule_roi"), ("Feature Promotion Tracker", "feature_promotion")]:
        rows = feedback.get(key) or []
        if rows:
            st.markdown(f"### {title}")
            st.dataframe(rows, width="stretch", hide_index=True)
    promotion = feedback.get("feature_promotion") or []
    if promotion:
        st.caption("Promotion candidates require human controlled-validation review; this page cannot alter V1.")
    aggregates = summary.get("aggregate_statistics") or []
    for title, kind in [("Setup Performance", "setup"), ("Market Performance", "market"), ("Lifecycle Performance", "lifecycle")]:
        rows = [row for row in aggregates if row.get("summary_type") == kind]
        if rows:
            st.markdown(f"### {title}")
            st.dataframe(rows, width="stretch", hide_index=True)