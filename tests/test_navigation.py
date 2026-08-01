"""Replay, Regression, Reports and Learning became tabs on one Research page,
taking the navigation from seven entries to four."""

from app.ui.pages import research


def test_research_carries_every_page_it_absorbed():
    assert set(research.TABS) == {"Replay", "Regression", "Reports", "Learning"}
    assert set(research.RENDERERS) == set(research.TABS)


def test_a_session_left_on_an_absorbed_page_lands_on_research():
    """Streamlit raises when a radio's stored session value is not an option, so
    a tab open across the redeploy would break on its next rerun."""
    from app.dashboard import _migrate_dashboard_page

    for folded in ("Replay", "Regression", "Reports", "Learning"):
        assert _migrate_dashboard_page(folded) == "Research"


def test_a_session_on_a_surviving_page_is_left_alone():
    from app.dashboard import _migrate_dashboard_page

    for kept in ("Trading", "Validation", "Research", "Developer"):
        assert _migrate_dashboard_page(kept) == kept


def test_a_new_session_and_an_unrecognised_value_both_start_on_trading():
    from app.dashboard import _migrate_dashboard_page

    assert _migrate_dashboard_page(None) == "Trading"
    assert _migrate_dashboard_page("Nonsense") == "Trading"


def test_regression_and_learning_never_ask_for_the_scanner_frame():
    """Opening Research must not cost a read of scanner_output.xlsx unless a tab
    that needs it is the one being rendered."""
    asked = []

    research._render_regression(None, lambda: asked.append("frame"))
    research._render_learning(None, lambda: asked.append("frame"))

    assert asked == []
