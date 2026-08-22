from __future__ import annotations

import json

import pandas as pd

from app.analytics.trend_health import TREND_HEALTH_MAX

from app.storage.daily_paths import daily_path, live_path
from app.promotion.promotion_rules import evaluate_promotion
from app.analytics.market_regime import evaluate_market_regime
from app.analytics.trade_lifecycle import evaluate_trade_lifecycle
from app.analytics.performance_statistics import (
    build_performance_statistics,
    build_spread_calibration,
)


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
        # Only rows whose outcome is actually known may be scored. `winner` is
        # false both for "was measured and lost" and for "was never resolved",
        # and counting the second as a prevented loss is what made every rule
        # look perfect: before this, LOW_RR ranked first at ROI 1000 on 1,000
        # rows of which **zero** had been resolved, on the day it refused every
        # trade. Coverage is reported beside the score so a rule with nothing
        # measured reads as unknown rather than as flawless.
        # A known winner is resolved whatever else is recorded: the bridge sets
        # `winner` and `target_first` together, but a caller supplying only
        # `winner` still knows the outcome and must not be discarded as unknown.
        resolved_mask = (
            evidence.get("target_first", pd.Series(False, index=evidence.index)).astype(bool)
            | evidence.get("stop_first", pd.Series(False, index=evidence.index)).astype(bool)
            | evidence.get("winner", pd.Series(False, index=evidence.index)).fillna(False).astype(bool)
        )
        # Zero coverage has two very different meanings and they must not print
        # the same. A rule firing *before* the scanner picks a side has no
        # direction, entry, stop or target on any of its candidates -- there is
        # no trade to replay, and "would it have won" is undefined rather than
        # pending. Measured 2026-08-12: LOW_RR, RISK_REJECTED,
        # NO_DIRECTIONAL_EDGE, NO_ENTRY_TRIGGER and STALE_MARKET_DATA carry 0
        # directional candidates between them, while every row that does have a
        # direction has a full triplet (1,585 of 1,585). So `scoreable` splits
        # "cannot be scored, by construction" from "has a thesis, not yet
        # replayed", and an unscoreable rule reports roi None instead of a
        # flattering number built from its own block count.
        # A dataset with no `direction` column at all says nothing about whether
        # a thesis existed, so it must not be read as "no thesis". Only an
        # explicit non-directional value marks a rule unscoreable.
        if "direction" in evidence.columns:
            directional = evidence["direction"].astype(str).str.upper().isin(
                {"CALL", "PUT", "BULLISH", "BEARISH", "LONG", "SHORT"}
            )
        else:
            directional = pd.Series(True, index=evidence.index)
        for rule, group in evidence.groupby(evidence.get("rule_evaluation", pd.Series("UNKNOWN", index=evidence.index)).fillna("UNKNOWN")):
            scored = group[resolved_mask.reindex(group.index, fill_value=False)]
            with_thesis = int(directional.reindex(group.index, fill_value=False).sum())
            winners = scored.get("winner", pd.Series(False, index=scored.index)).astype(bool)
            blocked_winners = int(winners.sum()); prevented = int((~winners).sum())
            scoreable = with_thesis > 0
            roi.append({
                "rule": str(rule),
                "losses_prevented": prevented,
                "winners_blocked": blocked_winners,
                "roi": (prevented - blocked_winners) if scoreable else None,
                "candidates": int(len(group)),
                "directional": with_thesis,
                "scoreable": scoreable,
                "resolved": int(len(scored)),
                "coverage_pct": round(100 * len(scored) / with_thesis, 1) if with_thesis else None,
            })
    success = refresh.get("outcome", pd.Series(dtype=object)).astype(str).tail(50).eq("LIVE_QUOTE")
    confidence = min(95, round(15 + min(35, evidence_days * 33 / 19) + min(42, completed_trades), 1))
    lift = None
    features = []
    for name in ["Entry Timing", "Trade Ranking", "Exit Confidence", "Grace Zone"]:
        status, reason = evaluate_promotion(completed_trades, lift, confidence)
        features.append({"feature_name": name, "shadow_days": evidence_days, "completed_trades": completed_trades, "improvement_pct": lift, "confidence": confidence, "status": status, "recommended_action": reason})
    return {"refresh_success_rate_last_50": round(float(success.mean() * 100), 1) if len(success) else None, "tqs_calibration": calibration, "rule_roi": sorted(roi, key=lambda row: (row["scoreable"], row["resolved"], row["roi"] or 0), reverse=True), "feature_promotion": features}


