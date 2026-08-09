"""The last 15m bucket is emitted while still forming.

A scan at 10:11 gets a bar stamped 10:00 whose Close is the last completed 5m
bar. That keeps entry data within one 5m bar of the tape -- the alert latency
investigation confirmed dispatch is instant and data is fresh -- but every
indicator on that bar is provisional.

`drop_incomplete` is the other side of that trade, and these tests pin both.
"""

import pandas as pd

from app.utils.timeframe_resampler import resample_timeframe


def five_minute_bars(count, start="2026-08-05 10:00"):

    index = pd.date_range(start, periods=count, freq="5min")

    return pd.DataFrame(
        {
            "Open": range(100, 100 + count),
            "High": range(101, 101 + count),
            "Low": range(99, 99 + count),
            "Close": range(101, 101 + count),
            "Volume": [10] * count,
        },
        index=index,
    )


def test_a_forming_bucket_is_emitted_by_default():
    """Current behaviour, kept: freshness over stability.

    Four bars is 10:00-10:15 closed plus one bar into the next bucket. A scan
    at 10:16 sees a "10:15" bar holding a third of its eventual price action.
    """

    out = resample_timeframe(five_minute_bars(4), "15m")

    assert len(out) == 2
    assert str(out.index[-1])[11:16] == "10:15"
    # Its Close is the single 5m bar so far, not the bucket's eventual close.
    assert out["Close"].iloc[-1] == 104


def test_a_closed_bucket_is_kept():
    """Three 5m bars fill a 15m bucket; the last covers 10:10-10:15."""

    out = resample_timeframe(five_minute_bars(3), "15m", drop_incomplete=True)

    assert len(out) == 1
    assert out["Close"].iloc[-1] == 103


def test_dropping_removes_only_the_forming_bucket():

    # Six bars: two complete buckets, nothing forming.
    complete = resample_timeframe(five_minute_bars(6), "15m", drop_incomplete=True)
    assert len(complete) == 2

    # Seven: the third bucket holds one bar of three.
    partial = resample_timeframe(five_minute_bars(7), "15m", drop_incomplete=True)
    assert len(partial) == 2
    assert str(partial.index[-1])[11:16] == "10:15"


def test_a_mid_session_gap_is_not_mistaken_for_a_forming_bar():
    """Only the last bucket is checked; a feed gap must not rewrite history."""

    bars = five_minute_bars(6)
    # Drop 10:05, leaving the first bucket short but long since closed.
    gapped = bars.drop(bars.index[1])

    out = resample_timeframe(gapped, "15m", drop_incomplete=True)

    assert len(out) == 2
    assert str(out.index[0])[11:16] == "10:00"


def test_a_single_bar_is_left_alone_rather_than_guessed_at():
    """One bar says nothing about its own interval, so nothing is inferred."""

    out = resample_timeframe(five_minute_bars(1), "15m", drop_incomplete=True)

    assert len(out) == 1


def test_the_hourly_frame_uses_its_own_bucket_size():
    """Twelve 5m bars per hour, not three -- the count is derived, not assumed."""

    # 13 bars: 10:00-11:00 closed, one bar into the next hour.
    out = resample_timeframe(five_minute_bars(13), "1h", drop_incomplete=True)

    assert len(out) == 1
    assert str(out.index[0])[11:16] == "10:00"


def test_an_empty_frame_is_returned_unchanged():

    empty = pd.DataFrame()

    assert resample_timeframe(empty, "15m", drop_incomplete=True).empty
