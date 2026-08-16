"""The two 2026-08-16 changes: a late-session entry cutoff and option give-back.

Both come from §5.13 and §5.14. The entry cutoff removes candidates that reach
+10% on the option only 4-8% of the time against a 20.7% random baseline. The
give-back stops a winner becoming a loser without capping it, which is the part
the first attempt got wrong -- armed at 10% it exited a real PLTR call at +4.8%
out of a trade that reached +69.3%.
"""

import os
from unittest import mock

import pandas as pd
import pytest

from app.exit.exit_engine import (
    _option_giveback_exit,
    option_giveback_arm_pct,
    option_giveback_keep,
)
from app.gates.entry_gate import _late_session_refused, late_session_cutoff_et


def row(timestamp):
    return {"Data Timestamp ET": timestamp}


class TestLateSessionCutoff:

    def test_default_is_one_oh_five(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENTRY_LATE_SESSION_CUTOFF_ET", None)
            assert late_session_cutoff_et() == "13:05"

    def test_morning_candidate_allowed(self):
        assert _late_session_refused(row("2026-08-14 10:30:00 EDT")) is False

    def test_just_before_cutoff_allowed(self):
        assert _late_session_refused(row("2026-08-14 13:04:00 EDT")) is False

    def test_exactly_at_cutoff_allowed(self):
        """The cutoff is inclusive: 13:05 itself is still the good band."""
        assert _late_session_refused(row("2026-08-14 13:05:00 EDT")) is False

    def test_after_cutoff_refused(self):
        assert _late_session_refused(row("2026-08-14 13:06:00 EDT")) is True

    def test_afternoon_refused(self):
        assert _late_session_refused(row("2026-08-14 14:45:00 EDT")) is True

    def test_empty_cutoff_disables_the_rule(self):
        with mock.patch.dict(os.environ, {"ENTRY_LATE_SESSION_CUTOFF_ET": ""}):
            assert _late_session_refused(row("2026-08-14 15:30:00 EDT")) is False

    def test_cutoff_is_configurable(self):
        with mock.patch.dict(os.environ, {"ENTRY_LATE_SESSION_CUTOFF_ET": "11:00"}):
            assert _late_session_refused(row("2026-08-14 11:30:00 EDT")) is True
            assert _late_session_refused(row("2026-08-14 10:30:00 EDT")) is False

    @pytest.mark.parametrize("value", [None, "", "nan", "none", "garbage", "2026-08-14"])
    def test_unparseable_timestamp_never_refuses(self, value):
        """A timestamp we cannot read must not silently block the whole book."""
        assert _late_session_refused(row(value)) is False

    def test_missing_field_never_refuses(self):
        assert _late_session_refused({}) is False


class TestOptionGiveback:

    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXIT_OPTION_GIVEBACK_ARM_PCT", None)
            os.environ.pop("EXIT_OPTION_GIVEBACK_KEEP", None)
            assert option_giveback_arm_pct() == 25.0
            assert option_giveback_keep() == 0.5

    def test_no_option_prices_is_a_silent_no_op(self):
        fired, _peak, _floor = _option_giveback_exit({"entry_type": "BREAKOUT_LONG"}, None)
        assert fired is False

    def test_none_trade_state_is_safe(self):
        assert _option_giveback_exit(None, None)[0] is False

    def test_below_arming_gain_never_fires(self):
        """The PLTR case: a 16% peak is noise and must not arm at all."""
        state = {"option_entry_mid": 4.72, "option_current_mid": 4.95}
        fired, peak, floor = _option_giveback_exit(state, 5.50)
        assert fired is False
        assert peak == pytest.approx(16.5, abs=0.5)
        assert floor is None

    def test_armed_and_holding_does_not_fire(self):
        state = {"option_entry_mid": 4.72, "option_current_mid": 7.05}
        fired, peak, floor = _option_giveback_exit(state, 7.98)
        assert fired is False
        assert peak == pytest.approx(69.0, abs=1.0)
        assert floor == pytest.approx(34.5, abs=1.0)

    def test_armed_and_given_back_fires(self):
        """Peak +69%, floor +34.5%; a fall to +30% must exit."""
        state = {"option_entry_mid": 4.72, "option_current_mid": 6.14}
        fired, _peak, _floor = _option_giveback_exit(state, 7.98)
        assert fired is True

    def test_the_floor_rises_with_the_peak(self):
        """The protection follows profit up and never pulls it down."""
        state = {"option_entry_mid": 10.0, "option_current_mid": 13.0}
        _fired, _peak, low_floor = _option_giveback_exit(state, 13.0)
        _fired, _peak, high_floor = _option_giveback_exit(state, 20.0)
        assert high_floor > low_floor

    def test_arming_threshold_is_configurable(self):
        state = {"option_entry_mid": 10.0, "option_current_mid": 10.5}
        with mock.patch.dict(os.environ, {"EXIT_OPTION_GIVEBACK_ARM_PCT": "10"}):
            assert _option_giveback_exit(state, 11.6)[0] is True
        with mock.patch.dict(os.environ, {"EXIT_OPTION_GIVEBACK_ARM_PCT": "25"}):
            assert _option_giveback_exit(state, 11.6)[0] is False

    def test_legacy_option_mid_key_is_read(self):
        state = {"option_mid": 4.72, "option_current_mid": 6.14}
        assert _option_giveback_exit(state, 7.98)[0] is True

    def test_zero_entry_price_is_a_no_op(self):
        state = {"option_entry_mid": 0.0, "option_current_mid": 5.0}
        assert _option_giveback_exit(state, 6.0)[0] is False

    def test_a_losing_trade_never_arms(self):
        """Give-back protects a gain. It must never fire on a trade in loss."""
        state = {"option_entry_mid": 10.0, "option_current_mid": 6.0}
        fired, _peak, _floor = _option_giveback_exit(state, 10.2)
        assert fired is False
