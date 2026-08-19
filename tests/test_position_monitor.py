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
    monkeypatch.setattr("app.utils.polygon_client.get_live_price",
                        lambda symbol: 142.00)

    price, source = pm._price_for("SPCX")

    assert price == 142.00, "a stale stream must not decide a stop"
    assert source == "last_trade"


def test_an_empty_stream_falls_back_to_rest(monkeypatch):
    from app.runtime import position_stream

    monkeypatch.setenv("POSITION_MONITOR_STREAM_ENABLED", "true")
    monkeypatch.setattr(position_stream, "get_stream",
                        lambda: _FakeStream(None, age=None))
    monkeypatch.setattr("app.utils.polygon_client.get_live_price",
                        lambda symbol: 142.00)

    price, source = pm._price_for("SPCX")

    assert price == 142.00
    assert source == "last_trade"


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


# --------------------------------------------------------------------------
# Momentum on 1-minute bars
# --------------------------------------------------------------------------

def test_momentum_is_off_unless_switched_on(monkeypatch):

    # Pinned rather than read from the ambient environment. `settings.py` calls
    # load_dotenv() at import, so before `.env` was synced to Render on
    # 2026-08-19 this passed only because the local file happened to omit the
    # variable -- while production had it true. A test for a *code default* must
    # not assert whatever the operator last deployed.
    monkeypatch.delenv("POSITION_MONITOR_MOMENTUM_ENABLED", raising=False)

    assert pm.momentum_enabled() is False


def test_the_rebuild_interval_never_goes_below_the_bar_that_feeds_it(monkeypatch):
    """A 1-minute bar cannot change faster than a minute. A shorter interval
    spends quota re-reading identical numbers."""

    assert pm.momentum_interval_seconds() == 60

    monkeypatch.setenv("POSITION_MONITOR_MOMENTUM_SECONDS", "5")
    assert pm.momentum_interval_seconds() == 20


def test_a_put_whose_setup_name_reads_long_is_refused_not_guessed():
    """`_is_short_entry` reads the setup NAME; `direction` holds PUT/CALL. When
    they disagree the engine evaluates a short as a long -- R inverts and every
    momentum rule fires on the wrong side. The price path resolves `direction`
    first, but `evaluate_exit` has no such parameter, so the only safe answer is
    to not run."""

    assert pm._direction_agrees(
        {"direction": "PUT", "entry_type": "VWAP_REJECTION"}
    ) is True
    assert pm._direction_agrees(
        {"direction": "CALL", "entry_type": "EMA_PULLBACK"}
    ) is True
    assert pm._direction_agrees(
        {"direction": "PUT", "entry_type": "EMA_PULLBACK"}
    ) is False


def test_a_disagreeing_trade_is_skipped_and_never_priced(monkeypatch):
    """The refusal must happen before the fetch, or a broken row still costs a
    Polygon call every minute it stays open."""

    called = []
    monkeypatch.setattr(pm, "_momentum_frame",
                        lambda symbol, now=None: called.append(symbol))

    assert pm._check_momentum(
        {"direction": "PUT", "entry_type": "EMA_PULLBACK"}, "SPCX"
    ) is None
    assert called == []


def test_the_frame_is_cached_so_a_20s_loop_does_not_fetch_three_times(monkeypatch):

    fetches = []

    def _fake_get(symbol, multiplier, timespan, days):
        fetches.append((symbol, multiplier, timespan))
        return _bars()

    monkeypatch.setattr(
        "app.indicators.technical_indicators.get_polygon_data", _fake_get
    )
    monkeypatch.setattr(
        "app.indicators.technical_indicators.compute_indicators",
        lambda frame, interval=None, symbol=None: frame,
    )
    pm._momentum_frames.clear()

    pm._momentum_frame("SPCX", now=1000.0)
    pm._momentum_frame("SPCX", now=1020.0)
    pm._momentum_frame("SPCX", now=1040.0)

    assert len(fetches) == 1, "three 20s passes inside one minute is one fetch"
    assert fetches[0][1:] == (1, "minute"), "the point is 1-minute bars"

    pm._momentum_frame("SPCX", now=1100.0)
    assert len(fetches) == 2, "past the interval it must refresh"


def test_a_dead_feed_is_cached_so_it_is_not_retried_every_pass(monkeypatch):

    def _boom(*_args, **_kwargs):
        raise RuntimeError("polygon down")

    monkeypatch.setattr(
        "app.indicators.technical_indicators.get_polygon_data", _boom
    )
    pm._momentum_frames.clear()

    assert pm._momentum_frame("SPCX", now=2000.0) is None
    assert pm._momentum_frames["SPCX"][1] is None