def _number_mean(frame, column):
    values = pd.to_numeric(frame.get(column, pd.Series(dtype=float)), errors="coerce")
    return round(float(values.mean()), 2) if values.notna().any() else None


def build_aggregate_statistics(evidence):
    """Per-dimension performance, over candidates that actually became trades.

    This used to aggregate every scanned candidate. Three things went wrong with
    that, and they compounded into a figure that was not merely imprecise but
    backwards. On 2026-07-30 it reported EMA_PULLBACK as `sample_size 51, wins 0,
    losses 51, average_r 29.02`:

    * `sample_size` counted candidates, not trades. 51 EMA_PULLBACK candidates
      were scanned; 5 were traded.
    * every untraded candidate was booked as a loss, because `winner` defaults to
      False for a candidate that was never entered.
    * `average_r` was filled from `trend_capture`, a percentage, so it read 29.02
      alongside zero wins -- arithmetically impossible for an R multiple.

    A candidate that was never entered has no outcome and must not vote. Rows are
    now restricted to those with a real trade, and R comes from the trade's own
    `r_multiple`.
    """

    evidence = evidence if evidence is not None else pd.DataFrame()
    records = []

    if evidence.empty:
        return records

    traded = _traded_candidates(evidence)

    if traded.empty:
        return records

    for kind, column in [("setup", "setup"), ("regime", "regime"), ("market", "regime"), ("lifecycle", "decision")]:
        if column not in traded.columns: continue
        for value, group in traded.groupby(column, dropna=True):
            r = pd.to_numeric(group.get("r_multiple", pd.Series(dtype=float)), errors="coerce").dropna()
            wins = r[r > 0]
            losses = r[r < 0]
            gross_profit = float(wins.sum()) if len(wins) else 0.0
            gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
            records.append({
                "summary_type": kind,
                "dimension": column,
                "dimension_value": str(value),
                "sample_size": int(len(group)),
                "wins": int(len(wins)),
                "losses": int(len(losses)),
                "average_r": round(float(r.mean()), 2) if len(r) else None,
                "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
            })
    return records


def _traded_candidates(evidence):
    """Rows that became a real trade, by whichever column this dataset carries."""

    if "trade_status" in evidence.columns:
        status = evidence["trade_status"].astype(str).str.upper()
        return evidence[status.ne("NOT_CREATED") & status.ne("NAN") & status.ne("NONE")]

    if "entered" in evidence.columns:
        return evidence[evidence["entered"].astype(bool)]

    if "r_multiple" in evidence.columns:
        return evidence[pd.to_numeric(evidence["r_multiple"], errors="coerce").notna()]

    return evidence.iloc[0:0]


def _counts(frame, column):
    """Value counts for `column`, honouring a `row_count` weight column.

    `_waterfalls_for` now asks Postgres to group the day's waterfall, so each
    row it returns stands for a whole (stage, rule_name, blocking) group with
    the group size in `row_count`. A plain `value_counts()` would count the 42
    groups instead of the 24,000 rows behind them. The CSV fallback carries no
    `row_count` and is still counted row by row.
    """

    if frame is None or frame.empty or column not in frame.columns:
        return pd.Series(dtype="int64")

    if "row_count" not in frame.columns:
        return frame[column].value_counts()

    weights = pd.to_numeric(frame["row_count"], errors="coerce").fillna(0)

    return (
        weights.groupby(frame[column]).sum().astype(int).sort_values(ascending=False)
    )


