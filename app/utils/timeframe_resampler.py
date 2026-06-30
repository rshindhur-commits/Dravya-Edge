import pandas as pd


def resample_timeframe(df, timeframe):

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

    return resampled