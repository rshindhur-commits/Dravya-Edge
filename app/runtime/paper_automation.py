from __future__ import annotations

import pandas as pd


def run_auto_paper_entries(df, controls):

    from app.alerts.telegram_alerts import maybe_send_paper_entry_alert
    from app.runtime.paper_automation_support import (
        _allow_review_tv_chart_auto_paper,
        _annotate_paper_affordability_override,
        _auto_paper_entry_reason,
        _auto_paper_trade_count_today,
        auto_paper_session_block_reason,
        should_record_auto_paper_session_skip,
        _decision_log_rows,
        _auto_paper_actionable_rows,
        _paper_trade_candidates,
        _paper_candidate_filter_reason,
        _real_entry_checklist,
        _real_trade_readiness,
        _record_auto_paper_decision,
        _safe_float,
        _scanner_block_reason,
        _scanner_context_from_row,
    )
    from app.state.paper_trade_manager import load_paper_trades, open_paper_trade

    try:

        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    actionable_rows = _auto_paper_actionable_rows(df)
    accounted = set()
    outcomes = {"OPENED": 0, "BLOCKED": 0, "SKIPPED": 0}

    def record_terminal(row, decision, reason, trade=None):
        _record_auto_paper_decision(
            row.get("Symbol"),
            decision,
            reason,
            row,
            trade=trade,
            controls=controls,
        )
        execution_eligibility = {
            "OPENED": "ELIGIBLE",
            "BLOCKED": "INELIGIBLE",
            "SKIPPED": "NOT_EXECUTED",
        }[decision]
        df.loc[row.name, "Execution Eligibility"] = execution_eligibility
        df.loc[row.name, "Execution Outcome"] = decision
        df.loc[row.name, "Execution Reason"] = reason
        df.loc[row.name, "Trade Status"] = "OPEN" if decision == "OPENED" else "NOT_CREATED"
        if decision != "OPENED":
            df.loc[row.name, "Telegram Status"] = "NO_LIFECYCLE_EVENT"
            df.loc[row.name, "Telegram Reason"] = "NO_LIFECYCLE_EVENT"
        accounted.add(row.name)
        outcomes[decision] += 1

    def report_accounting():
        unaccounted = actionable_rows.loc[
            ~actionable_rows.index.isin(accounted)
        ]
        for _, row in unaccounted.iterrows():
            record_terminal(row, "BLOCKED", "UNACCOUNTED_AUTO_PAPER_CANDIDATE")
        print(
            "[AUTO PAPER ACCOUNTING] "
            f"actionable={len(actionable_rows)} "
            f"opened={outcomes['OPENED']} "
            f"blocked={outcomes['BLOCKED']} "
            f"skipped={outcomes['SKIPPED']} "
            f"unaccounted={len(unaccounted)}"
        )

    if controls["auto_paper_enabled"]:
        session_block = auto_paper_session_block_reason()
        if session_block and should_record_auto_paper_session_skip(session_block):
            _record_auto_paper_decision(
                "SYSTEM",
                "SKIPPED",
                session_block,
                controls=controls,
            )
        if session_block:
            for _, row in actionable_rows.iterrows():
                record_terminal(row, "SKIPPED", session_block)
            report_accounting()
            return []

    candidates = _paper_trade_candidates(df)
    excluded_actionable = actionable_rows.loc[
        ~actionable_rows.index.isin(candidates.index)
    ]
    for _, row in excluded_actionable.iterrows():
        record_terminal(row, "SKIPPED", _paper_candidate_filter_reason(row))

    if candidates.empty:

        log_rows = _decision_log_rows(df).drop(index=actionable_rows.index, errors="ignore")

        if controls["auto_paper_enabled"]:

            if df.empty:

                _record_auto_paper_decision(
                    "SYSTEM",
                    "SKIPPED",
                    "auto paper enabled; scanner output empty",
                    controls=controls
                )

                report_accounting()
                return []

            market_closed_rows = pd.DataFrame()

            if "Action Status" in df.columns:

                market_closed_rows = df[
                    df["Action Status"].isin([
                        "NO_TRADE_MARKET_CLOSED",
                        "OPTION_MARKET_CLOSED"
                    ])
                ]

            if not market_closed_rows.empty:

                market_log_rows = _decision_log_rows(market_closed_rows)

                if market_log_rows.empty:

                    market_log_rows = log_rows

                if not market_log_rows.empty:

                    for _, row in market_log_rows.iterrows():

                        _record_auto_paper_decision(
                            row.get("Symbol"),
                            "SKIPPED",
                            "market closed",
                            row,
                            controls=controls
                        )
                else:

                    _record_auto_paper_decision(
                        "SYSTEM",
                        "SKIPPED",
                        "auto paper enabled; market closed but no symbol rows found",
                        controls=controls
                    )

                report_accounting()
                return []

            if not log_rows.empty:

                for _, row in log_rows.iterrows():

                    _record_auto_paper_decision(
                        row.get("Symbol"),
                        "SKIPPED",
                        _scanner_block_reason(row),
                        row,
                        controls=controls
                    )

                report_accounting()
                return []

            _record_auto_paper_decision(
                "SYSTEM",
                "SKIPPED",
                "auto paper enabled; no eligible entry candidates and no symbol rows found",
                controls=controls
            )

            report_accounting()
            return []

        if not log_rows.empty:

            for _, row in log_rows.iterrows():

                _record_auto_paper_decision(
                    row.get("Symbol"),
                    "SKIPPED",
                    "auto paper disabled",
                    row,
                    controls=controls
                )

            report_accounting()
            return []

        _record_auto_paper_decision(
            "SYSTEM",
            "SKIPPED",
            "auto paper disabled; no current candidates and no symbol rows found",
            controls=controls
        )

        report_accounting()
        return []

    if not controls["auto_paper_enabled"]:

        for _, row in candidates.iterrows():

            record_terminal(row, "SKIPPED", "auto paper disabled")

        report_accounting()
        return []

    opened = []

    for _, row in candidates.iterrows():

        allowed, reason = _auto_paper_entry_reason(row, controls, paper_trades)

        if not allowed:

            record_terminal(row, "BLOCKED", reason)
            continue

        try:

            from app.state.suggested_trade_manager import promote_suggestion_to_paper_trade

        except Exception:

            promote_suggestion_to_paper_trade = None

        row_for_trade = _annotate_paper_affordability_override(row)
        scanner_context = _scanner_context_from_row(row_for_trade)
        is_review_validation = (
            str(row_for_trade.get("Action Status") or "").strip().upper() == "REVIEW_TV_CHART"
            and _allow_review_tv_chart_auto_paper()
        )
        entry_source = "AUTO_PAPER_REVIEW_VALIDATION" if is_review_validation else "AUTO_PAPER"
        notes_prefix = "Auto paper review validation entry" if is_review_validation else "Auto paper entry"
        spread_note = "; missing spread allowed for paper" if _safe_float(row.get("Option Spread %"), None) is None else ""
        try:
            opened_trade = open_paper_trade(
                symbol=row_for_trade.get("Symbol"),
                direction=row_for_trade.get("Candidate Direction"),
                entry_price=row_for_trade.get("Candidate Entry Price"),
                stop_loss=row_for_trade.get("Candidate Stop Price"),
                take_profit=row_for_trade.get("Candidate Target Price"),
                entry_type=row_for_trade.get("Entry"),
                option_ticker=row_for_trade.get("Option Ticker"),
                option_bid=row_for_trade.get("Option Bid"),
                option_ask=row_for_trade.get("Option Ask"),
                notes=f"{notes_prefix}: {reason}{spread_note}",
                scanner_context=scanner_context,
                entry_source=entry_source,
                trade_mode="PAPER",
                include_in_strategy_stats=not is_review_validation
            )
        except Exception as exc:
            record_terminal(row, "BLOCKED", f"PAPER_OPEN_FAILED:{type(exc).__name__}:{exc}")
            continue
        opened_log_row = row_for_trade.copy()
        opened_log_row["Paper Trade Opened"] = True
        opened_log_row["Real Trade Readiness"] = _real_trade_readiness(opened_log_row)
        opened_log_row["Real Entry Checklist"] = _real_entry_checklist(opened_log_row)
        record_terminal(opened_log_row, "OPENED", reason, trade=opened_trade)
        paper_trades = load_paper_trades()
        opened.append(row.get("Symbol"))

        if promote_suggestion_to_paper_trade:

            try:
                promote_suggestion_to_paper_trade(
                    symbol=row_for_trade.get("Symbol"),
                    direction=row_for_trade.get("Candidate Direction"),
                    setup_type=row_for_trade.get("Entry"),
                    option_ticker=row_for_trade.get("Option Ticker"),
                    opened_at=opened_trade.get("opened_at"),
                    trade_key=opened_trade.get("trade_key")
                )
            except Exception as exc:
                print(f"[AUTO PAPER PROMOTION WARNING] {row.get('Symbol')}: {exc}")

        try:
            telegram_entry_result = maybe_send_paper_entry_alert(
                opened_trade,
                scanner_context,
                reason=f"{notes_prefix}: {reason}"
            )
        except Exception as exc:
            telegram_entry_result = {"reason": f"TELEGRAM_ENTRY_ALERT_FAILED:{type(exc).__name__}:{exc}"}

        telegram_sent = bool(telegram_entry_result.get("sent"))
        df.loc[row.name, "Telegram Status"] = "SENT" if telegram_sent else "NOT_SENT"
        df.loc[row.name, "Telegram Reason"] = telegram_entry_result.get("reason")

        _record_auto_paper_decision(
            row.get("Symbol"),
            "TELEGRAM_ENTRY_ALERT",
            telegram_entry_result.get("reason"),
            opened_log_row,
            controls=controls
        )

        if _auto_paper_trade_count_today(paper_trades) >= controls["max_daily"]:

            remaining = candidates.loc[~candidates.index.isin(accounted)]
            for _, remaining_row in remaining.iterrows():
                record_terminal(remaining_row, "BLOCKED", "DAILY_AUTO_PAPER_LIMIT_REACHED")
            break

    report_accounting()
    return opened


