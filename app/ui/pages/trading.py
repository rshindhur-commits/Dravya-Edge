import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[3]
ET_TZ = ZoneInfo("America/New_York")
TELEGRAM_AUDIT_FILE = ROOT_DIR / "data" / "live" / "telegram_dispatch_audit.jsonl"


def _action_label(value):
    return str(value or "NO TRADE").replace("_", " ")


def _trading_day(state):
    explicit = str((state or {}).get("trading_day") or "")
    if explicit:
        return explicit
    scan_id = str((state or {}).get("scan_id") or "")
    if len(scan_id) >= 10 and scan_id[:10].count("-") == 2:
        return scan_id[:10]
    return datetime.now(ET_TZ).date().isoformat()


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _active_positions():
    from app.state.paper_trade_manager import load_paper_trades

    return [
        trade for trade in load_paper_trades().values()
        if str(trade.get("status") or "").upper() in {"OPEN", "PAUSED"}
    ]


def _telegram_rows(trading_day):
    prefix = str(trading_day or "")
    return [
        row for row in _read_jsonl(TELEGRAM_AUDIT_FILE)
        if str(row.get("observed_at_utc") or "").startswith(prefix)
        and not str(row.get("message_type") or "").startswith("TEST_")
    ]


def _position_rows(positions, telegram_rows):
    delivered_trade_ids = {
        str(row.get("trade_id"))
        for row in telegram_rows
        if row.get("event") == "SENT" and row.get("trade_id")
    }
    rows = []
    for trade in positions:
        trade_id = str(trade.get("trade_id") or "")
        rows.append({
            "Symbol": trade.get("symbol"),
            "State": trade.get("status"),
            "R": trade.get("rr_progress"),
            "Qty": trade.get("option_contracts") or 1,
            "Hold": trade.get("holding_profile") or "INTRADAY",
            "Next Action": _action_label(trade.get("trade_action") or "HOLD"),
            "Telegram": "SENT" if trade_id and trade_id in delivered_trade_ids else "PENDING",
        })
    return rows


def _render_live_positions(state):
    import streamlit as st

    positions = _active_positions()
    telegram_rows = _telegram_rows(_trading_day(state))
    st.subheader("Live Positions")
    if not positions:
        st.caption("No active paper positions.")
        return

    rows = _position_rows(positions, telegram_rows)
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    for trade in positions:
        symbol = trade.get("symbol") or "Unknown"
        with st.expander(f"{symbol} position details"):
            details = [
                ("Entry", trade.get("entry_price")),
                ("Current", trade.get("current_price") or trade.get("close_price")),
                ("Stop", trade.get("stop_loss")),
                ("Target", trade.get("take_profit")),
                ("R Progress", trade.get("rr_progress")),
                ("Holding Profile", trade.get("holding_profile")),
                ("Trend Health", trade.get("last_trend_health_status") or trade.get("trend_health")),
                ("Exit Confidence", trade.get("last_exit_confidence_score")),
                ("Option", trade.get("option_ticker")),
                ("Opened", trade.get("opened_at_et") or trade.get("opened_at")),
                ("Trade ID", trade.get("trade_id")),
            ]
            st.dataframe(pd.DataFrame(details, columns=["Field", "Value"]), width="stretch", hide_index=True)


def _activity_category(event, source):
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


def _paper_activity_context(row):
    decision = str(row.get("decision") or "").upper()
    reason = row.get("reason")
    action_status = str(row.get("action_status") or "").upper()
    if decision == "SKIPPED" and str(reason or "").upper() == action_status:
        return "No execution gate recorded (legacy decision row)"
    return reason


def _activity_marker(event, category):
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


