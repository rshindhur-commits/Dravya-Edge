def render(df, auto_paper_controls, refresh_state=None):

    from app.dashboard import (
        _current_trading_day,
        _render_lazy_developer_section,
        _render_market_coverage_lazy,
        _render_metadata_card,
        _render_runtime_performance_panel,
        _render_suggestion_lifecycle,
        _scan_metadata,
        _status_label,
    )
    from app.ui.pages.data_health import render as _render_data_health
    from app.ui.pages.entry_diagnostics import render as _render_entry_diagnostics

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

    # Six sections were removed here on 2026-08-02, all of them reading files
    # this container does not write now that the Render worker runs the scan --
    # so each rendered blank during the session, and before the state files were
    # untracked, rendered a developer machine's July state instead.
    #
    #   Regression Snapshot          plumbing telemetry; no decision hung on it
    #   Scanner Watchlist            the Trading page already shows the scan
    #   Auto-Paper Summary           a 30-minute window on `auto_paper_decision`
    #   Full Auto-Paper Decision Log the same log again, capped at 500 rows
    #   Telemetry & Debug            win rate and avg R, from worse data than
    #                                Validation and Postmortem already use
    #
    # The decision data behind the two auto-paper sections is in Postgres and is
    # surfaced properly by the Postmortem tab, unbounded and durable.
    with st.expander("Developer Diagnostics", expanded=True):

        _render_lazy_developer_section(
            "Database Tables",
            "database_catalog",
            render_database_catalog,
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
            "Suggestion Lifecycle",
            "suggestion_lifecycle",
            lambda: _render_suggestion_lifecycle(df)
        )
        # Both read Postgres now. The frame-backed versions said "No scanner rows
        # available" through every session on this host, and the data-health one
        # was worse: it compared three files this container never writes, so it
        # reported agreement between nothing and nothing.
        _render_lazy_developer_section(
            "Entry Diagnostics",
            "entry_diagnostics",
            _render_entry_diagnostics,
        )
        _render_lazy_developer_section(
            "Validation Data Health",
            "validation_data_health",
            _render_data_health,
        )