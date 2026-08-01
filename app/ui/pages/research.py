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

TABS = ("Replay", "Regression", "Reports", "Learning")


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


def _render_reports(_refresh_state, load_frame):
    import streamlit as st

    import pandas as pd

    from app.ui.pages.reports import render

    if _cached("report_state.json", profile="reports"):
        render(pd.DataFrame())
        st.caption("Rendered from report_state.json.")
        return

    frame = load_frame()
    if frame is None:
        return
    render(frame)


def _render_learning(_refresh_state, _load_frame):
    from app.ui.pages.learning import render

    render()


RENDERERS = {
    "Replay": _render_replay,
    "Regression": _render_regression,
    "Reports": _render_reports,
    "Learning": _render_learning,
}


def render(refresh_state=None, load_frame=lambda: None):
    import streamlit as st

    for tab, name in zip(st.tabs(list(TABS)), TABS):
        with tab:
            RENDERERS[name](refresh_state, load_frame)