def _activity_rows(state, df):
    trading_day = _trading_day(state)
    daily_dir = ROOT_DIR / "data" / "daily" / str(trading_day)
    trace_path = daily_dir / "activity_trace.csv"
    if trace_path.exists() and trace_path.stat().st_size:
        trace = pd.read_csv(trace_path)
        trace = trace.rename(columns={
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
        })
        trace["Marker"] = trace.apply(
            lambda row: _activity_marker(row.get("Event"), row.get("Category")),
            axis=1,
        )
        trace["_sort"] = pd.to_datetime(trace["Time"], errors="coerce", utc=True)
        return trace.sort_values("_sort", ascending=False).reset_index(drop=True)
    events = []
    for row in _read_jsonl(daily_dir / "trade_timeline.jsonl"):
        payload = row.get("payload") or {}
        event = row.get("event_type")
        category = _activity_category(event, "Trades")
        events.append({
            "Time": row.get("occurred_at"),
            "Symbol": payload.get("symbol"),
            "Category": category,
            "Marker": _activity_marker(event, category),
            "Event": _action_label(event),
            "Context": payload.get("exit_phase") or payload.get("entry_reason"),
            "Origin": "Trade lifecycle",
            "Stage": "Trade lifecycle",
            "Rule": None,
            "Passed": None,
        })
    decisions_path = daily_dir / "auto_paper_decisions.csv"
    if decisions_path.exists():
        decisions = pd.read_csv(decisions_path)
        for _, row in decisions.iterrows():
            event = row.get("decision")
            if (
                str(row.get("symbol") or "").upper() == "SYSTEM"
                and str(event or "").upper() == "SKIPPED"
            ):
                continue
            category = _activity_category(event, "Paper")
            events.append({
                "Time": row.get("timestamp"),
                "Symbol": row.get("symbol"),
                "Category": category,
                "Marker": _activity_marker(event, category),
                "Event": _action_label(event),
                "Context": _paper_activity_context(row),
                "Origin": "Auto-paper gate",
                "Stage": "Auto-paper",
                "Rule": row.get("blocked_by") or "Execution eligibility",
                "Passed": str(event or "").upper() == "OPENED",
            })
    for row in _telegram_rows(trading_day):
        event = row.get("message_type") or row.get("event")
        category = _activity_category(event, "Telegram")
        events.append({
            "Time": row.get("observed_at_utc"),
            "Symbol": row.get("symbol"),
            "Category": category,
            "Marker": _activity_marker(event, category),
            "Event": _action_label(event),
            "Context": row.get("event") if row.get("event") != "FAILED" else row.get("error"),
            "Origin": "Telegram dispatcher",
            "Stage": "Telegram",
            "Rule": row.get("message_type"),
            "Passed": row.get("event") == "SENT",
        })
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            event = row.get("Action Status")
            if str(event or "").upper() in {"", "NO_TRADE_MARKET_CLOSED"}:
                continue
            category = _activity_category(event, "Scanner")
            events.append({
                "Time": row.get("Current ET") or row.get("Data Timestamp ET"),
                "Symbol": row.get("Symbol"),
                "Category": category,
                "Marker": _activity_marker(event, category),
                "Event": _action_label(event),
                "Context": row.get("Action Reason") or row.get("Blocked By"),
                "Origin": "Scanner decision",
                "Stage": row.get("ENTRY_GATE_FAILURE_STAGE") or "Decision",
                "Rule": row.get("Blocked By") or "Action Status",
                "Passed": str(event or "").upper() in {"ENTER", "ENTER_PAPER"},
            })
    timeline = pd.DataFrame(events)
    if timeline.empty:
        return timeline
    timeline["_sort"] = pd.to_datetime(timeline["Time"], errors="coerce", utc=True)
    return timeline.sort_values("_sort", ascending=False).reset_index(drop=True)


def _render_activity_feed(state, df):
    import streamlit as st

    st.subheader("Activity Feed")
    timeline = _activity_rows(state, df)
    if timeline.empty:
        st.caption("No trading, paper, Telegram, or scanner events recorded yet.")
        return
    st.caption(f"Today: {len(timeline)} events")
    symbols = ["All Symbols"] + sorted(
        symbol for symbol in timeline["Symbol"].dropna().astype(str).unique()
        if symbol and symbol.lower() != "nan"
    )
    filters = st.columns([1, 1, 2, 1])
    category = filters[0].selectbox(
        "Type",
        ["All", "Trades", "Telegram", "Paper", "Scanner", "System", "Errors"],
        key=f"activity_category_{_trading_day(state)}",
    )
    symbol = filters[1].selectbox(
        "Symbol",
        symbols,
        key=f"activity_symbol_{_trading_day(state)}",
    )
    search = filters[2].text_input("Search", key=f"activity_search_{_trading_day(state)}")
    grouped = filters[3].checkbox("Group symbols", key=f"activity_group_{_trading_day(state)}")
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
    page_size = 25
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = st.number_input(
        "Page",
        min_value=1,
        max_value=total_pages,
        value=1,
        step=1,
        key=f"activity_page_{_trading_day(state)}",
    )
    start = (page - 1) * page_size
    st.caption(f"Page {page} of {total_pages}")
    st.dataframe(filtered.iloc[start:start + page_size].drop(columns="_sort"), width="stretch", hide_index=True)