def test_the_engine_verdict_is_translated_into_the_close_path():
    """Two engines, two dialects. `_act_on` speaks one of them."""

    translated = pm._as_price_verdict({
        "exit_signal": True,
        "exit_rule": "MACD",
        "exit_reason": "MACD bearish crossover (long)",
        "exit_fill_price": 143.45,
        "updated_stop": 143.90,
        "rr_progress": -0.75,
    })

    assert translated["exit"] is True
    assert translated["exit_code"] == "MACD"
    assert translated["fill_price"] == 143.45


def test_a_momentum_verdict_never_moves_the_stop():
    """The engine proposes a trail from an unfinished bar while the 20s price
    loop moves the stop from a source that cannot be revised. Letting both write
    means a stop that walks backwards when the forming bar changes its mind."""

    translated = pm._as_price_verdict({
        "exit_signal": True, "exit_rule": "VWAP", "exit_reason": "x",
        "exit_fill_price": 141.0, "updated_stop": 999.0, "rr_progress": 0.1,
    })

    assert translated["updated_stop"] is None


def test_a_stale_bar_may_not_decide_a_stop(monkeypatch):
    """`evaluate_exit` reads the last COMPLETED 1-minute bar, so its view of
    price is up to a minute old. The 20s loop judges the same stop against a live
    quote. If the staler one could close, a stop that was touched and recovered
    would still take the trade out a minute later."""

    import pandas as pd

    monkeypatch.setattr(pm, "_momentum_frame",
                        lambda symbol, now=None: pd.DataFrame({"Close": [1.0]}))
    monkeypatch.setattr("app.strategies.momentum_strategy.analyze_setup",
                        lambda frame: {})

    trade = {"direction": "CALL", "entry_type": "EMA_PULLBACK"}

    for rule, expected in (
        ("HARD_STOP", None),
        ("HARD_TARGET", None),
        ("NEAR_CLOSE", None),
        ("MACD", "kept"),
        ("VWAP", "kept"),
        ("EMA", "kept"),
    ):
        monkeypatch.setattr(
            "app.exit.exit_engine.evaluate_exit",
            lambda *a, _rule=rule, **k: {"exit_signal": True, "exit_rule": _rule},
        )
        verdict = pm._check_momentum(trade, "SPCX")

        if expected is None:
            assert verdict is None, f"{rule} must be left to the price loop"
        else:
            assert verdict is not None, f"{rule} is what this path exists for"


# --------------------------------------------------------------------------
# The end-of-day guard. 96% of the R ever lost past the -1R floor was two
# positions nothing closed.
# --------------------------------------------------------------------------

def test_the_eod_guard_is_off_unless_switched_on(monkeypatch):

    # Pinned for the same reason as the momentum switch above: production sets
    # this true on the position worker.
    monkeypatch.delenv("POSITION_MONITOR_EOD_CLOSE_ENABLED", raising=False)

    assert pm.eod_close_enabled() is False


def test_the_guard_uses_the_same_close_time_as_the_scanner():
    """Two copies of a time is how the guard and the rule it backs up drift
    apart, and this one only matters on the day the other has already failed."""

    from app.runtime.paper_automation_support import AUTO_PAPER_EOD_CLOSE

    assert pm.EOD_CLOSE_TIME == AUTO_PAPER_EOD_CLOSE


def test_nothing_closes_before_the_close(monkeypatch):

    monkeypatch.setattr(pm, "_open_positions",
                        lambda: [{"symbol": "SPCX", "holding_profile": "INTRADAY"}])

    with patch("app.state.paper_trade_manager.close_paper_trade") as close:
        closed, carried = pm.force_close_intraday(
            datetime(2026, 8, 19, 15, 54, tzinfo=ET)
        )

    assert not close.called
    assert closed == []


def test_an_intraday_position_still_open_at_the_close_is_closed(monkeypatch):
    """SMCI #149 was INTRADAY, opened 08-05 and closed 08-13 at -23.67R. Policy
    said close it that afternoon. Nothing did, for eight days."""

    monkeypatch.setattr(pm, "_open_positions",
                        lambda: [{"symbol": "SMCI", "holding_profile": "INTRADAY"}])
    monkeypatch.setattr(pm, "_price_for", lambda symbol: (30.58, "rest"))

    with patch("app.state.paper_trade_manager.close_paper_trade") as close:
        closed, carried = pm.force_close_intraday(
            datetime(2026, 8, 19, 15, 56, tzinfo=ET)
        )

    assert closed == ["SMCI"]
    assert close.call_args.kwargs["close_price"] == 30.58
    assert close.call_args.kwargs["notify_exit"] is True


