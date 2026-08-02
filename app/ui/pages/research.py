"""Research: everything that asks a question about archived days.

Replay, Regression, Reports and Learning were four separate entries in a
seven-item navigation radio, and all four answer the same shape of question --
what happened, and would different code have done better. Folding them into one
page with tabs leaves the navigation as Trading / Validation / Research /
Developer, which is the set of things an operator actually switches between.

Each tab keeps the exact two-tier behaviour it had as its own page: render from
the cached state file when one exists, otherwise fall back to the live scanner
frame. The frame is loaded lazily through `load_frame`, so opening the page for
the Regression tab does not pay to read `scanner_output.xlsx`.
"""

from __future__ import annotations

# "Reports" was here. It rendered `report_state.json` or the scanner frame, both
# written by the process that ran the scan, so on Render-owned scanning it was
# blank -- and what it showed before the state files were untracked was a
# developer machine's July snapshot. Everything in it is answered better by
# Postmortem (the day) and Validation (the performance), from Postgres.
TABS = ("Live Funnel", "Postmortem", "Replay", "Regression", "Learning")


def _cached(name, profile):
    from app.dashboard import _load_cached_state

    return _load_cached_state(name, profile=profile)


def _render_replay(refresh_state, load_frame):
    import streamlit as st

    from app.ui.pages.replay import render

    if _cached("replay_state.json", profile="replay"):
        render(df=None, refresh_state=refresh_state)
        st.caption("Rendered from replay_state.json.")
        return

    frame = load_frame()
    if frame is None:
        return
    render(df=frame, refresh_state=refresh_state)


def _render_regression(_refresh_state, _load_frame):
    from app.ui.pages.regression import render

    render()


def _render_learning(_refresh_state, _load_frame):
    from app.ui.pages.learning import render

    render()


def _render_postmortem(_refresh_state, _load_frame):
    """The completed day, reading nothing but Postgres.

    Replay still renders from a state file written by whichever process ran the
    scan, which is now the Render worker -- so on the dashboard it shows whatever
    the deploy shipped. This tab has no such dependency, which is why it is the
    one to open when something has gone wrong.
    """

    from app.ui.pages.postmortem import render

    render()


def _render_live_funnel(_refresh_state, _load_frame):
    """First tab, because it is the only one that answers a question you have
    while the market is open: why is nothing firing right now."""

    from app.ui.pages.live_funnel import render

    render()


RENDERERS = {
    "Live Funnel": _render_live_funnel,
    "Postmortem": _render_postmortem,
    "Replay": _render_replay,
    "Regression": _render_regression,
    "Learning": _render_learning,
}


def render(refresh_state=None, load_frame=lambda: None):
    import streamlit as st

    for tab, name in zip(st.tabs(list(TABS)), TABS):
        with tab:
            RENDERERS[name](refresh_state, load_frame)
