"""Why nothing is firing, right now.

The one page here meant to be read during the session rather than after it. It
leads with a sentence rather than a table, because the question it answers is
binary in practice -- is this the market or is this us -- and a grid of stage
counts leaves the operator to derive that themselves.

Same rule as the rest of the Postgres-backed pages: unavailable, empty and data
are three different renderings. A funnel that shows a database outage as "no
candidates" would send you looking at your thresholds during an incident.
"""

from __future__ import annotations


UNAVAILABLE = "Unavailable — the database could not be read. This is not the same as nothing happening."

WINDOWS = {"Last 30 minutes": 30, "Last hour": 60, "Last 3 hours": 180, "Today so far": 900}


def _table(container, rows, columns):
    import pandas as pd

    frame = pd.DataFrame(rows)
    container.dataframe(
        frame[[column for column in columns if column in frame.columns]],
        width="stretch",
        hide_index=True,
    )


def _section(title, rows, empty_message, columns, caption=None):
    import streamlit as st

    st.markdown(f"#### {title}")

    if caption:
        st.caption(caption)

    if rows is None:
        st.warning(UNAVAILABLE)
        return

    if not rows:
        st.caption(empty_message)
        return

    _table(st, rows, columns)


def render():
    import streamlit as st

    from app.analytics.live_funnel import build_live_funnel, narrative

    label = st.selectbox("Window", list(WINDOWS), index=1, key="live_funnel_window")
    report = build_live_funnel(WINDOWS[label])

    freshness = report.get("freshness")

    if freshness is None:
        st.warning(UNAVAILABLE)

    elif freshness:
        age = float(freshness.get("age_minutes") or 0)
        note = f"Last scan {age:.0f} min ago · {freshness.get('run_id')}"
        (st.warning if age > 20 else st.caption)(note)

    # The answer, before the evidence for it.
    st.info(narrative(report))

    stages = report["stages"]

    st.markdown("#### Stage funnel")

    if stages is None:
        st.warning(UNAVAILABLE)

    elif not stages:
        st.caption("No candidate was evaluated in this window.")

    else:
        st.caption(
            "Passed is symbols seen minus symbols blocked. A symbol passes a "
            "stage when nothing in that stage blocked it, not when some rule "
            "in it happened to pass."
        )
        _table(st, stages, ["stage", "seen", "passed", "blocked", "pass_rate", "blocks"])

    _section(
        "Rules doing the blocking", report["blocking_rules"],
        "No rule blocked anything in this window.",
        ["stage", "rule_name", "blocks", "symbols", "required_value", "worst_actual"],
        caption="Worst first. `required_value` against `worst_actual` says whether "
                "a threshold is close or nowhere near.",
    )

    _section(
        "Near misses — blocked by exactly one rule", report["near_misses"],
        "Nothing was blocked by a single rule; candidates are failing several at once.",
        ["symbol", "stage", "rule_name"],
        caption="The actionable rows. A name failing one gate is a threshold "
                "question; one failing six is simply not a setup.",
    )

    _section(
        "Entry decisions after the scanner passed them", report["entry_decisions"],
        "The entry layer made no decisions in this window.",
        ["decision", "blocked_by", "occurrences", "symbols"],
        caption="A candidate can clear every scanner gate and still not be taken — "
                "not the top candidate, or the daily cap already spent. These are "
                "the reasons most often misread as 'the scanner found nothing'.",
    )
