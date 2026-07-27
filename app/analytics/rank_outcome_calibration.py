from __future__ import annotations

import json

import pandas as pd

from app.storage.daily_paths import daily_path


def _number(value):
    return pd.to_numeric(value, errors="coerce")


def _rank_bucket(rank):
    if pd.isna(rank):
        return None
    rank = int(rank)
    if rank == 1:
        return "1"
    if rank <= 3:
        return "2-3"
    if rank <= 5:
        return "4-5"
    return "6+"


def build_rank_outcome_calibration(entry_snapshots, exit_snapshots):
    entries = (
        entry_snapshots.copy()
        if isinstance(entry_snapshots, pd.DataFrame)
        else pd.DataFrame(entry_snapshots or [])
    )
    exits = (
        exit_snapshots.copy()
        if isinstance(exit_snapshots, pd.DataFrame)
        else pd.DataFrame(exit_snapshots or [])
    )
    required_entry = {"trade_id", "candidate_rank"}
    if entries.empty or not required_entry.issubset(entries.columns) or exits.empty or "trade_id" not in exits:
        return pd.DataFrame()

    outcomes = entries[["trade_id", "candidate_rank"]].copy()
    exits_by_trade = exits.drop_duplicates("trade_id", keep="last").set_index("trade_id")
    for column in ("final_r", "trend_capture", "mfe"):
        outcomes[column] = (
            exits_by_trade[column].reindex(outcomes["trade_id"]).to_numpy()
            if column in exits_by_trade
            else pd.NA
        )
    outcomes["candidate_rank"] = _number(outcomes["candidate_rank"])
    outcomes = outcomes.dropna(subset=["candidate_rank", "final_r"])
    if outcomes.empty:
        return pd.DataFrame()
    outcomes["rank_bucket"] = outcomes["candidate_rank"].map(_rank_bucket)
    outcomes["winner"] = _number(outcomes["final_r"]) > 0
    rows = []
    for bucket, group in outcomes.groupby("rank_bucket", sort=False):
        rows.append({
            "rank_bucket": bucket,
            "trades": int(len(group)),
            "win_rate_pct": round(float(group["winner"].mean() * 100), 1),
            "average_final_r": round(float(_number(group["final_r"]).mean()), 2),
            "average_trend_capture": round(float(_number(group["trend_capture"]).mean()), 2) if _number(group["trend_capture"]).notna().any() else None,
            "average_mfe": round(float(_number(group["mfe"]).mean()), 2) if _number(group["mfe"]).notna().any() else None,
        })
    return pd.DataFrame(rows)


def load_rank_outcome_calibration(trading_day):
    path = daily_path(trading_day, "trade_timeline.jsonl")
    if not path.exists():
        return pd.DataFrame()
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    entries = [event.get("payload") for event in events if event.get("event_type") == "EntryOpened"]
    exits = [event.get("payload") for event in events if event.get("event_type") == "ExitTriggered"]
    return build_rank_outcome_calibration(entries, exits)