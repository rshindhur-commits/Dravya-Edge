"""What each Postgres table is for.

There are 32 of them, several with names that read alike -- `trade`,
`paper_trades` and `trade_exit_analysis` are three different things, and
`candidate_snapshot`, `scanner_snapshot` and `candidate_evidence` are three more.
Knowing which one answers a question is otherwise a matter of reading the
repository that writes it.

Grain is stated for every table because it is the thing that most often
surprises: `candidate_evidence` is one row per candidate per day while
`candidate_snapshot` is one row per candidate per scan, and confusing them
changes every count by a factor of thirty.
"""

from __future__ import annotations

# (table, group, grain, one-sentence purpose)
TABLES = (
    # ---- What the scanner saw -------------------------------------------
    ("scanner_runs", "Scanning", "one per scan",
     "Start and finish of every scanner run, with row counts and health payload; "
     "a row stuck at STARTED means the scan never recorded a finish."),
    ("scanner_snapshot", "Scanning", "one per scan per symbol",
     "The immutable regression archive: each row keeps the decision payload plus "
     "the last 200 5m, 80 15m and 40 1h bars, which is what makes a past day "
     "reconstructable after the container is wiped."),
    ("candidate_snapshot", "Scanning", "one per candidate per scan",
     "Every candidate the scanner ranked on every scan, including the ones it "
     "skipped or blocked."),
    ("candidate_evidence", "Scanning", "one per candidate per day",
     "The day-level roll-up of a candidate: repeated scans aggregated, joined to "
     "suggestion state, option quality, trend health and outcome."),
    ("candidate_outcome", "Scanning", "one per candidate",
     "Whether a candidate was entered and what became of it -- winner, loser, "
     "target hit, stop hit, trend developed."),
    ("activity_trace_event", "Scanning", "one per observed event",
     "The full narrative of a trading day, symbol by symbol, behind the Activity "
     "Feed."),

    # ---- Why it did or did not trade ------------------------------------
    ("gate_decisions", "Decisions", "one per candidate per scan",
     "The gate verdict for each candidate: action, what blocked it, and at which "
     "stage."),
    ("decision_waterfall", "Decisions", "one per rule per candidate per scan",
     "The ordered rule-by-rule walk that produced a decision, showing which rule "
     "was the blocking one."),
    ("rule_evaluation", "Decisions", "one per rule per candidate per scan",
     "Each individual rule check with its actual and required values."),
    ("rule_performance", "Decisions", "one per rule per day",
     "How often a rule blocked a trade, and how often the trade it blocked would "
     "have won -- the cost of a gate."),
    ("auto_paper_decision", "Decisions", "one per candidate per scan",
     "Every auto-paper OPENED / BLOCKED / SKIPPED verdict with the thresholds in "
     "force at that moment, so a skip can be traced to the gate that caused it."),
    ("quote_attribution", "Decisions", "one per non-live quote",
     "Why an option quote was judged stale: age, allowed age, provider and "
     "timestamp field used."),

    # ---- Trades ----------------------------------------------------------
    ("paper_trades", "Trades", "one per paper trade",
     "The paper book -- entry, exit, status, R multiple and the full trade "
     "payload; this is what survives when the container's state file does not."),
    ("trade", "Trades", "one per completed trade",
     "Trade-level facts: entry facts, exit facts and outcome as structured "
     "payloads."),
    ("trade_exit_analysis", "Trades", "one per completed trade",
     "Indicator state at the moment of exit (ema9, vwap, macd, rsi, atr, bars "
     "held) and the analysis on it -- trend capture, left on table, exit verdict. "
     "The source for the post-market review."),
    ("event_stream", "Trades", "one per lifecycle event",
     "Append-only trade and candidate lifecycle events: entries opened, exits "
     "triggered, promotions, demotions."),
    ("exit_quality_metrics", "Trades", "one per day",
     "Day-level exit quality: premature exits, average trend health at exit, "
     "average left on table."),

    # ---- Notifications ---------------------------------------------------
    ("alert_events", "Notifications", "one per alert",
     "Telegram alert audit with a unique dedupe key, which is what stops a "
     "retried send from becoming a second alert."),
    ("telegram_dispatch", "Notifications", "one per dispatch attempt",
     "Every send attempt with its decision, delivery status, failure reason and "
     "latency."),

    # ---- Learning and performance ---------------------------------------
    ("daily_engine_summary", "Learning", "one per day",
     "The day's engine summary -- completed trades, average R, trend capture."),
    ("analytics_summary", "Learning", "one per dimension value",
     "Performance sliced by a dimension (setup, regime, time bucket): sample "
     "size, wins, losses, average R, profit factor."),
    ("v2_learning_metrics", "Learning", "one per metric per day",
     "Named daily metrics from the V2 execution-learning dataset."),
    ("recommendation_fact", "Learning", "one per recommendation",
     "Every recommendation the scanner made, whether or not it was traded."),
    ("recommendation_horizon_outcome", "Learning", "one per recommendation per horizon",
     "What a recommendation was worth 5 and 10 sessions later, underlying and "
     "option."),
    ("feature_registry", "Learning", "one per feature version",
     "Lifecycle of an experimental feature: introduced, promoted, retired."),
    ("feature_statistics", "Learning", "one per feature version",
     "Measured performance of a feature version and whether it is ready to "
     "promote."),
    ("promotion_review", "Learning", "one per review",
     "Human sign-off on promoting a feature version."),

    # ---- Regression ------------------------------------------------------
    ("scanner_regression_baseline", "Regression", "one per day",
     "The frozen trade baseline for an archived day; never overwritten once "
     "frozen, so comparisons are always against fixed history."),
    ("regression_run", "Regression", "one per HSR run",
     "A Historical Scanner Regression execution: strategy version, git commit, "
     "status and summary."),
    ("regression_result", "Regression", "one per trade per run",
     "Per-trade comparison from a regression run -- baseline R against current R "
     "and the classification."),
    ("trade_comparison", "Regression", "one per matched pair",
     "V1 against V2 on the same trade, with the better engine named."),

    ("telegram_alert_state", "Notifications", "one per alert dedup key",
     "Which alerts have already been sent, so a container restart cannot make "
     "subscribers receive the day's alerts a second time. The JSON state file "
     "stays the hot path; this is hydrated into it once per process."),

    # ---- Runtime ---------------------------------------------------------
    ("scan_engine_heartbeat", "Runtime", "one per scan engine owner",
     "Which scan engine is alive, when it last scanned and when it is next due. "
     "The only way a dashboard can see a scanner running in another container, "
     "and the only way two engines scanning at once becomes visible -- the scan "
     "lock is a local file and cannot serialise across hosts."),

    ("retention_run", "Runtime", "one per retention pass",
     "When the daily prune last ran and how much it removed. The scheduler's "
     "own marker is a file on an ephemeral disk, so before this the only way to "
     "answer 'did retention run' was to query every retained table for rows "
     "past its window and infer it from their absence."),

    # ---- Reference -------------------------------------------------------
    ("earnings_calendar", "Reference", "one per symbol per report date",
     "Known earnings dates, used to keep entries away from an event."),
)

