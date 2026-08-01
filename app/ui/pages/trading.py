"""The operator console.

Answers, in order: is the engine healthy, what is the book doing, what needs a
decision, and what did the day produce. Data access lives in
`app.ui.render_context` and the activity timeline in `app.ui.pages.activity`;
this module renders.
"""

from __future__ import annotations

import pandas as pd

from app.ui.render_context import STALE_SCAN_MINUTES, RenderContext, is_post_market


def _action_label(value):
    return str(value or "NO TRADE").replace("_", " ")


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _health_cells(context):
    """The six facts an operator needs before trusting anything else on the page.

    Tones are stated here rather than inferred from the value text the way
    `_render_compact_card_grid` does: "990m ago" carries no keyword a matcher
    could read.
    """
    engine = context.engine
    sent = sum(1 for row in context.telegram if row.get("event") == "SENT")
    failed = sum(1 for row in context.telegram if row.get("event") == "FAILED")
    scan_age = context.scan_age_minutes
    # "Is an engine scanning", not "is there a thread in this process". The
    # worker has no thread here and never will.
    alive = bool(engine.get("running") or engine.get("thread_alive"))
    failures = int(engine.get("failures") or 0)
    max_daily = context.max_daily_entries
    entries = context.entries_used

    if scan_age is None:
        scan_tone, scan_text = ("neutral" if context.post_market else "bad"), "never"
    else:
        scan_text = f"{scan_age:.0f}m ago"
        # After the close the engine is meant to be quiet, so an ageing scan is
        # only a fault during the session.
        scan_tone = (
            "neutral" if context.post_market
            else "ok" if scan_age <= STALE_SCAN_MINUTES
            else "warn" if scan_age <= STALE_SCAN_MINUTES * 3
            else "bad"
        )

    scans = int(engine.get("scans") or 0)
    archived = context.archived_scans

    if archived is None:
        archive_text, archive_tone = "unavailable", "neutral"
    elif archived == 0 and scans:
        # The 2026-07-31 signature: the engine ran all day and nothing reached
        # the archive, so once the container was recycled the session was gone.
        archive_text, archive_tone = "NOT RECORDING", "bad"
    else:
        archive_text = f"{archived} scans"
        archive_tone = "warn" if scans and archived * 2 < scans else "ok"

    return [
        ("Engine",
         str(engine.get("status") or "IDLE") if alive else "NOT RUNNING",
         "ok" if alive else "bad"),
        ("Last scan", scan_text, scan_tone),
        ("Scans / fails",
         f"{scans} / {failures}",
         "warn" if failures else "ok"),
        ("Archive", archive_text, archive_tone),
        ("DB writes",
         "ACTIVE" if context.db_writes_active else "OFF",
         "ok" if context.db_writes_active else "bad"),
        ("Telegram",
         f"{sent} sent / {failed} failed",
         "bad" if failed else "ok"),
        ("Book",
         f"{len(context.positions)} open | {entries}"
         + (f"/{max_daily}" if max_daily else "") + " entries",
         "warn" if max_daily and entries >= max_daily else "neutral"),
    ]


