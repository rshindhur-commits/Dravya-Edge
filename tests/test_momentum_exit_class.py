"""Momentum exits are one rule wearing four names; the flag removes all four.

Disabling the EMA rule alone over 21 archived sessions moved return on capital
by -0.40sd, inside noise, because EMA exits went 75 -> 0 while MACD went
88 -> 129 and VWAP 16 -> 37. Testing a member measures substitution. Testing the
class measures the strategy.
"""

import pytest

from app.exit.exit_engine import _momentum_exits_allowed


@pytest.mark.parametrize(
    "profile,bars",
    [("INTRADAY", 0), ("INTRADAY", 12), ("MULTIDAY", 99), (None, 5)],
)
def test_the_class_is_on_by_default(profile, bars):

    assert _momentum_exits_allowed(profile, bars) is True


@pytest.mark.parametrize(
    "profile,bars",
    [("INTRADAY", 0), ("INTRADAY", 12), ("MULTIDAY", 99), (None, 5)],
)
def test_disabling_removes_them_for_every_profile(monkeypatch, profile, bars):
    """No holding profile keeps them; that is what "as a class" means."""

    monkeypatch.setenv("EXIT_MOMENTUM_ENABLED", "false")

    assert _momentum_exits_allowed(profile, bars) is False


def test_the_multiday_leash_still_applies_when_the_class_is_on(monkeypatch):
    """The pre-existing guard is untouched, not replaced."""

    monkeypatch.setenv("MULTIDAY_MOMENTUM_EXIT_MIN_BARS", "4")

    # Chosen above the default of 2, so a pass here cannot come from the
    # default the way it would at 3 bars.
    assert _momentum_exits_allowed("MULTIDAY", 3) is False
    assert _momentum_exits_allowed("MULTIDAY", 4) is True
    # An intraday position was never leashed and still is not.
    assert _momentum_exits_allowed("INTRADAY", 0) is True


def test_the_class_flag_overrides_the_leash(monkeypatch):
    """Off means off, however many bars have passed."""

    monkeypatch.setenv("EXIT_MOMENTUM_ENABLED", "false")
    monkeypatch.setenv("MULTIDAY_MOMENTUM_EXIT_MIN_BARS", "0")

    assert _momentum_exits_allowed("MULTIDAY", 100) is False
