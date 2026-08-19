"""The decision candle is not the price the trade opens at.

`entry_price` comes from the scanner's decision candle; the scan that acts on it
finishes minutes later. Measured over 40 trades the gap averages 5.6 minutes and
reaches 13. On 2026-08-19's five entries, booking the decision price rather than
the fill flattered the day by 0.73R -- +1.66R booked against +0.93R real, on a
book whose measured edge is +0.134R.

The cost is one-directional in a way that matters: a candidate approved at
exactly 2.00 RR whose price has since moved against it is a different trade, with
its stop that much closer, and nothing re-checked it.
"""

import os
from unittest import mock
from unittest.mock import patch

import pytest

from app.state.paper_trade_manager import entry_fill_slip, max_entry_fill_slip_r


def _live(price):
    return patch("app.utils.polygon_client.get_live_price", lambda symbol: price)


@pytest.fixture(autouse=True)
def _pin_to_code_defaults(monkeypatch):
    """`ENTRY_MAX_FILL_SLIP_R` is staged to 0 in `.env` so the first session
    records the drift without refusing on it. These cases assert the documented
    0.35 default, so they pin it rather than reading the rollout stage."""

    monkeypatch.delenv("ENTRY_MAX_FILL_SLIP_R", raising=False)
    monkeypatch.delenv("ENTRY_FILL_SANITY_PCT", raising=False)


class TestSlipSign:
    """Positive always means "the market moved against this trade"."""

    def test_a_call_filling_higher_has_slipped(self):
        with _live(339.31):                       # TSLA #340, 2026-08-19
            live, slip = entry_fill_slip("TSLA", "CALL", 338.31, 336.62)

        assert live == 339.31
        assert slip == pytest.approx(0.592, abs=0.001)

    def test_a_call_filling_lower_is_a_better_entry(self):
        with _live(173.14):                       # PLTR #352
            _live_price, slip = entry_fill_slip("PLTR", "CALL", 173.30, 172.01)

        assert slip < 0, "a cheaper call is not slippage"

    def test_a_put_filling_lower_has_slipped(self):
        """The sign that would silently invert every short."""

        with _live(360.44):                       # AVGO #351
            _live_price, slip = entry_fill_slip("AVGO", "PUT", 361.72, 365.76)

        assert slip == pytest.approx(0.317, abs=0.001)

    def test_a_put_filling_higher_is_a_better_entry(self):
        with _live(363.00):
            _live_price, slip = entry_fill_slip("AVGO", "PUT", 361.72, 365.76)

        assert slip < 0


class TestRefusal:

    def test_the_cap_refuses_tsla_and_allows_amzn(self):
        """2026-08-19's two extremes, against the shipped 0.35R cap."""

        cap = max_entry_fill_slip_r()

        with _live(339.31):
            _p, tsla = entry_fill_slip("TSLA", "CALL", 338.31, 336.62)
        with _live(261.17):
            _p, amzn = entry_fill_slip("AMZN", "CALL", 261.05, 259.74)

        assert tsla > cap, "TSLA slipped 0.59R and should be refused"
        assert amzn < cap, "AMZN filled 0.09R off and should stand"

    def test_zero_disables_the_refusal(self):
        with mock.patch.dict(os.environ, {"ENTRY_MAX_FILL_SLIP_R": "0"}, clear=False):
            assert not max_entry_fill_slip_r()


class TestFailureModes:

    def test_no_quote_falls_back_to_the_old_behaviour(self):
        """A missing quote must not block an entry."""

        with _live(None):
            assert entry_fill_slip("TSLA", "CALL", 338.31, 336.62) == (None, None)

    def test_a_raising_quote_is_swallowed(self):
        def _boom(symbol):
            raise RuntimeError("polygon down")

        with patch("app.utils.polygon_client.get_live_price", _boom):
            assert entry_fill_slip("TSLA", "CALL", 338.31, 336.62) == (None, None)

    def test_zero_risk_reports_the_price_without_dividing(self):
        with _live(100.0):
            live, slip = entry_fill_slip("X", "CALL", 100.0, 100.0)

        assert live == 100.0
        assert slip is None
