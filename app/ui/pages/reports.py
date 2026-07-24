def render(df):

    from app.dashboard import (
        _load_cached_state,
        _render_cached_report_state,
        _render_daily_validation_report_panel,
    )

    try:
        from app.db.learning_engine_repository import LearningEngineRepository
        learning_summary = LearningEngineRepository().get_daily_summary()
    except Exception:
        learning_summary = None

    cached = _load_cached_state("report_state.json", profile="reports")

    if cached:

        _render_cached_report_state(cached)
        if learning_summary:
            import streamlit as st
            st.caption("Historical Learning memory source: Neon PostgreSQL")
        return

    _render_daily_validation_report_panel()