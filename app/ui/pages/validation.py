"""Validation: did the trades we took do what we expected.

Rebuilt on Postgres. Every panel here previously read either
`validation_state.json` or a CSV under `data/daily/`, both written by the process
that runs the scan -- now the Render worker, in its own container. The page was
blank during the session, and before those files were untracked it rendered a
developer machine's July state as if it were current.

One read feeds all three panels. They were three separate file reads of the same
underlying trades, which is also how they came to disagree.
"""

from __future__ import annotations


UNAVAILABLE = "Unavailable — the database could not be read. This is not the same as no trades."

WINDOW_DAYS = 30


def render(df):

    import streamlit as st

    from app.analytics.performance_statistics import (
        build_performance_statistics,
        build_spread_calibration,
        closed_trades_from_db,
        trade_efficiency_summary,
    )
    from app.dashboard import _render_spread_calibration

    st.subheader("Validation")
    st.caption(f"Closed trades over the trailing {WINDOW_DAYS} days, from Postgres.")

    trades = closed_trades_from_db(WINDOW_DAYS)

    if trades is None:
        st.warning(UNAVAILABLE)
        return

    _render_performance(st, build_performance_statistics(trades), len(trades))
    _render_efficiency(st, trade_efficiency_summary(trades))

    # Zero measurable trades is the expected state until positions both open and
    # close after eb56f75 froze the entry ask, so the panel states that rather
    # than rendering empty -- an empty panel reads as "nothing wrong" when it
    # means "nothing measured yet".
    _render_spread_calibration(build_spread_calibration(trades))


def _render_performance(st, stats, total):

    stats = stats or {}
    completed = int(stats.get("completed_trades") or 0)

    st.markdown("#### Performance")

    if not completed:
        st.caption(
            f"No strategy trade has closed in this window. "
            f"{total} closed row(s) were read, none of them in scope — "
            "index-validation entries are excluded from strategy statistics."
        )
        return

    columns = st.columns(5)
    columns[0].metric("Closed", completed)
    columns[1].metric("Wins", stats.get("wins", 0))
    columns[2].metric("Win rate", _pct(stats.get("win_rate")))
    columns[3].metric("Total R", _num(stats.get("total_r"), "+.2f"))
    columns[4].metric("Avg R", _num(stats.get("average_r"), "+.2f"))

    # R is measured on the underlying while the position held is an option, and
    # the round trip is routinely wider than the move the stop allows. Showing
    # only R publishes the flattering half, so the premium figures sit alongside
    # it -- scoped to `priced_trades`, since a trade with no option marks cannot
    # contribute and averaging it in as zero would flatter the result.
    priced = int(stats.get("priced_trades") or 0)

    if priced:
        columns = st.columns(4)
        columns[0].metric("Priced trades", priced)
        columns[1].metric("Net win rate", _pct(stats.get("net_win_rate")))
        columns[2].metric("Avg premium P&L", _num(stats.get("average_option_pnl_pct"), "+.1f") + "%")
        columns[3].metric("Avg spread cost", _num(stats.get("average_spread_cost_pct"), ".1f") + "%")
        st.caption(
            "Premium figures are ask-to-bid, so they include the round-trip cost "
            "that R does not. Net win rate is the one to trust."
        )


def _render_efficiency(st, efficiency):

    st.markdown("#### Trade efficiency")

    if efficiency is None:
        st.warning(UNAVAILABLE)
        return

    if not efficiency.get("trades"):
        st.caption("No strategy trade in this window to measure.")
        return

    columns = st.columns(3)
    columns[0].metric("Trend capture", _num(efficiency.get("trend_capture"), ".2f"))
    columns[1].metric("Left on table", _num(efficiency.get("left_on_table"), ".2f"))
    columns[2].metric("Avg MFE (R)", _num(efficiency.get("mfe_r"), "+.2f"))
    st.caption(
        "Trend capture is the share of the available move the exit kept. "
        "Left on table is what it gave back after the peak."
    )


def _pct(value):
    return "—" if value is None else f"{float(value):.0f}%"


def _num(value, spec):
    return "—" if value is None else format(float(value), spec)
