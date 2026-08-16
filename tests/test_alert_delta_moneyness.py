"""Delta and moneyness on the alert's OPTION block.

Alerts printed `Contract: 78.0P` and nothing else. A subscriber who cannot
afford that contract has no basis for substituting a strike, and one who can has
no idea whether they are holding intrinsic value or premium. Both are answered by
two numbers the app already has.

Display only -- no selection logic is touched -- so the tests that matter are
that it renders correctly and, above all, that it **never breaks the alert** when
a value is missing. An alert that fails to send is far worse than one without a
delta on it.
"""

import pytest

from app.alerts.telegram_alerts import (
    _moneyness_label,
    _option_lifecycle_lines,
    _option_profile_line,
)


class TestMoneynessLabel:

    @pytest.mark.parametrize(
        "strike,spot,is_put,expected",
        [
            # Calls: struck above spot is out of the money.
            (110.0, 100.0, False, "10.0% OTM"),
            (90.0, 100.0, False, "10.0% ITM"),
            # Puts invert: struck below spot is out of the money.
            (90.0, 100.0, True, "10.0% OTM"),
            (110.0, 100.0, True, "10.0% ITM"),
            # The ATM band is -1%..+1%, matching TRADE_QUALITY_PLAN 7.3d.
            (100.0, 100.0, False, "ATM"),
            (100.9, 100.0, False, "ATM"),
            (99.1, 100.0, True, "ATM"),
        ],
    )
    def test_labels(self, strike, spot, is_put, expected):
        assert _moneyness_label(strike, spot, is_put) == expected

    def test_the_real_nfxl_alert_reads_atm(self):
        """From the 2026-08-14 book: NFLX 78.0P bought against a 77.92 entry."""

        assert _moneyness_label(78.0, 77.92, True) == "ATM"

    @pytest.mark.parametrize(
        "strike,spot",
        [(None, 100.0), (100.0, None), (100.0, 0), (100.0, -5), ("", 100.0)],
    )
    def test_missing_or_nonsense_prices_return_none(self, strike, spot):
        assert _moneyness_label(strike, spot, False) is None


class TestProfileLine:

    def test_both_values_render(self):
        line = _option_profile_line(
            {"option_delta": 0.62, "entry_price": 100.0}, {}, 105.0, False
        )
        assert line == "Delta: 0.62 · 5.0% OTM"

    def test_put_delta_is_shown_unsigned(self):
        """A negative delta beside the P in the contract name reads as a bug."""

        line = _option_profile_line(
            {"option_delta": -0.64, "entry_price": 100.0}, {}, 105.0, True
        )
        assert line.startswith("Delta: 0.64")

    def test_scanner_context_is_used_when_the_trade_has_nothing(self):
        line = _option_profile_line(
            {},
            {"Option Delta": 0.5, "Candidate Entry Price": 100.0},
            100.0,
            False,
        )
        assert line == "Delta: 0.50 · ATM"

    def test_delta_alone_still_renders(self):
        line = _option_profile_line({"option_delta": 0.5}, {}, None, False)
        assert line == "Delta: 0.50"

    def test_moneyness_alone_still_renders(self):
        line = _option_profile_line({"entry_price": 100.0}, {}, 110.0, False)
        assert line == "10.0% OTM"

    def test_nothing_available_yields_no_line(self):
        """Better an alert without a delta than one reading 'Delta: None'."""

        assert _option_profile_line({}, {}, None, False) is None


class TestTheAlertBlock:

    def test_the_line_is_appended_after_cost(self):
        lines = _option_lifecycle_lines({
            "direction": "PUT",
            "option_strike": 100.0,
            "option_expiration": "2026-08-21",
            "option_entry_mid": 1.31,
            "option_delta": -0.55,
            "entry_price": 100.0,
        })

        assert lines[0] == "<b>OPTION</b>"
        assert lines[1] == "Contract: 100.0P"
        assert lines[-1] == "Delta: 0.55 · ATM"

    def test_an_alert_with_no_option_data_still_builds(self):
        """The block must never raise: a lifecycle alert that fails to send is
        a far worse outcome than one missing a decoration."""

        lines = _option_lifecycle_lines({})

        assert lines[0] == "<b>OPTION</b>"
        assert not any("Delta" in line for line in lines)

    def test_none_trade_still_builds(self):
        assert _option_lifecycle_lines(None)[0] == "<b>OPTION</b>"
