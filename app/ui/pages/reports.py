def render(df):

    from app.dashboard import (
        _load_cached_state,
        _render_cached_report_state,
        _render_daily_validation_report_panel,
    )

    cached = _load_cached_state("report_state.json", profile="reports")

    if cached:

        _render_cached_report_state(cached)
        return

    _render_daily_validation_report_panel()