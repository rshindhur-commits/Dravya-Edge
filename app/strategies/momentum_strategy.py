from app.config.settings import get_bool_env
from app.utils.runtime_logging import debug_print

import pandas as pd


def relative_strength_benchmark_enabled():
    """Should relative strength be measured against a benchmark, or against 0?

    Off by default, and that default is the point: with it off, `analyze_setup`
    receives no benchmark and scores exactly what it scored before, so the
    switch can ship without changing a live session. Turn it on only behind an
    A/B, because it moves the composite setup score -- and that score is already
    measured non-predictive and inverted, so "more correct" does not imply
    "better" here. See docs/SIGNAL_GAP_ANALYSIS.md §2.1.
    """

    return get_bool_env("RELATIVE_STRENGTH_BENCHMARK_ENABLED", False)


def _category_based_score(latest, df):

    bull_trend = 0
    bear_trend = 0

    if latest["EMA9"] > latest["EMA20"]:

        bull_trend += 1

    if latest["EMA9"] < latest["EMA20"]:

        bear_trend += 1

    if latest.get("EMA9_SLOPE", 0) > 0:

        bull_trend += 1

    if latest.get("EMA9_SLOPE", 0) < 0:

        bear_trend += 1

    if latest["TREND_PHASE"] == "UPTREND":

        bull_trend += 1

    if latest["TREND_PHASE"] == "DOWNTREND":

        bear_trend += 1

    bull_trend = min(bull_trend, 3)
    bear_trend = min(bear_trend, 3)

    bull_momentum = 0
    bear_momentum = 0

    if not pd.isna(latest.get("MACD")) and not pd.isna(latest.get("MACD_SIGNAL")):

        if latest["MACD"] > latest["MACD_SIGNAL"]:

            bull_momentum += 1

        if latest["MACD"] < latest["MACD_SIGNAL"]:

            bear_momentum += 1

    if latest["RSI"] > 55:

        bull_momentum += 1

    if latest["RSI"] < 45:

        bear_momentum += 1

    if latest["Close"] > latest["VWAP"]:

        bull_momentum += 1

    if latest["Close"] < latest["VWAP"]:

        bear_momentum += 1

    bull_momentum = min(bull_momentum, 3)
    bear_momentum = min(bear_momentum, 3)

    bull_participation = 0
    bear_participation = 0

    if latest["REL_VOLUME"] > 1.2:

        if latest["Close"] >= latest["VWAP"]:

            bull_participation += 1

        else:

            bear_participation += 1

    if latest["VOLUME_SPIKE"]:

        if latest["Close"] >= latest["Open"]:

            bull_participation += 1

        else:

            bear_participation += 1

    if latest["ATR_PCT"] > 0.35:

        bull_participation += 1
        bear_participation += 1

    bull_participation = min(bull_participation, 2)
    bear_participation = min(bear_participation, 2)

    bull_structure = 0
    bear_structure = 0

    if latest["BREAKOUT"] or latest.get("ORB_BREAKOUT", False):

        bull_structure += 1

    if latest["HIGHER_HIGH"]:

        bull_structure += 1

    if latest["BREAKDOWN"] or latest.get("ORB_BREAKDOWN", False):

        bear_structure += 1

    if latest["LOWER_HIGH"] or latest.get("LOWER_LOW", False):

        bear_structure += 1

    bull_structure = min(bull_structure, 2)
    bear_structure = min(bear_structure, 2)

    bull_score = bull_trend + bull_momentum + bull_participation + bull_structure
    bear_score = bear_trend + bear_momentum + bear_participation + bear_structure

    return bull_score if bull_score >= bear_score else -bear_score


def analyze_setup(df, benchmark_move_pct=None):
    """`benchmark_move_pct` is the session move of the symbol's sector reference.

    None means "no benchmark available or the switch is off", and is treated as
    zero -- which is what this function assumed unconditionally before, so the
    default path is unchanged.
    """

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

        # This was hardcoded to 0 under a comment naming QQQ, with no benchmark
        # ever fetched -- so "Strong relative strength" meant "up more than 0.5%
        # today", and on a day the sector is up 1.5% a name up 0.6% is lagging
        # badly and scored bullish. main.py has computed the sector reference
        # move all along, in _sector_strength, and recorded it as telemetry
        # without ever feeding it back here.
        benchmark_move = (
            0.0 if benchmark_move_pct is None else float(benchmark_move_pct)
        )

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

    category_score = _category_based_score(latest, df)

    neutral_reasons.append(
        f"Shadow category score: {category_score}"
    )




    # =========================================
    # Entry Timing Filters
    # =========================================

    entry_timing_ok = True
    timing_penalty = 2

    def apply_timing_penalty(current_score):
        """Move the score toward neutral by `timing_penalty`, never past it.

        These filters exist to say "the edge may be real but this is a poor
        moment to take it", so the penalty has to reduce conviction whichever
        way the score points. A bare `score -= timing_penalty` only does that
        for a positive score; against a negative one it *adds* conviction.

        Filters 1 and 2 both measure direction-agnostic conditions -- a large
        candle body, distance from VWAP as an absolute -- so before this they
        turned a chased short into a stronger short. A 1.5% extension below
        VWAP pushed a -6 toward -8, i.e. from BEARISH across the -7 HIGH
        CONVICTION threshold, on the strength of a filter meant to hold it back.

        Filter 3 below already guards its sign explicitly (`and score > 0` /
        `and score < 0`); this is the same rule expressed once so filters 1 and
        2 cannot drift from it again.
        """

        if current_score > 0:
            return max(0, current_score - timing_penalty)

        if current_score < 0:
            return min(0, current_score + timing_penalty)

        return current_score

    # -----------------------------------------
    # 1. Extended Candle Filter
    # -----------------------------------------

    if latest["BODY_STRENGTH"] > 0.8:

        neutral_reasons.append(
            "Extended candle risk"
        )

        score = apply_timing_penalty(score)

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

        score = apply_timing_penalty(score)

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

        score -= timing_penalty

        entry_timing_ok = False

    if (
        latest["RSI"] < 22
        and score < 0
    ):

        neutral_reasons.append(
            "Bearish momentum exhaustion"
        )

        score += timing_penalty

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
        "category_score": round(category_score, 1),
        "market_regime": market_regime,
        "entry_timing_ok": entry_timing_ok,        
        "reasons": final_reasons
    }