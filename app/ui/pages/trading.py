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


def _render_decision_feed(df):
    import streamlit as st

    st.subheader("Live Decision Feed")
    if df is None or df.empty:
        st.caption("No scanner decisions available.")
        return
    rows = []
    for _, row in df.iterrows():
        action = str(row.get("Action Status") or "").upper()
        if action in {"", "WAIT", "NO_TRADE_MARKET_CLOSED"}:
            continue
        rows.append({
            "Time": row.get("Current ET") or row.get("Data Timestamp ET"),
            "Symbol": row.get("Symbol"),
            "Decision": _action_label(action),
            "Reason": row.get("Action Reason") or row.get("Blocked By"),
            "Telegram": row.get("Telegram Sent") or row.get("Telegram Block Reason"),
        })
    if not rows:
        st.caption("No material decision changes in the latest scan.")
        return
    st.dataframe(pd.DataFrame(rows).head(12), width="stretch", hide_index=True)


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

    st.subheader("Telegram Delivery")
    rows = _telegram_rows(_trading_day(state))
    sent = [row for row in rows if row.get("event") == "SENT"]
    failed = [row for row in rows if row.get("event") == "FAILED"]
    pending = [row for row in rows if row.get("event") == "ATTEMPT"]
    metrics = st.columns(4)
    metrics[0].metric("Sent", len(sent))
    metrics[1].metric("Pending", len(pending))
    metrics[2].metric("Failed", len(failed))
    metrics[3].metric("Last Latency", f"{sent[-1].get('latency_ms', 0) / 1000:.1f}s" if sent else "-")
    if sent or failed:
        display = [
            {
                "Time": row.get("observed_at_utc"),
                "Symbol": row.get("symbol"),
                "Lifecycle": row.get("message_type"),
                "Delivery": row.get("event"),
                "Reason": row.get("error"),
            }
            for row in (sent + failed)[-10:]
        ]
        st.dataframe(pd.DataFrame(display), width="stretch", hide_index=True)


def _render_market_pulse(state):
    import streamlit as st

    summary = state.get("summary") or {}
    positions = _active_positions()
    suggestions = sum(
        1 for candidate in (state.get("decision_center") or {}).get("ranked_opportunities") or []
        if str(candidate.get("action") or "").upper() in {"ENTER", "ENTER_PAPER", "REVIEW_TV_CHART"}
    )
    st.subheader("Market Pulse")
    metrics = st.columns(5)
    metrics[0].metric("Bias", state.get("market_bias") or "MIXED")
    metrics[1].metric("Universe", summary.get("scanned", 0))
    metrics[2].metric("Bullish", summary.get("bullish", 0))
    metrics[3].metric("Suggestions", suggestions)
    metrics[4].metric("Open", len(positions))


def _render_event_timeline(state):
    import streamlit as st

    trading_day = _trading_day(state)
    daily_dir = ROOT_DIR / "data" / "daily" / str(trading_day)
    events = []
    for row in _read_jsonl(daily_dir / "trade_timeline.jsonl"):
        events.append({
            "Time": row.get("occurred_at"),
            "Symbol": (row.get("payload") or {}).get("symbol"),
            "Event": row.get("event_type"),
            "Detail": (row.get("payload") or {}).get("exit_phase") or (row.get("payload") or {}).get("entry_reason"),
        })
    decisions_path = daily_dir / "auto_paper_decisions.csv"
    if decisions_path.exists():
        decisions = pd.read_csv(decisions_path)
        for _, row in decisions.tail(30).iterrows():
            events.append({
                "Time": row.get("timestamp"),
                "Symbol": row.get("symbol"),
                "Event": row.get("decision"),
                "Detail": row.get("reason"),
            })
    for row in _telegram_rows(trading_day):
        events.append({
            "Time": row.get("observed_at_utc"),
            "Symbol": row.get("symbol"),
            "Event": row.get("message_type"),
            "Detail": row.get("event"),
        })
    st.subheader("Live Event Timeline")
    if not events:
        st.caption("No lifecycle or delivery events recorded yet.")
        return
    timeline = pd.DataFrame(events)
    timeline["_sort"] = pd.to_datetime(timeline["Time"], errors="coerce", utc=True)
    timeline = timeline.sort_values("_sort", ascending=False).drop(columns="_sort")
    st.dataframe(timeline.head(30), width="stretch", hide_index=True)


def _render_trader_workspace(state, df):
    import streamlit as st

    _render_live_positions(state)
    left, right = st.columns([3, 2])
    with left:
        _render_decision_feed(df)
    with right:
        _render_risk_monitor(state)
    _render_opportunity_board(state)
    left, right = st.columns([3, 2])
    with left:
        _render_telegram_status(state)
    with right:
        _render_market_pulse(state)
    _render_event_timeline(state)


def render(state, df, refresh_state):
    _render_trader_workspace(state, df)


def render_from_state(state, refresh_state):
    _render_trader_workspace(state, pd.DataFrame())