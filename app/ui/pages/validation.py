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

# 7 to see the effect of a change just made, 90 to get a sample worth trusting.
# 30 was hardcoded, which is the one width that answers neither question well: too
# long to isolate a lever, too short to be significant.
WINDOW_OPTIONS = [7, 30, 90]


def render(df):

    import streamlit as st

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.analytics.performance_statistics import (
        build_performance_statistics,
        build_spread_calibration,
        exit_reason_summary,
        trade_efficiency_summary,
    )
    from app.config.performance import VALIDATION_TRADE_CACHE_TTL
    from app.dashboard import _load_validation_trades, _render_spread_calibration

    st.subheader("Validation")

    # Per-trade audit first, aggregates after. The roll-ups below answer "how is
    # the book doing"; this answers "what happened on that one trade, and can I
    # check it" -- which is the question the aggregates cannot be interrogated
    # for, and the one that surfaced three separate recording faults on
    # 2026-08-21.
    with st.expander("Trade lifecycle — one trade, every gate", expanded=False):

        from app.ui.pages.trade_lifecycle import render as render_lifecycle

        render_lifecycle()

    days = st.radio(
        "Window",
        WINDOW_OPTIONS,
        index=WINDOW_OPTIONS.index(WINDOW_DAYS),
        format_func=lambda value: f"{value} days",
        horizontal=True,
        key="validation_window_days",
    ) or WINDOW_DAYS

    today = datetime.now(ZoneInfo("America/New_York")).date()

    st.caption(
        f"Closed trades over the trailing {days} days, from Postgres. "
        "Every panel below is built from that one read."
    )

    trades, fetched_at = _load_validation_trades(days, today.isoformat())

    if trades is None:
        # Evict, so the next rerun retries rather than serving the outage back
        # for the rest of the TTL.
        _load_validation_trades.clear()
        st.warning(UNAVAILABLE)
        return

    _render_freshness(st, fetched_at, VALIDATION_TRADE_CACHE_TTL)

    _render_performance(st, build_performance_statistics(trades), len(trades))
    _render_efficiency(st, trade_efficiency_summary(trades))
    _render_exit_reasons(st, exit_reason_summary(trades))

    # Zero measurable trades is the expected state until positions both open and
    # close after eb56f75 froze the entry ask, so the panel states that rather
    # than rendering empty -- an empty panel reads as "nothing wrong" when it
    # means "nothing measured yet".
    _render_spread_calibration(build_spread_calibration(trades))

    _render_config_changes(st, today - timedelta(days=days), today)


def _render_freshness(st, fetched_at, ttl_seconds):
    """When these numbers were read, which is not when the page was drawn.

    The page redraws on the sidebar's timer while the query behind it is cached,
    so a redraw is not a refresh. Reporting the draw time as the data time is how
    a stale page passes for a live one -- the failure this whole page was rebuilt
    out of. Both are shown, and the data time is the one in bold.
    """

    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        fetched = datetime.fromisoformat(str(fetched_at))

    except (TypeError, ValueError):
        st.caption("Data age unknown.")
        return

    now = datetime.now(ZoneInfo("America/New_York"))
    age_seconds = max((now - fetched).total_seconds(), 0)
    age = (
        "just now" if age_seconds < 60
        else f"{int(age_seconds // 60)} min ago"
    )

    st.caption(
        f"**Data last read {fetched:%H:%M:%S} ET ({age}).** "
        f"Page drawn {now:%H:%M:%S} ET. "
        f"The read is cached for {ttl_seconds // 60} minutes, so a refresh inside "
        "that window redraws these numbers without re-querying — a redraw is not "
        "new data."
    )


def _render_exit_reasons(st, rows):
    """Which rule closed the trades, and what each one cost.

    `exit_reason` was on every closed trade and read by nothing here, so the page
    could report that the book lost money without saying what closed it.
    """

    st.markdown("#### What closed the trades")
    st.caption(
        "Every exit rule that fired in this window, and what each one cost or "
        "earned."
    )

    if not rows:
        st.caption("No exit reason recorded on the trades in this window.")
        return

    import pandas as pd

    st.dataframe(
        pd.DataFrame([
            {
                "Exit reason": row["exit_reason"],
                "Trades": row["trades"],
                "Priced": row["priced"],
                "Avg R": _num(row["avg_r"], "+.2f"),
                "Avg premium": _percent(row["avg_premium_pct"], "+.1f"),
                "Total P&L": _dollars(row["total_dollars"]),
            }
            for row in rows
        ]),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Worst cash first. `Priced` is how many of those trades carry premium "
        "marks — a reason with fewer priced than trades has a P&L covering only "
        "part of it."
    )


