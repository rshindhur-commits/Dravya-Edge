"""A stop may only be tested against price that came after it was set.

PLTR #352 on 2026-08-19 was closed at 11:24 by a breakeven stop moved at 11:20,
firing on the 11:15-11:30 bar's low of 172.92 -- set at 11:15, five minutes
before the stop existed at that level. Every low after the move was 174.10 or
higher. The trade was +1.04R and climbing toward a 176.67 target, and it booked
0.00R.

The failure is one-directional: a stop that has just moved has moved in the
profitable direction, so the pre-move extreme it fires on is always the wrong
side of it. It can only close trades that should have stayed open.
"""

import pandas as pd
import pytest

from app.exit.exit_engine import _stop_trigger_price


def _bar(open_, high, low, close, start):
    """One bar, indexed by its opening time the way the live frames arrive."""

    return pd.DataFrame(
        [{"Open": open_, "High": high, "Low": low, "Close": close}],
        index=pd.DatetimeIndex([pd.Timestamp(start)]),
    ).iloc[-1]


# The 11:15-11:30 bar PLTR was closed on, as recorded.
PLTR_BAR = _bar(173.00, 174.90, 172.92, 174.64, "2026-08-19T15:15:00Z")
PLTR_STOP = 173.30


def test_a_stop_moved_inside_the_bar_ignores_the_bar_low():

    price = _stop_trigger_price(
        PLTR_BAR, {"stop_moved_at": "2026-08-19T15:20:00+00:00"}, is_short=False
    )

    assert price == 174.64, "the Close, not the 172.92 low from before the move"
    assert price > PLTR_STOP, "so the stop must not fire"


def test_the_pltr_trade_stays_open():
    """The whole point, stated as the outcome rather than the mechanism."""

    price = _stop_trigger_price(
        PLTR_BAR, {"stop_moved_at": "2026-08-19T15:20:00+00:00"}, is_short=False
    )

    assert not (price <= PLTR_STOP)


def test_a_stop_set_before_the_bar_opened_still_uses_the_low():
    """TSLA #340, the same session, must not be broken by this.

    Its stop moved at 10:13 and the lows that took it out -- 338.02 at 10:14 and
    338.01 at 10:15 -- both came after. A real stop-out has to stay one.
    """

    price = _stop_trigger_price(
        PLTR_BAR, {"stop_moved_at": "2026-08-19T15:10:00+00:00"}, is_short=False
    )

    assert price == 172.92, "the move predates the bar, so the extreme is valid"
    assert price <= PLTR_STOP, "and the stop fires"


def test_a_stop_that_never_moved_uses_the_low():

    assert _stop_trigger_price(PLTR_BAR, {}, is_short=False) == 172.92
    assert _stop_trigger_price(PLTR_BAR, None, is_short=False) == 172.92


def test_a_short_reads_the_high_not_the_low():

    assert _stop_trigger_price(PLTR_BAR, {}, is_short=True) == 174.90

    moved = _stop_trigger_price(
        PLTR_BAR, {"stop_moved_at": "2026-08-19T15:20:00+00:00"}, is_short=True
    )

    assert moved == 174.64, "the Close serves both directions"


def test_an_unreadable_timestamp_falls_back_to_the_extreme():
    """A diagnostic that cannot read its inputs must not stop enforcing stops."""

    for bad in ("not-a-time", "", None, object()):
        assert _stop_trigger_price(
            PLTR_BAR, {"stop_moved_at": bad}, is_short=False
        ) == 172.92


def test_a_naive_bar_index_is_still_comparable():
    """Replay fixtures carry a naive index; both sides are read as UTC."""

    naive = _bar(173.00, 174.90, 172.92, 174.64, "2026-08-19T15:15:00")

    assert _stop_trigger_price(
        naive, {"stop_moved_at": "2026-08-19T15:20:00+00:00"}, is_short=False
    ) == 174.64


def test_a_move_exactly_on_the_bar_open_counts_as_before_it():
    """The boundary. A stop set as the bar opened was exposed to all of it."""

    assert _stop_trigger_price(
        PLTR_BAR, {"stop_moved_at": "2026-08-19T15:15:00+00:00"}, is_short=False
    ) == 172.92