def _blocking_rule_counts(waterfalls):
    """rule_name -> how many candidates that rule stopped.

    Only rows flagged `blocking` count. A rule can fail on a row without being
    the reason the candidate died -- once a candidate is stopped at Momentum no
    option is ever priced, so Option Quality records 0.0 and "fails" on every
    such row. Counting failures rather than blocks reads those cascade
    artifacts as rejections and buries the rules that are actually deciding.
    """

    if waterfalls is None or waterfalls.empty:
        return {}

    if "blocking" not in waterfalls.columns or "rule_name" not in waterfalls.columns:
        return {}

    blocking = waterfalls[
        waterfalls["blocking"].map(
            lambda value: str(value).strip().lower() in {"true", "1", "t", "yes"}
        )
    ]

    if blocking.empty:
        return {}

    counts = _counts(blocking, "rule_name")

    return {str(rule): int(count) for rule, count in counts.items()}


def build_daily_learning_summary(
    trading_day, v2_learning, comparisons, exits, waterfalls, closed_trades=None
):
    v2_learning = v2_learning if v2_learning is not None else pd.DataFrame()
    if (
        not v2_learning.empty
        and "engine_version" in v2_learning.columns
        and v2_learning["engine_version"].notna().any()
    ):
        v2_learning = v2_learning[
            v2_learning["engine_version"].astype(str).str.lower().eq("v2")
        ].copy()
    comparisons = comparisons if comparisons is not None else pd.DataFrame()
    exits = exits if exits is not None else pd.DataFrame()
    waterfalls = waterfalls if waterfalls is not None else pd.DataFrame()
    closed_trades = closed_trades if closed_trades is not None else pd.DataFrame()
    stages = _counts(waterfalls, "stage")
    # From the closed trades, not the waterfall. This asked the waterfall frame
    # for `v2_exit_confidence_score`, a column `decision_waterfall` does not have
    # and never had -- the argument was switched to the waterfall source to give
    # `blocking_stages` a `stage` column, and the confidence went with it. So
    # `daily_engine_summary.avg_exit_confidence` has been NULL on every row it has
    # ever written, which OPTIONS_QUALITY_PLAN logged on 07-31 as "looks stopped
    # rather than starved, cause unverified". This is the cause.
    #
    # `last_exit_confidence_score` is the live exit engine's own score, refreshed
    # on every exit evaluation, so on a closed trade it is the last reading before
    # the close. That makes this the confidence behind the exits actually taken
    # rather than the V2 shadow's -- a different number from the one originally
    # intended, and the only one the app durably records.
    confidence = _number_mean(closed_trades, "last_exit_confidence_score")
    # `Trend Health Score` is the 0-12 analytics scorer, not the 0-100 live one.
    # This compared it against 80, so `premature` has reported zero since the
    # metric was added -- twelve is the highest the column can hold.
    high_health = pd.to_numeric(
        exits.get("Trend Health Score", pd.Series(dtype=float)), errors="coerce"
    ) / TREND_HEALTH_MAX * 100 >= 80
    early = exits.get("Exit Verdict", pd.Series(dtype=object)).astype(str).eq("EXIT_TOO_EARLY")
    premature = int((high_health & early).sum())
    profit_1 = pd.to_numeric(exits.get("Profit +1 Bar", pd.Series(dtype=float)), errors="coerce")
    profit_2 = pd.to_numeric(exits.get("Profit +2 Bars", pd.Series(dtype=float)), errors="coerce")
    regimes = exits.get("Market Regime", pd.Series(dtype=object)).fillna("UNKNOWN").map(lambda value: evaluate_market_regime(value)["state"]).value_counts().to_dict()
    lifecycles = v2_learning.apply(lambda row: evaluate_trade_lifecycle(row.to_dict())["phase"], axis=1).value_counts().to_dict() if not v2_learning.empty else {}
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
        # The rule that actually stopped each candidate, which is what
        # `rule_performance(rule_name, blocked_count)` is named for.
        # `blocking_stages` above counts every evaluated row by stage, so it
        # cannot answer "which rule is doing the rejecting" -- a candidate that
        # sails through Entry still contributes an Entry row to it.
        "blocking_rules": _blocking_rule_counts(waterfalls),
        "market_distribution": {str(key): int(value) for key, value in regimes.items()},
        "lifecycle_distribution": {str(key): int(value) for key, value in lifecycles.items()},
    }


