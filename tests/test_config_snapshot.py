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


# ---------------------------------------------------------------------------
# A snapshot that covers only the buy side cannot explain a sell.
#
# NVDA on 2026-08-21 peaked at 1.87R and booked 0.20R. Settling whether the
# profit ladder had been live took an hour, because the run recorded 23 config
# values and not one was an exit setting -- the answer sat in a comment in an
# untracked .env. The ladder had in fact been switched on later that same day.
#
# Reading the code does not answer it either: four of these default ON and were
# deliberately shipped off by env on 2026-08-19.
# ---------------------------------------------------------------------------

EXIT_KEYS = (
    "exit_profit_ladder",
    "exit_trail_arm_r",
    "exit_breakeven_trigger_r",
    "exit_breakeven_on_peak",
    "exit_option_giveback_arm_pct",
    "soft_exit_hold_enabled",
    "exit_structure_trail_enabled",
    "exit_target_extend_enabled",
    "exit_momentum_enabled",
    "position_monitor_enabled",
    "position_monitor_momentum_enabled",
    "target_min_rr",
    "target_max_reward_atr",
)


def test_every_exit_lever_is_in_the_snapshot():
    recorded = config_snapshot()["config"]

    for key in EXIT_KEYS:
        assert key in recorded, f"{key} would be unreconstructable from an archived run"


def test_the_snapshot_records_the_env_not_the_code_default(monkeypatch):
    """The whole point: four of these default ON and were shipped off by env."""

    monkeypatch.setenv("EXIT_PROFIT_LADDER", "")
    monkeypatch.setenv("SOFT_EXIT_HOLD_ENABLED", "true")

    recorded = config_snapshot()["config"]

    assert recorded["exit_profit_ladder"] == ""
    assert recorded["soft_exit_hold_enabled"] is True


def test_a_malformed_value_is_recorded_not_raised(monkeypatch):
    """Telemetry on a finished scan must never be the reason it fails."""

    monkeypatch.setenv("TARGET_MIN_RR", "not-a-number")

    recorded = config_snapshot()["config"]

    assert "target_min_rr" in recorded


def test_the_exit_levers_carry_no_secret():
    """Same rule the buy-side keys are held to."""

    recorded = config_snapshot()["config"]

    for key in EXIT_KEYS:
        assert not any(marker in key.lower() for marker in SECRET_MARKERS)

