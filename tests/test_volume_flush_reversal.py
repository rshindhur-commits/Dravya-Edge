"""The volume-flush reversal exit: a turn with conviction behind it.

Measured on the live book by the share of trades that were up 10% and finished at
or below zero: swing break 48%, lower low 52%, a 15-minute EMA cross 62%, the
book as it runs today 48% -- against 33% for this. Every structural definition of
"the trend broke" confirms the turn after the money has gone. Heavy volume prints
on the bar it happens.

These tests are mostly about what must NOT fire it, because a reversal exit that
triggers on ordinary drift is just a faster way to lose.
"""

import os
from unittest import mock

import pandas as pd
import pytest

from app.exit.exit_engine import _volume_flush_reversal, volume_flush_enabled


def frame(volumes):
    return pd.DataFrame({"Volume": volumes})


def bar(close, open_, high, low, volume, atr=1.0):
    return {"Close": close, "Open": open_, "High": high,
            "Low": low, "Volume": volume, "ATR": atr}


QUIET = frame([100.0] * 21)
ON = {"EXIT_VOLUME_FLUSH_ENABLED": "true"}


class TestItFires:

    def test_a_real_flush_against_a_call(self):
        """Red bar, 3x volume, range above 1 ATR."""
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=300.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is True

    def test_a_real_flush_against_a_put(self):
        """Mirrored: a green bar is the reversal when we are short."""
        latest = bar(close=102.0, open_=99.0, high=102.5, low=98.5, volume=300.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=True) is True


class TestWhatMustNotFireIt:

    def test_a_bar_in_our_favour_never_fires(self):
        """Heavy volume WITH the position is strength, not a reversal."""
        latest = bar(close=102.0, open_=99.0, high=102.5, low=98.5, volume=300.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False

    def test_ordinary_drift_never_fires(self):
        """Red bar, but volume is normal -- this is the slow bleed, not a turn."""
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=105.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False

    def test_a_small_bar_never_fires(self):
        """Heavy volume but the range is inside 1 ATR -- churn, not conviction."""
        latest = bar(close=99.5, open_=100.0, high=100.1, low=99.4, volume=300.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False

    def test_volume_exactly_at_the_multiple_does_not_fire(self):
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=150.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False

    @pytest.mark.parametrize("field", ["Close", "Open", "High", "Low", "Volume", "ATR"])
    def test_a_missing_field_never_fires(self, field):
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=300.0)
        latest[field] = None
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False

    def test_zero_atr_never_fires(self):
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=300.0, atr=0.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False

    def test_an_empty_history_never_fires(self):
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=300.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(frame([]), latest, is_short=False) is False


class TestTheAverageExcludesTheCurrentBar:

    def test_a_huge_current_bar_cannot_raise_its_own_bar(self):
        """If the flush bar were included in its own average it could never
        clear the multiple on a quiet day, which would silently disable the
        rule exactly when it matters most."""
        history = frame([100.0] * 20 + [10_000.0])
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=10_000.0)
        with mock.patch.dict(os.environ, ON):
            assert _volume_flush_reversal(history, latest, is_short=False) is True


class TestSwitches:

    def test_disabled_by_default(self):
        """Off: it loses to the floor alone once the real stop is used."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXIT_VOLUME_FLUSH_ENABLED", None)
            assert volume_flush_enabled() is False

    def test_disabled_never_fires(self):
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=300.0)
        with mock.patch.dict(os.environ, {"EXIT_VOLUME_FLUSH_ENABLED": "false"}):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False

    def test_the_multiple_is_configurable(self):
        latest = bar(close=99.0, open_=102.0, high=102.5, low=98.5, volume=120.0)
        with mock.patch.dict(os.environ, dict(ON, EXIT_FLUSH_VOLUME_MULT="1.1")):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is True
        with mock.patch.dict(os.environ, dict(ON, EXIT_FLUSH_VOLUME_MULT="3.0")):
            assert _volume_flush_reversal(QUIET, latest, is_short=False) is False
