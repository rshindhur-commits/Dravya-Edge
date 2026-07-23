from __future__ import annotations

import json

from app.storage.daily_paths import live_path


def render(df=None):

    import streamlit as st

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
    feedback = summary.get("feedback_loop") or {}
    st.subheader("Feedback Loop")
    st.metric("Refresh Success Rate (Last 50)", feedback.get("refresh_success_rate_last_50") if feedback.get("refresh_success_rate_last_50") is not None else "-")
    for title, key in [("TQS Outcome Calibration", "tqs_calibration"), ("Rule ROI", "rule_roi"), ("Feature Promotion Tracker", "feature_promotion")]:
        rows = feedback.get(key) or []
        if rows:
            st.markdown(f"### {title}")
            st.dataframe(rows, width="stretch", hide_index=True)