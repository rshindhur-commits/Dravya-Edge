"""The parity gate.

A backtest is only worth running if it reproduces decisions the live engine
actually made. These tests replay 2026-07-30 and 2026-07-31 at live's own scan
times and assert the replay reaches the same entries.

If this file fails, the numbers any replay produces are fiction and no
conclusion drawn from them survives.

Hermetic: frames come from ``tests/fixtures/market_cache`` and trades from the
frozen fixture, and ``requests.get`` is stubbed so a missing fixture cannot
become a silent network call. Option quotes are therefore unavailable here --
contract selection and fill pricing are covered by the option module's own
tests, and this file is about the decision path.
"""

import json
import os
import pathlib
from datetime import datetime

import pandas as pd
import pytest

import app.backtesting.historical_market_data as hmd
from app.backtesting.replay_engine import (
    ReplayConfig,
    _is_short,
    _open_trade,
    _within_entry_window,
    build_frames,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
MARKET_CACHE = FIXTURES / "market_cache"
TRADES_FILE = FIXTURES / "live_trades_2026_07_30_31.json"


@pytest.fixture(autouse=True)
def _use_fixture_cache(monkeypatch):

    monkeypatch.setattr(hmd, "_CACHE_ROOT", MARKET_CACHE)

    def _no_network(*args, **kwargs):

        raise AssertionError(
            "parity tests must not reach Polygon; a fixture is missing"
        )

    monkeypatch.setattr(hmd.requests, "get", _no_network)


@pytest.fixture(scope="module")
def live_trades():

    with open(TRADES_FILE) as handle:

        return json.load(handle)


def _replay_entry(trade, config, anchor_to_live_candle=False):
    """Run the live entry path for one fixture trade."""

    moment = datetime.strptime(trade["scan_id"], "%Y-%m-%d_%H%M%S")
    raw = hmd.load_replay_frames(
        trade["symbol"], trade["trading_day"], lookback_days=config.lookback_days
    )

    if anchor_to_live_candle:

        # Pin the frame to the bar live recorded, isolating the decision path
        # from the lag model that chooses the bar.
        anchor = pd.to_datetime(
            trade["scanner_context"]["Decision Candle Time ET"]
        ).tz_convert("UTC")
        raw = raw[raw.index <= anchor]
        # frame_as_of would then re-truncate; step past it by asking for a
        # moment far enough ahead that the anchored bar is the newest visible.
        moment = anchor.tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(
            minutes=5
        )

    frames = build_frames(raw, moment, trade["symbol"], config)

    if frames[0] is None:

        return None

    df_5m, df_15m, _, _, analysis_15m, _ = frames

    return _open_trade(
        trade["symbol"], moment, trade["scan_id"], df_5m, df_15m, analysis_15m, config
    )


def test_replay_reproduces_live_entries_at_live_scan_times(live_trades):
    """Driven by live's real scan clock, the replay reaches live's entries.

    Eight of nine reproduce with the direction and setup type live recorded and
    the entry price within a nickel. The ninth is one of the two trades where
    live read a staler bar than the lag model predicts, so its decision candle
    -- and therefore its fill -- differs; ``anchor_to_live_candle`` below shows
    the decision path itself agrees there too.
    """

    config = ReplayConfig()
    matched = 0

    for trade in live_trades:

        replayed = _replay_entry(trade, config)

        if replayed is None:
            continue

        if (
            replayed.direction == trade["direction"]
            and abs(replayed.entry_price - float(trade["entry_price"])) < 0.05
        ):

            matched += 1

    assert matched >= 8, f"entry parity fell to {matched}/9"


def test_replay_entry_direction_and_setup_always_agree(live_trades):
    """Direction and setup type must match on every trade, without tolerance.

    A price can drift a cent with the bar; a direction cannot drift at all. A
    replay that takes a CALL where live took a PUT is not modelling this
    system, and averaged over a year that error is invisible in the totals.
    """

    config = ReplayConfig()

    for trade in live_trades:

        replayed = _replay_entry(trade, config, anchor_to_live_candle=True)

        assert replayed is not None, (
            f"{trade['symbol']} {trade['scan_id']}: replay found no entry where "
            f"live opened a {trade['direction']}"
        )
        assert replayed.direction == trade["direction"], (
            f"{trade['symbol']} {trade['scan_id']}: replay {replayed.direction} "
            f"vs live {trade['direction']}"
        )
        assert replayed.entry_type == trade["entry_type"], (
            f"{trade['symbol']} {trade['scan_id']}: replay {replayed.entry_type} "
            f"vs live {trade['entry_type']}"
        )


def test_replay_reproduces_live_entry_price_when_candle_anchored(live_trades):
    """With the bar pinned to live's, fills agree to the cent."""

    config = ReplayConfig()

    for trade in live_trades:

        replayed = _replay_entry(trade, config, anchor_to_live_candle=True)

        assert replayed is not None

        assert abs(replayed.entry_price - float(trade["entry_price"])) < 0.01, (
            f"{trade['symbol']} {trade['scan_id']}: replay "
            f"{replayed.entry_price} vs live {trade['entry_price']}"
        )


def test_replay_reproduces_live_take_profit(live_trades):
    """Targets match exactly on all nine, across both code versions."""

    config = ReplayConfig()

    for trade in live_trades:

        replayed = _replay_entry(trade, config, anchor_to_live_candle=True)

        assert replayed is not None

        assert abs(replayed.take_profit - float(trade["take_profit"])) < 0.01, (
            f"{trade['symbol']} {trade['scan_id']} target: replay "
            f"{replayed.take_profit} vs live {trade['take_profit']}"
        )


def test_replay_reproduces_live_stop_on_current_code(live_trades):
    """Stops match exactly for trades taken by the code now in the tree.

    Compared against ``initial_stop_loss``, never ``stop_loss``: the latter is
    the *current* protective stop, which the exit engine trails during the
    trade. NVDA 2026-07-31_125759 is the case that shows the difference -- it
    trailed from 196.97 to 197.96, and only the frozen value is what
    ``calculate_risk`` produced at entry.

    Scoped to 2026-07-31 because ``2877ed7`` landed at 16:07 on 2026-07-30,
    after that day's six trades: it added ``MIN_STOP_DISTANCE_PCT`` and so
    changed the stops the same setups now produce. See the companion test.
    """

    config = ReplayConfig()
    checked = 0

    for trade in live_trades:

        if trade["trading_day"] != "2026-07-31":
            continue

        replayed = _replay_entry(trade, config, anchor_to_live_candle=True)

        assert replayed is not None

        expected = float(trade["initial_stop_loss"])

        assert abs(replayed.stop_loss - expected) < 0.02, (
            f"{trade['symbol']} {trade['scan_id']} stop: replay "
            f"{replayed.stop_loss} vs live entry stop {expected}"
        )

        checked += 1

    assert checked == 3


def test_replay_applies_the_stop_floor_that_postdates_the_older_fixtures(live_trades):
    """The 2026-07-30 stops are expected to differ, in a known direction.

    Those six trades were taken before ``2877ed7`` added a 0.50% absolute floor
    on stop distance. Replaying them on current code must therefore produce
    stops at least as wide as live's, landing on the floor -- which is the
    whole point of that commit: the day's stops sat at 0.13%-0.44% of price
    against option round-trip spreads of 2.1%-8.0%, so the move needed to clear
    the spread was several times the distance to the stop.

    This asserts the divergence is the floor rather than drift. If it ever
    becomes an equality the fixtures have been re-recorded on current code, and
    the scoping above should move with them.
    """

    config = ReplayConfig()
    floor_pct = float(os.getenv("MIN_STOP_DISTANCE_PCT", "0.50"))
    checked = 0

    for trade in live_trades:

        if trade["trading_day"] != "2026-07-30":
            continue

        replayed = _replay_entry(trade, config, anchor_to_live_candle=True)

        assert replayed is not None

        live_distance = abs(
            float(trade["entry_price"]) - float(trade["initial_stop_loss"])
        )
        replay_distance = abs(replayed.entry_price - replayed.stop_loss)

        assert replay_distance >= live_distance - 0.02, (
            f"{trade['symbol']} {trade['scan_id']}: current code stopped "
            f"{replay_distance:.3f} from entry, narrower than the "
            f"{live_distance:.3f} live used before the floor existed"
        )

        floor_distance = replayed.entry_price * (floor_pct / 100.0)

        assert replay_distance >= floor_distance - 0.02, (
            f"{trade['symbol']} {trade['scan_id']}: stop distance "
            f"{replay_distance:.3f} is inside the {floor_pct}% floor "
            f"({floor_distance:.3f})"
        )

        checked += 1

    assert checked == 6


def test_entry_window_matches_the_live_auto_paper_window():
    """09:45-15:30 ET. Entering outside it reports trades live would not take."""

    config = ReplayConfig()

    assert not _within_entry_window(datetime(2026, 7, 31, 9, 30), config)
    assert _within_entry_window(datetime(2026, 7, 31, 9, 45), config)
    assert _within_entry_window(datetime(2026, 7, 31, 12, 0), config)
    assert _within_entry_window(datetime(2026, 7, 31, 15, 30), config)
    assert not _within_entry_window(datetime(2026, 7, 31, 15, 45), config)


def test_short_inference_reads_entry_type_not_the_stop():
    """The live rule, and the reason for it.

    ``app/main.py`` infers direction from entry_type because a short whose stop
    has trailed to breakeven would infer LONG from stop-vs-entry and flip every
    exit comparison.
    """

    assert _is_short(None, "EMA_REJECTION_SHORT")
    assert _is_short(None, "BREAKDOWN_SHORT")
    assert not _is_short(None, "EMA_PULLBACK")
    assert not _is_short(None, "BREAKOUT")
    assert _is_short("PUT", None)
    assert not _is_short("CALL", None)
