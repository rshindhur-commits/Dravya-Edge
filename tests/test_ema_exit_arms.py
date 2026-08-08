"""The EMA exit is the book's largest single loss source; both arms must work.

151 fires against the hard stop's 75 across 601 archived trades, at -6.81% of
premium against the stop's -7.62%. Before that can be A/B'd, the two knobs have
to do what they claim -- an inert flag has cost this project a full replay run
more than once.
"""

import pandas as pd
import pytest

from app.exit.exit_engine import _ema_exit_signalled


def bars(closes, ema9, slope):
    """A frame where every bar shares an EMA9 and slope, closes vary."""

    return pd.DataFrame([
        {"Close": close, "EMA9": ema9, "EMA9_SLOPE": slope}
        for close in closes
    ])


def long_invalidated(n=4):
    """Price below a falling EMA9 on every bar -- a long should exit."""

    return bars([98.0] * n, ema9=100.0, slope=-0.5)


def signalled(df, is_short=False, price=None):

    latest = df.iloc[-1]

    return _ema_exit_signalled(
        df, latest, price if price is not None else latest["Close"], is_short
    )


def test_it_fires_by_default():

    assert signalled(long_invalidated()) is True


def test_a_healthy_trend_does_not_fire():
    """Price above a rising EMA9 is the setup working, not failing."""

    assert signalled(bars([102.0] * 4, ema9=100.0, slope=0.5)) is False


def test_disabling_removes_the_rule(monkeypatch):

    monkeypatch.setenv("EXIT_EMA_ENABLED", "false")

    assert signalled(long_invalidated()) is False


def test_confirmation_holds_fire_on_a_single_bar(monkeypatch):
    """One bar through the EMA is an excursion; the rule should wait."""

    monkeypatch.setenv("EXIT_EMA_CONFIRM_BARS", "1")

    # Only the final bar is below the EMA.
    df = bars([101.0, 101.0, 101.0, 98.0], ema9=100.0, slope=-0.5)

    assert signalled(df) is False


def test_confirmation_still_fires_once_it_persists(monkeypatch):

    monkeypatch.setenv("EXIT_EMA_CONFIRM_BARS", "1")

    assert signalled(long_invalidated()) is True


def test_two_bar_confirmation_needs_three_bars(monkeypatch):

    monkeypatch.setenv("EXIT_EMA_CONFIRM_BARS", "2")

    two_bars = bars([101.0, 98.0, 98.0], ema9=100.0, slope=-0.5)
    three_bars = bars([98.0, 98.0, 98.0], ema9=100.0, slope=-0.5)

    assert signalled(two_bars) is False
    assert signalled(three_bars) is True


def test_confirmation_does_not_read_past_the_start(monkeypatch):
    """A trade opened moments ago has no history to confirm against."""

    monkeypatch.setenv("EXIT_EMA_CONFIRM_BARS", "3")

    assert signalled(long_invalidated(n=2)) is False


def test_shorts_use_the_mirror_condition():

    rising = bars([102.0] * 4, ema9=100.0, slope=0.5)

    assert signalled(rising, is_short=True) is True
    assert signalled(long_invalidated(), is_short=True) is False


@pytest.mark.parametrize("missing", [None, float("nan")])
def test_a_missing_ema_never_fires(missing):

    df = pd.DataFrame([{"Close": 98.0, "EMA9": missing, "EMA9_SLOPE": -0.5}] * 3)

    assert signalled(df) is False