def _render_config_changes(st, start_day, end_day):
    """Levers that moved inside the window the numbers above were measured on.

    A 30-day average taken over a spread ceiling of 2 and then 3 measures neither.
    The source is the config each scan actually enforced, not the changelog and
    not the environment -- both of those record intent.
    """

    from app.dashboard import _load_validation_config_changes

    changes = _load_validation_config_changes(
        start_day.isoformat(), end_day.isoformat()
    )

    st.markdown("#### Config changes inside this window")
    st.caption(
        "Settings the scanner actually enforced that moved while these trades "
        "were being taken."
    )

    if changes is None:
        # Same rule as the trade read: an outage must not be served back for the
        # rest of the TTL after the archive comes back.
        _load_validation_config_changes.clear()
        st.caption(
            "Unavailable — the scan archive could not be read. This is not the "
            "same as nothing having changed."
        )
        return

    if not changes:
        st.caption(
            "None. Every trade above was taken under the same enforced settings."
        )
        return

    import pandas as pd

    st.dataframe(
        pd.DataFrame([
            {
                "Day": change["day"],
                "Setting": change["setting"],
                "From": change["from"],
                "To": change["to"],
            }
            for change in changes
        ]),
        width="stretch",
        hide_index=True,
    )
    st.warning(
        f"{len(changes)} setting(s) changed inside this window, so the averages "
        "above are blends of before and after. Narrow the window to the period "
        "you care about before drawing a conclusion from them."
    )
    st.caption(
        "Dated by the first scan that enforced the new value, which is the day it "
        "took effect — not necessarily the day someone edited it."
    )


def _render_performance(st, stats, total):

    stats = stats or {}
    completed = int(stats.get("completed_trades") or 0)

    st.markdown("#### Performance")
    st.caption(
        "What the closed trades did — in R on the underlying, in premium percent, "
        "and in cash."
    )

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

    # R concentrates as hard as cash does, and on the window that prompted this it
    # concentrated harder -- one position was more than the entire total.
    worst_r = stats.get("worst_r")
    total_r_ex_worst = stats.get("total_r_ex_worst")

    if worst_r is not None and total_r_ex_worst is not None:
        st.caption(
            f"Worst single trade: {stats.get('worst_r_symbol') or 'unknown'} at "
            f"{worst_r:+.2f}R. **Total R excluding it: {total_r_ex_worst:+.2f}R** "
            f"across the other {max(completed - 1, 0)} trades."
        )

    # R is measured on the underlying while the position held is an option, and
    # the round trip is routinely wider than the move the stop allows. Showing
    # only R publishes the flattering half, so the premium figures sit alongside
    # it -- scoped to `priced_trades`, since a trade with no option marks cannot
    # contribute and averaging it in as zero would flatter the result.
    priced = int(stats.get("priced_trades") or 0)

    if priced:
        columns = st.columns(5)
        columns[0].metric("Priced trades", priced)
        columns[1].metric("Net win rate", _pct(stats.get("net_win_rate")))
        columns[2].metric("Avg premium P&L", _percent(stats.get("average_option_pnl_pct"), "+.1f"))
        columns[3].metric("Median premium P&L", _percent(stats.get("median_option_pnl_pct"), "+.1f"))
        columns[4].metric("Avg spread cost", _percent(stats.get("average_spread_cost_pct"), ".1f"))
        st.caption(
            "Premium figures are ask-to-bid, so they include the round-trip cost "
            "that R does not. Net win rate is the one to trust."
        )

    _render_cash(st, stats)


