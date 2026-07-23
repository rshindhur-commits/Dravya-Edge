from __future__ import annotations

import json

import pandas as pd

from app.analytics.candidate_evidence import load_candidate_evidence
from app.storage.daily_paths import daily_path


GOOD_CANDIDATE_COLUMNS = [
    "candidate_id", "symbol", "setup", "direction", "rr", "setup_score",
    "option_quality", "trend_health", "top_candidate", "decision",
    "rule_evaluation", "replay_outcome", "candidate_outcome", "verdict",
    "missed_winner_type", "recommended_action",
]


def _number(series):
    return pd.to_numeric(series, errors="coerce")


def _truth(value):
    return str(value).strip().lower() in {"true", "1", "yes"}


def _reason_type(row):
    reason = " ".join([
        str(row.get("rule_evaluation") or ""),
        str(row.get("engineering_root_cause") or ""),
        str(row.get("quote_freshness") or ""),
    ]).upper()
    if any(token in reason for token in ["UNKNOWN_QUOTE", "TIMESTAMP", "DATA", "PROVIDER_ERROR"]):
        return "DATA_QUALITY_MISS", "Investigate market data and timestamp provenance."
    if any(token in reason for token in ["STALE_QUOTE", "DELAYED", "REALTIME", "QUOTE"]):
        return "OPERATIONAL_MISS", "Inspect quote attribution before changing a strategy rule."
    if any(token in reason for token in ["RR", "RISK", "GEOMETRY", "STOP"]):
        return "RISK_MISS", "Review thresholds only after sufficient evidence."
    return "INTENTIONAL_SKIP", "No action unless this pattern persists across evidence days."


def build_candidate_intelligence(evidence):
    rows = evidence.copy() if evidence is not None else pd.DataFrame()
    if rows.empty:
        return {
            "summary": {},
            "good_candidates": pd.DataFrame(columns=GOOD_CANDIDATE_COLUMNS),
            "high_quality_blocked": pd.DataFrame(columns=GOOD_CANDIDATE_COLUMNS),
            "investigation_queue": pd.DataFrame(columns=GOOD_CANDIDATE_COLUMNS),
            "outcome_matrix": pd.DataFrame(),
            "missed_winner_breakdown": pd.DataFrame(),
            "all_candidates": rows,
        }

    rows = rows.copy()

    for column in GOOD_CANDIDATE_COLUMNS:

        if column not in rows.columns:

            rows[column] = None

    for column in ["rr", "setup_score", "option_quality"]:
        rows[column] = _number(rows.get(column, pd.Series(index=rows.index, dtype=float)))
    rows["entered"] = rows.get("entered", pd.Series(False, index=rows.index)).map(_truth)
    rows["winner"] = rows.get("winner", pd.Series(False, index=rows.index)).map(_truth)
    rows["target_first"] = rows.get("target_first", pd.Series(False, index=rows.index)).map(_truth)
    rows["stop_first"] = rows.get("stop_first", pd.Series(False, index=rows.index)).map(_truth)
    trend = rows.get("trend_health", pd.Series("", index=rows.index)).astype(str).str.upper()
    rows["is_good_candidate"] = (
        rows["setup_score"].ge(70)
        & rows["rr"].ge(1.8)
        & rows["option_quality"].ge(80)
        & trend.isin({"HEALTHY", "STRONG"})
    )
    rows["is_blocked"] = (~rows["entered"]) & rows.get("rule_evaluation", pd.Series("", index=rows.index)).fillna("").astype(str).str.strip().ne("")
    rows["candidate_outcome"] = "NEUTRAL"
    rows.loc[rows["entered"] & rows["winner"], "candidate_outcome"] = "OPENED_WON"
    rows.loc[rows["entered"] & ~rows["winner"], "candidate_outcome"] = "OPENED_LOST"
    rows.loc[~rows["entered"] & rows["stop_first"], "candidate_outcome"] = "CORRECT_SKIP"
    rows.loc[~rows["entered"] & rows["target_first"], "candidate_outcome"] = "MISSED_WINNER"
    rows["verdict"] = rows["candidate_outcome"].replace({
        "CORRECT_SKIP": "Correct Block",
        "MISSED_WINNER": "Missed Winner",
        "OPENED_WON": "Excellent",
        "OPENED_LOST": "Opened And Lost",
        "NEUTRAL": "Neutral",
    })
    missed = rows["candidate_outcome"].eq("MISSED_WINNER")
    classifications = rows[missed].apply(_reason_type, axis=1)
    rows["missed_winner_type"] = None
    rows["recommended_action"] = "Observe; DO NOT CHANGE RULE."
    if not classifications.empty:
        rows.loc[missed, "missed_winner_type"] = [item[0] for item in classifications]
        rows.loc[missed, "recommended_action"] = [item[1] for item in classifications]
    good = rows[rows["is_good_candidate"]].copy()
    blocked = good[good["is_blocked"]].copy()
    investigate = rows[
        (rows["rr"].ge(3) & rows["setup_score"].ge(70) & ~rows["entered"])
        | (rows["entered"] & rows["candidate_outcome"].eq("OPENED_LOST") & rows["setup_score"].ge(70))
    ].copy()
    investigate["why_investigate"] = investigate.apply(
        lambda row: "High RR blocked by " + str(row.get("rule_evaluation") or "no recorded block")
        if not row["entered"]
        else "Entered and lost despite high-quality setup",
        axis=1,
    )
    investigate = investigate.sort_values(["rr", "setup_score"], ascending=False).head(10)
    matrix = good.groupby(["candidate_outcome", "is_blocked"], dropna=False).size().reset_index(name="count")
    missed_breakdown = good[good["candidate_outcome"].eq("MISSED_WINNER")].groupby(
        ["rule_evaluation", "missed_winner_type"], dropna=False
    ).size().reset_index(name="missed_winners").sort_values("missed_winners", ascending=False)
    summary = {
        "good_candidates": int(len(good)),
        "opened": int(good["entered"].sum()),
        "skipped": int((~good["entered"] & ~good["is_blocked"]).sum()),
        "blocked": int(good["is_blocked"].sum()),
        "correct_skips": int(good["candidate_outcome"].eq("CORRECT_SKIP").sum()),
        "correct_blocks": int((good["candidate_outcome"].eq("CORRECT_SKIP") & good["is_blocked"]).sum()),
        "missed_winners": int(good["candidate_outcome"].eq("MISSED_WINNER").sum()),
        "investigate": int(len(investigate)),
    }
    return {
        "summary": summary,
        "good_candidates": good[GOOD_CANDIDATE_COLUMNS],
        "high_quality_blocked": blocked[GOOD_CANDIDATE_COLUMNS],
        "investigation_queue": investigate[GOOD_CANDIDATE_COLUMNS + ["why_investigate"]],
        "outcome_matrix": matrix,
        "missed_winner_breakdown": missed_breakdown,
        "all_candidates": rows,
    }


def write_candidate_intelligence(trading_day):
    intelligence = build_candidate_intelligence(load_candidate_evidence(trading_day))
    rows = intelligence["all_candidates"]
    if rows.empty:
        return None
    csv_path = daily_path(trading_day, "candidate_intelligence.csv")
    json_path = daily_path(trading_day, "candidate_intelligence_summary.json")
    rows.to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(intelligence["summary"], indent=2), encoding="utf-8")
    return {"path": str(csv_path), "rows": len(rows)}