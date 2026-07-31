import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[3]
ET_TZ = ZoneInfo("America/New_York")
TELEGRAM_AUDIT_FILE = ROOT_DIR / "data" / "live" / "telegram_dispatch_audit.jsonl"
MARKET_CLOSE = time(16, 0)
STALE_SCAN_MINUTES = 15


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


def _is_post_market(now=None):
    now = now or datetime.now(ET_TZ)
    return now.weekday() >= 5 or now.time() >= MARKET_CLOSE


def _minutes_since(value):
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(stamp):
        return None
    return (pd.Timestamp.now(tz="UTC") - stamp).total_seconds() / 60.0


def _paper_events(trading_day):
    path = ROOT_DIR / "data" / "daily" / str(trading_day) / "paper_trade_events.csv"
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _entries_used(trading_day):
    """Entries opened on this trading day, counted off the trade key.

    Counting OPEN events undercounts: the 2026-07-30 file holds an AUTO_EXIT for
    a trade whose OPEN row never landed, so the day reported zero entries while
    a position had plainly been taken. The key embeds its own open timestamp
    (`SYMBOL|OPTION|YYYY-MM-DD HH:MM:SS`), which survives a missing event row and
    also keeps a next-day close from counting as a fresh entry.
    """
    events = _paper_events(trading_day)
    if events.empty or "trade_key" not in events.columns:
        return 0

    day = str(trading_day)
    keys = {
        key for key in events["trade_key"].dropna().astype(str)
        if key.rsplit("|", 1)[-1].strip().startswith(day)
    }
    return len(keys)


def _engine_status():
    try:
        from app.runtime.scan_supervisor import status

        return status()
    except Exception:
        return {}


def _db_writes_active():
    try:
        from app.db.persistence import db_writes_enabled

        return bool(db_writes_enabled())
    except Exception:
        return False


def _health_cells(state):
    """The six facts an operator needs before trusting anything else on the page.

    Every one of these was previously either absent or buried in the sidebar, so
    a stalled engine or a dead DB writer looked identical to a quiet market.
    """
    engine = _engine_status()
    positions = _active_positions()
    trading_day = _trading_day(state)
    telegram = _telegram_rows(trading_day)
    sent = sum(1 for row in telegram if row.get("event") == "SENT")
    failed = sum(1 for row in telegram if row.get("event") == "FAILED")

    scan_age = _minutes_since(
        ((state or {}).get("scanner_health") or {}).get("timestamp")
        or ((state or {}).get("metadata") or {}).get("created_at")
        or (state or {}).get("generated_at")
    )
    entries = _entries_used(trading_day)
    max_daily = 0
    try:
        from app.runtime.paper_automation_support import load_auto_paper_controls

        max_daily = int(load_auto_paper_controls().get("max_daily") or 0)
    except Exception:
        pass

    alive = bool(engine.get("thread_alive"))
    failures = int(engine.get("failures") or 0)
    db_active = _db_writes_active()
    post_market = _is_post_market()

    if scan_age is None:
        scan_tone, scan_text = ("neutral" if post_market else "bad"), "never"
    else:
        scan_text = f"{scan_age:.0f}m ago"
        # After the close the engine is meant to be quiet, so an ageing scan is
        # only a fault during the session.
        scan_tone = (
            "neutral" if post_market
            else "ok" if scan_age <= STALE_SCAN_MINUTES
            else "warn" if scan_age <= STALE_SCAN_MINUTES * 3
            else "bad"
        )

    return [
        ("Engine",
         str(engine.get("status") or "IDLE") if alive else "NOT RUNNING",
         "ok" if alive else "bad"),
        ("Last scan", scan_text, scan_tone),
        ("Scans / fails",
         f"{int(engine.get('scans') or 0)} / {failures}",
         "warn" if failures else "ok"),
        ("DB writes",
         "ACTIVE" if db_active else "OFF",
         "ok" if db_active else "bad"),
        ("Telegram",
         f"{sent} sent / {failed} failed",
         "bad" if failed else "ok"),
        ("Book",
         f"{len(positions)} open | {entries}"
         + (f"/{max_daily}" if max_daily else "") + " entries",
         "warn" if max_daily and entries >= max_daily else "neutral"),
    ]


