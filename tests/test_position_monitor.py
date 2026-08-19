"""Exits are evaluated only on the scan cycle, and the scan is 300s.

SPCX on 2026-08-18 peaked at +0.75R at 10:16 and was at +0.09R by 10:21 -- the
whole reversal inside one gap, with nothing watching. `position_monitor` closes
that hole by running the price-level rules on held symbols every 20s.

It writes to the book, so the two things these tests care about most are that it
closes on the right side of the trade, and that a moved stop is persisted.
"""

import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.exit.exit_engine import evaluate_price_exits
from app.runtime import position_monitor as pm

ET = ZoneInfo("America/New_York")

# SPCX PUT #277 as recorded.
SPCX = {
    "entry_price": 141.24,
    "stop_loss": 142.25,
    "initial_stop_loss": 142.25,
    "take_profit": 139.22,
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "POSITION_MONITOR_ENABLED",
        "POSITION_MONITOR_INTERVAL_SECONDS",
        "EXIT_BREAKEVEN_TRIGGER_R",
        "EXIT_BREAKEVEN_ON_PEAK",
    ):
        monkeypatch.delenv(key, raising=False)


# --------------------------------------------------------------------------
# Direction. The bug this caught before it shipped.
# --------------------------------------------------------------------------

def test_a_put_is_short_even_though_is_short_entry_has_never_heard_of_puts():
    """`_is_short_entry` matches SHORT/BEARISH/BREAKDOWN/REJECTION. `paper_trades`
    stores PUT/CALL in `direction`. A monitor passing the trade row through
    would have evaluated every short as a long: stop on the wrong side, R
    inverted, and an instant false stop-out on the first pass."""

    # Price fell from 141.24 -> 140.71, which is +0.5R for a PUT. The trigger is
    # lowered so the arm fires and exposes `rr_progress`; at the 1.0R default
    # nothing fires and the function correctly returns None.
    os.environ["EXIT_BREAKEVEN_TRIGGER_R"] = "0.5"
    verdict = evaluate_price_exits(SPCX, {"direction": "PUT"}, 140.71)

    assert verdict is not None
    assert verdict["rr_progress"] > 0, "a falling price must be profit for a PUT"


def test_a_setup_name_still_resolves_the_direction():
    """The scanner passes `entry_type`, not `direction`. Both must work."""

    os.environ["EXIT_BREAKEVEN_TRIGGER_R"] = "0.5"
    verdict = evaluate_price_exits(
        SPCX, {"entry_type": "VWAP_REJECTION"}, 140.71
    )

    assert verdict is not None
    assert verdict["rr_progress"] > 0


# --------------------------------------------------------------------------
# The trade this module exists for
# --------------------------------------------------------------------------

def _walk(prices, trigger):
    os.environ["EXIT_BREAKEVEN_TRIGGER_R"] = str(trigger)
    stop = SPCX["stop_loss"]

    for price in prices:
        verdict = evaluate_price_exits(
            {**SPCX, "stop_loss": stop}, {"direction": "PUT"}, price
        )

        if verdict is None:
            continue

        if verdict.get("updated_stop") is not None:
            stop = verdict["updated_stop"]

        if verdict.get("exit"):
            return verdict, stop

    return None, stop


SPCX_PATH = [141.24, 140.71, 140.48, 141.15, 141.35, 142.53]


def test_the_shipped_trigger_rides_the_trade_to_its_hard_stop():
    """1.0R needs price 140.23. The trade's best print was 140.48."""

    verdict, stop = _walk(SPCX_PATH, 1.0)

    assert verdict["exit_code"] == "HARD_STOP"
    assert stop == pytest.approx(142.25)
    assert verdict["rr_progress"] == pytest.approx(-1.28, abs=0.01)


