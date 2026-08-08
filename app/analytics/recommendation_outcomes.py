from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pandas as pd

from app.db.persistence import db_writes_enabled
from app.storage.daily_paths import daily_path
from app.storage.incremental_csv import append_new_rows


HORIZON_SESSIONS = (5, 10)
ROOT_DIR = Path(__file__).resolve().parents[2]
LEDGER_DIR = ROOT_DIR / "data" / "recommendation_outcomes"
FACT_PATH = LEDGER_DIR / "recommendation_facts.csv"
OUTCOME_PATH = LEDGER_DIR / "recommendation_horizon_outcomes.csv"
SUMMARY_PATH = LEDGER_DIR / "recommendation_outcome_summary.csv"
HIGH_SCORE_COLUMNS = [
    "trading_day", "scan_id", "symbol", "direction", "setup", "setup_score",
    "candidate_rank", "scanner_recommendation", "execution_eligibility",
    "execution_outcome", "execution_reason", "trade_status", "telegram_status",
]


FACT_COLUMNS = [
    "recommendation_id", "trading_day", "scan_id", "recommended_at", "symbol",
    "direction", "setup", "candidate_rank", "top_candidate", "entry_price",
    "option_ticker", "option_entry_mid", "scanner_recommendation",
    "execution_eligibility", "execution_outcome", "execution_reason",
]
OUTCOME_COLUMNS = [
    "recommendation_id", "horizon_sessions", "evaluation_trading_day",
    "evaluated_at", "symbol", "direction", "entry_price", "evaluation_price",
    "underlying_return_pct", "directional_return_pct", "option_return_pct",
    "option_outcome_status",
]


def _rank_bucket(value):
    rank = _number(value)
    if rank is None:
        return "UNKNOWN"
    if rank <= 3:
        return "1-3"
    if rank <= 10:
        return "4-10"
    return "11+"


def _read_csv(path: Path):
    try:
        return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _value(row, *names):
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        if str(value).strip().lower() not in {"", "nan", "none"}:
            return value
    return None


def _number(value):
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def _recommendation_id(trading_day, scan_id, symbol, direction, setup):
    source = "|".join(map(str, [trading_day, scan_id, symbol, direction, setup]))
    return hashlib.sha256(source.encode()).hexdigest()[:24]


def build_recommendation_facts(rows, trading_day, scan_id, observed_at):
    facts = []
    for row in rows.to_dict("records") if hasattr(rows, "to_dict") else rows or []:
        recommendation = str(_value(row, "Scanner Recommendation", "Action Status") or "").upper()
        if recommendation not in {"ENTRY_RECOMMENDED", "ENTER", "ENTER_PAPER"}:
            continue
        symbol = _value(row, "Symbol", "symbol")
        direction = _value(row, "Candidate Direction", "direction")
        setup = _value(row, "Entry", "setup_type", "setup")
        if not symbol or not setup:
            continue
        facts.append({
            "recommendation_id": _recommendation_id(trading_day, scan_id, symbol, direction, setup),
            "trading_day": trading_day,
            "scan_id": _value(row, "Scan ID", "scan_id") or scan_id,
            "recommended_at": _value(row, "Current ET", "Data Timestamp ET") or observed_at,
            "symbol": str(symbol).upper(),
            "direction": str(direction or "").upper(),
            "setup": str(setup).upper(),
            "candidate_rank": _number(_value(row, "Candidate Rank", "candidate_rank")),
            "top_candidate": _value(row, "Top Candidate", "top_candidate"),
            "entry_price": _number(_value(row, "Candidate Entry Price", "Price", "entry_price")),
            "option_ticker": _value(row, "Option Ticker", "option_ticker"),
            "option_entry_mid": _number(_value(row, "Option Mid Price", "option_mid_price")),
            "scanner_recommendation": "ENTRY_RECOMMENDED",
            "execution_eligibility": _value(row, "Execution Eligibility", "execution_eligibility"),
            "execution_outcome": _value(row, "Execution Outcome", "execution_outcome"),
            "execution_reason": _value(row, "Execution Reason", "execution_reason"),
        })
    return pd.DataFrame(facts, columns=FACT_COLUMNS)


