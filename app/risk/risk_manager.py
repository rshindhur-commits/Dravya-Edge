from app.config.settings import get_float_env
from app.utils.runtime_logging import debug_print
from app.gates import validate_price_geometry
from app.strategies.setup_registry import is_short_setup


def _safe_value(row, key, default=None):

    value = row.get(key, default)

    try:

        if value != value:

            return default

    except Exception:

        return default

    return value


def _is_short_entry(entry_type):
    """Delegates to the setup registry; this listed a setup that cannot occur."""

    return is_short_setup(entry_type)


def _risk_direction(analysis, entry_type):

    if _is_short_entry(entry_type):

        return "PUT"

    signal = str(
        (analysis or {}).get("signal", "")
    ).upper()

    if "BEARISH" in signal:

        return "PUT"

    if "BULLISH" in signal:

        return "CALL"

    return "UNKNOWN"


# Setups whose stop is anchored to local structure rather than the swing extreme.
# COILED_BREAKOUT was listed here and cannot be emitted; see setup_registry.
STRUCTURE_STOP_SETUPS = {
    "BREAKOUT",
    "BREAKOUT_LONG",
    "BREAKDOWN_SHORT",
}


def calculate_risk(df, analysis, entry_setup, stop_anchor="SWING"):
    """Size risk for a candidate setup.

    `stop_anchor` selects where a breakout/breakdown stop is anchored:

    * "SWING"     - current production behaviour. The stop is the wider of the
                    recent swing extreme and an ATR stop, then floored at a full
                    ATR. On 2026-07-29 this put BREAKDOWN_SHORT at an average RR
                    of 1.13 (6 of 64 clearing the 1.5 floor) and BREAKOUT at 1.33,
                    because `recent_high`/`recent_low` sit at the far end of the
                    swing price just travelled.
    * "STRUCTURE" - candidate change under evaluation. Anchors the stop to local
                    structure and applies the 0.25 x ATR floor, i.e. the treatment
                    EMA_PULLBACK already gets (average RR 3.05).

    "SWING" is the default so nothing changes until a measured comparison
    supports the switch. tools/regression_ab.py runs both arms over an archived
    day and reports the R difference.
    """

    entry_type_for_anchor = (
        (entry_setup or {}).get("entry_type")
        if isinstance(entry_setup, dict)
        else None
    )
    structure_stops = (
        str(stop_anchor or "SWING").upper() == "STRUCTURE"
        and str(entry_type_for_anchor or "").upper() in STRUCTURE_STOP_SETUPS
    )

    recent_high = (
        df["High"]
        .shift(1)
        .tail(5)
        .max()
    )

    recent_low = (
        df["Low"]
        .shift(1)
        .tail(5)
        .min()
    )

    latest = df.iloc[-1]

    if analysis["signal"] in [
        "NEUTRAL",
        "INVALID"
    ]:

        return {

            "risk_reward": 0,

            "max_loss": 0,

            "position_size": 0,

            "trade_allowed": False,

            "entry_price": None,

            "stop_loss": None,

            "take_profit": None,

            "reasons": [
                "Neutral trend environment"
            ]
        }    

    if entry_setup is None:

        return {

            "risk_reward": 0,

            "max_loss": 0,

            "position_size": 0,

            "trade_allowed": False,

            "entry_price": None,

            "stop_loss": None,

            "take_profit": None,

            "reasons": [
                "No valid entry setup"
            ]
        }    

    entry_price = latest["Close"]

    stop_loss = None
    take_profit = None
    risk_reward = 0
    trade_allowed = True
    max_risk_pct = 1.0

    reasons = []

    atr = latest["ATR"]

    entry_type = entry_setup.get(
        "entry_type",
        "NO_ENTRY"
    )

    rolling_support = _safe_value(
        latest,
        "ROLLING_SUPPORT",
        recent_low
    )

    rolling_resistance = _safe_value(
        latest,
        "ROLLING_RESISTANCE",
        recent_high
    )

    prev_low = _safe_value(
        latest,
        "PREV_LOW",
        recent_low
    )

    prev_high = _safe_value(
        latest,
        "PREV_HIGH",
        recent_high
    )

    market_regime = analysis.get(
        "market_regime",
        "CHOPPY"
    )

    # =========================
    # Dynamic ATR Risk Engine
    # =========================

    stop_atr_multiplier = 1.3
    target_atr_multiplier = 3.0

    if market_regime == "HIGH_VOLATILITY":

        stop_atr_multiplier = 1.8
        target_atr_multiplier = 4.0

    elif market_regime == "LOW_VOLATILITY":

        stop_atr_multiplier = 0.6
        target_atr_multiplier = 1.8

    elif market_regime == "CHOPPY":

        stop_atr_multiplier = 1.0
        target_atr_multiplier = 1.5

    elif "TRENDING" in market_regime:

        stop_atr_multiplier = 1.5
        target_atr_multiplier = 3.5    


    if _is_short_entry(entry_type):

        if entry_type == "EMA_REJECTION_SHORT":

            stop_loss = max(
                latest["High"] + (atr * 0.15),
                latest["EMA9"] + (atr * 0.10)
            )

            structure_target = min(
                rolling_support,
                prev_low,
                entry_price - (atr * 1.2)
            ) - (atr * 0.10)

            atr_target = (
                entry_price - (atr * 1.8)
            )

            take_profit = min(
                structure_target,
                atr_target
            )

        elif entry_type == "VWAP_REJECTION":

            stop_loss = max(
                latest["VWAP"] + (atr * 0.15),
                latest["High"] + (atr * 0.10)
            )

            take_profit = min(
                rolling_support - (atr * 0.15),
                entry_price - (atr * 2.0)
            )

        elif structure_stops:

            # Local-structure stop, mirroring EMA_REJECTION_SHORT/VWAP_REJECTION.
            stop_loss = max(
                latest["High"] + (atr * 0.15),
                latest["EMA9"] + (atr * 0.10)
            )

            take_profit = min(
                recent_low - (atr * 0.20),
                entry_price - (
                    atr * target_atr_multiplier
                )
            )

        else:

            stop_loss = max(
                recent_high,
                entry_price + (
                    atr * stop_atr_multiplier
                )
            )

            take_profit = min(
                recent_low - (atr * 0.20),
                entry_price - (
                    atr * target_atr_multiplier
                )
            )
        
        # DEBUG: Show SHORT stop calculation
        debug_print(
            f"[SHORT STOP DEBUG] type={entry_type} "
            f"entry={entry_price} recent_high={recent_high} "
            f"support={rolling_support} prev_low={prev_low} "
            f"atr={atr} stop_atr_mult={stop_atr_multiplier} "
            f"atr_calc={atr * stop_atr_multiplier} "
            f"stop_loss={stop_loss} target={take_profit}"
        )

    else:
        if entry_type == "EMA_PULLBACK":

            stop_loss = min(
                latest["Low"] - (atr * 0.15),
                latest["EMA9"] - (atr * 0.10)
            )

            take_profit = max(
                rolling_resistance + (atr * 0.10),
                entry_price + (atr * 1.8)
            )

        elif entry_type in [
            "BREAKOUT",
            "BREAKOUT_LONG",
            "COILED_BREAKOUT"
        ]:

            stop_loss = (
                # Local-structure stop, mirroring the EMA_PULLBACK treatment.
                min(
                    latest["Low"] - (atr * 0.15),
                    latest["EMA9"] - (atr * 0.10)
                )
                if structure_stops
                else min(
                    recent_low,
                    entry_price - (
                        atr * stop_atr_multiplier
                    )
                )
            )

            take_profit = max(
                rolling_resistance + (atr * 0.20),
                entry_price + (
                    atr * target_atr_multiplier
                )
            )

        else:

            atr_stop = (
                entry_price -
                (atr * stop_atr_multiplier)
            )
            vwap_stop = latest["VWAP"]
            if abs(entry_price - vwap_stop) <= atr:
                stop_loss = min(
                    atr_stop,
                    vwap_stop
                )
            else:
                stop_loss = atr_stop
            take_profit = (
                entry_price + (
                    atr * target_atr_multiplier
                )
            )

    # =========================
    # Risk Per Share
    # =========================

    minimum_stop_distance = atr

    if entry_type == "EMA_PULLBACK" or structure_stops:

        # A structure-anchored stop must not be widened back to a full ATR;
        # doing so is what collapsed breakout/breakdown RR below the 1.5 floor.
        minimum_stop_distance = atr * 0.25

    # Absolute floor on stop distance, as a fraction of price.
    #
    # The ATR floor above is anchored to short-bar ATR, so a fraction of it can
    # be an arbitrarily small distance in percentage terms. On 2026-07-30 every
    # EMA_PULLBACK entry cleared that floor and still got a stop of 0.13%-0.36%
    # of price, against option round-trip spreads of 2.1%-8.0%. Those trades were
    # unwinnable by construction: the move required to clear the spread was
    # several times the distance to the stop, so the R multiples they produced
    # measured noise rather than edge.
    #
    # This floor is in price terms because that is the term the pathology lives
    # in, and it leaves legitimate structure stops alone -- a stop already wider
    # than the floor is untouched. It sits below max_stop_distance_pct below, so
    # the two cannot fight. Tunable so it can be A/B tested against archived days
    # rather than argued about; raising it means fewer candidates clear the RR
    # gate, which is the intended trade.
    minimum_stop_pct = get_float_env("MIN_STOP_DISTANCE_PCT", 0.50)
    minimum_stop_distance = max(
        minimum_stop_distance,
        entry_price * (minimum_stop_pct / 100.0)
    )

    original_stop_loss = stop_loss
    original_risk_per_share = abs(entry_price - stop_loss)
    original_reward = abs(take_profit - entry_price)
    original_risk_reward = (
        original_reward / original_risk_per_share
        if original_risk_per_share > 0
        else 0
    )

    if abs(entry_price - stop_loss) < minimum_stop_distance:

        if stop_loss < entry_price:

            stop_loss = (
                entry_price
                - minimum_stop_distance
            )

        else:

            stop_loss = (
                entry_price
                + minimum_stop_distance
            )

        adjusted_risk_per_share = abs(entry_price - stop_loss)
        adjusted_risk_reward = (
            original_reward / adjusted_risk_per_share
            if adjusted_risk_per_share > 0
            else 0
        )
        reasons.append(
            "ATR floor adjusted stop: "
            f"original_stop={round(original_stop_loss, 2)} "
            f"adjusted_stop={round(stop_loss, 2)} "
            f"rr_before={round(original_risk_reward, 2)} "
            f"rr_after={round(adjusted_risk_reward, 2)}"
        )


    risk_per_share = abs(
        entry_price - stop_loss
    )

    stop_distance_pct = (
        risk_per_share / entry_price
    ) * 100

    max_stop_distance_pct = 0.75

    if market_regime == "HIGH_VOLATILITY":

        max_stop_distance_pct = 1.15

    elif market_regime == "LOW_VOLATILITY":

        max_stop_distance_pct = 0.50

    elif "TRENDING" in market_regime:

        max_stop_distance_pct = 0.95

    
    if stop_distance_pct > max_stop_distance_pct:

        trade_allowed = False

        reasons.append(
            f"Stop too wide: {round(stop_distance_pct, 2)}%"
        )

    # Prevent division issues
    if risk_per_share <= 0:

        return {

            "trade_allowed": False,
            "reasons": ["Invalid risk calculation"]
        }

    intended_direction = _risk_direction(
        analysis,
        entry_type
    )

    if not validate_price_geometry(
        intended_direction,
        entry_price,
        stop_loss,
        take_profit
    ):

        return {
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "risk_reward": 0,
            "max_risk_pct": round(max_risk_pct, 2),
            "trade_allowed": False,
            "reasons": reasons + [
                "INVALID_PRICE_GEOMETRY"
            ]
        }


    # =========================
    # Risk Reward Ratio
    # =========================

    reward = abs(
        take_profit - entry_price
    )

    if reward < (atr * 1.2):

        trade_allowed = False

        reasons.append(
            "Target too close to justify trade"
        )

    risk_reward = (
        reward / risk_per_share
    )

    # DEBUG: Print exact RR before rounding
    debug_print(
        f"[RR DEBUG] "
        f"reward={reward:.6f} "
        f"risk_per_share={risk_per_share:.6f} "
        f"rr_exact={risk_reward:.6f} "
        f"rr_rounded={round(risk_reward, 2)}"
    )

    # =========================
    # Poor RR Filter
    # =========================

    if risk_reward >= 3:

        reasons.append(
            "Excellent asymmetric setup"
        )

    elif risk_reward >= 2:

        reasons.append(
            "Strong reward/risk profile"
        )

    elif risk_reward < 1.2:

        reasons.append(
            "Weak asymmetric profile"
        )    

    RR_MIN_THRESHOLD = 1.5
    RR_EPSILON = 1e-9  # Epsilon for floating-point safety
    
    debug_print(
        f"[RR THRESHOLD] "
        f"rr={risk_reward:.6f} < {RR_MIN_THRESHOLD}? {risk_reward < RR_MIN_THRESHOLD - RR_EPSILON} "
        f"entry_quality={entry_setup.get('entry_quality', 'UNKNOWN')}"
    )

    if risk_reward < RR_MIN_THRESHOLD - RR_EPSILON:

        trade_allowed = False

        reasons.append(
            f"Risk/Reward below minimum threshold ({RR_MIN_THRESHOLD})"
        )

        if entry_setup.get("entry_quality", "UNKNOWN") == "LOW":

            reasons.append(
                "Low quality entry with poor RR"
            )

    # =========================
    # Choppy Market Filter
    # =========================

    #market_regime = analysis.get("market_regime", "UNKNOWN")

    if market_regime == "CHOPPY":

        max_risk_pct = 0.5

        reasons.append(
            "Reduced size due to choppy market"
        )

    # =========================
    # High Conviction Boost
    # =========================

    if analysis["signal"] == "HIGH CONVICTION BULLISH":

        max_risk_pct = min(
            max_risk_pct + 0.5,
            2.0
        )

        reasons.append(
            "High conviction setup"
        )

    # =========================
    # Weak Entry Filter
    # =========================

    if entry_setup.get("entry_quality", "UNKNOWN") == "LOW":

        max_risk_pct = min(
            max_risk_pct,
            0.5
        )

        reasons.append(
            "Weak entry quality"
        )

    # =========================
    # Avoid Chasing Filter
    # =========================

    if entry_setup.get("avoid_chasing", False):

        trade_allowed = False

        reasons.append(
            "Avoid chasing extended move"
        )

    return {

        "entry_price": round(entry_price, 2),

        "stop_loss": round(stop_loss, 2),

        "take_profit": round(take_profit, 2),

        "risk_reward": round(risk_reward, 2),

        "max_risk_pct": round(max_risk_pct, 2),

        "trade_allowed": trade_allowed,

        "reasons": reasons
    }


def build_risk_rule_evaluations(risk_result, scan_id, symbol, setup=None, min_rr=1.5):
    """Native structured audit for risk-manager output."""
    from app.gates.rule_evaluation import RuleEvaluation

    risk_result = risk_result or {}
    rr = risk_result.get("risk_reward", 0)
    allowed = bool(risk_result.get("trade_allowed"))
    return [
        RuleEvaluation(scan_id, symbol, setup, "RR", "Risk", rr, min_rr, rr >= min_rr, rr < min_rr, 90),
        RuleEvaluation(scan_id, symbol, setup, "Risk Geometry", "Risk", allowed, True, allowed, not allowed, 85),
    ]