def test_the_lower_trigger_exits_the_same_trade_flat():
    """0.5R arms at 140.735, cleared at 140.71."""

    verdict, stop = _walk(SPCX_PATH, 0.5)

    assert verdict["exit_code"] == "BREAKEVEN_STOP"
    assert stop == pytest.approx(141.24)
    assert verdict["rr_progress"] > -0.2


def test_a_breakeven_exit_is_never_labelled_a_hard_stop():
    """Exit-mix comparisons are only readable if the two stay separate. The
    label follows where the stop sits, not whether it moved on this call."""

    verdict, _stop = _walk(SPCX_PATH, 0.5)

    assert verdict["exit_code"] == "BREAKEVEN_STOP"


def test_nothing_fires_while_the_trade_is_simply_open():

    assert evaluate_price_exits(SPCX, {"direction": "PUT"}, 141.20) is None


# --------------------------------------------------------------------------
# Writing to the book
# --------------------------------------------------------------------------

def test_a_moved_stop_is_persisted_or_the_arm_silently_undoes_itself():
    """`evaluate_price_exits` is stateless -- it re-derives the arm from the stop
    it is handed. An unpersisted breakeven move means the next pass reads the
    opening stop and the protection is gone, with nothing in the log to say so."""

    trade = {"symbol": "SPCX", "stop_loss": 142.25, "highest_price": 140.48}
    verdict = {"exit": False, "updated_stop": 141.24, "rr_progress": 0.53}

    with patch("app.state.paper_trade_manager.update_paper_trade") as update, \
         patch("app.state.paper_trade_manager.close_paper_trade") as close:
        pm._act_on(trade, "SPCX", 140.71, verdict)

    assert update.called, "a moved stop must be written back"
    assert update.call_args.kwargs["updated_stop"] == 141.24
    assert not close.called


def test_an_exit_verdict_closes_the_position():

    trade = {"symbol": "SPCX", "stop_loss": 141.24}
    verdict = {
        "exit": True,
        "exit_code": "BREAKEVEN_STOP",
        "reason": "Protective stop hit",
        "fill_price": 141.24,
        "updated_stop": 141.24,
        "rr_progress": -0.11,
    }

    with patch("app.state.paper_trade_manager.close_paper_trade") as close:
        pm._act_on(trade, "SPCX", 141.35, verdict)

    assert close.called
    assert close.call_args.kwargs["close_price"] == 141.24
    assert "BREAKEVEN_STOP" in close.call_args.kwargs["exit_reason"]
    assert close.call_args.kwargs["notify_exit"] is True


def test_a_tuple_fill_is_unpacked_before_it_reaches_the_book():
    """`resolve_exit_fill` returns a tuple for some codes and a bare price for
    others. A tuple written as a close price corrupts the row."""

    trade = {"symbol": "SPCX", "stop_loss": 142.25}
    verdict = {
        "exit": True, "exit_code": "HARD_STOP", "reason": "x",
        "fill_price": (142.25, 1.01), "updated_stop": 142.25, "rr_progress": -1.28,
    }

    with patch("app.state.paper_trade_manager.close_paper_trade") as close:
        pm._act_on(trade, "SPCX", 142.53, verdict)

    assert close.call_args.kwargs["close_price"] == 142.25


def test_an_unchanged_stop_is_not_written():
    """Every pass that changes nothing must stay silent, or a 20s loop writes
    the same row 1,170 times a session."""

    trade = {"symbol": "SPCX", "stop_loss": 141.24}
    verdict = {"exit": False, "updated_stop": 141.24, "rr_progress": 0.1}

    with patch("app.state.paper_trade_manager.update_paper_trade") as update:
        pm._act_on(trade, "SPCX", 141.20, verdict)

    assert not update.called


# --------------------------------------------------------------------------
# When it runs at all
# --------------------------------------------------------------------------

def test_it_is_off_unless_switched_on():

    assert pm.enabled() is False


