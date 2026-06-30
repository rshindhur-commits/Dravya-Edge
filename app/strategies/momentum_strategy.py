from app.utils.runtime_logging import debug_print

import pandas as pd


def analyze_setup(df):

    required_columns = [
        "EMA9",
        "EMA20",
        "RSI",
        "VWAP",
        "ATR"
    ]

    for column in required_columns:

        if column not in df.columns:

            return {
                "signal": "INVALID",
                "score": 0,
                "reasons": [f"Indicator warmup pending: {column}"]
            }

    latest = df.iloc[-1]

    for column in required_columns:

        if pd.isna(latest[column]):

            return {
                "signal": "INVALID",
                "score": 0,
                "reasons": [f"{column} is NaN"]
            }

    score = 0
    bullish_reasons = []

    bearish_reasons = []

    neutral_reasons = []

    # =========================================
    # Opening Range Breakout Momentum
    # =========================================

    if latest["ORB_BREAKOUT"]:

        score += 1

        bullish_reasons.append(
            "Opening range breakout"
        )

        # Stronger continuation
        if latest["Close"] > latest["VWAP"]:

            score += 1

            bullish_reasons.append(
                "ORB holding above VWAP"
            )


    if latest["ORB_BREAKDOWN"]:

        score -= 1

        bearish_reasons.append(
            "Opening range breakdown"
        )

        # Strong bearish continuation
        if latest["Close"] < latest["VWAP"]:

            score -= 1

            bearish_reasons.append(
                "ORB below VWAP"
            )    

    # =========================================
    # Relative Strength / Weakness
    # =========================================

    relative_strength = 0

    try:

        symbol_move = latest["SYMBOL_MOVE_PCT"]

        # Compare against market benchmark
        # Tech names → QQQ style behavior
        benchmark_move = 0

        if symbol_move > benchmark_move + 0.5:

            relative_strength += 1

            bullish_reasons.append(
                "Strong relative strength"
            )

        elif symbol_move < benchmark_move - 0.5:

            relative_strength -= 1

            bearish_reasons.append(
                "Relative weakness vs market"
            )

    except Exception:

        pass

    score += relative_strength    

    # EMA Trend
    if latest["EMA9"] > latest["EMA20"]:
        score += 1
        bullish_reasons.append("EMA bullish")

    # EMA Acceleration
    ema_trend = (
        df["EMA9_SLOPE"]
        .tail(4)
        .gt(0)
        .sum()
    )

    if ema_trend >= 3:
        score += 1
        bullish_reasons.append("EMA rising consistently")

    # MACD
    if (
        not pd.isna(latest.get("MACD"))
        and not pd.isna(latest.get("MACD_SIGNAL"))
    ):

        if latest["MACD"] > latest["MACD_SIGNAL"]:

            score += 1

            bullish_reasons.append(
                "MACD bullish"
            )

    else:

        neutral_reasons.append(
            "MACD warmup pending"
        )

    # RSI
    if 50 < latest["RSI"] < 75:
        score += 1
        bullish_reasons.append("RSI bullish")

    # RSI Direction
    rsi_trend = (
        df["RSI_SLOPE"]
        .tail(4)
        .gt(0)
        .sum()
    )

    if rsi_trend >= 3:
        score += 1
        bullish_reasons.append("RSI strengthening consistently")

    # VWAP
    if latest["Close"] > latest["VWAP"]:
        score += 1
        bullish_reasons.append("Above VWAP")

    if (

        latest["EMA9"] > latest["EMA20"]
        and latest["Close"] > latest["EMA9"]
        and latest["RSI"] > 60

    ):

        score += 1

        bullish_reasons.append(
            "Strong trend continuation"
        )


    # Intraday bearish trend

    if (

        latest["Close"] < latest["VWAP"]
        and latest["EMA9"] < latest["EMA20"]

    ):

        score -= 2

        bearish_reasons.append(
            "Intraday bearish trend"
        )        


    # Strong VWAP Separation
    if latest["VWAP_DISTANCE"] > 0.15:

        if latest["Close"] > latest["VWAP"]:

            score += 1

            bullish_reasons.append(
                "Strong bullish VWAP separation"
            )

        else:

            score -= 1

            bearish_reasons.append(
                "Strong bearish VWAP separation"
            )

    # Volume
    # =========================
    # Volume Structure
    # =========================

    # Strong relative volume
    if latest["REL_VOLUME"] > 1.2:

        if latest["Close"] > latest["VWAP"]:

            score += 1

            bullish_reasons.append(
                "Strong bullish relative volume"
            )

        else:

            score -= 1

            bearish_reasons.append(
                "Strong bearish relative volume"
            )

    # Increasing participation
    if latest["VOLUME_TREND"]:

        if latest["Close"] > latest["VWAP"]:

            score += 0.5

            bullish_reasons.append(
                "Bullish volume participation"
            )

        else:

            score -= 0.5

            bearish_reasons.append(
                "Bearish volume participation"
            )

    # Breakout confirmation spike
    if (
        latest["VOLUME_SPIKE"]
        and latest["Close"] > latest["VWAP"]
    ):

        score += 1

        bullish_reasons.append(
            "Volume breakout confirmation"
        )

    # Potential exhaustion spike
    if (
        latest["VOLUME_SPIKE"]
        and latest["BODY_STRENGTH"] < 0.3
    ):

        score -= 1

        bearish_reasons.append(
            "Possible exhaustion volume spike"
        )

    # ATR Volatility Expansion
    if latest["ATR_PCT"] > 0.35:

        if latest["Close"] > latest["VWAP"]:

            score += 1

            bullish_reasons.append(
                "Strong bullish volatility expansion"
            )

        else:

            score -= 1

            bearish_reasons.append(
                "Strong bearish volatility expansion"
            )

    # Candle Strength
    if latest["BODY_STRENGTH"] > 0.5:

        if latest["Close"] > latest["Open"]:

            score += 1

            bullish_reasons.append(
                "Strong bullish candle bodies"
            )

        else:

            score -= 1

            bearish_reasons.append(
                "Strong bearish candle bodies"
            )


    # =========================
    # Structure Analysis
    # =========================

    # Trend phase bonus

    if latest["TREND_PHASE"] == "UPTREND":

        score += 1

        bullish_reasons.append(
            "Confirmed uptrend phase"
        )

    elif latest["TREND_PHASE"] == "DOWNTREND":

        score -= 1

        bearish_reasons.append(
            "Confirmed downtrend phase"
        )

    elif latest["TREND_PHASE"] == "CONSOLIDATION":

        neutral_reasons.append(
            "Consolidation phase"
        )


    # =========================================
    # Volume Confirmed Breakdown
    # =========================================

    if latest["BREAKDOWN"]:

        # Strong institutional sell pressure
        if (

            latest["REL_VOLUME"] > 1.5

            and latest["Close"] < latest["VWAP"]

        ):

            score -= 3

            bearish_reasons.append(
                "High-volume bearish breakdown"
            )

        # Moderate breakdown
        elif latest["REL_VOLUME"] > 1.1:

            score -= 2

            bearish_reasons.append(
                "Confirmed bearish breakdown"
            )

        # Weak / low conviction breakdown
        else:

            score -= 1

            bearish_reasons.append(
                "Weak bearish breakdown"
            )

    # Bullish structure

    if (

        latest["HIGHER_HIGH"]
        and latest["HIGHER_LOW"]

    ):

        score += 1

        bullish_reasons.append(
            "Bullish market structure"
        )

    # Bearish structure

    if (
        latest["LOWER_HIGH"]
        and latest["LOWER_LOW"]
    ):

        score -= 1

        bearish_reasons.append(
            "Bearish market structure"
        )

    # Active breakout

    if latest["BREAKOUT"]:

        score += 1.5

        bullish_reasons.append(
            "Active breakout structure"
        )

    # Failed breakout

    if latest["FAILED_BREAKOUT"]:

        score -= 2

        bearish_reasons.append(
            "Failed breakout"
        )


    # =========================================
    # Failed Bullish Reclaim Detection
    # =========================================

    failed_vwap_reclaim = (

        latest["Close"] < latest["VWAP"]

        and latest["Open"] > latest["VWAP"]

        and latest["Close"] < latest["Open"]

    )

    if failed_vwap_reclaim:

        debug_print(
            f"[FAILED RECLAIM] "
            f"{failed_vwap_reclaim} "
            f"REL_VOL={latest['REL_VOLUME']}"
        )

        # Heavy rejection with participation
        if latest["REL_VOLUME"] > 1.3:

            score -= 3

            bearish_reasons.append(
                "High-volume failed VWAP reclaim"
            )

        # Moderate rejection
        else:

            score -= 1.5

            bearish_reasons.append(
                "Failed bullish reclaim"
            )        

    # Bullish consolidation

    if (

        latest["CONSOLIDATING"]
        and latest["EMA9"] > latest["EMA20"]

    ):

        score += 0.5

        bullish_reasons.append(
            "Bullish consolidation"
        )




    # Near resistance warning
    if (
        latest["DISTANCE_TO_RESISTANCE"] < 0.1
        and latest["RSI"] > 82
    ):

        score -= 1

        neutral_reasons.append(
            "Near resistance level"
        )

    # Near support bounce zone
    if (
        latest["DISTANCE_TO_SUPPORT"] < 0.5
        and latest["Close"] > latest["VWAP"]
    ):

        score += 0.5

        bullish_reasons.append(
            "Near support zone"
        )


    # =========================
    # Extension Analysis
    # =========================

    # Too extended above VWAP
    if latest["VWAP_DISTANCE"] > 2.5:

        score -= 1

        bearish_reasons.append(
            "Overextended above VWAP"
        )

    # Too extended above EMA9
    ema_extension = (
        (
            latest["Close"]
            - latest["EMA9"]
        ) / latest["EMA9"]
    ) * 100

    if ema_extension > 1.2:

        score -= 1

        bearish_reasons.append(
            "Overextended above EMA9"
        )

    # Healthy pullback zone
    if (
        0.1 < ema_extension < 0.6
        and latest["Close"] > latest["EMA9"]
    ):

        score += 0.5

        bullish_reasons.append(
            "Healthy EMA pullback structure"
        )





    # Weakening Trend Penalty
    if (
        latest["EMA9_SLOPE"] < 0
        and latest["RSI_SLOPE"] < 0
        and latest["Close"] < latest["EMA9"]
    ):
        score -= 1
        bearish_reasons.append("Trend exhaustion detected")


    if (

        latest["Close"] > latest["EMA20"]
        and latest["Close"] > latest["VWAP"]

    ):

        score += 0.5

        bullish_reasons.append(
            "Healthy pullback above support"
        )


    # Weak volatility penalty
    if (
        latest["ATR_PCT"] < 0.15
        and latest["EMA9"] < latest["EMA20"]
    ):
        score -= 0.5
        neutral_reasons.append("Low volatility / possible chop")

    #score = max(score, 0)

    # =========================================
    # Bearish Momentum Scoring
    # =========================================

    bearish_strength = 0

    # Below VWAP
    if latest["Close"] < latest["VWAP"]:

        bearish_strength += 1

    # EMA bearish stack
    if latest["EMA9"] < latest["EMA20"]:

        bearish_strength += 1

    # Lower highs

    if latest["LOWER_HIGH"]:

        bearish_strength += 1

    # Consecutive lower highs
    if latest["LOWER_HIGH_SEQUENCE"]:

        bearish_strength += 2

        bearish_reasons.append(
            "Consecutive lower highs forming"
        )

    # Breakdown structure
    if latest["BREAKDOWN"]:

        bearish_strength += 2

    # MACD bearish
    if latest["MACD"] < latest["MACD_SIGNAL"]:

        bearish_strength += 1

    # RSI weak
    if latest["RSI"] < 45:

        bearish_strength += 1


    # Weighted bearish conviction
    if bearish_strength >= 6:

        score -= 3

        bearish_reasons.append(
            "Strong bearish momentum"
        )

    elif bearish_strength >= 4:

        score -= 2

        bearish_reasons.append(
            "Moderate bearish momentum"
        )

    elif bearish_strength >= 2:

        score -= 1

        bearish_reasons.append(
            "Weak bearish pressure"
        )


    # =========================
    # Market Regime Detection
    # =========================

    market_regime = "CHOPPY"

    # High volatility environment
    if latest["ATR_PCT"] > 0.45:

        market_regime = "HIGH_VOLATILITY"

    # Low volatility / compressed market
    elif latest["ATR_PCT"] < 0.18:

        market_regime = "LOW_VOLATILITY"

    # Trending bullish market
    elif (

        latest["EMA9"] > latest["EMA20"]
        and latest["RSI"] > 55
        and (
            pd.isna(latest.get("MACD_SIGNAL"))
            or latest["MACD"] > latest["MACD_SIGNAL"]
        )

    ):

        market_regime = "TRENDING_BULLISH"

    # Trending bearish market
    elif (

        latest["EMA9"] < latest["EMA20"]
        and latest["RSI"] < 45
        and (
            pd.isna(latest.get("MACD_SIGNAL"))
            or latest["MACD"] < latest["MACD_SIGNAL"]
        )
    ):

        market_regime = "TRENDING_BEARISH"

    # Add regime notes
    # =========================
    # Regime Adjusted Scoring
    # =========================

    if market_regime == "LOW_VOLATILITY":

        score -= 0.25

        neutral_reasons.append(
            "Reduced score due to low volatility"
        )

    elif market_regime == "CHOPPY":

        score -= 0.25

        neutral_reasons.append(
            "Reduced score due to choppy conditions"
        )

    elif market_regime == "TRENDING_BULLISH":

        score += 0.5

        bullish_reasons.append(
            "Boosted score for bullish regime"
        )

    # elif market_regime == "HIGH_VOLATILITY":

    #     score += 0.5

    #     reasons.append(
    #         "Boosted score for volatility expansion"
    #     )

    # Prevent negative scores
    #score = max(score, 0)


    # ==========================================
    # STRUCTURAL MARKET BIAS
    # ==========================================

    bullish_bias = False
    bearish_bias = False

    # Strong bearish structure
    if (

        latest["Close"] < latest["VWAP"]

        and latest["EMA9"] < latest["EMA20"]

        and (
            latest["TREND_PHASE"] == "DOWNTREND"
            or latest["LOWER_HIGH"]
            or latest["BREAKDOWN"]
        )

    ):

        bearish_bias = True

    # Strong bullish structure
    if (

        latest["Close"] > latest["VWAP"]

        and latest["EMA9"] > latest["EMA20"]

        and (
            latest["TREND_PHASE"] == "UPTREND"
            or latest["HIGHER_HIGH"]
            or latest["BREAKOUT"]
        )

    ):

        bullish_bias = True


    # ==========================================
    # DIRECTIONAL SCORE WEIGHTING
    # ==========================================

    if bearish_bias:

        if latest["MACD"] > latest["MACD_SIGNAL"]:

            score -= 0.5

            bearish_reasons.append(
                "Bullish momentum weakened by bearish structure"
            )

        if latest["RSI"] > 50:

            score -= 0.5

            bearish_reasons.append(
                "RSI recovery weakened by bearish structure"
            )

    if bullish_bias:

        if latest["MACD"] < latest["MACD_SIGNAL"]:

            score += 0.5

            bullish_reasons.append(
                "Bearish momentum weakened by bullish structure"
            )

        if latest["RSI"] < 50:

            score += 0.5

            bullish_reasons.append(
                "Weak RSI softened by bullish structure"
            )


    debug_print(
        f"[BIAS DEBUG] "
        f"bullish_bias={bullish_bias} "
        f"bearish_bias={bearish_bias}"
    )

    debug_print(
        f"[STRUCTURE CHECK] "
        f"Close<VWAP={latest['Close'] < latest['VWAP']} "
        f"EMA9<EMA20={latest['EMA9'] < latest['EMA20']} "
        f"TREND={latest['TREND_PHASE']} "
        f"LOWER_HIGH={latest['LOWER_HIGH']} "
        f"BREAKDOWN={latest['BREAKDOWN']}"
    )




    # =========================================
    # Entry Timing Filters
    # =========================================

    entry_timing_ok = True

    # -----------------------------------------
    # 1. Extended Candle Filter
    # -----------------------------------------

    if latest["BODY_STRENGTH"] > 0.8:

        neutral_reasons.append(
            "Extended candle risk"
        )

        score -= 2

        entry_timing_ok = False

    # -----------------------------------------
    # 2. VWAP Extension Filter
    # -----------------------------------------

    vwap_distance_pct = abs(

        (
            latest["Close"]
            - latest["VWAP"]
        )

        / latest["VWAP"]

    ) * 100

    if vwap_distance_pct > 1.0:

        neutral_reasons.append(
            "Price extended from VWAP"
        )

        score -= 2

        entry_timing_ok = False

    # -----------------------------------------
    # 3. Momentum Exhaustion Filter
    # -----------------------------------------

    if (
        latest["RSI"] > 78
        and score > 0
    ):

        neutral_reasons.append(
            "Bullish momentum exhaustion"
        )

        score -= 2

        entry_timing_ok = False

    if (
        latest["RSI"] < 22
        and score < 0
    ):

        neutral_reasons.append(
            "Bearish momentum exhaustion"
        )

        score -= 2

        entry_timing_ok = False


    # Signal Logic
    if score >= 7:

        signal = "HIGH CONVICTION BULLISH"

    elif score >= 4:

        signal = "BULLISH"

    elif score <= -7:

        signal = "HIGH CONVICTION BEARISH"

    elif score <= -4:

        signal = "BEARISH"

    else:

        signal = "NEUTRAL"

    # =====================================
    # Direction-consistent narrative
    # =====================================

    if "BULLISH" in signal:
        final_reasons = (
            bullish_reasons
            + neutral_reasons
        )

    elif "BEARISH" in signal:
        final_reasons = (
            bearish_reasons
            + neutral_reasons
        )

    else:
        final_reasons = neutral_reasons

    return {
        "signal": signal,
        "score": round(score, 1),
        "market_regime": market_regime,
        "entry_timing_ok": entry_timing_ok,        
        "reasons": final_reasons
    }