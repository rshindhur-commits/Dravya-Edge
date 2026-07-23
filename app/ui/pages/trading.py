from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


def _action_label(value):
    return str(value or "NO TRADE").replace("_", " ")


def _render_decision(state):
    import streamlit as st

    decision_center = state.get("decision_center") or {}
    st.subheader("Today's Decision Center")
    columns = st.columns(2)

    for column, label, key in [
        (columns[0], "Best Call", "best_call"),
        (columns[1], "Best Put", "best_put"),
    ]:
        candidate = decision_center.get(key) or {}

        with column:
            st.markdown(f"### {label}")
            if not candidate:
                st.caption("No qualifying decision")
                continue
            st.markdown(f"**{candidate.get('symbol') or 'NONE'}**  |  {_action_label(candidate.get('action'))}")
            rows = [
                ("Setup", candidate.get("setup")),
                ("Entry", candidate.get("entry_price")),
                ("Stop", candidate.get("stop_price")),
                ("Target", candidate.get("target_price")),
                ("RR", candidate.get("rr")),
                ("Trend", candidate.get("trend_health")),
                ("Entry Timing", candidate.get("entry_timing_score")),
                ("Trade Quality", candidate.get("trade_quality_score")),
                ("Option", candidate.get("option_ticker")),
                ("Quality", candidate.get("option_quality")),
                ("Telegram", candidate.get("telegram")),
            ]
            st.dataframe(
                pd.DataFrame(rows, columns=["Field", "Value"]),
                width="stretch",
                hide_index=True,
            )


def _render_ranked_opportunities(state):
    import streamlit as st

    st.subheader("Ranked Opportunities")
    candidates = (state.get("decision_center") or {}).get("ranked_opportunities") or []

    if not candidates:
        st.info("No ranked opportunities in the latest scan.")
        return

    rows = []
    for rank, candidate in enumerate(candidates, start=1):
        rows.append({
            "Rank": rank,
            "TQS Rank": candidate.get("candidate_rank"),
            "Symbol": candidate.get("symbol"),
            "Decision": _action_label(candidate.get("action")),
            "TQS": candidate.get("trade_quality_score"),
            "Entry Timing": candidate.get("entry_timing_score"),
            "Timing Grade": candidate.get("entry_timing_grade"),
            "Setup": candidate.get("setup"),
            "RR": candidate.get("rr"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_open_trades(state):
    import streamlit as st

    st.subheader("Open Trades")
    trades = state.get("open_trades") or []

    if not trades:
        st.caption("No active V1 scanner-managed trades.")
        return

    columns = st.columns(min(3, len(trades)))
    for column, trade in zip(columns, trades):
        with column:
            st.markdown(f"### {trade.get('symbol')}")
            st.metric("R Progress", trade.get("r_progress") or "-")
            st.caption(f"Trend: {trade.get('trend_health') or '-'}")
            st.caption(f"Action: {_action_label(trade.get('action'))}")


def _render_market_summary(state):
    import streamlit as st

    summary = state.get("summary") or {}
    st.subheader("Market Summary")
    st.caption(
        f"Bias: {state.get('market_bias') or 'MIXED'} | "
        f"Symbols scanned: {summary.get('scanned', 0)} | "
        f"Bullish: {summary.get('bullish', 0)} | "
        f"Bearish: {summary.get('bearish', 0)}"
    )


def _render_trader_workspace(state):
    from app.dashboard import _render_today_performance

    _render_decision(state)
    _render_ranked_opportunities(state)
    _render_open_trades(state)
    _render_today_performance(state)
    _render_market_summary(state)


def render(state, df, refresh_state):

    _render_trader_workspace(state)


def render_from_state(state, refresh_state):
    _render_trader_workspace(state)