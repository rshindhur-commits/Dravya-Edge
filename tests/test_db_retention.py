"""Retention is irreversible, so the guard rails are what these cover.

The delete path itself needs a live Postgres and is exercised by the dry run in
tools/run_retention.py; what is tested here is everything that decides *whether*
and *what* to delete.
"""
import pytest

from app.db import retention
from app.db.retention import (
    NEVER_PRUNED,
    RETENTION_RULES,
    RetentionRule,
    _cutoff_sql,
    _validate,
    run_retention,
)


def test_trading_record_tables_are_never_pruned():
    """The evidence for every reported R-multiple must survive retention."""
    pruned = {rule.table for rule in RETENTION_RULES}

    for table in ("trade", "paper_trades", "recommendation_fact", "trade_exit_analysis"):
        assert table in NEVER_PRUNED
        assert table not in pruned


def test_no_rule_targets_a_never_pruned_table():
    for rule in RETENTION_RULES:
        assert rule.table not in NEVER_PRUNED
        _validate(rule)


def test_scanner_snapshot_window_covers_the_regression_requirement():
    """tools/regression_ab.py needs about 10 archived days.

    Shortening this below that silently empties the A/B rather than failing it,
    which is the failure mode worth a test.
    """
    rule = next(r for r in RETENTION_RULES if r.table == "scanner_snapshot")

    assert rule.keep_days >= 10


def test_every_rule_has_a_distinct_table():
    tables = [rule.table for rule in RETENTION_RULES]

    assert len(tables) == len(set(tables))


def test_date_and_timestamp_columns_use_different_cutoffs():
    """A date column compared against NOW() shifts the cutoff by up to a day."""
    date_rule = next(r for r in RETENTION_RULES if r.column_kind == "date")
    ts_rule = next(r for r in RETENTION_RULES if r.column_kind == "timestamp")

    assert "CURRENT_DATE" in _cutoff_sql(date_rule)
    assert "NOW()" in _cutoff_sql(ts_rule)


def test_validate_rejects_unsafe_identifiers():
    bad = RetentionRule("trade; DROP TABLE trade", "day", "date", 7, "")

    with pytest.raises(ValueError):
        _validate(bad)


def test_validate_rejects_unknown_column_kind():
    with pytest.raises(ValueError):
        _validate(RetentionRule("some_table", "day", "epoch", 7, ""))


def test_env_override_is_honoured(monkeypatch):
    rule = RETENTION_RULES[0]
    monkeypatch.setenv(rule.env_key(), "3")

    assert rule.resolved_keep_days() == 3


@pytest.mark.parametrize("value", ["0", "-5", "abc", ""])
def test_env_override_falls_back_on_nonsense(monkeypatch, value):
    """keep_days=0 would delete the entire table; it must not be reachable."""
    rule = RETENTION_RULES[0]
    monkeypatch.setenv(rule.env_key(), value)

    assert rule.resolved_keep_days() == rule.keep_days


def test_apply_is_blocked_when_db_writes_are_disabled(monkeypatch):
    monkeypatch.setattr(retention, "db_writes_enabled", lambda: False)

    report = run_retention(dry_run=False)

    assert report["skipped"] == "db_writes_disabled"
    assert report["tables"] == {}


def test_delete_path_opens_its_own_transaction(monkeypatch):
    """The apply path must not nest a transaction inside the caller's.

    run_retention() counts rows before deleting, which autobegins a transaction
    on its connection; calling connection.begin() again raises
    InvalidRequestError and makes --apply a no-op that only shows up as a logged
    warning. A dry run cannot catch this because it never reaches the delete.
    """
    batches = []

    class _Result:
        rowcount = 0

    class _Txn:
        def __enter__(self):
            batches.append("begin")
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return _Result()

    engine = type("E", (), {"begin": staticmethod(lambda: _Txn())})()
    rule = RETENTION_RULES[0]

    deleted = retention._delete_expired(engine, rule, rule.keep_days, 5000)

    assert deleted == 0
    # Proves it went through engine.begin(), not a caller-supplied connection.
    assert batches == ["begin"]


def test_dry_run_never_calls_the_delete_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        retention, "_delete_expired",
        lambda *a, **k: calls.append(a) or 0,
    )
    monkeypatch.setattr(retention, "count_expired", lambda *a, **k: 42)

    class _Result:
        @staticmethod
        def scalar():
            return 1

    class _Conn:
        def execute(self, *a, **k):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(retention, "get_engine", lambda: type(
        "E", (), {"connect": staticmethod(lambda: _Conn())})())

    report = run_retention(dry_run=True)

    assert calls == []
    assert report["total_deleted"] == 0
    assert all(row["expired"] == 42 for row in report["tables"].values())
