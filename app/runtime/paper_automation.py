from __future__ import annotations

import pandas as pd


NO_GATE_VERDICT = "NO_GATE_VERDICT_RECORDED"


def _is_blank(value):

    if value is None:
        return True

    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass

    return str(value).strip() == ""


def audit_unrecorded_entry_recommendations(df, controls=None):
    """Guarantee every scanner entry recommendation carries an execution verdict.

    run_auto_paper_entries() accounts for every actionable row it sees, on every
    return path. But it is only reachable from scan finalization, so if it never
    runs -- a lost runtime job, an exception raised before it, or an older code
    path -- an ENTRY_RECOMMENDED candidate reaches the artifacts with no recorded
    gate verdict at all.

    That is exactly what happened to the 2026-07-29 PUT recommendations: three
    candidates passed every quality gate, never opened, and left no row in
    auto_paper_decisions.csv explaining why. Recording an explicit verdict keeps
    the gap diagnosable instead of silent.
    """

    from app.runtime.paper_automation_support import (
        _auto_paper_actionable_rows,
        _record_auto_paper_decision,
    )

    if df is None or df.empty or "Action Status" not in df.columns:
        return []

    actionable = _auto_paper_actionable_rows(df)

    if actionable.empty:
        return []

    unrecorded = []

    for _, row in actionable.iterrows():

        if "Execution Outcome" in row.index and not _is_blank(row.get("Execution Outcome")):
            continue

        symbol = row.get("Symbol")

        try:
            _record_auto_paper_decision(
                symbol,
                "SKIPPED",
                NO_GATE_VERDICT,
                row,
                controls=controls or {},
            )
        except Exception as exc:
            print(f"[AUTO PAPER AUDIT WARNING] {symbol}: {exc}")

        if row.name in df.index:
            df.loc[row.name, "Execution Eligibility"] = "NOT_EXECUTED"
            df.loc[row.name, "Execution Outcome"] = "SKIPPED"
            df.loc[row.name, "Execution Reason"] = NO_GATE_VERDICT
            df.loc[row.name, "Trade Status"] = "NOT_CREATED"

        unrecorded.append(symbol)

    if unrecorded:
        print(
            "[AUTO PAPER AUDIT] no gate verdict was recorded for "
            f"{len(unrecorded)} entry recommendation(s): "
            + ", ".join(str(symbol) for symbol in unrecorded)
        )

    return unrecorded


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
    from app.state.paper_trade_manager import (
        entry_fill_slip,
        load_paper_trades,
        max_entry_fill_slip_r,
        open_paper_trade,
    )

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
        # Refuse a fill that has drifted away from the geometry it was approved
        # on. The gate judged this candidate against the decision candle, and the
        # scan acting on it lands minutes later -- 5.6 on average over 40 trades,
        # up to 13. A candidate approved at exactly 2.00 RR whose price has since
        # moved 0.6R against it is a different trade, with its stop that much
        # closer, and nothing re-checked it.
        _fill_price, _fill_slip = entry_fill_slip(
            row_for_trade.get("Symbol"),
            row_for_trade.get("Candidate Direction"),
            row_for_trade.get("Candidate Entry Price"),
            row_for_trade.get("Candidate Stop Price"),
        )
        _slip_cap = max_entry_fill_slip_r()

        if _slip_cap and _fill_slip is not None and _fill_slip > _slip_cap:
            record_terminal(
                row,
                "BLOCKED",
                f"ENTRY_FILL_SLIPPED:{_fill_slip:+.2f}R>{_slip_cap:.2f}R",
            )
            continue

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