GROUP_ORDER = (
    "Scanning", "Decisions", "Trades", "Notifications",
    "Learning", "Regression", "Runtime", "Reference",
)


def catalog():
    """Table metadata grouped for display, in a stable order."""
    grouped = {group: [] for group in GROUP_ORDER}

    for table, group, grain, purpose in TABLES:
        grouped.setdefault(group, []).append({
            "table": table,
            "grain": grain,
            "purpose": purpose,
        })

    return [(group, grouped[group]) for group in GROUP_ORDER if grouped.get(group)]


def row_counts():
    """Live row count per table, or {} when the database cannot be read."""
    try:
        from sqlalchemy import text

        from app.db.connection import get_engine

        with get_engine().connect() as connection:
            rows = connection.execute(text("""
                SELECT relname, n_live_tup
                FROM pg_stat_user_tables
                WHERE schemaname = 'public'
            """)).all()
        return {name: int(count or 0) for name, count in rows}
    except Exception:
        return {}


def undocumented(known_tables):
    """Tables present in the database but missing from this catalog.

    A migration that adds a table without a line here should show up rather than
    quietly appear as an unexplained name in the Developer page.
    """
    documented = {table for table, _group, _grain, _purpose in TABLES}
    return sorted(set(known_tables or ()) - documented)
