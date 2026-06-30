import pandas as pd
import numpy as np


def enrich_indicators(df):

    df = df.copy()

    # =========================
    # EMA
    # =========================

    df["EMA9"] = (
        df["Close"]
        .ewm(span=9)
        .mean()
    )

    df["EMA20"] = (
        df["Close"]
        .ewm(span=20)
        .mean()
    )

    # =========================
    # EMA SLOPE
    # =========================

    df["EMA9_SLOPE"] = (
        df["EMA9"]
        .diff()
    )

    # =========================
    # VWAP
    # =========================

    df["VWAP"] = (

        (
            df["Close"]
            * df["Volume"]
        ).cumsum()

        /

        df["Volume"].cumsum()
    )

    # =========================
    # VWAP DISTANCE
    # =========================

    df["VWAP_DISTANCE"] = (

        (
            df["Close"]
            - df["VWAP"]
        )

        / df["VWAP"]

    ) * 100

    # =========================
    # ATR
    # =========================

    df["ATR"] = (
        df["High"]
        - df["Low"]
    ).rolling(14).mean()

    df["ATR_PCT"] = (
        df["ATR"]
        / df["Close"]
    ) * 100

    # =========================
    # RSI
    # =========================

    delta = df["Close"].diff()

    gain = (
        delta.clip(lower=0)
        .rolling(14)
        .mean()
    )

    loss = (
        -delta.clip(upper=0)
        .rolling(14)
        .mean()
    )

    rs = gain / loss

    df["RSI"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    df["RSI_SLOPE"] = (
        df["RSI"]
        .diff()
    )

    # =========================
    # Opening Range Breakout
    # =========================

    opening_high = (
        df["High"]
        .head(5)
        .max()
    )

    opening_low = (
        df["Low"]
        .head(5)
        .min()
    )

    df["ORB_BREAKOUT"] = (
        df["Close"] > opening_high
    )

    df["ORB_BREAKDOWN"] = (
        df["Close"] < opening_low
    )    

    # =========================
    # Relative Volume
    # =========================

    volume_avg = (

        df["Volume"]

        .rolling(20)

        .mean()

    )

    df["REL_VOLUME"] = (

        df["Volume"]

        / volume_avg

    )    

    # =========================
    # Volume Trend
    # =========================

    volume_ma_short = (

        df["Volume"]

        .rolling(5)

        .mean()

    )

    volume_ma_long = (

        df["Volume"]

        .rolling(20)

        .mean()

    )

    df["VOLUME_TREND"] = (

        volume_ma_short
        > volume_ma_long

    )
        
    # =========================
    # Volume Spike Detection
    # =========================

    volume_baseline = (

        df["Volume"]

        .rolling(20)

        .mean()

    )

    df["VOLUME_SPIKE"] = (

        df["Volume"]

        > (

            volume_baseline
            * 1.8

        )

    )        

    # =========================
    # Candle Body Strength
    # =========================

    candle_range = (

        df["High"]

        - df["Low"]

    )

    candle_body = (

        (
            df["Close"]

            - df["Open"]

        ).abs()

    )

    df["BODY_STRENGTH"] = (

        candle_body

        / candle_range.replace(
            0,
            0.0001
        )

    )



    # =========================
    # Trend Phase Classification
    # =========================

    conditions = [

        (
            (df["EMA9"] > df["EMA20"])
            &
            (df["Close"] > df["VWAP"])
        ),

        (
            (df["EMA9"] < df["EMA20"])
            &
            (df["Close"] < df["VWAP"])
        )

    ]

    choices = [

        "UPTREND",

        "DOWNTREND"

    ]

    df["TREND_PHASE"] = np.select(

        conditions,

        choices,

        default="SIDEWAYS"

    )

    # =========================
    # Breakout / Breakdown
    # =========================

    rolling_high = (

        df["High"]

        .rolling(20)

        .max()

        .shift(1)

    )

    rolling_low = (

        df["Low"]

        .rolling(20)

        .min()

        .shift(1)

    )

    df["BREAKOUT"] = (

        df["Close"]

        > rolling_high

    )

    df["BREAKDOWN"] = (

        df["Close"]

        < rolling_low

    )    

    # =========================
    # Market Structure
    # =========================

    prev_high = (
        df["High"]
        .shift(1)
    )

    prev_low = (
        df["Low"]
        .shift(1)
    )

    df["HIGHER_HIGH"] = (
        df["High"]
        > prev_high
    )

    df["HIGHER_LOW"] = (
        df["Low"]
        > prev_low
    )

    df["LOWER_HIGH"] = (
        df["High"]
        < prev_high
    )

    df["LOWER_LOW"] = (
        df["Low"]
        < prev_low
    )    


    # =========================
    # Failed Breakout Detection
    # =========================

    df["FAILED_BREAKOUT"] = (

        df["BREAKOUT"]

        &

        (
            df["Close"]
            < df["Open"]
        )

    )

    df["FAILED_BREAKDOWN"] = (

        df["BREAKDOWN"]

        &

        (
            df["Close"]
            > df["Open"]
        )

    )    


    # =========================
    # Consolidation Detection
    # =========================

    rolling_range = (

        (
            df["High"]
            .rolling(10)
            .max()
        )

        -

        (
            df["Low"]
            .rolling(10)
            .min()
        )

    )

    consolidation_threshold = (

        df["ATR"] * 2

    )

    df["CONSOLIDATING"] = (

        rolling_range
        < consolidation_threshold

    )    


    # =========================
    # Support / Resistance Distance
    # =========================

    resistance = (

        df["High"]

        .rolling(20)

        .max()

    )

    support = (

        df["Low"]

        .rolling(20)

        .min()

    )

    df["DISTANCE_TO_RESISTANCE"] = (

        (
            resistance
            - df["Close"]
        )

        / df["Close"]

    ) * 100

    df["DISTANCE_TO_SUPPORT"] = (

        (
            df["Close"]
            - support
        )

        / df["Close"]

    ) * 100    

    # =========================
    # Multi-Candle Structure Sequences
    # =========================

    df["LOWER_HIGH_SEQUENCE"] = (

        (df["High"] < df["High"].shift(1))

        &

        (
            df["High"].shift(1)
            < df["High"].shift(2)
        )

    )

    df["HIGHER_LOW_SEQUENCE"] = (

        (df["Low"] > df["Low"].shift(1))

        &

        (
            df["Low"].shift(1)
            > df["Low"].shift(2)
        )

    )


    # =========================
    # MACD
    # =========================

    ema_fast = (

        df["Close"]

        .ewm(span=12)

        .mean()

    )

    ema_slow = (

        df["Close"]

        .ewm(span=26)

        .mean()

    )

    df["MACD"] = (

        ema_fast
        - ema_slow

    )

    df["MACD_SIGNAL"] = (

        df["MACD"]

        .ewm(span=9)

        .mean()

    )    

    return df