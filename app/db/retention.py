"""Bounded retention for the diagnostic tables.

Nothing pruned the artifact tables, so they grew without limit. On 2026-08-02 the
database was 240 MB and growing about 34 MB per active trading day, with
`activity_trace_event` and `scanner_snapshot` alone accounting for 26 MB.

**The original windows were sized against a 512 MB Neon free-tier cap. That cap
does not apply.** This project is on Neon Launch, which is usage-priced at about
$0.35/GB-month with no hard ceiling -- so the binding constraint was never
storage, and every window here was solving a problem that had already gone away.

What that cost was measurement. On 2026-08-13 the setup score could not be
evaluated because only 286 resolved candidates existed, and the entry-timing
finding rested on 122. The advice given at the time -- "revisit near 1,000
resolved" -- was impossible: `candidate_evidence` was pruned at 21 days and
accrues roughly 290 rows in 13 trading days, so the sample resets faster than it
grows and can never approach 1,000. The tables that feed analysis are now kept
for 90 days for that reason. Roughly: `scanner_snapshot` moves from ~400 MB to
~1.7 GB, taking the database to about 2 GB, which is under a dollar a month.

Windows stay per-table rather than one global number. `activity_trace_event` is
a firehose read over days, not a record, and is unchanged at 7. The measurement
tables are a record and are treated as one.

What is *not* here matters as much as what is. The trading record -- `trade`,
`paper_trades`, `recommendation_fact`, `trade_exit_analysis` -- is never pruned.
Those tables total under 1 MB, they are the evidence for every R-multiple the
system reports, and no storage argument justifies deleting them.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from sqlalchemy import text

from app.db.connection import get_engine
from app.db.persistence import db_writes_enabled

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

# Rows deleted per statement. Large enough that the loop is not chatty, small
# enough that no single DELETE holds locks long enough to stall a live scan.
DEFAULT_BATCH_SIZE = 5_000


@dataclass(frozen=True)
class RetentionRule:
    table: str
    column: str
    # "date" compares against CURRENT_DATE, "timestamp" against NOW(). Mixing
    # them silently shifts the cutoff by up to a day.
    column_kind: str
    keep_days: int
    reason: str

    def env_key(self) -> str:
        return f"RETENTION_KEEP_DAYS_{self.table.upper()}"

    def resolved_keep_days(self) -> int:
        raw = os.getenv(self.env_key(), "").strip()

        if not raw:
            return self.keep_days

        try:
            value = int(raw)
        except ValueError:
            logger.warning("%s=%r is not an integer; using %d", self.env_key(), raw, self.keep_days)
            return self.keep_days

        if value < 1:
            logger.warning("%s=%d would delete everything; using %d", self.env_key(), value, self.keep_days)
            return self.keep_days

        return value


# Windows are calendar days; storage accrues per *trading* day, so a window of
# N calendar days holds roughly N*5/7 days of data. Sized against measured
# growth (34 MB per active day as of 2026-08-02) the steady state lands near
# 330 MB, about 64% of the cap, which leaves room for a heavy session without an
# emergency. Extending any of these means reducing what the scanner writes per
# scan first; the budget is the constraint, not the policy.
RETENTION_RULES: tuple[RetentionRule, ...] = (
    RetentionRule(
        "activity_trace_event", "trading_day", "date", 7,
        "~15.9 MB/day, the largest single writer. Seven days covers the "
        "debugging window it is actually read over; it is a firehose, not a record.",
    ),
    RetentionRule(
        "scanner_snapshot", "trading_day", "date", 90,
        "Feeds historical replay and regression: tools/regression_ab.py reads "
        "distinct trading_day from this table and "
        "app/regression/historical_scanner.py loads it per day. Was 21, sized "
        "against a free-tier cap this project is not on. 21 calendar days is 15 "
        "trading days, which is the bare minimum for a single A/B and leaves "
        "nothing for a second look -- and a day whose snapshots are pruned can "
        "never be regressed again, frozen baseline or not. 90 calendar days is "
        "about 64 trading days, and costs roughly 1.3 GB more.",
    ),
    RetentionRule(
        "event_stream", "occurred_at", "timestamp", 10,
        "~3.6 MB/day of trade lifecycle payloads, superseded by the trade "
        "tables once a position closes.",
    ),
    RetentionRule("decision_waterfall", "timestamp", "timestamp", 21, "Per-scan gate telemetry."),
    RetentionRule("rule_evaluation", "timestamp", "timestamp", 21, "Per-scan rule outcomes."),
    RetentionRule("alert_events", "created_at", "timestamp", 21, "Alert audit trail."),
    RetentionRule("gate_decisions", "created_at", "timestamp", 21, "Per-scan gate decisions."),
    RetentionRule("candidate_snapshot", "trading_day", "date", 21, "Per-scan candidate rows."),
    RetentionRule(
        "candidate_evidence", "trading_day", "date", 90,
        "Per-day candidate evidence, and the table every measurement of this "
        "strategy is drawn from -- setup score, entry-timing score, RR bands. "
        "Was 21, which capped the resolved sample near 460 rows and made the "
        "sample-size problem permanent rather than temporary.",
    ),
    RetentionRule(
        "candidate_outcome", "created_at", "timestamp", 90,
        "Candidate outcome scoring; the resolution half of candidate_evidence "
        "and useless kept for a shorter window than the evidence it scores.",
    ),
    RetentionRule(
        "candidate_price_log", "trading_day", "date", 21,
        "One row per forming candidate per 20-second poll -- by construction the "
        "highest-volume writer in the database, ahead of activity_trace_event. "
        "Its own migration says retention has to reach it or it outgrows every "
        "other table within a quarter, and it was created without a rule here. "
        "21 days matches candidate_snapshot, which is the table it is read "
        "beside, and is about 15 trading days: enough to answer what price does "
        "inside a scan gap, which is a question that gets answered once rather "
        "than re-asked forever.",
    ),
    RetentionRule("auto_paper_decision", "trading_day", "date", 21, "Auto-paper decision log."),
    RetentionRule("scanner_runs", "started_at", "timestamp", 21, "Scan run index."),
)

# Named so the exclusion is a decision on the record rather than an oversight.
NEVER_PRUNED = (
    "trade",
    "paper_trades",
    "recommendation_fact",
    "recommendation_horizon_outcome",
    "trade_exit_analysis",
    "trade_comparison",
    "regression_run",
    "regression_result",
    "scanner_regression_baseline",
    "telegram_dispatch",
    "telegram_alert_state",  # has its own prune(keep_days=90)
    "earnings_calendar",
    "feature_registry",
    "feature_statistics",
    "promotion_review",
    "quote_attribution",
    "v2_learning_metrics",
    "rule_performance",
    "analytics_summary",
    "daily_engine_summary",
    "exit_quality_metrics",
    "scan_engine_heartbeat",
)


def _cutoff_sql(rule: RetentionRule) -> str:
    if rule.column_kind == "date":
        return "CURRENT_DATE - CAST(:keep_days AS INTEGER)"

    return "NOW() - make_interval(days => CAST(:keep_days AS INTEGER))"


def _validate(rule: RetentionRule) -> None:
    # Table and column are interpolated into SQL because identifiers cannot be
    # bound. They are constants in this module, but validating means a typo
    # fails loudly here instead of becoming a malformed statement.
    if not _IDENTIFIER.match(rule.table) or not _IDENTIFIER.match(rule.column):
        raise ValueError(f"unsafe identifier in retention rule: {rule.table}.{rule.column}")

    if rule.table in NEVER_PRUNED:
        raise ValueError(f"{rule.table} is marked never-pruned but has a retention rule")

    if rule.column_kind not in {"date", "timestamp"}:
        raise ValueError(f"unknown column_kind {rule.column_kind!r} for {rule.table}")


def count_expired(connection, rule: RetentionRule, keep_days: int) -> int:
    return int(
        connection.execute(
            text(
                f"SELECT count(*) FROM {rule.table} "
                f"WHERE {rule.column} < {_cutoff_sql(rule)}"
            ),
            {"keep_days": keep_days},
        ).scalar()
        or 0
    )


def _delete_expired(engine, rule: RetentionRule, keep_days: int, batch_size: int) -> int:
    """Delete in batches, by ctid, until nothing expired remains.

    A single unbounded DELETE against activity_trace_event would touch tens of
    thousands of rows in one transaction while scans are writing to it.

    Each batch gets its own connection and transaction via `engine.begin()`
    rather than reusing the caller's. Reusing it raised InvalidRequestError --
    the caller has already autobegun a transaction by counting rows, so a nested
    `connection.begin()` is illegal. That path is unreachable during a dry run,
    so it has to be kept honest here rather than found on the first apply.
    """
    statement = text(
        f"""
        DELETE FROM {rule.table}
        WHERE ctid IN (
            SELECT ctid FROM {rule.table}
            WHERE {rule.column} < {_cutoff_sql(rule)}
            LIMIT {int(batch_size)}
        )
        """
    )
    deleted = 0

    while True:
        with engine.begin() as connection:
            removed = connection.execute(statement, {"keep_days": keep_days}).rowcount or 0

        deleted += removed

        if removed < batch_size:
            return deleted


def run_retention(dry_run: bool = True, batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    """Apply every retention rule. Reports what it would do when `dry_run`.

    Defaults to a dry run: a function that deletes on the strength of a
    forgotten argument is the wrong default for something irreversible.
    """
    for rule in RETENTION_RULES:
        _validate(rule)

    if not dry_run and not db_writes_enabled():
        logger.warning("DB writes disabled; retention skipped")
        return {"dry_run": dry_run, "skipped": "db_writes_disabled", "tables": {}}

    report: dict = {"dry_run": dry_run, "tables": {}, "total_deleted": 0}
    engine = get_engine()

    with engine.connect() as connection:
        before = connection.execute(text("SELECT pg_database_size(current_database())")).scalar()

        for rule in RETENTION_RULES:
            keep_days = rule.resolved_keep_days()

            try:
                expired = count_expired(connection, rule, keep_days)
                deleted = (
                    0 if dry_run or not expired
                    else _delete_expired(engine, rule, keep_days, batch_size)
                )
            except Exception:
                # One malformed table must not stop the rest from being bounded.
                logger.warning("retention failed for %s", rule.table, exc_info=True)
                report["tables"][rule.table] = {"error": True, "keep_days": keep_days}
                continue

            report["tables"][rule.table] = {
                "keep_days": keep_days,
                "expired": expired,
                "deleted": deleted,
            }
            report["total_deleted"] += deleted

        report["database_bytes_before"] = before
        report["database_bytes_after"] = connection.execute(
            text("SELECT pg_database_size(current_database())")
        ).scalar()

    return report


def vacuum(tables: list[str] | None = None) -> list[str]:
    """Plain VACUUM ANALYZE on pruned tables.

    DELETE only marks tuples dead; the space is reused by later inserts but is
    not returned to Neon, so the reported database size barely moves. Plain
    VACUUM takes no exclusive lock and is safe to run against a live scanner.
    Reclaiming space to the filesystem needs VACUUM FULL, which does take an
    exclusive lock and is deliberately not run here.
    """
    targets = tables or [rule.table for rule in RETENTION_RULES]
    done = []

    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for table in targets:
            if not _IDENTIFIER.match(table):
                continue

            try:
                connection.execute(text(f"VACUUM ANALYZE {table}"))
                done.append(table)
            except Exception:
                logger.warning("VACUUM failed for %s", table, exc_info=True)

    return done
