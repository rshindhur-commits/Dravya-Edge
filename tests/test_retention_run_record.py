"""Retention must record that it ran, and must still run if it cannot.

The marker was a file on an ephemeral disk, so "did retention run" could only be
answered by querying every retained table for rows past its window and inferring
from their absence. The record makes it a fact. It must not become a new way for
retention to fail.
"""

import json

import pytest

from app.runtime import retention_scheduler as scheduler


@pytest.fixture
def marker(tmp_path, monkeypatch):

    path = tmp_path / "retention_state.json"
    monkeypatch.setattr(scheduler, "_marker_path", lambda: path)

    return path


def test_the_database_answers_first(monkeypatch, marker):

    marker.write_text(json.dumps({"last_run_day": "2026-08-01"}), encoding="utf-8")
    monkeypatch.setattr(scheduler, "_last_run_day_from_db", lambda: "2026-08-08")

    assert scheduler.last_run_day() == "2026-08-08"


def test_the_file_still_answers_when_the_database_cannot(monkeypatch, marker):
    """An unreachable database must not read as "never ran"."""

    marker.write_text(json.dumps({"last_run_day": "2026-08-01"}), encoding="utf-8")
    monkeypatch.setattr(scheduler, "_last_run_day_from_db", lambda: None)

    assert scheduler.last_run_day() == "2026-08-01"


def test_neither_source_means_due(monkeypatch, marker):

    monkeypatch.setattr(scheduler, "_last_run_day_from_db", lambda: None)

    assert scheduler.last_run_day() is None


def test_a_failed_record_does_not_stop_retention(monkeypatch, marker):
    """An un-migrated deployment has no table; the pass must still complete."""

    def explode(*_args, **_kwargs):

        raise RuntimeError("relation retention_run does not exist")

    monkeypatch.setattr(scheduler, "_record_run_in_db", explode)

    with pytest.raises(RuntimeError):
        scheduler._record_run_in_db("2026-08-08", {})

    # _record_run swallows it and still writes the file.
    monkeypatch.setattr(
        scheduler, "_record_run_in_db", lambda *a, **k: None
    )
    scheduler._record_run("2026-08-08", {"total_deleted": 5, "tables": {}})

    assert json.loads(marker.read_text())["last_run_day"] == "2026-08-08"


def test_due_is_false_once_the_day_is_recorded(monkeypatch):

    from datetime import datetime

    monkeypatch.setattr(scheduler, "last_run_day", lambda: "2026-08-08")

    assert scheduler.due(datetime(2026, 8, 8, 10, 0), "SLEEPING_WEEKEND") is False
    assert scheduler.due(datetime(2026, 8, 9, 10, 0), "SLEEPING_WEEKEND") is True
    # Never during a session, however long since the last run.
    assert scheduler.due(datetime(2026, 8, 9, 10, 0), None) is False
