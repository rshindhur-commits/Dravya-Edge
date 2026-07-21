from __future__ import annotations

from enum import Enum

import pandas as pd

from app.analytics.market_coverage import _first_existing, _normalize_symbol, _safe_numeric, load_daily_inputs


class MissReason(Enum):

    MOMENTUM = "MOMENTUM"
    ENTRY = "ENTRY"
    RISK = "RISK"
    OPTION = "OPTION"
    AFFORDABILITY = "AFFORDABILITY"
    EXIT = "EXIT"
    UNKNOWN = "UNKNOWN"


RECOMMENDATIONS = {
    MissReason.MOMENTUM: "Observe scanner momentum detection",
    MissReason.ENTRY: "Review entry timing and persistence",
    MissReason.RISK: "Review risk/RR gate evidence",
    MissReason.OPTION: "Review option quote, spread, and liquidity rejection",
    MissReason.AFFORDABILITY: "Keep paper override; real readiness remains strict",
    MissReason.EXIT: "Review exit timing and trend guard",
    MissReason.UNKNOWN: "Inspect full candidate snapshot and chart",
}


def _classify_row(row) -> MissReason:

    text = " ".join(
        str(row.get(column) or "")
        for column in [
            "blocked_reason",
            "Blocked By",
            "Action Reason",
            "Option Rejection Reason",
            "action",
            "Action Status",
            "exit_reason",
        ]
    ).upper()
    setup = str(row.get("setup") or row.get("Entry") or "").upper()
    score = row.get("score", row.get("15m Score"))

    try:

        score_value = float(score)

    except Exception:

        score_value = None

    if any(token in text for token in ["OPTION", "QUOTE", "SPREAD", "BID", "ASK", "LIQUID"]):

        return MissReason.OPTION

    if any(token in text for token in ["AFFORD", "EXPENSIVE", "CAPITAL", "CONTRACT COST"]):

        return MissReason.AFFORDABILITY

    if any(token in text for token in ["RR", "RISK", "GEOMETRY", "STOP", "TARGET"]):

        return MissReason.RISK

    if any(token in text for token in ["EXIT", "EARLY", "EMA9 INVALIDATION", "VWAP INVALIDATION"]):

        return MissReason.EXIT

    if setup in {"", "NO_ENTRY", "NO_SETUP", "NAN", "NONE"}:

        return MissReason.ENTRY

    if score_value is not None and abs(score_value) < 4:

        return MissReason.MOMENTUM

    if any(token in text for token in ["WAIT", "OPENING_RANGE", "REVIEW", "LATE", "CHASE"]):

        return MissReason.ENTRY

    return MissReason.UNKNOWN


def build_loss_attribution(report_date: str, move_threshold_pct: float = 2.0):

    inputs = load_daily_inputs(report_date)
    audit = inputs.get("audit", pd.DataFrame())

    if audit is None or audit.empty:

        return pd.DataFrame()

    rows = audit.copy()
    symbol_column = _first_existing(rows, ["symbol", "Symbol"])
    move_column = _first_existing(rows, ["market_move_pct", "Symbol Move %"])

    if not symbol_column or not move_column:

        return pd.DataFrame()

    rows["symbol"] = rows[symbol_column].map(_normalize_symbol)
    rows["_move_pct"] = _safe_numeric(rows[move_column]).fillna(0)
    rows = rows[rows["_move_pct"].abs() >= move_threshold_pct].copy()

    if rows.empty:

        return pd.DataFrame()

    action_column = _first_existing(rows, ["action", "Action Status"])
    outcome_column = _first_existing(rows, ["final_outcome", "Replay Outcome"])
    action = rows[action_column].astype(str).str.upper() if action_column else pd.Series("", index=rows.index)
    outcome = rows[outcome_column].astype(str).str.upper() if outcome_column else pd.Series("", index=rows.index)
    missed_mask = ~action.isin(["ENTER", "ENTER_PAPER", "OPENED"])
    winner_mask = outcome.str.contains("WIN|TARGET|PROFIT", regex=True, na=False)
    missed = rows[missed_mask | winner_mask].copy()

    if missed.empty:

        return pd.DataFrame()

    records = []

    for _, row in missed.iterrows():

        reason = _classify_row(row)
        blocked_reason = row.get("blocked_reason") or row.get("Blocked By") or row.get("Action Reason")
        rule = None
        threshold = None
        would_have_passed_if = None

        if str(blocked_reason or "").upper().startswith("RR"):
            rule = "RR Threshold"
            threshold = row.get("Candidate RR") or row.get("Risk Reward")
            would_have_passed_if = str(threshold or "")
        elif str(blocked_reason or "").upper().startswith("SETUP"):
            rule = "Setup Threshold"
            threshold = row.get("Setup %") or row.get("Setup")
            would_have_passed_if = str(threshold or "")
        elif str(blocked_reason or "").upper().startswith("OPTION"):
            rule = "Option Quality"
            threshold = row.get("Option Quality Score")
            would_have_passed_if = str(threshold or "")
        else:
            rule = "Gate"
            threshold = blocked_reason
            would_have_passed_if = None

        records.append({
            "symbol": row.get("symbol"),
            "setup": row.get("setup") or row.get("Entry"),
            "move_pct": row.get("_move_pct"),
            "reason": reason.value,
            "recommendation": RECOMMENDATIONS[reason],
            "blocked_reason": blocked_reason,
            "action": row.get("action") or row.get("Action Status"),
            "top_candidate": row.get("top_candidate") or row.get("Top Candidate"),
            "root_cause": blocked_reason,
            "blocked_by": blocked_reason,
            "rule": rule,
            "threshold": threshold,
            "would_have_passed_if": would_have_passed_if,
            "confidence": "MEDIUM",
        })

    return pd.DataFrame(records)
