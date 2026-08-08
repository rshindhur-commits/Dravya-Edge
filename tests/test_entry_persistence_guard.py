"""An entry alert promises an exit alert. Only the database can keep it.

On 2026-08-06 and 07 nothing was written -- no snapshots, no trades, no
decisions -- while entry alerts kept going out, because Telegram needs no
database. The position opened on the 5th was still sitting OPEN three days
later, having peaked at 1.28R. Subscribers got the entry and never got the exit.
"""

import pytest

from app.alerts import telegram_alerts


@pytest.fixture(autouse=True)
def clear_probe_cache():

    telegram_alerts._persistence_probe.update({"checked_at": 0.0, "status": None})
    yield
    telegram_alerts._persistence_probe.update({"checked_at": 0.0, "status": None})


def set_status(monkeypatch, status):

    monkeypatch.setattr(
        "app.db.persistence.database_status",
        lambda: status,
    )


def test_a_reachable_database_allows_entries(monkeypatch):

    set_status(monkeypatch, "ON")

    ok, reason = telegram_alerts.entry_persistence_available()

    assert ok is True
    assert reason is None


@pytest.mark.parametrize("status", ["OFF", "UNREACHABLE"])
def test_entries_are_blocked_when_nothing_would_be_recorded(monkeypatch, status):

    set_status(monkeypatch, status)

    ok, reason = telegram_alerts.entry_persistence_available()

    assert ok is False
    assert reason == f"ENTRY_NOT_PERSISTABLE_DB_{status}"


def test_a_failing_probe_blocks_rather_than_assumes_healthy(monkeypatch):
    """The failure that caused this was a silent one; do not add another."""

    def explode():

        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.db.persistence.database_status", explode)

    ok, reason = telegram_alerts.entry_persistence_available()

    assert ok is False
    assert reason == "ENTRY_NOT_PERSISTABLE_DB_UNREACHABLE"


def test_the_probe_is_cached_rather_than_run_per_candidate(monkeypatch):

    calls = []

    def counting():

        calls.append(1)

        return "ON"

    monkeypatch.setattr("app.db.persistence.database_status", counting)

    for _ in range(5):
        telegram_alerts.entry_persistence_available()

    assert len(calls) == 1


def test_the_guard_can_be_switched_off(monkeypatch):

    monkeypatch.setenv("TELEGRAM_REQUIRE_PERSISTED_ENTRY", "false")
    set_status(monkeypatch, "UNREACHABLE")

    ok, reason = telegram_alerts.entry_persistence_available()

    assert ok is True
    assert reason is None


def test_entry_alert_stops_at_the_guard(monkeypatch):

    monkeypatch.setattr(telegram_alerts, "telegram_entry_alerts_enabled", lambda: True)
    set_status(monkeypatch, "UNREACHABLE")

    result = telegram_alerts.maybe_send_paper_entry_alert(
        {"symbol": "SMCI", "direction": "PUT"},
        scanner_context={"Action Status": "ENTER_PAPER", "Realtime Ready": True},
    )

    assert result["sent"] is False
    assert result["reason"] == "ENTRY_NOT_PERSISTABLE_DB_UNREACHABLE"


def test_exits_are_never_gated_on_persistence(monkeypatch):
    """Closing an already-open position is always safe; blocking it is the harm."""

    set_status(monkeypatch, "UNREACHABLE")
    monkeypatch.setenv("TELEGRAM_EXIT_ALERTS_ENABLED", "true")

    assert telegram_alerts.telegram_exit_alerts_enabled() is True
