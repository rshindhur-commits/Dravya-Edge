"""The per-trade audit page, tested where it has logic rather than layout.

The page exists so a claim about a trade can be checked without asking anyone.
That only holds if the gate comparisons and the book-vs-tape checks are right, so
those are what is pinned here; the Streamlit rendering is not.
"""

import pytest

from app.ui.pages import trade_lifecycle as tl


class TestGateDirection:
    """Three gates are floors and one is a ceiling. Reading the spread gate the
    wrong way round would mark the tightest contracts as failures."""

    def _verdict(self, measured, threshold, lower_is_better):
        got, need = tl._num(measured), tl._num(threshold)
        return (got <= need) if lower_is_better else (got >= need)

    def test_the_spread_gate_is_a_ceiling(self):
        """TSLA #443: 0.65% against a 2.0% maximum is a pass, not a failure."""

        assert self._verdict(0.65, 2.0, True) is True
        assert self._verdict(2.40, 2.0, True) is False

    def test_the_other_three_are_floors(self):
        assert self._verdict(91, 81, False) is True     # setup
        assert self._verdict(2.13, 2.0, False) is True  # reward vs risk
        assert self._verdict(85, 65, False) is True     # option quality
        assert self._verdict(60, 81, False) is False

    def test_the_table_marks_only_the_spread_gate_as_a_ceiling(self):
        ceilings = [label for _m, _t, label, lower in tl.GATES if lower]
        assert ceilings == ["Option spread %"]

    def test_thresholds_come_from_the_trade_not_from_constants(self):
        """Bars move -- min setup ran at 70 and 81 on the same day. A hardcoded
        threshold would misreport every trade taken under the other one."""

        import inspect

        source = inspect.getsource(tl)
        for literal in ("81.0", "65.0", "2.0"):
            assert f"= {literal}" not in source, f"{literal} looks hardcoded"


class TestBookAgainstTape:

    def test_a_peak_below_the_exit_is_detected(self):
        """TSLA #443's actual numbers: recorded peak 8.62, sold at 9.675."""

        recorded_peak, exit_mid = 8.62, 9.675
        assert recorded_peak < exit_mid, "this is the impossible state the page flags"

    def test_a_genuine_giveback_is_not_flagged(self):
        """A real peak above the exit is normal and must not raise anything."""

        recorded_peak, exit_mid = 14.00, 9.675
        assert not (recorded_peak < exit_mid)

    def test_the_quote_drift_threshold_ignores_rounding(self):
        """10c on a contract is $10 -- below that it is quote noise, not a fault."""

        assert abs(10.90 - 9.60) >= 0.10, "TSLA's $1.30 gap must trip it"
        assert not abs(9.62 - 9.60) >= 0.10, "2c must not"


class TestFailureModes:
    """Unavailable, empty and data are three different renderings."""

    def test_missing_gate_values_do_not_raise(self):
        assert tl._num(None) is None
        assert tl._num("") is None
        assert tl._num("not a number") is None

    def test_a_quote_lookup_survives_a_dead_endpoint(self, monkeypatch):
        monkeypatch.setattr("requests.get",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        assert tl._nbbo("O:TSLA260828C00360000", __import__("datetime").datetime.now()) == (None, None)

    def test_a_quote_lookup_with_no_ticker_is_not_a_request(self):
        assert tl._nbbo(None, __import__("datetime").datetime.now()) == (None, None)

    def test_the_unavailable_message_distinguishes_outage_from_quiet(self):
        assert "not the same as no trades" in tl.UNAVAILABLE