def _render_header(state):
    """Page identity, session mode, and the six facts that gate trust."""
    import streamlit as st

    from app.ui.components import operator_bar, status_card_grid

    engine = _engine_status()
    post_market = _is_post_market()
    trading_day = _trading_day(state)
    pills = [
        (trading_day, "neutral"),
        ("POST-MARKET" if post_market else "LIVE SESSION",
         "post" if post_market else "live"),
        ("ENGINE DOWN", "bad") if not engine.get("thread_alive")
        else ("ENGINE " + str(engine.get("status") or "IDLE"), "live"),
    ]
    operator_bar("Operator Console", pills)

    status_card_grid(_health_cells(state))

    if not engine.get("thread_alive"):
        st.error(
            "Scan engine thread is not running. No scans are happening until "
            "Streamlit restarts."
        )
    elif engine.get("last_error") and int(engine.get("failures") or 0):
        st.warning(f"Last scan error: {engine['last_error']}")


def _position_tone(trade):
    """How close this position is to needing a decision."""
    r_progress = _number(trade.get("rr_progress"))
    confidence = _number(trade.get("last_exit_confidence_score"))
    phase = str(trade.get("last_exit_phase") or "").upper()

    if (r_progress is not None and r_progress <= -0.8) or phase in {
        "TREND_FAILURE", "HARD_STOP"
    }:
        return "bad"
    if (confidence is not None and confidence >= 90) or phase == "END_OF_DAY":
        return "warn"
    if r_progress is not None and r_progress > 0:
        return "ok"
    return "neutral"