def _render_opportunity_board(state):
    import streamlit as st

    st.subheader("Current Opportunity Board")
    candidates = (state.get("decision_center") or {}).get("ranked_opportunities") or []
    if not candidates:
        st.caption("No ranked opportunities in the latest scan.")
        return
    rows = []
    for candidate in candidates[:10]:
        rows.append({
            "Rank": candidate.get("candidate_rank"),
            "Symbol": candidate.get("symbol"),
            "Decision": _action_label(candidate.get("action")),
            "Setup": candidate.get("setup"),
            "TQS": candidate.get("trade_quality_score"),
            "RR": candidate.get("rr"),
            "Timing": candidate.get("entry_timing_grade"),
            "Holding": candidate.get("holding_profile") or "-",
            "Telegram": candidate.get("telegram"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _risk_alerts(positions):
    alerts = []
    for trade in positions:
        symbol = trade.get("symbol") or "Unknown"
        r_progress = _number(trade.get("rr_progress"), 0.0)
        confidence = _number(trade.get("last_exit_confidence_score"), 0.0)
        phase = str(trade.get("last_exit_phase") or "").upper()
        if r_progress is not None and r_progress <= -0.8:
            alerts.append((symbol, "Stop proximity", f"{r_progress:.2f}R"))
        if confidence is not None and confidence >= 90:
            alerts.append((symbol, "High exit confidence", confidence))
        if phase in {"TREND_FAILURE", "END_OF_DAY", "HARD_STOP"}:
            alerts.append((symbol, "Exit signal", phase))
        if trade.get("holding_profile_override_source"):
            alerts.append((symbol, "Manual profile override", trade.get("holding_profile_override_source")))
    return alerts


def _render_risk_monitor(state):
    import streamlit as st

    st.subheader("Active Risk Monitor")
    alerts = _risk_alerts(_active_positions())
    if not alerts:
        st.success("No active risks")
        return
    st.dataframe(
        pd.DataFrame(alerts, columns=["Symbol", "Risk", "Detail"]),
        width="stretch",
        hide_index=True,
    )


def _render_telegram_status(state):
    import streamlit as st

    st.markdown("#### Telegram")
    rows = _telegram_rows(_trading_day(state))
    sent = [row for row in rows if row.get("event") == "SENT"]
    failed = [row for row in rows if row.get("event") == "FAILED"]
    pending = [row for row in rows if row.get("event") == "ATTEMPT"]
    health = "HEALTHY" if not failed else "ATTENTION"
    latency = f"{sent[-1].get('latency_ms', 0) / 1000:.1f}s" if sent else "-"
    st.caption(f"{health} | Sent {len(sent)} | Pending {len(pending)} | Failed {len(failed)} | Last {latency}")


def _render_market_pulse(state):
    import streamlit as st

    summary = state.get("summary") or {}
    positions = _active_positions()
    suggestions = sum(
        1 for candidate in (state.get("decision_center") or {}).get("ranked_opportunities") or []
        if str(candidate.get("action") or "").upper() in {"ENTER", "ENTER_PAPER", "REVIEW_TV_CHART"}
    )
    st.markdown("#### Market")
    st.caption(
        f"{state.get('market_bias') or 'MIXED'} | {summary.get('scanned', 0)} scanned | "
        f"{summary.get('bullish', 0)} bullish | {summary.get('bearish', 0)} bearish | "
        f"{suggestions} suggestions | {len(positions)} active"
    )


def _render_trader_workspace(state, df):
    import streamlit as st

    _render_live_positions(state)
    left, right = st.columns([3, 2])
    with left:
        _render_activity_feed(state, df)
    with right:
        _render_risk_monitor(state)
    _render_opportunity_board(state)
    left, right = st.columns(2)
    with left:
        _render_telegram_status(state)
    with right:
        _render_market_pulse(state)


def render(state, df, refresh_state):
    _render_trader_workspace(state, df)


def render_from_state(state, refresh_state):
    _render_trader_workspace(state, pd.DataFrame())