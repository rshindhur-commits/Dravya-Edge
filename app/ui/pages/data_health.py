"""Does the decision record agree with the book?

Replaces the file-backed `Validation Data Health`, which compared the local
decision log, `paper_trade_state.json` and the suggestion state. On a dashboard
that writes none of those it was comparing nothing to nothing and reporting
agreement -- a reconciliation that cannot fail is not a reconciliation.

`auto_paper_decision` and `paper_trades` are written by different code on
different paths, so a disagreement between them is real information: one of them
is wrong about a position that either does or does not exist.
"""

from __future__ import annotations


UNAVAILABLE = "Unavailable — the database could not be read, so nothing was compared."


def render(trading_day=None):
    import pandas as pd
    import streamlit as st

    from app.analytics.day_postmortem import build_reconciliation, previous_trading_days

    st.subheader("Validation Data Health")

    days = [day.isoformat() for day in previous_trading_days(10)]

    if trading_day and trading_day not in days:
        days.insert(0, str(trading_day))

    chosen = st.selectbox("Trading day", days, index=0, key="data_health_day")
    report = build_reconciliation(chosen)

    if report is None:
        st.warning(UNAVAILABLE)
        return

    columns = st.columns(4)
    columns[0].metric("Entries decided", report["intended"])
    columns[1].metric("Positions recorded", report["recorded"])
    columns[2].metric("Decided, not opened", len(report["missing_positions"]))
    columns[3].metric("Opened, not decided", len(report["unexplained_positions"]))

    if not report["missing_positions"] and not report["unexplained_positions"]:
        st.success("The decision record and the book agree for this day.")
        return

    if report["missing_positions"]:
        st.error(
            "Decided to enter, but no position carries that key. Either the open "
            "failed after the decision was logged, or it opened under a different "
            "key — the second means the daily cap counted it twice."
        )
        st.dataframe(pd.DataFrame(report["missing_positions"]),
                     width="stretch", hide_index=True)

    if report["unexplained_positions"]:
        st.warning(
            "A position with no decision behind it. A manual entry belongs here; "
            "an AUTO_PAPER one does not, and means a trade was opened on a path "
            "that never recorded why."
        )
        st.dataframe(pd.DataFrame(report["unexplained_positions"]),
                     width="stretch", hide_index=True)