def _sessions_elapsed(recommendation_day, evaluation_day):
    start = pd.Timestamp(recommendation_day).date()
    end = pd.Timestamp(evaluation_day).date()
    if end <= start:
        return 0
    return len(pd.bdate_range(start + pd.offsets.BDay(1), end))


def _current_quotes(rows):
    quotes = {}
    for row in rows.to_dict("records") if hasattr(rows, "to_dict") else rows or []:
        symbol = _value(row, "Symbol", "symbol")
        price = _number(_value(row, "Price", "entry_price"))
        if symbol and price and price > 0:
            quotes[str(symbol).upper()] = {
                "price": price,
                "option_ticker": _value(row, "Option Ticker", "option_ticker"),
                "option_mid": _number(_value(row, "Option Mid Price", "option_mid_price")),
            }
    return quotes


def build_horizon_outcomes(facts, existing_outcomes, current_rows, evaluation_day, observed_at):
    quotes = _current_quotes(current_rows)
    existing_keys = {
        (str(row.get("recommendation_id")), int(row.get("horizon_sessions")))
        for row in existing_outcomes.to_dict("records") if not existing_outcomes.empty
    }
    outcomes = []
    for fact in facts.to_dict("records") if not facts.empty else []:
        quote = quotes.get(str(fact.get("symbol") or "").upper())
        entry_price = _number(fact.get("entry_price"))
        if not quote or not entry_price or entry_price <= 0:
            continue
        elapsed = _sessions_elapsed(fact.get("trading_day"), evaluation_day)
        for horizon in HORIZON_SESSIONS:
            key = (str(fact.get("recommendation_id")), horizon)
            if elapsed < horizon or key in existing_keys:
                continue
            underlying_return = ((quote["price"] - entry_price) / entry_price) * 100
            direction = str(fact.get("direction") or "").upper()
            directional_return = -underlying_return if direction in {"PUT", "SHORT", "BEARISH"} else underlying_return
            option_return = None
            option_status = "OPTION_CONTRACT_NOT_REFRESHED"
            option_entry = _number(fact.get("option_entry_mid"))
            if option_entry and option_entry > 0 and fact.get("option_ticker") == quote.get("option_ticker") and quote.get("option_mid"):
                option_return = ((quote["option_mid"] - option_entry) / option_entry) * 100
                option_status = "AVAILABLE"
            outcomes.append({
                "recommendation_id": fact.get("recommendation_id"),
                "horizon_sessions": horizon,
                "evaluation_trading_day": evaluation_day,
                "evaluated_at": observed_at,
                "symbol": fact.get("symbol"),
                "direction": direction,
                "entry_price": entry_price,
                "evaluation_price": quote["price"],
                "underlying_return_pct": round(underlying_return, 4),
                "directional_return_pct": round(directional_return, 4),
                "option_return_pct": round(option_return, 4) if option_return is not None else None,
                "option_outcome_status": option_status,
            })
    return pd.DataFrame(outcomes, columns=OUTCOME_COLUMNS)


def _append_unique(path, current, key_columns):
    """Store rows with unseen keys and return the whole ledger.

    This ledger spans every trading day rather than one, so it is the file that
    grows without bound, and the horizon maths downstream genuinely does need
    all of it. What it does not need is the old version's second copy: a concat
    of the full ledger plus a rewrite of the full ledger, on every scan.
    """
    existing = _read_csv(path)

    if current is None or current.empty:

        return existing

    if not append_new_rows(path, current, key_columns):

        return existing

    merged = pd.concat([existing, current], ignore_index=True, sort=False)

    if key_columns and all(column in merged.columns for column in key_columns):

        merged = merged.drop_duplicates(key_columns, keep="first")

    return merged


