import pandas as pd


def resample_timeframe(df, timeframe, drop_incomplete=False):
    """Resample 5m bars up to `timeframe`.

    The final bucket is emitted while it is still forming: a scan at 10:11 gets
    a bar stamped 10:00 whose Close is the last completed 5m bar, not the 10:15
    close. That keeps the data fresh -- entry decisions are never more than one
    5m bar behind the tape -- but it means every indicator on that bar is
    provisional, and a setup that exists two thirds of the way through a bucket
    may not exist when the bucket closes.

    `drop_incomplete=True` removes that final partial bucket, so decisions are
    taken only on bars that will not change. It is off by default because the
    cost is freshness: the same decision then waits up to a full bucket. Which
    trade is better is an A/B, not an opinion.

    Only the last bucket is checked. A mid-session bucket short of bars is a gap
    in the feed, and dropping those would silently rewrite history.
    """

    if df.empty:
        return df

    rule_map = {
        "15m": "15min",
        "1h": "1h"
    }

    rule = rule_map.get(timeframe)

    if not rule:
        raise ValueError(
            f"Unsupported timeframe: {timeframe}"
        )

    resampled = pd.DataFrame()

    resampled["Open"] = (
        df["Open"]
        .resample(rule)
        .first()
    )

    resampled["High"] = (
        df["High"]
        .resample(rule)
        .max()
    )

    resampled["Low"] = (
        df["Low"]
        .resample(rule)
        .min()
    )

    resampled["Close"] = (
        df["Close"]
        .resample(rule)
        .last()
    )

    resampled["Volume"] = (
        df["Volume"]
        .resample(rule)
        .sum()
    )

    resampled = resampled[
    resampled["Close"].notna()
]

    if drop_incomplete and not resampled.empty:

        expected = _bars_per_bucket(df.index, rule)

        if expected:

            held = int(df["Close"].resample(rule).count().reindex(resampled.index).iloc[-1])

            if held < expected:

                resampled = resampled.iloc[:-1]

    return resampled


def _bars_per_bucket(index, rule):
    """How many source bars a closed bucket of `rule` should hold, or None.

    Derived from the source spacing rather than assumed, so this stays correct
    for the 1h frame as well as 15m. The modal gap is used rather than the
    smallest, because a session with a missing bar would otherwise look
    finer-grained than it is.

    Returns None when the spacing cannot be established -- a single bar says
    nothing about its own interval -- and the caller then leaves the frame
    alone rather than guessing a bucket incomplete.
    """

    if len(index) < 2:

        return None

    gaps = pd.Series(index).diff().dt.total_seconds().dropna()
    gaps = gaps[gaps > 0]

    if gaps.empty:

        return None

    step = gaps.mode().iloc[0]
    span = pd.tseries.frequencies.to_offset(rule).nanos / 1e9

    return int(span // step) if step and span >= step else None