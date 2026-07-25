from __future__ import annotations

from datetime import date


def render():
    import pandas as pd
    import streamlit as st

    from app.db.scanner_snapshot_repository import (
        RegressionBaselineRepository,
        RegressionRunRepository,
        ScannerSnapshotRepository,
    )
    from app.regression import freeze_baseline, run_historical_regression

    st.subheader("Regression")
    selected_date = st.date_input("Trading Day", value=date.today(), key="regression_trading_day")
    trading_day = selected_date.isoformat()
    strategy_version = st.text_input("Strategy Version", value="Current", key="regression_strategy_version")

    archive = ScannerSnapshotRepository().archive_status(trading_day)
    baseline = RegressionBaselineRepository().load(trading_day)
    columns = st.columns(4)
    columns[0].metric("Archive Status", "Available" if archive["available"] else "Missing")
    columns[1].metric("Scans Archived", archive["scans"])
    columns[2].metric("Snapshot Rows", archive["rows"])
    columns[3].metric("Baseline", baseline.get("baseline_version") if baseline else "Missing")

    if not archive["available"]:
        st.info("No durable snapshots are available for this day. Enable REGRESSION_SNAPSHOT_ENABLED before a future scanner session.")
    elif not baseline:
        st.warning("Snapshots are available, but the day has not been finalized to freeze its baseline.")

    if baseline:
        frozen_at = baseline.get("frozen_at")
        st.caption(f"Baseline frozen: {frozen_at} | Version: {baseline.get('baseline_version')}")
    elif st.button("Freeze Baseline", disabled=not archive["available"]):
        with st.spinner("Freezing immutable regression baseline..."):
            try:
                baseline_path = freeze_baseline(trading_day)
                if not baseline_path:
                    st.error("Baseline could not be created because no completed archive or paper-trade evidence is available.")
                else:
                    st.success("Baseline frozen. It cannot be overwritten.")
                    st.rerun()
            except Exception as exc:
                st.error(f"Baseline freeze failed: {exc}")

    if st.button("Run Regression", type="primary", disabled=not (archive["available"] and baseline)):
        with st.spinner("Running read-only historical regression..."):
            try:
                st.session_state["regression_last_result"] = run_historical_regression(
                    trading_day,
                    current_strategy_version=strategy_version or "Current",
                )
            except Exception as exc:
                st.error(f"Regression failed: {exc}")

    summary = st.session_state.get("regression_last_result")
    if summary and summary.get("trading_day") == trading_day:
        st.markdown("### Regression Result")
        baseline_metrics = summary["baseline"]
        current_metrics = summary["current"]
        difference = summary["comparison"]
        columns = st.columns(3)
        columns[0].metric("Baseline Trades", baseline_metrics["trades"])
        columns[1].metric("Current Trades", current_metrics["trades"], current_metrics["trades"] - baseline_metrics["trades"])
        columns[2].metric("Net R", f"{summary['net_gain_r']:+.2f}R")
        columns = st.columns(3)
        columns[0].metric("Baseline Win Rate", f"{baseline_metrics['win_rate']:.1f}%")
        columns[1].metric("Current Win Rate", f"{current_metrics['win_rate']:.1f}%", f"{current_metrics['win_rate'] - baseline_metrics['win_rate']:+.1f}%")
        columns[2].metric("Changed Trades", len(difference["changed"]))
        st.success(summary["verdict"])

    history = RegressionRunRepository().list_recent()
    if history:
        rows = []
        for run in history:
            summary = run.get("summary") or {}
            rows.append({
                "Run": str(run.get("run_id") or "")[:8],
                "Strategy": run.get("strategy_version"),
                "Date Tested": str(run.get("trading_day")),
                "Verdict": summary.get("verdict", run.get("status")),
                "Net R": summary.get("net_gain_r"),
                "Completed": run.get("completed_at"),
            })
        st.markdown("### Regression History")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)