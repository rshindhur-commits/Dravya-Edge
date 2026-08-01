"""The activity feed: one filterable timeline of everything the day did.

Split out of `trading.py` on 2026-07-31. It is a forensic tool rather than an
operator one -- it reads the whole activity trace (17,742 rows on 2026-07-31)
and re-sorts it -- so the console keeps it collapsed and this module keeps it
out of the console's own code.

When `activity_trace.csv` exists it is the whole story. When it does not, the
timeline is reconstructed from the four sources that each hold part of it:
the trade lifecycle jsonl, the auto-paper decisions, the telegram audit, and the
scanner frame.
"""

from __future__ import annotations

import pandas as pd

from app.ui.render_context import ROOT_DIR, read_jsonl
from app.ui.timestamps import to_utc_series

TRACE_COLUMNS = {
    "time": "Time",
    "symbol": "Symbol",
    "category": "Category",
    "event": "Event",
    "context": "Context",
    "origin": "Origin",
    "previous_state": "Previous State",
    "state_changed": "State Changed",
    "setup_score": "Setup Score",
    "rr": "RR",
    "option_quality": "Option Quality",
    "candle_time": "Decision Candle Time",
    "candle_open": "Candle Open",
    "candle_high": "Candle High",
    "candle_low": "Candle Low",
    "candle_close": "Candle Close",
    "candle_volume": "Candle Volume",
    "scanner_recommendation": "Scanner Recommendation",
    "execution_eligibility": "Execution Eligibility",
    "execution_outcome": "Execution Outcome",
    "execution_reason": "Execution Reason",
    "trade_status": "Trade Status",
    "telegram_status": "Telegram Status",
    "telegram_reason": "Telegram Reason",
}

PAGE_SIZE = 25


def action_label(value):
    return str(value or "NO TRADE").replace("_", " ")


def activity_category(event, source):
    event = str(event or "").upper()
    if source in {"Scanner", "Scanner decision", "Rule evaluation"}:
        return "Scanner"
    if source == "Telegram":
        return "Telegram"
    if source == "Paper" or event in {"OPENED", "BLOCKED", "SKIPPED"}:
        return "Paper"
    if event in {"ENTRYOPENED", "EXITTRIGGERED", "PARTIAL_PROFIT", "HOLD", "EXIT"}:
        return "Trades"
    if event in {"FAILED", "ERROR"}:
        return "Errors"
    return "System"


def paper_activity_context(row):
    decision = str(row.get("decision") or "").upper()
    reason = row.get("reason")
    action_status = str(row.get("action_status") or "").upper()
    if decision == "SKIPPED" and str(reason or "").upper() == action_status:
        return "No execution gate recorded (legacy decision row)"
    return reason


def activity_marker(event, category):
    event = str(event or "").upper()
    if category == "Telegram":
        return "BLUE"
    if category == "Errors":
        return "RED"
    if event in {"OPENED", "ENTRYOPENED", "ENTER", "ENTER_PAPER"}:
        return "GREEN"
    if event in {"EXIT", "EXITTRIGGERED", "HARD_STOP", "HARD_TARGET"}:
        return "RED"
    if event in {"BLOCKED", "REVIEW_TV_CHART"}:
        return "YELLOW"
    if event in {"WAIT", "SKIPPED"}:
        return "GRAY"
    return "ORANGE"


def _sorted_by_time(frame):
    """Newest first.

    Times arrive as `2026-07-31 00:38:19 EDT` from the scanner's `%Z` formatter,
    which pandas parses only with a FutureWarning and will eventually refuse.
    `to_utc_series` reads the abbreviation and treats naive values as Eastern.
    """
    frame["_sort"] = to_utc_series(frame["Time"])
    return frame.sort_values("_sort", ascending=False).reset_index(drop=True)


def _from_trace(path):
    trace = pd.read_csv(path).rename(columns=TRACE_COLUMNS)
    trace["Marker"] = trace.apply(
        lambda row: activity_marker(row.get("Event"), row.get("Category")), axis=1
    )
    return _sorted_by_time(trace)


def _from_trade_timeline(daily_dir):
    events = []
    for row in read_jsonl(daily_dir / "trade_timeline.jsonl"):
        payload = row.get("payload") or {}
        event = row.get("event_type")
        category = activity_category(event, "Trades")
        events.append({
            "Time": row.get("occurred_at"),
            "Symbol": payload.get("symbol"),
            "Category": category,
            "Marker": activity_marker(event, category),
            "Event": action_label(event),
            "Context": payload.get("exit_phase") or payload.get("entry_reason"),
            "Origin": "Trade lifecycle",
            "Stage": "Trade lifecycle",
            "Rule": None,
            "Passed": None,
        })
    return events


def _from_auto_paper(daily_dir):
    path = daily_dir / "auto_paper_decisions.csv"
    if not path.exists():
        return []

    events = []
    for _, row in pd.read_csv(path).iterrows():
        event = row.get("decision")
        if (
            str(row.get("symbol") or "").upper() == "SYSTEM"
            and str(event or "").upper() == "SKIPPED"
        ):
            continue
        category = activity_category(event, "Paper")
        events.append({
            "Time": row.get("timestamp"),
            "Symbol": row.get("symbol"),
            "Category": category,
            "Marker": activity_marker(event, category),
            "Event": action_label(event),
            "Context": paper_activity_context(row),
            "Origin": "Auto-paper gate",
            "Stage": "Auto-paper",
            "Rule": row.get("blocked_by") or "Execution eligibility",
            "Passed": str(event or "").upper() == "OPENED",
            "Scanner Recommendation": row.get("scanner_recommendation") or row.get("action_status"),
            "Execution Eligibility": row.get("execution_eligibility"),
            "Execution Outcome": row.get("execution_outcome") or event,
            "Execution Reason": row.get("execution_reason") or row.get("reason"),
            "Trade Status": row.get("trade_status"),
            "Telegram Status": row.get("telegram_status"),
            "Telegram Reason": row.get("telegram_reason"),
        })
    return events