def build_recommendation_outcome_summary(facts, outcomes):
    if facts.empty or outcomes.empty:
        return pd.DataFrame(columns=[
            "horizon_sessions", "rank_bucket", "recommendations", "executed",
            "execution_rate", "directional_win_rate", "average_directional_return_pct",
            "option_return_coverage", "average_option_return_pct",
        ])
    summary = outcomes.merge(
        facts[["recommendation_id", "candidate_rank", "execution_outcome"]],
        on="recommendation_id",
        how="left",
    )
    summary["rank_bucket"] = summary["candidate_rank"].map(_rank_bucket)
    summary["executed"] = summary["execution_outcome"].astype(str).str.upper().eq("OPENED")
    summary["directional_win"] = summary["directional_return_pct"] > 0
    summary["option_available"] = summary["option_return_pct"].notna()
    rows = []
    for (horizon, bucket), group in summary.groupby(["horizon_sessions", "rank_bucket"], dropna=False):
        rows.append({
            "horizon_sessions": int(horizon),
            "rank_bucket": bucket,
            "recommendations": int(len(group)),
            "executed": int(group["executed"].sum()),
            "execution_rate": round(float(group["executed"].mean()), 4),
            "directional_win_rate": round(float(group["directional_win"].mean()), 4),
            "average_directional_return_pct": round(float(group["directional_return_pct"].mean()), 4),
            "option_return_coverage": round(float(group["option_available"].mean()), 4),
            "average_option_return_pct": round(float(group.loc[group["option_available"], "option_return_pct"].mean()), 4) if group["option_available"].any() else None,
        })
    return pd.DataFrame(rows)


def build_high_score_execution_audit(rows, trading_day, scan_id, min_setup_score=80):
    audit = []
    for row in rows.to_dict("records") if hasattr(rows, "to_dict") else rows or []:
        recommendation = str(_value(row, "Scanner Recommendation", "Action Status") or "").upper()
        setup_score = _number(_value(row, "Setup %", "15m Score", "setup_score"))
        if recommendation not in {"ENTRY_RECOMMENDED", "ENTER", "ENTER_PAPER"} or setup_score is None or setup_score < min_setup_score:
            continue
        audit.append({
            "trading_day": trading_day,
            "scan_id": _value(row, "Scan ID", "scan_id") or scan_id,
            "symbol": _value(row, "Symbol", "symbol"),
            "direction": _value(row, "Candidate Direction", "direction"),
            "setup": _value(row, "Entry", "setup_type", "setup"),
            "setup_score": setup_score,
            "candidate_rank": _number(_value(row, "Candidate Rank", "candidate_rank")),
            "scanner_recommendation": "ENTRY_RECOMMENDED",
            "execution_eligibility": _value(row, "Execution Eligibility", "execution_eligibility"),
            "execution_outcome": _value(row, "Execution Outcome", "execution_outcome"),
            "execution_reason": _value(row, "Execution Reason", "execution_reason"),
            "trade_status": _value(row, "Trade Status", "trade_status"),
            "telegram_status": _value(row, "Telegram Status", "telegram_status"),
        })
    return pd.DataFrame(audit, columns=HIGH_SCORE_COLUMNS)


def write_recommendation_outcomes(rows, trading_day, scan_id, observed_at):
    facts = build_recommendation_facts(rows, trading_day, scan_id, observed_at)
    ledger = _append_unique(FACT_PATH, facts, ["recommendation_id"])
    existing_outcomes = _read_csv(OUTCOME_PATH)
    new_outcomes = build_horizon_outcomes(ledger, existing_outcomes, rows, trading_day, observed_at)
    outcomes = _append_unique(OUTCOME_PATH, new_outcomes, ["recommendation_id", "horizon_sessions"])
    daily_outcomes = outcomes[outcomes.get("evaluation_trading_day", pd.Series(dtype=object)).astype(str) == str(trading_day)]
    daily_outcomes.to_csv(daily_path(trading_day, "recommendation_outcomes.csv"), index=False)
    summary = build_recommendation_outcome_summary(ledger, outcomes)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(SUMMARY_PATH, index=False)
    summary.to_csv(daily_path(trading_day, "recommendation_outcome_summary.csv"), index=False)
    high_score_audit = build_high_score_execution_audit(rows, trading_day, scan_id)
    high_score_audit.to_csv(daily_path(trading_day, "high_score_execution_audit.csv"), index=False)

    if db_writes_enabled():
        try:
            from app.db.recommendation_outcome_repository import RecommendationOutcomeRepository

            repository = RecommendationOutcomeRepository()
            repository.batch_insert_facts(facts.to_dict("records"))
            repository.batch_insert_outcomes(new_outcomes.to_dict("records"))
        except Exception:
            pass

    return {
        "facts_created": len(facts),
        "outcomes_created": len(new_outcomes),
        "fact_path": str(FACT_PATH),
        "outcome_path": str(OUTCOME_PATH),
        "summary_path": str(SUMMARY_PATH),
        "high_score_rows": len(high_score_audit),
    }