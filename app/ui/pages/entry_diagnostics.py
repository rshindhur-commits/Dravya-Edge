"""Why did *this* symbol not fire.

Replaces the frame-backed panel of the same name. That one read
`scanner_output_latest.xlsx`, written by the process that ran the scan, so on the
dashboard it said "No scanner rows available for entry diagnostics" through every
session -- and before the frame was untracked, described a July scan from a
developer machine.

`decision_waterfall` holds the same evaluation, per symbol per rule per scan, and
keeps it. The Live Funnel tab answers which rules block the most; this answers
the question you ask after seeing a setup you expected to be taken.
"""

from __future__ import annotations


UNAVAILABLE = "Unavailable — the database could not be read. This is not the same as nothing being evaluated."

WINDOW_MINUTES = 180


def render():
    import pandas as pd
    import streamlit as st

    from app.db.decision_funnel_repository import DecisionFunnelRepository

    st.subheader("Entry Diagnostics")

    repository = DecisionFunnelRepository()
    symbols = repository.evaluated_symbols(WINDOW_MINUTES)

    if symbols is None:
        st.warning(UNAVAILABLE)
        return

    if not symbols:
        st.caption(
            f"No symbol was evaluated in the last {WINDOW_MINUTES} minutes. "
            "Check the Live Funnel tab for whether anything is scanning."
        )
        return

    symbol = st.selectbox("Symbol", symbols, key="entry_diagnostics_symbol")
    rows = repository.symbol_waterfall(symbol, WINDOW_MINUTES)

    if rows is None:
        st.warning(UNAVAILABLE)
        return

    if not rows:
        st.caption(f"No evaluation recorded for {symbol} in this window.")
        return

    # Blocking first. The rules that stopped it are the answer; the ones it
    # passed are the context, and burying the former under the latter is what
    # makes a waterfall table hard to read.
    blocking = [row for row in rows if row.get("blocking")]

    st.markdown("**What stopped it**")

    if blocking:
        _table(st, pd.DataFrame(blocking),
               ["stage", "rule_name", "actual_value", "required_value", "summary"])
    else:
        st.caption(
            f"{symbol} was not blocked by any rule in this window — if it still "
            "did not trade, the entry layer declined it. See Live Funnel."
        )

    with st.expander(f"Every rule evaluated for {symbol}", expanded=False):
        _table(st, pd.DataFrame(rows),
               ["stage", "rule_name", "passed", "blocking",
                "actual_value", "required_value", "timestamp"])


def _table(container, frame, columns):
    container.dataframe(
        frame[[column for column in columns if column in frame.columns]],
        width="stretch",
        hide_index=True,
    )
