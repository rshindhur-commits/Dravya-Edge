def render(df, auto_paper_controls, refresh_state=None):

    from app.dashboard import (
        _current_trading_day,
        _render_action_center,
        _render_auto_paper_decision_log,
        _render_compact_auto_paper_summary,
        _render_entry_diagnostics,
        _render_lazy_developer_section,
        _render_market_coverage_lazy,
        _render_metadata_card,
        _render_paper_exit_controls,
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

    with st.expander("Developer Diagnostics", expanded=True):

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
        _render_lazy_developer_section(
            "Action Center",
            "action_center",
            lambda: _render_action_center(df, auto_paper_controls)
        )
        _render_lazy_developer_section(
            "Scanner Watchlist",
            "scanner_watchlist",
            lambda: _render_scanner_watchlist(df)
        )
        _render_lazy_developer_section(
            "Paper Exit Controls",
            "paper_exit_controls",
            lambda: _render_paper_exit_controls(df)
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