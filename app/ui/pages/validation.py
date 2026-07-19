def render(df):

    import streamlit as st
    from app.dashboard import (
        _load_cached_state,
        _render_cached_validation_state,
        _render_daily_validation_report_panel,
        _render_paper_validation_performance,
        _render_trade_efficiency_card,
    )

    cached = _load_cached_state("validation_state.json", profile="validation")

    if cached:

        _render_cached_validation_state(cached)
        return

    st.subheader("Validation")
    _render_paper_validation_performance()
    _render_trade_efficiency_card()
    _render_daily_validation_report_panel()