def run_auto_paper_exits(df, controls):
    from app.runtime.paper_automation_support import (
        _auto_exit_reason,
        _close_paper_trade,
        _scanner_context_from_row,
    )
    from app.alerts.telegram_alerts import (
        maybe_send_multiday_position_continue_alert,
        maybe_send_paper_trade_update_alert,
    )
    from app.state.paper_trade_manager import load_paper_trades
    from app.state.trade_session_lifecycle import initialize_session_lifecycle

    try:

        session_lifecycle = initialize_session_lifecycle(
            restore_multiday_positions=controls.get("restore_multiday_positions", True),
        )
        for carried_trade in session_lifecycle.get("carried_intraday_positions", []):
            print(
                "[INTRADAY OVERNIGHT CARRY] "
                f"{carried_trade.get('symbol')}: "
                f"{carried_trade.get('overnight_carry_warning')}"
            )
        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    current_prices = {}

    if not df.empty and "Symbol" in df.columns:

        current_prices = df.set_index("Symbol")["Price"].to_dict()

    closed = []

    for _, trade in paper_trades.items():

        symbol = trade.get("symbol")

        if trade.get("status") != "OPEN":

            continue

        current_price = current_prices.get(symbol, trade.get("entry_price"))
        scanner_row = None

        if not df.empty and "Symbol" in df.columns:

            matching_rows = df[df["Symbol"] == symbol]

            if not matching_rows.empty:

                scanner_row = matching_rows.iloc[0]

        scanner_context = (
            _scanner_context_from_row(scanner_row)
            if scanner_row is not None
            else None
        )
        reason = _auto_exit_reason(trade, current_price, scanner_row, controls)

        if not reason:

            try:

                maybe_send_multiday_position_continue_alert(
                    trade,
                    current_price,
                    scanner_context,
                )
                maybe_send_paper_trade_update_alert(
                    trade,
                    current_price,
                    scanner_context,
                )

            except Exception as exc:

                print(
                    f"[PAPER TELEGRAM UPDATE ALERT ERROR] "
                    f"{symbol}: {exc}"
                )

            continue

        _close_paper_trade(
            symbol,
            current_price,
            scanner_context=scanner_context,
            exit_reason=reason
        )
        closed.append(symbol)

    return closed