def _render_live_positions(state):
    import streamlit as st

    from app.ui.components import position_card

    positions = _active_positions()
    telegram_rows = _telegram_rows(_trading_day(state))
    st.subheader("Book")
    if not positions:
        st.caption("No active paper positions.")
        return

    delivered = {
        str(row.get("trade_id"))
        for row in telegram_rows
        if row.get("event") == "SENT" and row.get("trade_id")
    }

    for trade in positions:
        symbol = trade.get("symbol") or "Unknown"
        trade_id = str(trade.get("trade_id") or "")
        position_card(
            symbol,
            " · ".join(str(part) for part in (
                trade.get("status") or "OPEN",
                trade.get("holding_profile") or "INTRADAY",
                _action_label(trade.get("trade_action") or "HOLD"),
            )),
            _number(trade.get("rr_progress")),
            [
                ("Entry", trade.get("entry_price")),
                ("Current", trade.get("current_price") or trade.get("close_price")),
                ("Stop", trade.get("stop_loss")),
                ("Target", trade.get("take_profit")),
                ("Trend", trade.get("last_trend_health_status") or trade.get("trend_health")),
                ("Exit conf", trade.get("last_exit_confidence_score")),
                ("Telegram", "SENT" if trade_id and trade_id in delivered else "PENDING"),
            ],
            tone=_position_tone(trade),
        )

    for trade in positions:
        symbol = trade.get("symbol") or "Unknown"
        with st.expander(f"{symbol} chart and detail"):
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
            left, right = st.columns([2, 3])
            with left:
                # Values are deliberately mixed types; casting keeps Arrow from
                # trying to make one numeric column out of prices and labels.
                st.dataframe(
                    pd.DataFrame(
                        [(label, "-" if value in (None, "") else str(value))
                         for label, value in details],
                        columns=["Field", "Value"],
                    ),
                    width="stretch",
                    hide_index=True,
                )
            with right:
                from app.ui.trade_chart import build_markers, render_chart

                render_chart(
                    symbol,
                    _trading_day(state),
                    markers=build_markers(trade),
                    key=f"position_chart_{trade.get('trade_id') or symbol}",
                )


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
            "scanner_recommendation": "Scanner Recommendation",
            "execution_eligibility": "Execution Eligibility",
            "execution_outcome": "Execution Outcome",
            "execution_reason": "Execution Reason",
            "trade_status": "Trade Status",
            "telegram_status": "Telegram Status",
            "telegram_reason": "Telegram Reason",
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
                "Scanner Recommendation": row.get("scanner_recommendation") or row.get("action_status"),
                "Execution Eligibility": row.get("execution_eligibility"),
                "Execution Outcome": row.get("execution_outcome") or event,
                "Execution Reason": row.get("execution_reason") or row.get("reason"),
                "Trade Status": row.get("trade_status"),
                "Telegram Status": row.get("telegram_status"),
                "Telegram Reason": row.get("telegram_reason"),
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
                "Scanner Recommendation": row.get("Scanner Recommendation") or event,
                "Execution Eligibility": row.get("Execution Eligibility"),
                "Execution Outcome": row.get("Execution Outcome"),
                "Execution Reason": row.get("Execution Reason"),
                "Trade Status": row.get("Trade Status"),
                "Telegram Status": row.get("Telegram Status"),
                "Telegram Reason": row.get("Telegram Reason"),
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
    from app.ui.trade_chart import tradingview_url

    rows = []
    for candidate in candidates[:10]:
        rows.append({
            "Rank": candidate.get("candidate_rank"),
            "Symbol": candidate.get("symbol"),
            "Chart": tradingview_url(candidate.get("symbol")),
            "Scanner Recommendation": _action_label(
                candidate.get("scanner_recommendation") or candidate.get("action")
            ),
            "Execution": _action_label(
                candidate.get("execution_outcome")
                or candidate.get("execution_eligibility")
                or "NOT_REQUESTED"
            ),
            "Execution Reason": candidate.get("execution_reason") or "-",
            "Trade": _action_label(candidate.get("trade_status") or "NOT_CREATED"),
            "Telegram": _action_label(candidate.get("telegram_status") or "NO_LIFECYCLE_EVENT"),
            "Setup": candidate.get("setup"),
            "TQS": candidate.get("trade_quality_score"),
            "RR": candidate.get("rr"),
            "Timing": candidate.get("entry_timing_grade"),
            "Holding": candidate.get("holding_profile") or "-",
        })
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        column_config={
            "Chart": st.column_config.LinkColumn(
                "Chart",
                display_text="TV",
                help="Open this symbol on TradingView at 5m",
            )
        },
    )

    symbols = [str(candidate.get("symbol")) for candidate in candidates[:10]
               if candidate.get("symbol")]
    if not symbols:
        return
    with st.expander("Candidate chart", expanded=False):
        chosen = st.selectbox("Symbol", symbols, key="opportunity_chart_symbol")
        candidate = next(
            (item for item in candidates if str(item.get("symbol")) == chosen), {}
        )
        from app.ui.trade_chart import build_markers, render_chart

        render_chart(
            chosen,
            _trading_day(state),
            markers=build_markers(candidate),
            key=f"opportunity_chart_{chosen}",
        )


def _closed_trades(trading_day):
    """Today's completed trades, entry and exit stitched back into one row.

    `paper_trade_events.csv` is append-only and one-sided: the OPEN row carries
    the entry time, the close row carries the exit. Charting a trade needs both.
    """
    events = _paper_events(trading_day)
    if events.empty or "event_type" not in events.columns:
        return []

    events["event_type"] = events["event_type"].astype(str).str.upper()
    opens = {
        str(row.get("trade_key")): row
        for _, row in events[events["event_type"] == "OPEN"].iterrows()
    }
    closes = events[events["event_type"].isin({"MANUAL_CLOSE", "AUTO_EXIT"})]

    trades = []
    for _, close in closes.iterrows():
        opened = opens.get(str(close.get("trade_key")), {})
        trades.append({
            "trade_key": close.get("trade_key"),
            "symbol": close.get("symbol"),
            "direction": close.get("direction"),
            "entry_price": close.get("entry_price"),
            "exit_price": close.get("exit_price"),
            "r_multiple": close.get("r_multiple"),
            "exit_reason": close.get("exit_reason"),
            "entry_time": opened.get("event_time_et") if len(opened) else None,
            "exit_time": close.get("event_time_et"),
            "closed_how": close.get("event_type"),
        })
    return trades