def _render_cash(st, stats):
    """The book in cash, and how much of it is one position.

    A percentage average is silent about size, so a window can report a steady
    small loss while being one disaster and a profitable remainder. That is not a
    hypothetical: the first clean window read -6.7% average premium P&L across 17
    trades, and in cash was -$143 total against a single -$207 position, with
    every other trade combined making +$64.
    """

    if not int(stats.get("priced_dollar_trades") or 0):
        return

    total = stats.get("total_option_pl_dollars")
    worst = stats.get("worst_trade_dollars")
    ex_worst = stats.get("total_ex_worst_dollars")
    symbol = stats.get("worst_trade_symbol") or "worst"

    columns = st.columns(4)
    columns[0].metric("Total P&L", _dollars(total))
    columns[1].metric("Avg per trade", _dollars(stats.get("average_option_pl_dollars")))
    columns[2].metric(f"Worst trade ({symbol})", _dollars(worst))
    columns[3].metric("Total excluding worst", _dollars(ex_worst))
    st.caption(
        "Realised premium, ask to bid, on the contracts actually sized. This is "
        "the book — the percentages above are per-trade and say nothing about size."
    )

    # The case the panel exists for: the window's result is one position, and
    # every rule inferred from the average is being fitted to that trade.
    if (
        total is not None and worst is not None and ex_worst is not None
        and abs(worst) > abs(ex_worst)
    ):
        st.warning(
            f"One position is the whole result. {symbol} lost "
            f"{_dollars(worst)} against {_dollars(ex_worst)} from every other "
            "trade combined. Read the averages above as describing that trade, "
            "not the strategy, and check what happened to it before changing a rule."
        )


def _render_efficiency(st, efficiency):

    st.markdown("#### Trade efficiency")
    st.caption(
        "How much of each move the exits actually kept, and how much they gave "
        "back after the peak."
    )

    if efficiency is None:
        st.warning(UNAVAILABLE)
        return

    if not efficiency.get("trades"):
        st.caption("No strategy trade in this window to measure.")
        return

    total = int(efficiency.get("trades") or 0)
    measured = int(efficiency.get("trend_capture_trades") or 0)

    columns = st.columns(4)
    # Units are in the labels because these are not the same kind of number and
    # were previously shown side by side as bare decimals: capture is a percent
    # of the available move, giveback is dollars per share on the underlying.
    #
    # Median first. It is the honest headline for a quantity that can only reach
    # +100 but has no floor -- see `trade_efficiency_summary`.
    columns[0].metric("Trend capture (median)", _percent(efficiency.get("trend_capture_median"), ".1f"))
    columns[1].metric("Trend capture (mean)", _percent(efficiency.get("trend_capture"), ".1f"))
    columns[2].metric("Left on table ($/share)", _num(efficiency.get("left_on_table"), ".2f"))
    columns[3].metric("Avg MFE (R)", _num(efficiency.get("mfe_r"), "+.2f"))

    st.caption(
        "Trend capture is the share of the available move the exit kept; under "
        "0% the exit gave back more than the move was worth. The mean runs far "
        "below the median because a few very bad exits have no floor — trust the "
        "median and read the mean as a tail warning. Left on table is the "
        "underlying's move after the peak, in dollars per share -- read it per "
        "trade, since averaging it mixes a $40 stock with a $400 one."
    )

    # Capture and giveback come from the post-market review, MFE from the trade
    # itself, so the two are measured on different sets. Saying so is cheaper
    # than someone later reconciling an average against the wrong denominator.
    if measured < total:
        st.caption(
            f"Capture and giveback are measured on {measured} of {total} trades — "
            "the rest have no post-market review on record."
        )


def _pct(value):
    return "—" if value is None else f"{float(value):.0f}%"


def _num(value, spec):
    return "—" if value is None else format(float(value), spec)


def _percent(value, spec):
    """Formatted percent, or a bare dash.

    Not `_num(...) + "%"`: that renders a missing measurement as "—%", which
    reads as a unit attached to nothing.
    """

    return "—" if value is None else format(float(value), spec) + "%"


def _dollars(value):
    """Signed dollars. The sign is the point, so it is never dropped."""

    if value is None:
        return "—"

    value = float(value)

    return f"-${abs(value):,.2f}" if value < 0 else f"+${value:,.2f}"
