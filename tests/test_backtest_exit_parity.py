"""Exit-path parity, and a regression guard for the profit-lock fix.

Exits are where this system loses money -- all three winners in the fixture set
exited on a soft rule while in profit -- so the exit path needs its own gate,
not just the entry one.

Every fixture trade was exited by code older than the tree. ``eb56f75`` landed
Fri 2026-07-31 15:13 and ``edcd27a`` at 15:44; the last fixture trade closed at
14:30. So "replay matches live" is the wrong assertion for the trades those
commits touched. What is asserted instead:

* the exits neither commit affects reproduce **exactly**, and
* the pathology ``eb56f75`` was written for reproduces exactly too, which is
  what makes it a usable regression guard.

Hermetic, like the other parity tests.
"""

import json
import pathlib
from datetime import datetime, timedelta

import pytest

import app.backtesting.historical_market_data as hmd


@pytest.fixture(autouse=True)
def _momentum_exits_on(monkeypatch):
    """Every fixture trade was exited with the momentum class active.

    Pinned so that whatever `.env` carries, these trades replay under the exit
    engine that produced them rather than the one currently deployed. (The
    deployed value has moved twice: false on 2026-08-16, true again by
    2026-08-19 -- which is exactly why this is pinned and not assumed.)

    `EXIT_BREAKEVEN_TRIGGER_R` is deleted for the same reason. The fixture trades
    were exited when the breakeven move was hard-wired to a full 1R; production
    now runs 0.5, and once `.env` was synced to Render on 2026-08-19 that moved
    stops on replayed trades live had left alone.
    """

    monkeypatch.setenv("EXIT_MOMENTUM_ENABLED", "true")
    monkeypatch.delenv("EXIT_BREAKEVEN_TRIGGER_R", raising=False)
