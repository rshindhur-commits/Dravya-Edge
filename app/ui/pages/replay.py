def render(df=None, refresh_state=None):

    import pandas as pd
    import streamlit as st
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.dashboard import (
        _current_trading_day,
        _display_safe_dataframe,
        _file_row_count,
        _generate_offline_replay,
        _load_cached_state,
        _render_cached_replay_state,
        _render_compact_card_grid,
        _render_file_download_button,
        _render_metadata_card,
        _scan_metadata,
        _status_label,
    )
    from app.storage.daily_paths import daily_path

    trading_day = _current_trading_day()
    cached = _load_cached_state("replay_state.json", profile="replay")

    if cached:

        _render_cached_replay_state(cached, trading_day)
        return

    st.subheader("Replay")
    input_path = daily_path(trading_day, "scanner_output_close.csv")
    output_path = daily_path(trading_day, "offline_replay.csv")
    summary_path = output_path.with_name("offline_replay_summary.csv")
    metadata = _scan_metadata(df, refresh_state=refresh_state)
    replay_generated = (
        datetime.fromtimestamp(summary_path.stat().st_mtime, ZoneInfo("America/New_York")).strftime("%m/%d/%Y %H:%M:%S ET")
        if summary_path.exists()
        else "Not generated"
    )
    scanner_rows_for_metadata = _file_row_count(input_path, pd.read_csv)
    replay_rows_for_metadata = _file_row_count(output_path, pd.read_csv)
    coverage_for_metadata = (
        f"{replay_rows_for_metadata} / {scanner_rows_for_metadata} ({round((replay_rows_for_metadata / scanner_rows_for_metadata) * 100, 1)}%)"
        if scanner_rows_for_metadata
        else "pending"
    )

    _render_metadata_card(
        "Replay Session",
        [
            ("Replay Status", "READY OK" if summary_path.exists() else "MISSING"),
            ("Replay Scan ID", metadata["scan_id"]),
            ("Replay Generated", replay_generated),
            ("Based On Scan", metadata["scanner_finished"]),
            ("Replay Coverage", coverage_for_metadata),
            ("Replay Version", "v1"),
            ("Data Version", metadata["scan_id"]),
            ("Status", _status_label(metadata["status"])),
        ]
    )
    st.caption(f"Input: {input_path}")

    if st.button("Generate Replay", key="generate_offline_replay"):

        try:

            _generate_offline_replay(trading_day)
            st.success("Offline replay generated.")

        except Exception as exc:

            st.error(f"Offline replay failed: {exc}")

    st.markdown("**Today's Replay Analysis**")
    replay_df = pd.DataFrame()
    summary_df = pd.DataFrame()

    if output_path.exists() and output_path.stat().st_size > 0:

        try:

            replay_df = pd.read_csv(output_path)

        except Exception:

            replay_df = pd.DataFrame()

    if summary_path.exists() and summary_path.stat().st_size > 0:

        try:

            summary_df = pd.read_csv(summary_path)

        except Exception:

            summary_df = pd.DataFrame()

    if replay_df.empty and summary_df.empty:

        st.info("Generate replay after a scanner run to see coverage, blockers, and ticker-level replay results.")

    else:

        scanner_rows = _file_row_count(input_path, pd.read_csv)
        replay_rows = len(replay_df) if not replay_df.empty else len(summary_df)
        missing_indicators = 0

        if not replay_df.empty and "FAILED_ENTRY_CONDITIONS" in replay_df.columns:

            missing_indicators = int(
                replay_df["FAILED_ENTRY_CONDITIONS"]
                .astype(str)
                .str.contains("Missing replay indicators", na=False)
                .sum()
            )

        coverage_pct = round((replay_rows / scanner_rows) * 100, 2) if scanner_rows else 0
        cards = [
            ("Symbols Replayed", replay_rows),
            ("Coverage", f"{coverage_pct}%"),
            ("Missing Indicators", missing_indicators),
            ("Partial Replay", missing_indicators),
        ]
        _render_compact_card_grid(cards)

        blocker_source = summary_df if not summary_df.empty else replay_df

        if "Gate Failure Stage" in blocker_source.columns:

            blockers = (
                blocker_source["Gate Failure Stage"]
                .fillna("Unknown")
                .astype(str)
                .value_counts(normalize=True)
                .mul(100)
                .round(1)
                .reset_index()
            )
            blockers.columns = ["Blocker", "Share %"]
            st.markdown("**Today's Biggest Blockers**")
            st.dataframe(
                _display_safe_dataframe(blockers),
                width="stretch",
                hide_index=True
            )

        if not summary_df.empty:

            st.markdown("**Replay Summary**")
            preferred_columns = [
                "Symbol",
                "Closest Setup",
                "Readiness",
                "First Failed Rule",
                "Recommendation",
                "Trade Block Details",
                "Final Decision",
                "Gate Failure Stage",
            ]
            display_summary = summary_df[
                [column for column in preferred_columns if column in summary_df.columns]
            ].copy()
            st.dataframe(
                _display_safe_dataframe(display_summary),
                width="stretch",
                hide_index=True
            )

    _render_file_download_button(
        "Download offline_replay.csv",
        output_path,
        file_name="offline_replay.csv",
        mime="text/csv",
        key="download_offline_replay",
        container=st
    )
    _render_file_download_button(
        "Download offline_replay_summary.csv",
        summary_path,
        file_name="offline_replay_summary.csv",
        mime="text/csv",
        key="download_offline_replay_summary",
        container=st
    )