def _from_telegram(rows):
    events = []
    for row in rows:
        event = row.get("message_type") or row.get("event")
        category = activity_category(event, "Telegram")
        events.append({
            "Time": row.get("observed_at_utc"),
            "Symbol": row.get("symbol"),
            "Category": category,
            "Marker": activity_marker(event, category),
            "Event": action_label(event),
            "Context": row.get("event") if row.get("event") != "FAILED" else row.get("error"),
            "Origin": "Telegram dispatcher",
            "Stage": "Telegram",
            "Rule": row.get("message_type"),
            "Passed": row.get("event") == "SENT",
        })
    return events


def _from_scanner_frame(df):
    if df is None or df.empty:
        return []

    events = []
    for _, row in df.iterrows():
        event = row.get("Action Status")
        if str(event or "").upper() in {"", "NO_TRADE_MARKET_CLOSED"}:
            continue
        category = activity_category(event, "Scanner")
        events.append({
            "Time": row.get("Current ET") or row.get("Data Timestamp ET"),
            "Symbol": row.get("Symbol"),
            "Category": category,
            "Marker": activity_marker(event, category),
            "Event": action_label(event),
            "Context": row.get("Action Reason") or row.get("Blocked By"),
            "Origin": "Scanner decision",
            "Stage": row.get("ENTRY_GATE_FAILURE_STAGE") or "Decision",
            "Rule": row.get("Blocked By") or "Action Status",
            "Passed": str(event or "").upper() in {"ENTER", "ENTER_PAPER"},
            "Scanner Recommendation": row.get("Scanner Recommendation") or event,
            "Execution Eligibility": row.get("Execution Eligibility"),
            "Execution Outcome": row.get("Execution Outcome"),
            "Execution Reason": row.get("Execution Reason"),
            "Trade Status": row.get("Trade Status"),
            "Telegram Status": row.get("Telegram Status"),
            "Telegram Reason": row.get("Telegram Reason"),
        })
    return events


def activity_rows(context):
    daily_dir = ROOT_DIR / "data" / "daily" / str(context.trading_day)
    trace_path = daily_dir / "activity_trace.csv"
    if trace_path.exists() and trace_path.stat().st_size:
        return _from_trace(trace_path)

    events = (
        _from_trade_timeline(daily_dir)
        + _from_auto_paper(daily_dir)
        + _from_telegram(context.telegram)
        + _from_scanner_frame(context.df)
    )
    timeline = pd.DataFrame(events)
    return timeline if timeline.empty else _sorted_by_time(timeline)


def render(context):
    import streamlit as st

    st.subheader("Activity Feed")
    timeline = activity_rows(context)
    if timeline.empty:
        st.caption("No trading, paper, Telegram, or scanner events recorded yet.")
        return

    st.caption(f"Today: {len(timeline)} events")
    day = context.trading_day
    symbols = ["All Symbols"] + sorted(
        symbol for symbol in timeline["Symbol"].dropna().astype(str).unique()
        if symbol and symbol.lower() != "nan"
    )
    filters = st.columns([1, 1, 2, 1])
    category = filters[0].selectbox(
        "Type",
        ["All", "Trades", "Telegram", "Paper", "Scanner", "System", "Errors"],
        key=f"activity_category_{day}",
    )
    symbol = filters[1].selectbox("Symbol", symbols, key=f"activity_symbol_{day}")
    search = filters[2].text_input("Search", key=f"activity_search_{day}")
    grouped = filters[3].checkbox("Group symbols", key=f"activity_group_{day}")

    filtered = timeline.copy()
    if category != "All":
        filtered = filtered[filtered["Category"] == category]
    if symbol != "All Symbols":
        filtered = filtered[filtered["Symbol"].astype(str) == symbol]
    if search.strip():
        search_text = search.strip().lower()
        mask = filtered[["Symbol", "Event", "Context", "Origin", "Stage", "Rule"]].fillna("").astype(str).apply(
            lambda row: row.str.lower().str.contains(search_text, regex=False).any(),
            axis=1,
        )
        filtered = filtered[mask]

    if grouped:
        for grouped_symbol, rows in filtered.groupby(filtered["Symbol"].fillna("System"), sort=True):
            with st.expander(f"{grouped_symbol} ({len(rows)} events)"):
                st.dataframe(rows.drop(columns="_sort"), width="stretch", hide_index=True)
        return

    total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input(
        "Page", min_value=1, max_value=total_pages, value=1, step=1,
        key=f"activity_page_{day}",
    )
    start = (page - 1) * PAGE_SIZE
    st.caption(f"Page {page} of {total_pages}")
    st.dataframe(
        filtered.iloc[start:start + PAGE_SIZE].drop(columns="_sort"),
        width="stretch",
        hide_index=True,
    )
