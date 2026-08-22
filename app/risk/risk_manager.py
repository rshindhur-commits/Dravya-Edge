from app.config.settings import get_bool_env, get_float_env
from app.utils.runtime_logging import debug_print
from app.gates import validate_price_geometry
from app.risk import swing_anchor
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


def avoid_chasing_blocks():
    """Whether an extended entry should be refused outright.

    On by default: this is a switch for an experiment, not a decision. Read at
    call time rather than import so an arm can set it without a restart, and so
    tests can exercise both sides. See the enforcement site for what it costs.
    """

    return get_bool_env("AVOID_CHASING_BLOCKS", True)


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


def calculate_risk(df, analysis, entry_setup, stop_anchor="SWING", htf=None):
    """Size risk for a candidate setup.

    `htf` is the higher-timeframe frame (1h in both live and replay) and is only
    read when SWING_STRUCTURE_ENABLED is on, in which case it replaces the whole
    stop/target geometry -- see app/risk/swing_anchor.py for why that is the one
    change with a ceiling above break-even. Passing it costs nothing when the
    mode is off; not passing it while the mode is on rejects the trade rather
    than quietly reverting to the intraday anchor.

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

    # Scaled together at the end of this block by ATR_DISTANCE_SCALE, which is
    # what actually moves the trade's size. Raising the max-stop cap alone does
    # nothing: it stops rejecting wide stops, and these decide whether any get
    # produced. Two knobs that each look sufficient and are only jointly so is
    # how three earlier arms in this project ran byte-identical to their
    # baselines.
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

    # Both scaled by the same factor so the reward-to-risk ratio the gate checks
    # is unchanged -- this widens the trade, it does not make it look better.
    # MAX_STOP_DISTANCE_SCALE has to move with it or the cap rejects everything
    # the wider multiplier produces.
    _distance_scale = get_float_env("ATR_DISTANCE_SCALE", 1.0)
    stop_atr_multiplier *= _distance_scale
    target_atr_multiplier *= _distance_scale

    if _is_short_entry(entry_type):

        if entry_type == "EMA_REJECTION_SHORT":

            # Mirror of the EMA_PULLBACK anchor; same reason for the scale.
            stop_loss = max(
                latest["High"] + (atr * 0.15 * _distance_scale),
                latest["EMA9"] + (atr * 0.10 * _distance_scale)
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

            # Anchored to this 15m bar's low and its EMA9, which for a liquid
            # megacap sit a fraction of a percent from price. That anchor, not
            # the max-stop cap, is why every stop lands at 0.5-0.75% and why the
            # strategy can only hunt moves smaller than the option spread it
            # pays. _distance_scale widens the offsets so the same setup can be
            # replayed hunting a multi-percent move; at 1.0 the arithmetic is
            # unchanged.
            stop_loss = min(
                latest["Low"] - (atr * 0.15 * _distance_scale),
                latest["EMA9"] - (atr * 0.10 * _distance_scale)
            )

            take_profit = max(
                rolling_resistance + (atr * 0.10),
                entry_price + (atr * 1.8 * _distance_scale)
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
    # Higher-timeframe anchor
    # =========================

    # Replaces whichever stop the block above produced. The setups differ in
    # where they read local structure from; under this mode none of that matters,
    # because the pathology is the timeframe itself rather than the setup.
    swing_mode = swing_anchor.swing_mode_enabled()
    swing_pivot = None

    if swing_mode:

        levels = swing_anchor.swing_levels(
            htf,
            entry_price,
            _is_short_entry(entry_type) or _risk_direction(analysis, entry_type) == "PUT",
        )

        if levels is None:

            # Deliberately not a fallback to the intraday anchor. Running some
            # trades on 1h structure and the rest on the 15m bar would produce a
            # blended arm that answers neither question, and the blend would be
            # invisible in the result.
            return {
                "risk_reward": 0,
                "max_loss": 0,
                "position_size": 0,
                "trade_allowed": False,
                "entry_price": round(entry_price, 2),
                "stop_loss": None,
                "take_profit": None,
                "reasons": [
                    "Swing anchor unavailable: no usable higher-timeframe frame"
                ],
            }

        stop_loss, take_profit, swing_pivot, _htf_atr = levels

        swing_is_short = (
            _is_short_entry(entry_type)
            or _risk_direction(analysis, entry_type) == "PUT"
        )

        # The RR floor cannot filter in this mode -- reward is a fixed multiple
        # of risk, so RR is constant. This is what replaces it.
        has_headroom, available = swing_anchor.headroom_ok(
            htf, entry_price, stop_loss, swing_is_short
        )

        if not has_headroom:

            trade_allowed = False

            reasons.append(
                "No headroom to the next 1h level: "
                f"{round(available / entry_price * 100, 2)}% available"
            )

        reasons.append(
            f"Swing anchor: pivot={round(swing_pivot, 2)} "
            f"stop={round(stop_loss, 2)} "
            f"distance={round(abs(entry_price - stop_loss) / entry_price * 100, 2)}%"
        )

    # =========================
    # Risk Per Share
    # =========================

    minimum_stop_distance = atr

    if swing_mode:

        # The 15m ATR floor is meaningless against a 1h pivot -- it is smaller by
        # construction -- and applying it would only ever narrow a stop this mode
        # widened on purpose. The percentage band below governs instead.
        minimum_stop_distance = 0.0

    elif entry_type == "EMA_PULLBACK" or structure_stops:

        # Was a flat `atr * 0.25`, on the reasoning that widening a
        # structure-anchored stop to a full ATR collapsed breakout RR below the
        # 1.5 floor. True, and it optimised the wrong thing: RR is only worth
        # protecting if the stop survives long enough to collect it.
        #
        # 2026-08-12 is the case that broke the argument. SMCI was flagged CALL
        # five times on an EMA_PULLBACK, RR up to 8.02, on a day it moved 18.86%.
        # Its stop sat 0.50% below entry -- a quarter-ATR on a name whose session
        # range was 12.4% -- and price took it out at 11:00 before running to
        # 38.15. At a full ATR the stop held and the same trade reached its
        # target, at RR 2.06.
        #
        # Measured over 181 archived candidates, widening this is the single
        # largest improvement available to the stop geometry:
        #
        #     x0.25  23.4% win  -0.170R      x1.50  40.4% win  -0.078R
        #     x1.00  29.3% win  -0.169R      x3.00  58.4% win  -0.037R
        #
        # None of those are positive, so this does not make the strategy work --
        # it stops one specific way of losing. Note also that a wider stop lowers
        # RR on the same target, so the RR gate has to be recalibrated with it:
        # at x1.50 the break-even RR is about 1.47, not 2.0. Changing this alone
        # trades stop-outs for RR-gate rejections.
        #
        # **Default deliberately left at 0.25.** Raising it here does not fix
        # SMCI: its 0.81% stop was structure-anchored, already wider than both
        # this floor (0.47%) and the price floor (0.50%), so a floor change would
        # not have reached it. Only a floor above the structure level does, and
        # that means overriding structure everywhere -- which is what
        # `test_ema_pullback_does_not_force_full_atr_stop_floor` exists to
        # prevent, and it breaks replay parity, the one instrument available for
        # judging whether the change was right.
        #
        # So this is a switch, not a decision. Set the variable to run the
        # experiment against archived days; leave it alone and behaviour is
        # exactly as before.
        minimum_stop_distance = atr * get_float_env(
            "EMA_PULLBACK_ATR_STOP_MULT", 0.25
        )

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
    minimum_stop_pct = (
        swing_anchor.min_stop_distance_pct()
        if swing_mode
        else get_float_env("MIN_STOP_DISTANCE_PCT", 0.50)
    )
    price_floor_distance = entry_price * (minimum_stop_pct / 100.0)

    # ...but bounded in ATR, because a fraction of *price* is a proxy for a cost
    # that lives in the *option's spread*, and the proxy breaks on a
    # low-volatility underlying.
    #
    # `stop_viability` says so directly: "a 0.50% stop is ample on a penny-wide
    # contract and still unwinnable on one quoted 8% wide, and a single
    # price-term floor cannot tell those apart." It has enforced the real term --
    # the specific contract's round-trip spread, at MIN_STOP_SPREAD_MULTIPLE --
    # since 2026-07-31. The economic floor is handled per contract; this one is
    # the crude stand-in that predates it.
    #
    # Where the stand-in goes wrong, measured over 09:30-15:59 on 2026-08-19..21,
    # as the 0.50% floor expressed in ATR:
    #
    #     SPY   4.08     QQQ   2.31        <- never traded, 0 of 817 Risk passes
    #     GOOGL 1.73     AAPL  1.51
    #     NFLX  1.43     AMZN  1.39        <- the widest the app actually trades
    #     PLTR  0.78     SPCX  0.59
    #
    # SPY and QQQ produced 3,300+ entry signals each over ten days and passed the
    # Risk stage **zero** times: 958 RR readings apiece, maxima of 0.81 and 1.64
    # against a 2.0 gate. Not a close call -- an arithmetic impossibility fixed
    # before the market opened, because reward is pinned to ATR while this floor
    # is pinned to price.
    #
    # 2.0 sits above every symbol the app currently trades, so this cannot loosen
    # a stop on any trade being taken today; it only releases the instruments the
    # floor had made unreachable. A stop two ATR from entry is wide by any
    # standard, so the cap is defensible on its own terms rather than only as the
    # number that separates these two groups. 0 disables it and restores the
    # unbounded price floor exactly.
    atr_cap_multiple = get_float_env("MIN_STOP_DISTANCE_ATR_CAP", 2.0)

    if atr_cap_multiple > 0 and atr and atr == atr and atr > 0:

        price_floor_distance = min(price_floor_distance, atr * atr_cap_multiple)

    minimum_stop_distance = max(
        minimum_stop_distance,
        price_floor_distance
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

    # A stop that had to be invented is not a stop.
    #
    # The floor above rescues a setup whose structure gave less distance than
    # the option spread needs. But rescuing it puts the stop where nothing in
    # the chart says it belongs, and the target -- set at a multiple of that
    # distance -- inherits the same arbitrariness. What the floor actually
    # detects is a setup with no usable stop, and the honest response to that
    # is to decline it rather than to invent one.
    #
    # Over 5-7 Aug, seven of twelve alerted trades had a stop sitting exactly on
    # this floor. All seven lost, with holds of 6, 10, 13, 20, 22, 32 minutes.
    # That looked decisive and was not.
    #
    # MEASURED AND REFUTED. Against 310 trades over 21 archived days, the floor
    # binds on 178 of them, so it is structurally real -- but the trades it
    # binds on lose $23.6 each against $24.3 for the trades whose structure gave
    # a real stop. Win rates 19% and 18%. Removing them saves $4,580 only by
    # removing 194 trades; per trade they are indistinguishable, and no stop
    # band is profitable at all. The twelve-trade result was selection after the
    # fact and did not survive the larger sample.
    #
    # Left in place, off, so the question is not reopened from the same twelve
    # trades. Turning it on does less of an equally unprofitable thing, which is
    # not the same as an improvement.
    if (original_risk_per_share < price_floor_distance
            and get_bool_env("REJECT_SUB_FLOOR_STOPS", False)):

        trade_allowed = False

        reasons.append(
            f"Stop below the {minimum_stop_pct}% floor: structure gave "
            f"{round(original_risk_per_share / entry_price * 100, 3)}% "
            f"and the floor would have invented the rest"
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

    # These four numbers are the boundary of what this strategy can be.
    #
    # With a 2R target they cap the largest move it will ever aim at around
    # 1.5-2.3% of price, over a hold of twenty to sixty minutes, against an
    # option round trip measured at 1.5-3.4%. Mean peak across 21 archived
    # sessions is 0.39R, roughly 0.3% of price, which is inside the ordinary
    # noise band of a liquid megacap -- so half of all entries never travel at
    # all, and the winners that do reach the target are 3.8% of the book.
    #
    # Nothing inside this boundary can fix that. The scale factor exists so the
    # boundary itself can be tested: at 4.0 the same scanner, gates and exits
    # hunt 6% moves over days instead of 1.5% moves over minutes, which is the
    # only untested change with a ceiling above break-even. The regime spread is
    # preserved rather than replaced, because which regime tolerates more risk
    # is a separate question from how much risk the strategy takes at all.
    max_stop_distance_pct *= get_float_env("MAX_STOP_DISTANCE_SCALE", 1.0)

    if swing_mode:

        # Replaces the band outright rather than scaling it. A 1h pivot lands
        # where it lands; keeping a 0.75% ceiling would reject the entire
        # treatment arm and report it as "no trades" instead of as a result.
        max_stop_distance_pct = swing_anchor.max_stop_distance_pct()

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
    # Target floor on the risk actually taken
    # =========================

    # Every target above is an absolute distance -- 1.8 ATR for EMA_PULLBACK,
    # `target_atr_multiplier` elsewhere -- while the stop floats with structure.
    # So RR reduces to 1.8 / stop_in_ATR, and clearing a 2.0 bar needs a stop
    # under 0.9 ATR. That only happens when price is sitting on the EMA, which
    # is early in a move, before it has proven anything. The strategy is
    # therefore priced out of joining a trend already underway, by arithmetic
    # rather than by judgement.
    #
    # Measured 2026-08-13: NFLX rallied all afternoon and produced 19 LOW_RR
    # blocks. Its one setup with geometry recorded was entry 77.29, stop 76.86,
    # target 77.96 -- risk 1.16 ATR, reward 1.8 ATR, RR 1.55, refused. Price
    # reached 78.64, so a target set from the risk taken (2.0x -> 78.15,
    # 2.5x -> 78.36) would also have been reached.
    #
    # This extends the target to at least TARGET_MIN_RR times the risk, instead
    # of a fixed distance that ignores it. It never pulls a target closer.
    #
    # Off at 0, which leaves every target exactly as it was. The obvious way for
    # this to be wrong is a target that satisfies the ratio but is further than
    # price will travel, converting refusals into stop-outs -- so it wants an
    # A/B against archived days before it is switched on.
    # The extension is capped, and the cap is the whole safety of this feature.
    # Uncapped, it sets RR to exactly TARGET_MIN_RR for every candidate that
    # reaches it -- measured on synthetic frames at ATR 2.0, a setup with a true
    # RR of 0.95 had its target pushed 4.00 (two full ATR) to manufacture 2.0.
    # That turns the RR gate into a tautology: the gate checks a number the
    # target was just adjusted to satisfy, so it refuses nothing, and the worst
    # setups receive the least reachable targets.
    #
    # Capping the total reward in ATR keeps the fix to its actual purpose --
    # a stop that widened because price left the EMA, on a move that is still
    # going -- and leaves genuinely poor geometry to be refused on its merits.
    target_min_rr = get_float_env("TARGET_MIN_RR", 0.0)
    # 2.5 rather than 3.0: the EMA_PULLBACK target already starts at 1.8 ATR, so
    # this permits a ~39% stretch and no more. Measured on the synthetic frames
    # above, 2.5 rescues the NFLX case (true RR 1.55) while leaving RR 1.29 and
    # 0.95 refused; 3.0 also admits the 1.29, which is a wider claim than the
    # evidence supports.
    target_max_reward_atr = get_float_env("TARGET_MAX_REWARD_ATR", 2.5)

    if (
        target_min_rr > 0
        and risk_per_share > 0
        and take_profit is not None
        and atr > 0
    ):

        required_reward = risk_per_share * target_min_rr

        if (
            abs(take_profit - entry_price) < required_reward
            and required_reward <= atr * target_max_reward_atr
        ):

            take_profit = (
                entry_price + required_reward
                if take_profit >= entry_price
                else entry_price - required_reward
            )

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

    # The single hardest constraint in the system, and the least examined.
    # `entry_engine` raises `avoid_chasing` when price sits more than 1.2% from
    # EMA9 or 1.5% from VWAP, and this converts that into an outright refusal.
    #
    # So the app is structurally unable to enter a move already underway. On
    # 2026-08-13 MU travelled 5.67% and SMCI 7.33% and neither produced a
    # tradeable candidate. It is also the shape that would make entry timing
    # measure *worse than a random minute* (§2.2h): a rule admitting only
    # moments when price has not moved selects precisely the horizon at which
    # liquid megacaps mean-revert.
    #
    # Switched, not changed. Off by default means the behaviour is exactly what
    # it has always been; `AVOID_CHASING_BLOCKS=false` lifts the refusal while
    # leaving the flag computed and recorded, so the archive still carries which
    # candidates *would* have been called chased and the arm remains measurable
    # against the control. This mirrors `SETUP_GATE_ENABLED`, which removes a
    # refusal without removing its measurement.
    #
    # Lifting it changes which candidates exist, not merely which ones pass, so
    # no archived candidate set spans a change here. See CHANGE_IMPACT_MAP §1a.
    if entry_setup.get("avoid_chasing", False) and avoid_chasing_blocks():

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