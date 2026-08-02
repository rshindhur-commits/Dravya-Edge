"""The scheduler decides *when* an irreversible delete runs, so the gating is
what matters here: never during a session, and never twice in a day."""
import json
from datetime import datetime

import pytest

from app.runtime import retention_scheduler
from app.runtime.retention_scheduler import due, maybe_run_retention


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / "retention_state.json"
    monkeypatch.setattr(retention_scheduler, "_marker_path", lambda: path)
    return path


NOW = datetime(2026, 8, 2, 9, 0)


def test_not_due_during_a_live_session(marker):
    """idle_reason is falsy while the market is open."""

    assert due(NOW, None) is False
    assert due(NOW, "") is False


def test_due_when_idle_and_never_run(marker):
    assert due(NOW, "MARKET_CLOSED") is True


def test_not_due_twice_on_the_same_day(marker):
    marker.write_text(json.dumps({"last_run_day": "2026-08-02"}), encoding="utf-8")

    assert due(NOW, "MARKET_CLOSED") is False


def test_due_again_the_next_day(marker):
    marker.write_text(json.dumps({"last_run_day": "2026-08-01"}), encoding="utf-8")

    assert due(NOW, "MARKET_CLOSED") is True


def test_unreadable_marker_does_not_block_the_run(marker):
    marker.write_text("{ not json", encoding="utf-8")

    assert due(NOW, "MARKET_CLOSED") is True


def test_session_gate_beats_the_daily_gate(marker):
    """Even on a day it has never run, an open market must not trigger it."""

    assert due(NOW, None) is False


def test_run_records_the_marker(marker, monkeypatch):
    monkeypatch.setattr(
        retention_scheduler, "_marker_path", lambda: marker
    )
    import app.db.retention as retention_module

    monkeypatch.setattr(
        retention_module, "run_retention",
        lambda dry_run: {"total_deleted": 7, "tables": {"event_stream": {"deleted": 7}}},
    )
    monkeypatch.setattr(retention_module, "vacuum", lambda *a, **k: [])

    report = maybe_run_retention(NOW, "MARKET_CLOSED")

    assert report["total_deleted"] == 7
    assert json.loads(marker.read_text())["last_run_day"] == "2026-08-02"
    # Second call the same day is now a no-op.
    assert maybe_run_retention(NOW, "MARKET_CLOSED") is None


def test_failure_is_swallowed_and_marker_untouched(marker, monkeypatch):
    import app.db.retention as retention_module

    def boom(dry_run):
        raise RuntimeError("neon unreachable")

    monkeypatch.setattr(retention_module, "run_retention", boom)

    assert maybe_run_retention(NOW, "MARKET_CLOSED") is None
    # Not marked as run, so it retries rather than skipping the day.
    assert not marker.exists()


def test_vacuum_skipped_when_nothing_was_deleted(marker, monkeypatch):
    import app.db.retention as retention_module

    vacuumed = []
    monkeypatch.setattr(
        retention_module, "run_retention",
        lambda dry_run: {"total_deleted": 0, "tables": {}},
    )
    monkeypatch.setattr(
        retention_module, "vacuum", lambda *a, **k: vacuumed.append(True) or []
    )

    maybe_run_retention(NOW, "MARKET_CLOSED")

    assert vacuumed == []


def test_run_is_never_a_dry_run(marker, monkeypatch):
    """The scheduler exists to actually delete; a dry run here would be a
    silent no-op that looks healthy forever."""
    import app.db.retention as retention_module

    seen = {}
    monkeypatch.setattr(
        retention_module, "run_retention",
        lambda dry_run: seen.update(dry_run=dry_run) or {"total_deleted": 0, "tables": {}},
    )
    monkeypatch.setattr(retention_module, "vacuum", lambda *a, **k: [])

    maybe_run_retention(NOW, "MARKET_CLOSED")

    assert seen["dry_run"] is False
