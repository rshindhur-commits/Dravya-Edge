from __future__ import annotations

import pandas as pd


def run_auto_paper_entries(df, controls):

    from app.alerts.telegram_alerts import maybe_send_paper_entry_alert
    from app.runtime.paper_automation_support import (
        _allow_review_tv_chart_auto_paper,
        _annotate_paper_affordability_override,
        _auto_paper_entry_reason,
        _auto_paper_trade_count_today,
        _decision_log_rows,
        _paper_trade_candidates,
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

    candidates = _paper_trade_candidates(df)

    if candidates.empty:

        log_rows = _decision_log_rows(df)

        if controls["auto_paper_enabled"]:

            if df.empty:

                _record_auto_paper_decision(
                    "SYSTEM",
                    "SKIPPED",
                    "auto paper enabled; scanner output empty",
                    controls=controls
                )

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

                return []

            _record_auto_paper_decision(
                "SYSTEM",
                "SKIPPED",
                "auto paper enabled; no eligible entry candidates and no symbol rows found",
                controls=controls
            )

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

            return []

        _record_auto_paper_decision(
            "SYSTEM",
            "SKIPPED",
            "auto paper disabled; no current candidates and no symbol rows found",
            controls=controls
        )

        return []

    if not controls["auto_paper_enabled"]:

        for _, row in candidates.iterrows():

            _record_auto_paper_decision(
                row.get("Symbol"),
                "SKIPPED",
                "auto paper disabled",
                row,
                controls=controls
            )

        return []

    opened = []

    for _, row in candidates.iterrows():

        allowed, reason = _auto_paper_entry_reason(row, controls, paper_trades)

        if not allowed:

            _record_auto_paper_decision(
                row.get("Symbol"),
                "BLOCKED",
                reason,
                row,
                controls=controls
            )
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
        paper_trades = load_paper_trades()
        opened.append(row.get("Symbol"))

        if promote_suggestion_to_paper_trade:

            promote_suggestion_to_paper_trade(
                symbol=row_for_trade.get("Symbol"),
                direction=row_for_trade.get("Candidate Direction"),
                setup_type=row_for_trade.get("Entry"),
                option_ticker=row_for_trade.get("Option Ticker"),
                opened_at=opened_trade.get("opened_at"),
                trade_key=opened_trade.get("trade_key")
            )

        telegram_entry_result = maybe_send_paper_entry_alert(
            opened_trade,
            scanner_context,
            reason=f"{notes_prefix}: {reason}"
        )
        opened_log_row = row_for_trade.copy()
        opened_log_row["Paper Trade Opened"] = True
        opened_log_row["Real Trade Readiness"] = _real_trade_readiness(opened_log_row)
        opened_log_row["Real Entry Checklist"] = _real_entry_checklist(opened_log_row)

        _record_auto_paper_decision(
            row.get("Symbol"),
            "TELEGRAM_ENTRY_ALERT",
            telegram_entry_result.get("reason"),
            opened_log_row,
            controls=controls
        )
        _record_auto_paper_decision(
            row.get("Symbol"),
            "OPENED",
            reason,
            opened_log_row,
            trade=opened_trade,
            controls=controls
        )

        if _auto_paper_trade_count_today(paper_trades) >= controls["max_daily"]:

            break

    return opened


def run_auto_paper_exits(df, controls):

    if not controls["auto_exit_enabled"]:

        return []

    from app.runtime.paper_automation_support import (
        _auto_exit_reason,
        _close_paper_trade,
        _scanner_context_from_row,
    )
    from app.state.paper_trade_manager import load_paper_trades

    try:

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

        reason = _auto_exit_reason(trade, current_price, scanner_row, controls)

        if not reason:

            continue

        scanner_context = _scanner_context_from_row(scanner_row) if scanner_row is not None else None
        _close_paper_trade(
            symbol,
            current_price,
            scanner_context=scanner_context,
            exit_reason=reason
        )
        closed.append(symbol)

    return closed