def _waterfalls_for(trading_day, fallback=None):
    """The day's decision waterfall, preferring the durable source.

    There is no per-scan `decision_waterfall.csv` under data/daily -- only the
    on-demand daily review export writes one -- so Postgres is the reliable
    source here rather than merely the more durable one. `decision_waterfall`
    carries a row per candidate per rule with the `blocking` flag set on the one
    rule that actually stopped it, which is the signal `rule_performance` exists
    to record.

    Postgres does the counting. Only two numbers are read off this frame -- rows
    per stage, and blocking rows per rule -- but it used to fetch every column of
    every row to derive them, and it runs once per scan against a day that keeps
    growing. That made it the largest read the app issues: 24,775 rows and 650 KB
    on the last scan of a normal day, ~95 times over. Grouping in the query
    returns 42 rows and leaves the plan untouched -- the index range scan on
    `scan_id` is doing the same work either way, the rows just stop crossing the
    wire. `symbol` and `passed` are no longer selected because nothing read them.

    Returns an empty frame rather than raising: a missing waterfall must not
    take the rest of the daily summary down with it.
    """

    try:
        from sqlalchemy import text

        from app.db.connection import get_engine

        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    "select stage, rule_name, blocking, count(*) as row_count "
                    "from decision_waterfall where scan_id like :prefix "
                    "group by stage, rule_name, blocking"
                ),
                {"prefix": f"{trading_day}%"},
            ).mappings().all()

        if rows:
            return pd.DataFrame([dict(row) for row in rows])

    except Exception:
        pass

    return fallback if fallback is not None else pd.DataFrame()


def _closed_trades_for(trading_day, paper_events):
    """Closed trades for the day, preferring the durable source.

    `paper_trade_events.csv` lives under data/daily/, which is ephemeral on
    Streamlit Cloud: a container restart deletes it, and the learning summary then
    reported `completed_trades: 0` for a day that had traded. Postgres survives the
    restart, so it is tried first and the CSV becomes the fallback for local runs
    or a database that is unreachable.

    Only the richer source wins. If the database returns nothing the CSV is used
    unchanged, so this can never reduce what was already being measured.
    """

    paper_events = paper_events if paper_events is not None else pd.DataFrame()

    try:
        from app.db.paper_trade_repository import PaperTradeRepository

        rows = PaperTradeRepository().fetch_closed(trading_day)
    except Exception:
        rows = []

    if rows and len(rows) >= len(paper_events):
        return pd.DataFrame(rows)

    return paper_events


def write_daily_learning_summary(trading_day):
    def read(name):
        path = daily_path(trading_day, name)
        try:
            return pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame()
        except Exception:
            return pd.DataFrame()
    comparisons = read("engine_trade_comparisons.csv")
    paper_events = read("paper_trade_events.csv")
    # Hoisted above the summary because `avg_exit_confidence` reads off it. One
    # fetch, used twice -- it also backs `performance` and `spread_calibration`
    # below, and this runs once per scan.
    closed_today = _closed_trades_for(trading_day, paper_events)
    summary = build_daily_learning_summary(
        trading_day,
        read("v2_learning_dataset.csv"),
        comparisons,
        read("trend_capture_analysis.csv"),
        # Was `entry_exit_v2_shadow.csv`, which has no `stage` column and no
        # `blocking` flag, so `blocking_stages` was always {} and
        # `rule_performance` had never received a single row.
        _waterfalls_for(trading_day, read("decision_waterfall.csv")),
        closed_today,
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
    summary["aggregate_statistics"] = build_aggregate_statistics(evidence)
    summary["performance"] = build_performance_statistics(closed_today)
    # Open question from 2026-08-01: does option_quality_score predict the round
    # trip actually paid? Accumulated daily so it is answered by data rather than
    # re-argued. See watchlist 2.6.
    summary["spread_calibration"] = build_spread_calibration(closed_today)
    from app.db.learning_engine_repository import LearningEngineRepository
    repository = LearningEngineRepository()
    db_persisted = repository.persist(summary, comparisons.to_dict("records"))
    summary["db_persisted"] = db_persisted
    path = daily_path(trading_day, "daily_engine_summary.json")
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    live_path("daily_engine_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    return {"path": str(path), "summary": summary}