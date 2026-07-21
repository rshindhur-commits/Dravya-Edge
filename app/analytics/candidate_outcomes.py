from __future__ import annotations

import hashlib

import pandas as pd

from app.analytics.market_coverage import _first_existing, load_daily_inputs
from app.storage.daily_paths import daily_path


def _truth(value):
    return str(value or "").upper()


def build_candidate_outcomes(trading_day):
    inputs = load_daily_inputs(trading_day)
    audit = inputs.get("audit", pd.DataFrame())
    if audit is None or audit.empty:
        return pd.DataFrame()
    symbol = _first_existing(audit, ["symbol", "Symbol"])
    setup = _first_existing(audit, ["setup", "Entry"])
    action = _first_existing(audit, ["action", "Action Status"])
    outcome = _first_existing(audit, ["final_outcome", "Replay Outcome"])
    if not symbol:
        return pd.DataFrame()
    rows = []
    for _, row in audit.iterrows():
        action_value = _truth(row.get(action)) if action else ""
        outcome_value = _truth(row.get(outcome)) if outcome else ""
        entered = action_value in {"ENTER", "ENTER_PAPER", "OPENED"}
        winner = any(token in outcome_value for token in ["WIN", "TARGET", "PROFIT"])
        loser = any(token in outcome_value for token in ["LOSS", "STOP"])
        candidate_id = hashlib.sha256(f"{trading_day}|{row.get(symbol)}|{row.get(setup) if setup else ''}".encode()).hexdigest()[:24]
        telegram_sent = str(row.get("Telegram Sent")).lower() in {"true", "1", "yes"}
        rows.append({"candidate_id": candidate_id, "symbol": row.get(symbol), "setup": row.get(setup) if setup else None, "entered": entered, "profitable": winner if entered else None, "trend_developed": winner, "target_hit": "TARGET" in outcome_value, "stop_hit": "STOP" in outcome_value, "became_winner": winner, "became_loser": loser, "became_neutral": not winner and not loser, "telegram_sent": telegram_sent, "telegram_miss": winner and not telegram_sent, "false_alert": loser and telegram_sent})
    return pd.DataFrame(rows)


def write_candidate_outcomes(trading_day):
    outcomes = build_candidate_outcomes(trading_day)
    if outcomes.empty:
        return None
    path = daily_path(trading_day, "candidate_outcomes.csv")
    outcomes.to_csv(path, index=False)
    from app.db.candidate_outcome_repository import CandidateOutcomeRepository
    CandidateOutcomeRepository().batch_insert(outcomes.to_dict("records"))
    return path