from app.backtesting.replay_engine import (
    ReplayConfig,
    ReplayTrade,
    _et,
    _manage_trade,
    build_frames,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
MARKET_CACHE = FIXTURES / "market_cache"
TRADES_FILE = FIXTURES / "live_trades_2026_07_30_31.json"

# Exits reproduced exactly on current code: same rule, same R. These are the
# trades neither Friday commit changes, so they are a straight parity check.
VERSION_STABLE_EXITS = {
    "2026-07-30_143918",
    "2026-07-30_144250",
    "2026-07-31_105846",
    "2026-07-31_113633",
}


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


def _seed_trade(trade):
    """Open at live's own entry, so only the exit path is under test."""

    entry = float(trade["entry_price"])
    stop = float(trade["initial_stop_loss"])

    replay = ReplayTrade(
        symbol=trade["symbol"],
        direction=trade["direction"],
        entry_type=trade["entry_type"],
        scan_id=trade["scan_id"],
        entry_time=_et(datetime.strptime(trade["scan_id"], "%Y-%m-%d_%H%M%S")),
        entry_price=entry,
        stop_loss=stop,
        initial_stop_loss=stop,
        take_profit=float(trade["take_profit"]),
    )
    replay.state = {
        "symbol": trade["symbol"],
        "status": "OPEN",
        "entry_type": trade["entry_type"],
        "entry_price": entry,
        "stop_loss": stop,
        "initial_stop_loss": stop,
        "take_profit": float(trade["take_profit"]),
        "highest_price": entry,
        "lowest_price": entry,
        "bars_in_trade": 0,
        "partial_profit_taken": False,
        "holding_profile": "INTRADAY",
        "mfe_r": 0.0,
        "mae_r": 0.0,
    }

    return replay


def _run_to_exit(trade, config=None, collect=False):
    """Walk a 5-minute scan grid from entry to 15:55 ET."""

    config = config or ReplayConfig()
    raw = hmd.load_replay_frames(
        trade["symbol"], trade["trading_day"], lookback_days=config.lookback_days
    )
    replay = _seed_trade(trade)

    start = datetime.strptime(trade["scan_id"], "%Y-%m-%d_%H%M%S")
    end = start.replace(hour=15, minute=55, second=0)
    cursor = start + timedelta(minutes=5)
    track = []

    while cursor <= end and replay.is_open:

        frames = build_frames(raw, cursor, trade["symbol"], config)

        if frames[0] is not None:

            df_5m, df_15m, _, _, analysis_15m, _ = frames
            _manage_trade(replay, cursor, df_5m, df_15m, analysis_15m, config)

            if collect:

                track.append(
                    {
                        "time": cursor,
                        "mfe_r": replay.state.get("mfe_r"),
                        "stop": replay.state.get("stop_loss"),
                    }
                )

        cursor += timedelta(minutes=5)

    return (replay, track) if collect else replay


def test_version_stable_exits_reproduce_exactly(live_trades):
    """Trades neither Friday commit touches must reproduce rule and R."""

    checked = 0

    for trade in live_trades:

        if trade["scan_id"] not in VERSION_STABLE_EXITS:
            continue

        replay = _run_to_exit(trade)

        assert not replay.is_open, (
            f"{trade['symbol']} {trade['scan_id']}: replay never exited; live "
            f"closed on {trade['exit_reason']}"
        )
        assert replay.exit_reason == trade["exit_reason"], (
            f"{trade['symbol']} {trade['scan_id']}: replay exited on "
            f"{replay.exit_reason!r}, live on {trade['exit_reason']!r}"
        )
        assert abs(replay.r_multiple - float(trade["r_multiple"])) < 0.02, (
            f"{trade['symbol']} {trade['scan_id']}: replay R "
            f"{replay.r_multiple} vs live {trade['r_multiple']}"
        )

        checked += 1

    assert checked == len(VERSION_STABLE_EXITS)


def test_mfe_ratchets_rather_than_tracking_the_latest_scan(live_trades):
    """The defect eb56f75 fixed, asserted directly.

    MFE used to be overwritten with each scan's reading, so it fell back as the
    trade retraced and the peak it exists to record was lost. NVDA's live row
    still shows ``mfe_r = 0.0`` for a trade that ran to +1.66R, which is why
    profit lock -- gated on ``mfe_r >= 1.0`` -- could not have fired then even
    had it existed.
    """

    trade = next(t for t in live_trades if t["scan_id"] == "2026-07-31_125759")

    _, track = _run_to_exit(trade, collect=True)

    peaks = [point["mfe_r"] for point in track if point["mfe_r"] is not None]

    assert peaks, "no MFE recorded"
    assert peaks == sorted(peaks), "MFE fell back; it must ratchet, never overwrite"
    assert max(peaks) >= 1.6, f"peak MFE {max(peaks):.2f}, expected ~1.66R"


def test_nvda_profit_giveback_pathology_still_reproduces(live_trades):
    """The trade eb56f75 was written for, held as a regression guard.

    NVDA 2026-07-31 ran to +1.66R and closed at +0.60R on an EMA9 touch. The
    replay reproduces that on current code: profit lock does not fire, because
    at the exit bar the engine's confidence in the exit reads ~49 against the
    ``PROFIT_LOCK_MAX_EXIT_CONFIDENCE`` ceiling of 25. The 11.5 quoted in
    ``resolve_profit_lock``'s docstring occurs earlier in the hold, not at the
    exit.

    So the giveback this fix targets is still live on its own motivating case.
    This test pins that, deliberately: if a threshold change or a later fix
    makes profit lock engage here, this fails and the new behaviour gets
    recorded rather than sliding in unobserved.
    """

    trade = next(t for t in live_trades if t["scan_id"] == "2026-07-31_125759")

    replay, track = _run_to_exit(trade, collect=True)

    assert not replay.is_open
    assert replay.exit_reason == "EMA9 invalidation (long)"

    peak = max(point["mfe_r"] for point in track if point["mfe_r"] is not None)

    assert peak >= 1.6, f"peak MFE {peak:.2f}, expected ~1.66R"
    assert abs(replay.r_multiple - 0.60) < 0.05, (
        f"exited at {replay.r_multiple}R, expected ~0.60R"
    )

    giveback = peak - replay.r_multiple

    assert giveback > 1.0, (
        f"giveback fell to {giveback:.2f}R -- profit lock may now be engaging; "
        f"confirm the change is intended and update this guard"
    )


def test_every_fixture_winner_exited_soft_while_in_profit(live_trades):
    """The shape of the problem, asserted so it cannot be forgotten.

    All three winners exited on EMA/VWAP/MACD invalidation while profitable --
    the exact population profit lock targets -- while the losers exited on hard
    stops or on soft rules already underwater. Entries are not the leak.
    """

    winners = [t for t in live_trades if float(t["r_multiple"]) > 0]

    assert len(winners) == 3

    for trade in winners:

        reason = str(trade["exit_reason"]).upper()

        assert any(rule in reason for rule in ("EMA", "VWAP", "MACD")), (
            f"{trade['symbol']} {trade['scan_id']} won on {reason!r}, which is "
            f"not a soft-invalidation exit -- the population has changed"
        )
