def render(df, auto_paper_controls, refresh_state=None):

    from app.dashboard import (
        _current_trading_day,
        _render_auto_paper_decision_log,
        _render_compact_auto_paper_summary,
        _render_entry_diagnostics,
        _render_lazy_developer_section,
        _render_market_coverage_lazy,
        _render_metadata_card,
        _render_runtime_performance_panel,
        _render_scanner_watchlist,
        _render_suggestion_lifecycle,
        _render_telemetry_debug_panel,
        _render_validation_data_health,
        _scan_metadata,
        _status_label,
    )

    metadata = _scan_metadata(df, refresh_state=refresh_state)
    _render_metadata_card(
        "Developer Diagnostics",
        [
            ("Diagnostics Status", "READY OK" if df is not None and not df.empty else "MISSING"),
            ("Current Scan ID", metadata["scan_id"]),
            ("Diagnostics Built", metadata["last_refreshed"]),
            ("Based On Scan", metadata["scanner_finished"]),
            ("Symbols", metadata["symbols"]),
            ("Data Version", metadata["scan_id"]),
            ("Status", _status_label(metadata["status"])),
            ("Refresh Window", f"{metadata['refresh_minutes']} min"),
        ]
    )

    import streamlit as st

    def render_database_catalog():
        """What each table is for, with what is actually in it right now.

        Several names read alike -- `trade`, `paper_trades` and
        `trade_exit_analysis` are three different things -- and grain is the
        detail that most often surprises, so both are stated per table.
        """
        import pandas as pd

        from app.db.catalog import catalog, row_counts, undocumented

        counts = row_counts()
        if not counts:
            st.caption("Row counts unavailable (database not reachable).")

        for group, tables in catalog():
            st.markdown(f"**{group}**")
            st.dataframe(
                pd.DataFrame([{
                    "Table": entry["table"],
                    "Rows": f"{counts[entry['table']]:,}" if entry["table"] in counts else "-",
                    "One row is": entry["grain"],
                    "Purpose": entry["purpose"],
                } for entry in tables]),
                width="stretch",
                hide_index=True,
            )

        stray = undocumented(counts)
        if stray:
            st.warning(
                "In the database but not described in app/db/catalog.py: "
                + ", ".join(stray)
            )

    def render_regression_snapshot_metrics():
        import os

        from app.storage.daily_paths import live_path
        from app.utils.json_store import load_json_file

        metrics = load_json_file(str(live_path("regression_snapshot_metrics.json")), {})
        enabled = str(os.getenv("REGRESSION_SNAPSHOT_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}
        columns = st.columns(3)
        columns[0].metric("Enabled", "Yes" if enabled else "No")
        columns[1].metric("Queued", metrics.get("queued", 0))
        columns[2].metric("Completed", metrics.get("completed", 0))
        columns = st.columns(3)
        columns[0].metric("Dropped", metrics.get("dropped", 0))
        columns[1].metric("Failures", metrics.get("failures", 0))
        columns[2].metric("Average Persist", f"{metrics.get('average_persist_ms', 0):.0f} ms")

    with st.expander("Developer Diagnostics", expanded=True):

        _render_lazy_developer_section(
            "Database Tables",
            "database_catalog",
            render_database_catalog,
        )

        _render_lazy_developer_section(
            "Regression Snapshot",
            "regression_snapshot",
            render_regression_snapshot_metrics,
        )

        _render_lazy_developer_section(
            "Runtime Performance",
            "runtime_performance",
            _render_runtime_performance_panel
        )
        _render_lazy_developer_section(
            "Market Coverage",
            "market_coverage",
            lambda: _render_market_coverage_lazy(_current_trading_day())
        )
        # Action Center and Paper Exit Controls were here. Both acted on the
        # paper book from the dashboard, which was safe while this process was
        # also the scanner. It no longer is: the Render worker owns entries and
        # exits, and closing a position from here races its exit logic across two
        # hosts with no shared lock -- `scan_lock` is a local file and cannot
        # serialise anything between containers. Same failure shape as two scan
        # engines running at once, and the book is what it corrupts.
        _render_lazy_developer_section(
            "Scanner Watchlist",
            "scanner_watchlist",
            lambda: _render_scanner_watchlist(df)
        )
        _render_lazy_developer_section(
            "Auto-Paper Summary",
            "auto_paper_summary",
            _render_compact_auto_paper_summary
        )
        _render_lazy_developer_section(
            "Suggestion Lifecycle",
            "suggestion_lifecycle",
            lambda: _render_suggestion_lifecycle(df)
        )
        _render_lazy_developer_section(
            "Entry Diagnostics",
            "entry_diagnostics",
            lambda: _render_entry_diagnostics(df)
        )
        _render_lazy_developer_section(
            "Full Auto-Paper Decision Log",
            "auto_paper_decision_log",
            lambda: _render_auto_paper_decision_log(show_full_expander=False)
        )
        _render_lazy_developer_section(
            "Validation Data Health",
            "validation_data_health",
            lambda: _render_validation_data_health(df)
        )
        _render_lazy_developer_section(
            "Telemetry & Debug",
            "telemetry_debug",
            lambda: _render_telemetry_debug_panel(df)
        )