def _render_todays_result(state):
    """Post-market replacement for the Opportunity Board.

    After the close the board is stale by definition -- what the operator needs
    then is what the day actually did, and the chart to judge whether each exit
    was the right one.
    """
    import streamlit as st

    st.subheader("Today's Result")
    performance = (state or {}).get("today_performance") or {}
    trading_day = _trading_day(state)
    trades = _closed_trades(trading_day)

    from app.ui.components import status_card_grid

    average_r = _number(performance.get("average_r"))
    win_rate = _number(performance.get("win_rate"))
    capture = _number(performance.get("average_trend_capture"))
    too_early = int(performance.get("exit_too_early") or 0)
    status_card_grid([
        ("Completed", str(performance.get("completed_trades") or len(trades)), "neutral"),
        ("Avg R",
         "-" if average_r is None else f"{average_r:+.2f}",
         "neutral" if average_r is None else "ok" if average_r >= 0 else "bad"),
        ("Win rate",
         "-" if win_rate is None else f"{win_rate:.0f}%",
         "neutral" if win_rate is None else "ok" if win_rate >= 50 else "warn"),
        ("Trend capture",
         "-" if capture is None else f"{capture:.0f}%",
         "neutral" if capture is None else "ok" if capture >= 50 else "warn"),
        ("Exits too early", str(too_early), "warn" if too_early else "ok"),
        ("Excellent exits", str(performance.get("excellent_exits") or 0), "neutral"),
    ])

    if not trades:
        st.caption("No completed trades recorded for this day.")
        return

    from app.ui.trade_chart import build_markers, render_chart, tradingview_url

    table = pd.DataFrame([{
        "Symbol": trade["symbol"],
        "Chart": tradingview_url(trade["symbol"]),
        "Direction": trade["direction"],
        "Entry": trade["entry_price"],
        "Exit": trade["exit_price"],
        "R": trade["r_multiple"],
        "Closed By": trade["closed_how"],
        "Exit Reason": trade["exit_reason"],
    } for trade in trades])
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "Chart": st.column_config.LinkColumn(
                "Chart", display_text="TV", help="Open on TradingView at 5m"
            )
        },
    )

    labels = {
        f"{trade['symbol']} {trade['exit_time'] or ''} ({trade['r_multiple']}R)": trade
        for trade in trades
    }
    chosen = st.selectbox("Review trade", list(labels), key="result_chart_trade")
    trade = labels[chosen]
    render_chart(
        trade["symbol"],
        trading_day,
        markers=build_markers(trade),
        key=f"result_chart_{trade['trade_key']}",
    )
    st.caption(
        f"Exit reason: {trade['exit_reason'] or 'unknown'} | "
        f"closed by {trade['closed_how']}"
    )


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


def _unmanaged_position_alerts(state):
    """Open positions the last scan could not exit-evaluate.

    The scanner retries market data for held symbols; anything still listed here
    went a whole scan without an exit evaluation and needs operator attention.
    """
    unmanaged = (
        ((state or {}).get("scanner_health") or {}).get("paper_lifecycle") or {}
    ).get("unmanaged") or []
    return [
        (str(symbol), "Not managed last scan", "No market data for exit evaluation")
        for symbol in unmanaged
    ]


def _render_risk_monitor(state):
    import streamlit as st

    st.subheader("Active Risk Monitor")
    alerts = _unmanaged_position_alerts(state) + _risk_alerts(_active_positions())
    if not alerts:
        st.success("No active risks")
        return
    st.dataframe(
        pd.DataFrame(alerts, columns=["Symbol", "Risk", "Detail"]),
        width="stretch",
        hide_index=True,
    )


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

    _render_header(state)
    _render_market_pulse(state)

    left, right = st.columns([3, 2])
    with left:
        _render_live_positions(state)
    with right:
        _render_risk_monitor(state)

    if _is_post_market():
        _render_todays_result(state)
    else:
        _render_opportunity_board(state)

    # The activity feed reads the full activity trace -- 17,742 rows on
    # 2026-07-31 -- and re-sorts it on every rerun. It is a forensic tool, not an
    # operator one, so it no longer costs anything until it is opened.
    with st.expander("Activity feed", expanded=False):
        _render_activity_feed(state, df)


def render(state, df, refresh_state):
    _render_trader_workspace(state, df)


def render_from_state(state, refresh_state):
    _render_trader_workspace(state, pd.DataFrame())