def _render_header(context):
    """Page identity, session mode, and the six facts that gate trust."""
    import streamlit as st

    from app.ui.components import operator_bar, status_card_grid

    engine = context.engine
    # `running`, not `thread_alive`. Once scanning moved to the Render worker
    # there is no supervisor thread in this process by design, and asking for one
    # made a healthy system read as ENGINE DOWN permanently -- while the sidebar,
    # which had already been taught about the worker, reported it running two
    # feet away. See engine_status().
    running = bool(engine.get("running") or engine.get("thread_alive"))
    owner = str(engine.get("owner") or "").strip()

    operator_bar("Operator Console", [
        (context.trading_day, "neutral"),
        ("POST-MARKET" if context.post_market else "LIVE SESSION",
         "post" if context.post_market else "live"),
        ("ENGINE DOWN", "bad") if not running
        else ("ENGINE " + str(engine.get("status") or "IDLE")
              + (f" · {owner}" if owner and engine.get("remote") else ""), "live"),
    ])

    status_card_grid(_health_cells(context))

    if not running:
        st.error(
            "No scan engine is reporting. Nothing is scanning — check the Render "
            "worker, or set SCAN_ENGINE_OWNER back to `dashboard` to scan from "
            "here. Restarting Streamlit only helps if this app owns scanning."
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


def _render_live_positions(context):
    import streamlit as st

    from app.ui.components import position_card

    st.subheader("Book")
    if not context.positions:
        st.caption("No active paper positions.")
        return

    delivered = context.delivered_trade_ids

    for trade in context.positions:
        trade_id = str(trade.get("trade_id") or "")
        position_card(
            trade.get("symbol") or "Unknown",
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

    for trade in context.positions:
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
                    context.trading_day,
                    markers=build_markers(trade),
                    key=f"position_chart_{trade.get('trade_id') or symbol}",
                )


def _render_opportunity_board(context):
    import streamlit as st

    from app.ui.trade_chart import tradingview_url

    st.subheader("Current Opportunity Board")
    candidates = (context.state.get("decision_center") or {}).get("ranked_opportunities") or []
    if not candidates:
        st.caption("No ranked opportunities in the latest scan.")
        return

    rows = [{
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
    } for candidate in candidates[:10]]

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
            context.trading_day,
            markers=build_markers(candidate),
            key=f"opportunity_chart_{chosen}",
        )


def _render_todays_result(context):
    """Post-market replacement for the Opportunity Board.

    After the close the board is stale by definition -- what the operator needs
    then is what the day actually did, and the chart to judge whether each exit
    was the right one.
    """
    import streamlit as st

    from app.ui.components import status_card_grid

    st.subheader("Today's Result")
    performance = (context.state or {}).get("today_performance") or {}
    trades = context.closed_trades

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

    st.dataframe(
        pd.DataFrame([{
            "Symbol": trade["symbol"],
            "Chart": tradingview_url(trade["symbol"]),
            "Direction": trade["direction"],
            "Entry": trade["entry_price"],
            "Exit": trade["exit_price"],
            "R": trade["r_multiple"],
            "Closed By": trade["closed_how"],
            "Exit Reason": trade["exit_reason"],
        } for trade in trades]),
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
        context.trading_day,
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


def _render_risk_monitor(context):
    import streamlit as st

    st.subheader("Active Risk Monitor")
    alerts = _unmanaged_position_alerts(context.state) + _risk_alerts(context.positions)
    if not alerts:
        st.success("No active risks")
        return
    st.dataframe(
        pd.DataFrame(alerts, columns=["Symbol", "Risk", "Detail"]),
        width="stretch",
        hide_index=True,
    )


def _render_market_pulse(context):
    import streamlit as st

    summary = context.state.get("summary") or {}
    suggestions = sum(
        1 for candidate in (context.state.get("decision_center") or {}).get("ranked_opportunities") or []
        if str(candidate.get("action") or "").upper() in {"ENTER", "ENTER_PAPER", "REVIEW_TV_CHART"}
    )
    st.caption(
        f"{context.state.get('market_bias') or 'MIXED'} | {summary.get('scanned', 0)} scanned | "
        f"{summary.get('bullish', 0)} bullish | {summary.get('bearish', 0)} bearish | "
        f"{suggestions} suggestions"
    )


def _render_trader_workspace(context):
    import streamlit as st

    from app.ui.pages import activity

    _render_header(context)
    _render_market_pulse(context)

    left, right = st.columns([3, 2])
    with left:
        _render_live_positions(context)
    with right:
        _render_risk_monitor(context)

    if context.post_market:
        _render_todays_result(context)
    else:
        _render_opportunity_board(context)

    # The activity feed reads the full activity trace -- 17,742 rows on
    # 2026-07-31 -- and re-sorts it. It is a forensic tool, not an operator one,
    # so it costs nothing until it is opened.
    with st.expander("Activity feed", expanded=False):
        activity.render(context)


def render(state, df, refresh_state):
    _render_trader_workspace(RenderContext(state=state or {}, df=df))


def render_from_state(state, refresh_state):
    _render_trader_workspace(RenderContext(state=state or {}, df=pd.DataFrame()))
