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
    _giveback_floor,
    _option_giveback_exit,
    option_breakeven_arm_pct,
    option_giveback_arm_pct,
    option_giveback_keep,
)
from app.gates.entry_gate import (
    _late_session_refused,
    _trade_quality_refused,
    late_session_cutoff_et,
    min_trade_quality_score,
)


def row(timestamp):
    return {"Data Timestamp ET": timestamp}


class TestLateSessionCutoff:

    def test_default_is_two_oh_five(self):
        """14:05, chosen over the 13:05 the data prefers. See the docstring."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENTRY_LATE_SESSION_CUTOFF_ET", None)
            assert late_session_cutoff_et() == "14:05"

    def test_morning_candidate_allowed(self):
        assert _late_session_refused(row("2026-08-14 10:30:00 EDT")) is False

    def test_just_before_cutoff_allowed(self):
        assert _late_session_refused(row("2026-08-14 14:04:00 EDT")) is False

    def test_exactly_at_cutoff_allowed(self):
        """Inclusive: 14:05 itself is still allowed."""
        assert _late_session_refused(row("2026-08-14 14:05:00 EDT")) is False

    def test_the_kept_band_is_still_allowed(self):
        """13:05-14:05 hits 8.1% and is deliberately retained at this setting."""
        assert _late_session_refused(row("2026-08-14 13:30:00 EDT")) is False

    def test_after_cutoff_refused(self):
        assert _late_session_refused(row("2026-08-14 14:06:00 EDT")) is True

    def test_the_worst_band_is_refused(self):
        """14:05-15:35 hits 4.5%, well below the 20.7% random baseline."""
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


class TestMultidayIsExempt:
    """The cutoff measures whether the option can move before the bell. A
    position held for days is not constrained by the bell, and five of the nine
    MULTIDAY trades in the book opened after 14:05."""

    def test_a_late_multiday_candidate_is_allowed(self):
        assert _late_session_refused({
            "Data Timestamp ET": "2026-07-30 14:48:00 EDT",
            "Holding Profile": "MULTIDAY",
        }) is False

    def test_a_late_intraday_candidate_is_still_refused(self):
        assert _late_session_refused({
            "Data Timestamp ET": "2026-07-30 14:48:00 EDT",
            "Holding Profile": "INTRADAY",
        }) is True

    def test_an_unknown_profile_is_treated_as_intraday(self):
        """The default profile is INTRADAY; only an explicit MULTIDAY exempts."""
        assert _late_session_refused({
            "Data Timestamp ET": "2026-07-30 14:48:00 EDT",
        }) is True

    def test_the_profile_is_case_insensitive(self):
        assert _late_session_refused({
            "Data Timestamp ET": "2026-07-30 14:48:00 EDT",
            "holding_profile": "multiday",
        }) is False


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

    def test_the_pltr_case_stays_in(self):
        """A 16% peak arms only the breakeven floor, and +4.8% is above it."""
        state = {"option_entry_mid": 4.72, "option_current_mid": 4.95}
        fired, peak, floor = _option_giveback_exit(state, 5.50)
        assert fired is False
        assert peak == pytest.approx(16.5, abs=0.5)
        assert floor == 0.0


class TestTwoTierFloor:

    def test_default_breakeven_arm(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXIT_OPTION_BREAKEVEN_ARM_PCT", None)
            assert option_breakeven_arm_pct() == 10.0

    def test_unprotected_below_ten(self):
        # Pops the variable exactly as `test_default_breakeven_arm` above does.
        # Production runs this at 3, so reading the ambient value tests the
        # deployed arm rather than the 10.0 default this case is named for.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXIT_OPTION_BREAKEVEN_ARM_PCT", None)
            assert _giveback_floor(9.9) is None

    def test_breakeven_floor_between_ten_and_twentyfive(self):
        assert _giveback_floor(10.0) == 0.0
        assert _giveback_floor(20.0) == 0.0
        assert _giveback_floor(24.9) == 0.0

    def test_proportional_floor_above_twentyfive(self):
        assert _giveback_floor(25.0) == pytest.approx(12.5)
        assert _giveback_floor(69.3) == pytest.approx(34.65)

    def test_the_second_floor_never_drops_below_the_first(self):
        """The tiers must ratchet, not fight."""
        assert _giveback_floor(25.0) >= _giveback_floor(24.9)

    def test_none_peak_is_unprotected(self):
        assert _giveback_floor(None) is None

    def test_a_small_gain_that_reverses_is_now_caught(self):
        """The hole: up 20%, reversing, previously had only the hard stop."""
        state = {"option_entry_mid": 10.0, "option_current_mid": 9.5}
        fired, peak, floor = _option_giveback_exit(state, 12.0)
        assert peak == pytest.approx(20.0)
        assert floor == 0.0
        assert fired is True

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


class TestTradeQualityGate:
    """TQS is the only entry signal here that survived a holdout split.

    Q5 reaches +10% on the option 41.4% of the time against a 15.7% base, and the
    underlying-2R control -- which contract quality cannot flatter -- rises
    monotonically in the holdout half: 16.0, 17.2, 24.5, 30.7, 40.2.
    """

    def test_default_is_forty_eight(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENTRY_MIN_TRADE_QUALITY", None)
            assert min_trade_quality_score() == 48.0

    def test_a_low_score_is_refused(self):
        """NVDA 2026-07-30 scored 36.4 and lost $79.50."""
        assert _trade_quality_refused({"Trade Quality Score": 36.4}) is True

    def test_a_score_at_the_bar_is_allowed(self):
        assert _trade_quality_refused({"Trade Quality Score": 48.0}) is False

    def test_a_high_score_is_allowed(self):
        assert _trade_quality_refused({"Trade Quality Score": 83.0}) is False

    @pytest.mark.parametrize("value", [None, "", "nan", "none", "garbage"])
    def test_a_missing_score_never_refuses(self, value):
        """A caller that has not run the ranker must not lose its whole book."""
        assert _trade_quality_refused({"Trade Quality Score": value}) is False

    def test_absent_field_never_refuses(self):
        assert _trade_quality_refused({}) is False

    def test_zero_disables_the_gate(self):
        with mock.patch.dict(os.environ, {"ENTRY_MIN_TRADE_QUALITY": "0"}):
            assert _trade_quality_refused({"Trade Quality Score": 1.0}) is False

    def test_the_threshold_sits_in_a_gap(self):
        """44, 46, 48 and 52 give identical results on the live book -- nothing
        sits between 40.6 and 48 -- so this is not a fitted edge."""
        row = {"Trade Quality Score": 40.6}
        for bar_value in ("44", "46", "48", "52"):
            with mock.patch.dict(os.environ, {"ENTRY_MIN_TRADE_QUALITY": bar_value}):
                assert _trade_quality_refused(row) is True
