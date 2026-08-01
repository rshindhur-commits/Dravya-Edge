"""The Developer page's table reference.

32 tables with names that read alike -- `trade`, `paper_trades` and
`trade_exit_analysis` are three different things -- so the catalog states grain
as well as purpose, and has to stay in step with the migrations.
"""

import re
from pathlib import Path

from app.db import catalog

MIGRATIONS = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"


def test_every_table_is_described_once():
    names = [table for table, _group, _grain, _purpose in catalog.TABLES]

    assert len(names) == len(set(names)), "a table is listed twice"


def test_every_entry_states_a_grain_and_a_purpose():
    for table, group, grain, purpose in catalog.TABLES:
        assert grain.startswith("one per"), f"{table} does not state its grain"
        assert len(purpose) > 40, f"{table} has no real description"
        assert group in catalog.GROUP_ORDER, f"{table} is in an unknown group"


def test_grouping_keeps_every_table_and_drops_no_group():
    grouped = catalog.catalog()
    listed = [entry["table"] for _group, entries in grouped for entry in entries]

    assert sorted(listed) == sorted(
        table for table, _group, _grain, _purpose in catalog.TABLES
    )
    assert [group for group, _entries in grouped] == list(catalog.GROUP_ORDER)


def test_every_table_a_migration_creates_is_documented():
    """A migration that adds a table without a catalog line should fail here
    rather than surface as an unexplained name on the Developer page."""
    created = set()
    for path in MIGRATIONS.glob("*.sql"):
        created.update(
            match.lower()
            for match in re.findall(
                r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)",
                path.read_text(encoding="utf-8"),
                flags=re.IGNORECASE,
            )
        )

    documented = {table for table, _g, _gr, _p in catalog.TABLES}
    missing = sorted(created - documented)

    assert not missing, f"tables created by a migration but not described: {missing}"


def test_undocumented_reports_only_what_is_actually_unknown():
    assert catalog.undocumented(["paper_trades", "scanner_runs"]) == []
    assert catalog.undocumented(["paper_trades", "brand_new_table"]) == ["brand_new_table"]
    assert catalog.undocumented([]) == []
    assert catalog.undocumented(None) == []


def test_row_counts_degrade_to_empty_without_a_database(monkeypatch):
    """The section must still render when Postgres is unreachable."""
    def _explode():
        raise RuntimeError("DATABASE_URL is not configured")

    monkeypatch.setattr("app.db.connection.get_engine", _explode)

    assert catalog.row_counts() == {}


def test_the_three_trade_tables_are_distinguishable():
    """The reason the catalog exists: these names do not explain themselves."""
    purposes = {
        table: purpose for table, _g, _gr, purpose in catalog.TABLES
        if table in {"trade", "paper_trades", "trade_exit_analysis"}
    }

    assert len(purposes) == 3
    assert "paper book" in purposes["paper_trades"]
    assert "exit" in purposes["trade_exit_analysis"]
    assert purposes["trade"] != purposes["paper_trades"]