def test_a_multiday_carry_is_reported_and_left_alone(monkeypatch):
    """The guard enforces the holding policy; it does not overrule it."""

    monkeypatch.setattr(pm, "_open_positions",
                        lambda: [{"symbol": "NVDA", "holding_profile": "MULTIDAY"}])
    monkeypatch.setattr(pm, "_price_for", lambda symbol: (193.30, "rest"))

    with patch("app.state.paper_trade_manager.close_paper_trade") as close:
        closed, carried = pm.force_close_intraday(
            datetime(2026, 8, 19, 15, 56, tzinfo=ET)
        )

    assert not close.called
    assert closed == []
    assert carried == ["NVDA"]


def test_a_missing_price_never_closes_at_no_price(monkeypatch):
    """Better a loud unmanaged position than a trade booked at None."""

    monkeypatch.setattr(pm, "_open_positions",
                        lambda: [{"symbol": "SMCI", "holding_profile": "INTRADAY"}])
    monkeypatch.setattr(pm, "_price_for", lambda symbol: (None, None))

    with patch("app.state.paper_trade_manager.close_paper_trade") as close:
        closed, _carried = pm.force_close_intraday(
            datetime(2026, 8, 19, 15, 56, tzinfo=ET)
        )

    assert not close.called
    assert closed == []


def _bars():
    import pandas as pd

    index = pd.date_range("2026-08-18 09:30", periods=60, freq="1min", tz=ET)
    return pd.DataFrame(
        {
            "Open": 100.0, "High": 100.5, "Low": 99.5,
            "Close": 100.0, "Volume": 1000.0,
        },
        index=index,
    )


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


# --------------------------------------------------------------------------
# The price the rules are judged against
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload, self.text = payload, str(payload)

    def json(self):
        return self._payload


def test_the_rest_floor_asks_for_the_last_trade_not_the_previous_close():
    """The defect of 2026-08-19.

    `_price_for` fell back to `get_last_price`, which reads
    `/v2/aggs/ticker/{symbol}/prev` and returns the previous session's close.
    `POSITION_MONITOR_STREAM_ENABLED` is set on neither Render service, so that
    fallback was the only path: the sub-scan log held SPCX at 143.34 for 89
    consecutive polls while it traded down to 137.76.
    """

    from app.utils import polygon_client

    seen = []

    def _capture(url, params=None, timeout=10):
        seen.append(url)
        return _FakeResponse({"results": {"p": 137.76}})

    with patch.object(polygon_client, "safe_request", _capture), \
         patch.object(polygon_client, "get_polygon_api_key", lambda: "k"):

        polygon_client.get_live_price._cache = {}
        price = polygon_client.get_live_price("SPCX")

    assert price == 137.76
    assert "/v2/last/trade/SPCX" in seen[0]
    assert "/prev" not in seen[0], "the previous close is not a live price"


def test_an_unavailable_live_price_is_none_and_never_yesterdays_close():
    """No fallback, on purpose.

    A stale price that looks live is worse than no price: it disables every
    protective rule while the market appears motionless, which is the same
    reasoning that makes `_price_for` refuse an aged streamed price. The caller
    skips a symbol it cannot price.
    """

    from app.utils import polygon_client

    with patch.object(polygon_client, "safe_request",
                      lambda *a, **k: _FakeResponse({"results": None})), \
         patch.object(polygon_client, "get_polygon_api_key", lambda: "k"):

        polygon_client.get_live_price._cache = {}

        assert polygon_client.get_live_price("SPCX") is None


def test_the_two_price_calls_do_not_share_a_cache():
    """Different numbers with different shelf lives. A shared key would let
    whichever ran first answer for the other."""

    from app.utils import polygon_client

    polygon_client.get_live_price._cache = {"SPCX": (__import__("time").monotonic(), 137.76)}
    polygon_client.get_last_price._cache = {"SPCX": (__import__("time").monotonic(), 143.34)}

    with patch.object(polygon_client, "get_polygon_api_key", lambda: "k"):

        assert polygon_client.get_live_price("SPCX") == 137.76
        assert polygon_client.get_last_price("SPCX") == 143.34
