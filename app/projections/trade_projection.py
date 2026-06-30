import math


def project_trade(

    symbol,
    latest_price,
    analysis,
    entry_setup,
    risk_setup,
    alignment_score=0,
    option_data=None

):

    """
    Trade Projection Engine

    Projects:
    - expected move
    - projected option gain
    - probability
    - trade grade
    """

    try:

        signal = str(
            analysis["signal"]
        ).strip().upper()

        score = abs(
            analysis["score"]
        )

        rr = risk_setup[
            "risk_reward"
        ]

        entry_quality = entry_setup[
            "entry_quality"
        ]

        market_regime = analysis.get(
            "market_regime",
            "CHOPPY"
        )        

        # =====================================
        # ATR-aware expected move
        # =====================================

        atr = analysis.get(
            "ATR",
            analysis.get(
                "atr",
                0
            )
        )

        atr_pct = (

            (atr / latest_price) * 100

            if latest_price > 0

            else 0

        )

        alignment_multiplier = 1.0

        regime_multiplier = 1.0

        if market_regime == "TRENDING_BULLISH":

            regime_multiplier = 1.4

        elif market_regime == "TRENDING_BEARISH":

            regime_multiplier = 1.4

        elif market_regime == "HIGH_VOLATILITY":

            regime_multiplier = 1.6

        elif market_regime == "LOW_VOLATILITY":

            regime_multiplier = 0.7

        elif market_regime == "CHOPPY":

            regime_multiplier = 0.8        

        if abs(alignment_score) >= 5:

            alignment_multiplier = 1.8

        elif abs(alignment_score) >= 3:

            alignment_multiplier = 1.5

        elif abs(alignment_score) >= 1:

            alignment_multiplier = 1.2

        expected_move_pct = max(
            0.5,
            min(
                atr_pct
                * alignment_multiplier
                * regime_multiplier
                * 2.2,
                8
            )
        )

        if signal in [

            "HIGH CONVICTION BEARISH",
            "BEARISH"

        ]:

            expected_move_pct *= -1

        # =====================================
        # Volatility-aware option expansion
        # =====================================

        delta_boost = 1.0

        if option_data:

            delta = abs(
                option_data.get(
                    "delta",
                    0.5
                )
                or 0.5
            )

            gamma = (
                option_data.get(
                    "gamma",
                    0
                )
                or 0
            )

            delta_boost += (
                delta * 0.8
            )

            delta_boost += (
                gamma * 10
            )

        projected_option_gain = (

            abs(expected_move_pct)

            * 12

            * delta_boost

        )

        # =====================================
        # Probability model
        # =====================================

        probability = 35

        # Signal strength
        probability += min(
            score * 1.2,
            15
        )

        # Multi-timeframe alignment
        probability += min(
            abs(alignment_score) * 2,
            12
        )

        # RR bonus
        if rr >= 2:

            probability += 10

        elif rr >= 1.5:

            probability += 5

        # Entry quality
        if entry_quality == "HIGH":

            probability += 8

        elif entry_quality == "MEDIUM":

            probability += 4

        # Weak volatility penalty
        reasons_text = " ".join(

            analysis.get(
                "reasons",
                []
            )

        ).lower()

        if "low volatility" in reasons_text:

            probability -= 10

        if "chop" in reasons_text:

            probability -= 10

        # Clamp
        probability = max(

            5,

            min(
                round(probability),
                95
            )

        )

        # =========================
        # Trade Allowed Override
        # =========================

        if not risk_setup.get(
            "trade_allowed",
            True
        ):

            probability = min(
                probability,
                55
            )        

        # =====================================
        # Expiry intelligence
        # =====================================

        if abs(alignment_score) >= 6:

            expiry = "This Week"

        elif abs(alignment_score) >= 3:

            expiry = "Next Week"

        else:

            expiry = "2-4 Weeks"

        
        # =====================================
        # Trade quality grading
        # =====================================

        grade = "C"

        if (
            probability >= 85
            and rr >= 2
        ):

            grade = "A+"

        elif (
            probability >= 75
            and rr >= 1.8
        ):

            grade = "A"

        elif (
            probability >= 65
            and rr >= 1.5
        ):

            grade = "B"

        elif (
            probability >= 55
            and rr >= 1.2
        ):

            grade = "B-"

        elif probability < 45:

            grade = "AVOID"

        # =========================
        # Trade Allowed Override
        # =========================

        if not risk_setup.get(
            "trade_allowed",
            True
        ):

            grade = "AVOID"            

        # =====================================
        # Use canonical risk-engine levels
        # =====================================

        target_price = risk_setup[
            "take_profit"
        ]

        stop_price = risk_setup[
            "stop_loss"
        ]            

        return {

            "expected_move_pct":
                round(
                    expected_move_pct,
                    2
                ),

            "projected_option_gain":
                round(
                    projected_option_gain,
                    1
                ),

            "target_price":
                round(
                    target_price,
                    2
                ),

            "stop_price":
                round(
                    stop_price,
                    2
                ),                

            "probability":
                probability,

            "best_expiry":
                expiry,

            "trade_grade":
                grade,
            
            "market_regime":
                market_regime                

        }

    except Exception as e:

        print(
            f"[PROJECTION ERROR] {e}"
        )

        return {

            "expected_move_pct": 0,

            "projected_option_gain": 0,

            "probability": 0,

            "target_price": 0,

            "stop_price": 0,

            "best_expiry": "N/A",

            "trade_grade": "N/A"

        }