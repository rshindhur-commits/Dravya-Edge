from __future__ import annotations

import json

import pandas as pd

from app.storage.daily_paths import daily_path, live_path


def build_feedback_loop(candidate_evidence, refresh_events, evidence_days=0, completed_trades=0):
    evidence = candidate_evidence if candidate_evidence is not None else pd.DataFrame()
    refresh = refresh_events if refresh_events is not None else pd.DataFrame()
    tqs = pd.to_numeric(evidence.get("trade_quality_score", pd.Series(dtype=float)), errors="coerce")
    buckets = pd.cut(tqs, [-float("inf"), 70, 80, 90, float("inf")], labels=["<70", "70-80", "80-90", "90+"])
    calibration = []
    if not evidence.empty:
        work = evidence.copy(); work["TQS Bucket"] = buckets
        for bucket, group in work.groupby("TQS Bucket", observed=True):
            winners = pd.to_numeric(group.get("winner", pd.Series(dtype=float)), errors="coerce")
            calibration.append({"bucket": str(bucket), "candidates": int(len(group)), "winner_rate": round(float(winners.mean() * 100), 1) if winners.notna().any() else None})
    roi = []
    if not evidence.empty:
        for rule, group in evidence.groupby(evidence.get("rule_evaluation", pd.Series("UNKNOWN", index=evidence.index)).fillna("UNKNOWN")):
            winners = group.get("winner", pd.Series(False, index=group.index)).astype(bool)
            blocked_winners = int(winners.sum()); prevented = int((~winners).sum())
            roi.append({"rule": str(rule), "losses_prevented": prevented, "winners_blocked": blocked_winners, "roi": prevented - blocked_winners})
    success = refresh.get("outcome", pd.Series(dtype=object)).astype(str).tail(50).eq("LIVE_QUOTE")
    confidence = min(95, round(15 + min(35, evidence_days * 33 / 19) + min(42, completed_trades), 1))
    features = [{"feature_name": name, "shadow_days": evidence_days, "completed_trades": completed_trades, "improvement_pct": None, "confidence": confidence, "recommended_action": "PROMOTION_CANDIDATE" if evidence_days >= 20 and completed_trades >= 80 else "CONTINUE_SHADOW"} for name in ["Entry Timing", "Trade Ranking", "Exit Confidence", "Grace Zone"]]
    return {"refresh_success_rate_last_50": round(float(success.mean() * 100), 1) if len(success) else None, "tqs_calibration": calibration, "rule_roi": sorted(roi, key=lambda row: row["roi"], reverse=True), "feature_promotion": features}


def _number_mean(frame, column):
    values = pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce")
    return round(float(values.mean()), 2) if values.notna().any() else None


def build_daily_learning_summary(trading_day, v2_learning, comparisons, exits, waterfalls):
    v2_learning = v2_learning if v2_learning is not None else pd.DataFrame()
    comparisons = comparisons if comparisons is not None else pd.DataFrame()
    exits = exits if exits is not None else pd.DataFrame()
    waterfalls = waterfalls if waterfalls is not None else pd.DataFrame()
    stages = waterfalls.get("stage", pd.Series(dtype=object)).value_counts()
    confidence = _number_mean(waterfalls, "v2_exit_confidence_score")
    high_health = pd.to_numeric(exits.get("Trend Health Score", pd.Series(dtype=float)), errors="coerce") >= 80
    early = exits.get("Exit Verdict", pd.Series(dtype=object)).astype(str).eq("EXIT_TOO_EARLY")
    premature = int((high_health & early).sum())
    profit_1 = pd.to_numeric(exits.get("Profit +1 Bar", pd.Series(dtype=float)), errors="coerce")
    profit_2 = pd.to_numeric(exits.get("Profit +2 Bars", pd.Series(dtype=float)), errors="coerce")
    return {
        "trading_day": trading_day,
        "v2_shadow_trades": int(len(v2_learning)),
        "trades_compared": int(len(comparisons)),
        "avg_entry_efficiency": _number_mean(v2_learning, "entry_efficiency_score"),
        "avg_trend_capture": _number_mean(v2_learning, "trend_capture_pct"),
        "avg_exit_confidence": confidence,
        "avg_v1_r": _number_mean(comparisons, "final_r_v1"),
        "avg_v2_r": _number_mean(comparisons, "final_r_v2"),
        "avg_r_delta": _number_mean(comparisons, "final_r_delta"),
        "premature_exits": premature,
        "wait_one_bar_improved": int((profit_1 > 0).sum()),
        "wait_two_bars_improved": int((profit_2 > 0).sum()),
        "avg_wait_one_bar_move": round(float(profit_1.mean()), 4) if profit_1.notna().any() else None,
        "avg_wait_two_bars_move": round(float(profit_2.mean()), 4) if profit_2.notna().any() else None,
        "blocking_stages": {str(stage): int(count) for stage, count in stages.items()},
    }


def write_daily_learning_summary(trading_day):
    def read(name):
        path = daily_path(trading_day, name)
        try:
            return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
        except Exception:
            return pd.DataFrame()
    comparisons = read("engine_trade_comparisons.csv")
    summary = build_daily_learning_summary(
        trading_day,
        read("v2_learning_dataset.csv"),
        comparisons,
        read("trend_capture_analysis.csv"),
        read("entry_exit_v2_shadow.csv"),
    )
    try:
        evidence = read("candidate_evidence.csv")
        refresh_path = live_path("quote_refresh_events.jsonl")
        refresh = pd.DataFrame([
            json.loads(line) for line in refresh_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]) if refresh_path.exists() else pd.DataFrame()
    except Exception:
        evidence = pd.DataFrame(); refresh = pd.DataFrame()
    summary["feedback_loop"] = build_feedback_loop(
        evidence, refresh, completed_trades=int(len(read("trend_capture_analysis.csv")))
    )
    path = daily_path(trading_day, "daily_engine_summary.json")
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    live_path("daily_engine_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    from app.db.learning_engine_repository import LearningEngineRepository
    LearningEngineRepository().persist(
        summary,
        comparisons.to_dict("records"),
    )
    return {"path": str(path), "summary": summary}