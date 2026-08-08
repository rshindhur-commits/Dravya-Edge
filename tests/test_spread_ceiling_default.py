"""One variable, one default.

`OPTION_MAX_SPREAD_PCT` was read in two places with different fallbacks -- 6 in
app/config/settings.py, 10.0 in the scanner gate. Where the variable is unset
the two disagree, and rule_evaluation records which one won: 1,337 ENTRY
evaluations at required_value 10.0 through 2026-08-05, against a configuration
that says 6. 202 of the 947 contracts that passed that gate were wider than 6%.
"""

import importlib

import pytest


def scanner_gate_ceiling(monkeypatch, env=None):
    """The ceiling the scanner gate resolves, with the environment as given."""

    monkeypatch.delenv("OPTION_MAX_SPREAD_PCT", raising=False)

    if env is not None:
        monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", env)

    import app.main

    importlib.reload(app.main)

    return app.main.SCANNER_ENTRY_GATE_CONFIG.max_spread_pct


def settings_ceiling(monkeypatch, env=None):

    monkeypatch.delenv("OPTION_MAX_SPREAD_PCT", raising=False)

    if env is not None:
        monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", env)

    from app.config.settings import get_float_env

    return get_float_env("OPTION_MAX_SPREAD_PCT", 6)


def test_the_two_readers_agree_when_the_variable_is_unset(monkeypatch):
    """The case that actually happened in production."""

    assert scanner_gate_ceiling(monkeypatch) == settings_ceiling(monkeypatch)


def test_the_unset_default_is_six_not_ten(monkeypatch):

    assert scanner_gate_ceiling(monkeypatch) == 6.0


@pytest.mark.parametrize("value", ["3", "6", "8"])
def test_an_explicit_setting_still_wins(monkeypatch, value):

    assert scanner_gate_ceiling(monkeypatch, value) == float(value)
    assert settings_ceiling(monkeypatch, value) == float(value)
