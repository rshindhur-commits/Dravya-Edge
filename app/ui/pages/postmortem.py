"""One day, on one page, for a review or an incident.

Renders `app.analytics.day_postmortem`. The one rule this page keeps everywhere:
a section that could not be read says so. It never renders an unavailable read as
a zero, because being unable to tell those apart is the fault that made this page
necessary -- a container that could not reach Postgres reported "No positions
were closed this week" over a week with seven, and nothing on screen disagreed.
"""

from __future__ import annotations


UNAVAILABLE = "Unavailable — the database could not be read. This is not the same as nothing happening."


def _table(container, rows, columns=None):
    import pandas as pd

    frame = pd.DataFrame(rows)

    if columns:
        frame = frame[[column for column in columns if column in frame.columns]]

    container.dataframe(frame, width="stretch", hide_index=True)


def _section(title, rows, empty_message, render):
    """Three outcomes, three renderings: unavailable, empty, and data."""

    import streamlit as st

    st.markdown(f"#### {title}")

    if rows is None:
        st.warning(UNAVAILABLE)
        return

    if not rows:
        st.caption(empty_message)
        return

    render(st, rows)


def _clock(moment):
    return moment.strftime("%H:%M:%S") if hasattr(moment, "strftime") else "—"


def render(trading_day=None):
    import streamlit as st

    from app.analytics.day_postmortem import (
        alert_delivery,
        build_day_postmortem,
        previous_trading_days,
        trade_outcome,
    )

    days = [day.isoformat() for day in previous_trading_days(15)]

    if trading_day and trading_day not in days:
        days.insert(0, str(trading_day))

    chosen = st.selectbox("Trading day", days, index=0, key="postmortem_day")
    report = build_day_postmortem(chosen)

    coverage = report["coverage"]

    st.markdown("#### Scan coverage")

    if coverage is None:
        st.warning(UNAVAILABLE)

    else:
        columns = st.columns(5)
        columns[0].metric("Scans", coverage["scans"])
        columns[1].metric("Failed", coverage["failures"])
        # A scan that started and never wrote a completion. A deploy during the
        # session produces one every time, so a high count here is usually a
        # restart story rather than a scanner fault.
        columns[2].metric("Incomplete", coverage["incomplete"])
        columns[3].metric("Median", f"{coverage['median_seconds'] or 0:.0f}s")
        columns[4].metric("Slowest", f"{coverage['slowest_seconds'] or 0:.0f}s")
        st.caption(
            f"First {_clock(coverage['first_at'])} · last {_clock(coverage['last_at'])} ET"
        )

    def _gaps(container, rows):
        container.caption(
            "A gap is not proof the scanner was down. A scanner that ran and "
            "could not write leaves exactly the same hole — on 2026-08-01 that "
            "was the difference between a quiet Saturday and a blind container."
        )
        _table(container, [
            {
                "From (ET)": _clock(row["from_at"]),
                "To (ET)": _clock(row["to_at"]),
                "Minutes": row["minutes"],
            }
            for row in rows
        ])

    _section("Coverage gaps", report["gaps"],
             "No gap longer than 20 minutes.", _gaps)

    delivery = alert_delivery(report["alerts"])

    st.markdown("#### Alerts delivered")

    if delivery is None:
        st.warning(UNAVAILABLE)

    else:
        columns = st.columns(4)
        columns[0].metric("Dispatches", delivery["dispatches"])
        columns[1].metric("Delivered", delivery["delivered"])
        columns[2].metric("Failed", delivery["failed"])
        # Handed to the sender and never confirmed. It is not a failure and not a
        # success, and counting it as either is how a stuck queue reads as
        # healthy.
        columns[3].metric("Undelivered", delivery["undelivered"])

        if report["alerts"]:
            _table(st, report["alerts"],
                   ["message_type", "status", "dispatches", "delivered"])

    outcome = trade_outcome(report["trades"])

    st.markdown("#### Trades")

    if outcome is None:
        st.warning(UNAVAILABLE)

    else:
        columns = st.columns(4)
        columns[0].metric("Closed", outcome["closed"])
        columns[1].metric("Wins", outcome["wins"])
        columns[2].metric("Losses", outcome["losses"])
        columns[3].metric("Total R", f"{outcome['total_r']:+.2f}")

    _section(
        "Positions opened or closed", report["trades"],
        "No position was opened or closed on this day.",
        lambda container, rows: _table(container, rows, [
            "symbol", "direction", "status", "entry_source", "holding_profile",
            "entry_price", "close_price", "pnl_pct", "r_multiple",
        ]),
    )

    _section(
        "Why entries were not taken", report["entry_decisions"],
        "No entry decisions were recorded.",
        lambda container, rows: _table(container, rows,
                                       ["decision", "blocked_by", "occurrences", "symbols"]),
    )

    _section(
        "Rules that blocked candidates", report["blocking_rules"],
        "No blocking rule fired.",
        lambda container, rows: _table(container, rows,
                                       ["stage", "rule_name", "blocks", "symbols"]),
    )

    def _suppressions(container, rows):
        container.caption(
            "What was considered and not sent. A day with no alerts is either a "
            "quiet market or a suppression rule doing more than intended, and "
            "only this table separates the two."
        )
        _table(container, rows, ["alert_type", "status", "reason", "occurrences"])

    _section("Alerts considered and suppressed", report["alert_suppressions"],
             "No alert decisions were recorded.", _suppressions)