def test_the_session_window_excludes_premarket_the_close_and_weekends():

    cases = (
        (datetime(2026, 8, 19, 9, 20, tzinfo=ET), False),
        (datetime(2026, 8, 19, 9, 30, tzinfo=ET), True),
        (datetime(2026, 8, 19, 15, 59, tzinfo=ET), True),
        (datetime(2026, 8, 19, 16, 0, tzinfo=ET), False),
        (datetime(2026, 8, 22, 11, 0, tzinfo=ET), False),
    )

    for when, expected in cases:
        assert pm._in_session(when) is expected, when


# --------------------------------------------------------------------------
# Stage 2 -- streaming, and the staleness floor beneath it
# --------------------------------------------------------------------------

def test_streaming_is_off_unless_switched_on():
    from app.runtime import position_stream

    assert position_stream.stream_enabled() is False


def test_a_fresh_streamed_price_is_used(monkeypatch):
    from app.runtime import position_stream

    monkeypatch.setenv("POSITION_MONITOR_STREAM_ENABLED", "true")
    monkeypatch.setattr(position_stream, "get_stream",
                        lambda: _FakeStream(141.10, age=1.0))

    price, source = pm._price_for("SPCX")

    assert price == 141.10
    assert source.startswith("stream")


def test_a_stale_streamed_price_falls_back_to_rest(monkeypatch):
    """A socket can stop delivering without closing. A frozen last-heard price
    is indistinguishable from a motionless market, so every protective rule
    would stop working while looking healthy. Age is the only defence."""

    from app.runtime import position_stream

    monkeypatch.setenv("POSITION_MONITOR_STREAM_ENABLED", "true")
    monkeypatch.setattr(position_stream, "get_stream",
                        lambda: _FakeStream(141.10, age=999.0))
    monkeypatch.setattr("app.utils.polygon_client.get_last_price",
                        lambda symbol: 142.00)

    price, source = pm._price_for("SPCX")

    assert price == 142.00, "a stale stream must not decide a stop"
    assert source == "rest"


def test_an_empty_stream_falls_back_to_rest(monkeypatch):
    from app.runtime import position_stream

    monkeypatch.setenv("POSITION_MONITOR_STREAM_ENABLED", "true")
    monkeypatch.setattr(position_stream, "get_stream",
                        lambda: _FakeStream(None, age=None))
    monkeypatch.setattr("app.utils.polygon_client.get_last_price",
                        lambda symbol: 142.00)

    price, source = pm._price_for("SPCX")

    assert price == 142.00
    assert source == "rest"


def test_sync_subscribes_and_unsubscribes_to_match_the_book():
    from app.runtime.position_stream import PriceStream

    stream = PriceStream(api_key="x")
    stream._client = _FakeClient()
    stream._prices = {"OLD": (1.0, 0.0)}
    stream._subscribed = {"OLD"}

    stream.sync(["SPCX", "PLTR"])

    assert stream._client.subscribed == ["A.PLTR", "A.SPCX"]
    assert stream._client.unsubscribed == ["A.OLD"]
    assert stream._subscribed == {"SPCX", "PLTR"}
    assert "OLD" not in stream._prices, "a closed position must stop being tracked"


def test_a_handled_message_records_price_and_marks_healthy():
    from app.runtime.position_stream import PriceStream

    stream = PriceStream(api_key="x")
    stream._handle([_FakeAgg("SPCX", 140.48)])

    price, age = stream.latest("SPCX")

    assert price == 140.48
    assert age is not None and age < 5
    assert stream.healthy() is True


class _FakeStream:
    def __init__(self, price, age):
        self._price, self._age = price, age

    def start(self):
        return True

    def sync(self, symbols):
        pass

    def latest(self, symbol):
        return self._price, self._age


class _FakeClient:
    def __init__(self):
        self.subscribed, self.unsubscribed = [], []

    def subscribe(self, *args):
        self.subscribed.extend(args)

    def unsubscribe(self, *args):
        self.unsubscribed.extend(args)


class _FakeAgg:
    def __init__(self, symbol, close):
        self.symbol, self.close = symbol, close
