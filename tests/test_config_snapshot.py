"""What the scan enforced must be recorded, and no secret may ride along.

This exists because OPTION_MAX_SPREAD_PCT moved from 6 to 2 in Render and
nothing anywhere recorded it: the database held the effect and not the cause.
"""

import json

from app.runtime.config_snapshot import config_snapshot


SECRET_MARKERS = (
    "key", "token", "secret", "password", "url", "dsn", "chat_id", "bot",
)


def test_it_records_the_resolved_threshold_not_the_dataclass_default(monkeypatch):
    """The value recorded is the one the gate applied."""

    monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", "2")

    config = config_snapshot()["config"]

    assert config["gate_max_spread_pct"] == 2.0


def test_a_changed_setting_shows_up_as_a_changed_record(monkeypatch):
    """The whole point: two scans under different settings must differ."""

    monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", "6")
    before = config_snapshot()["config"]["gate_max_spread_pct"]

    monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", "2")
    after = config_snapshot()["config"]["gate_max_spread_pct"]

    assert (before, after) == (6.0, 2.0), (
        "a spread ceiling change has to be visible in the payload -- this is the "
        "exact question that cost a session on 2026-08-10"
    )


def test_no_field_can_carry_a_secret():
    """Named fields only; nothing here iterates the environment."""

    config = config_snapshot()["config"]

    for name in config:
        assert not any(marker in name.lower() for marker in SECRET_MARKERS), name


def test_the_snapshot_is_json_safe():
    """It is written into a JSONB column, so it has to serialise."""

    json.dumps(config_snapshot())


def test_a_malformed_setting_is_recorded_as_missing_not_raised(monkeypatch):
    """Telemetry on a completed scan must never be why one fails."""

    monkeypatch.setenv("MIN_STOP_SPREAD_MULTIPLE", "not-a-number")
    monkeypatch.setenv("AUTO_PAPER_MIN_RR", "")

    config = config_snapshot()["config"]

    assert "min_stop_spread_multiple" in config
    assert "auto_paper_min_rr" in config
