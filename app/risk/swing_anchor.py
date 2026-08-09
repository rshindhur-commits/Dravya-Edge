"""Anchor the stop on higher-timeframe structure instead of the current bar.

This is the one lever left with a ceiling above break-even, and it is not a
knob -- it is where the stop is measured from.

Today an EMA_PULLBACK stop is ``min(bar low - atr*0.15, EMA9 - atr*0.10)`` on
the 15m frame. For a liquid megacap that bar's own low sits a fraction of a
percent from price, always, so every stop lands at 0.5-0.75%, every 2R target
at 1.5%, and mean peak across 21 archived sessions is 0.39R -- about 0.3% of
price, inside the ordinary noise band of the names traded. The option round
trip costs 1.5-3.4%. The strategy is therefore structurally unable to hunt a
move large enough to pay for the instrument used to express it, and no setting
inside that geometry changes it: a fourfold ATR offset buys a 1.53x stop,
because the bar's low dominates and does not scale. Reaching 3% would need a
scale near fourteen.

A swing low on the 1h frame sits 1-4% away because that is what a swing low is.
That is the whole change. Everything downstream -- scanner, gates, entry rules,
contract selection -- is untouched.

Four things have to move together or the arm silently runs the control:

  * the anchor          (here)
  * the minimum stop %  (0.50 -> SWING_MIN_STOP_DISTANCE_PCT)
  * the maximum stop %  (0.75-1.15 -> SWING_MAX_STOP_DISTANCE_PCT)
  * the exits           (EXIT_MOMENTUM_ENABLED=false, or a 3% stop is decided
                         by a nine-period EMA within minutes and the wider
                         anchor is bought and never used)

The first three are applied by ``calculate_risk`` whenever this mode is on, so
they cannot be half-set. The fourth lives in the exit engine and is named in
``describe_mode`` so a run that forgot it is visible in its own output rather
than in a P&L months later.

Off by default. Nothing changes until an A/B says it should.
"""

from app.config.settings import get_bool_env, get_float_env, get_int_env

# Bars of the higher timeframe the pivot is taken from. Eight 1h bars is a
# little over one session, which is the horizon a MULTIDAY position is opened
# against. Shorter than this and the pivot degenerates back towards the entry
# bar, which is the pathology being fixed.
DEFAULT_LOOKBACK_BARS = 8

# Padding past the pivot, in higher-timeframe ATR. A stop resting exactly on a
# visible swing low is where everyone else's is.
DEFAULT_ATR_BUFFER = 0.25

# Floor on how far the stop may sit from entry, as a multiple of higher-frame
# ATR, for the case where price has already traded through the pivot and the
# pivot alone would put the stop above entry for a long.
DEFAULT_MIN_ATR_MULTIPLE = 0.5


def swing_mode_enabled():
    """Whether stops are anchored on the higher timeframe."""

    return get_bool_env("SWING_STRUCTURE_ENABLED", False)


def min_stop_distance_pct():

    return get_float_env("SWING_MIN_STOP_DISTANCE_PCT", 1.0)


def max_stop_distance_pct():

    return get_float_env("SWING_MAX_STOP_DISTANCE_PCT", 4.0)


def target_rr():
    """Reward as a multiple of the swing risk.

    A structure target is deliberately not used here. Higher-frame resistance
    can sit inside the swing stop distance, which would produce sub-1 RR on a
    setup whose whole point is a wide stop, and the RR floor would then reject
    exactly the trades this mode exists to take. Fixing reward at a multiple of
    risk keeps RR deterministic and above the 1.5 gate by construction.
    """

    return get_float_env("SWING_TARGET_RR", 2.0)


def headroom_multiple():
    """Room to the next higher-frame level, as a multiple of the swing risk.

    Fixing reward at a multiple of risk makes RR exactly SWING_TARGET_RR on
    every trade, which silently disables the RR floor -- the gate can never
    fire. On a four-day smoke test that took candidates clearing the gates from
    40 to 73 of 83. More trades at equal R is not an improvement, so the
    filtering the RR gate used to do has to be done by something.

    This does it in the term that actually matters at this timeframe: whether
    there is space above (below, for a short) before the next 1h level, at least
    as far as the target. A setup whose upside is capped by resistance one
    percent away is a bad swing trade no matter how clean the entry looks, and
    that is invisible to a gate reading a constant RR.

    Zero disables it, which is the arm measured first, so the two are separable.
    """

    return get_float_env("SWING_HEADROOM_MULTIPLE", 0.0)


def headroom_ok(htf, entry_price, stop_loss, is_short):
    """Whether the next higher-frame level leaves room for the target."""

    required = headroom_multiple()

    if required <= 0:

        return True, None

    column = "ROLLING_SUPPORT" if is_short else "ROLLING_RESISTANCE"

    if htf is None or column not in getattr(htf, "columns", []):

        return True, None

    try:

        level = float(htf[column].iloc[-1])

    except (TypeError, ValueError, IndexError):

        return True, None

    if level != level:

        return True, None

    risk = abs(entry_price - stop_loss)
    available = entry_price - level if is_short else level - entry_price

    return available >= risk * required, available


def usable(htf):
    """Whether the higher-timeframe frame can carry a pivot.

    ``compute_indicators`` returns an EMPTY frame below its per-interval
    minimum rather than raising, so an unusable frame arrives as an empty one.
    """

    if htf is None or getattr(htf, "empty", True):

        return False

    needed = {"High", "Low", "ATR"}

    if not needed.issubset(set(htf.columns)):

        return False

    return len(htf) >= 2


def swing_levels(htf, entry_price, is_short):
    """``(stop_loss, take_profit, pivot, htf_atr)`` from higher-frame structure.

    Returns ``None`` when the frame cannot carry a pivot. The caller rejects the
    trade in that case rather than falling back to the intraday anchor: a
    silent fallback would run part of the arm as the control, which is how three
    earlier experiments in this project came back byte-identical to their
    baselines and cost a week each.
    """

    if not usable(htf):

        return None

    lookback = max(2, get_int_env("SWING_STOP_LOOKBACK_BARS", DEFAULT_LOOKBACK_BARS))
    buffer_mult = get_float_env("SWING_STOP_ATR_BUFFER", DEFAULT_ATR_BUFFER)
    min_atr_mult = get_float_env("SWING_MIN_ATR_MULTIPLE", DEFAULT_MIN_ATR_MULTIPLE)

    window = htf.tail(lookback)

    try:

        htf_atr = float(htf["ATR"].iloc[-1])

    except (TypeError, ValueError):

        return None

    if htf_atr != htf_atr or htf_atr <= 0:

        return None

    buffer_amount = htf_atr * buffer_mult
    floor_amount = htf_atr * min_atr_mult

    if is_short:

        pivot = float(window["High"].max())

        # max() keeps the stop above entry even when price has already run past
        # the pivot; the ATR floor is what guarantees it.
        stop_loss = max(pivot + buffer_amount, entry_price + floor_amount)
        risk = stop_loss - entry_price
        take_profit = entry_price - (risk * target_rr())

    else:

        pivot = float(window["Low"].min())

        stop_loss = min(pivot - buffer_amount, entry_price - floor_amount)
        risk = entry_price - stop_loss
        take_profit = entry_price + (risk * target_rr())

    if risk <= 0:

        return None

    return stop_loss, take_profit, pivot, htf_atr


def describe_mode():
    """What the run is actually configured to do, for the arm's own output.

    A wider stop with the momentum exits still on is not this experiment: the
    position is closed by a nine-period EMA long before the stop matters, so the
    arm measures the control while looking like the treatment. That is worth a
    line in the report rather than a discovery afterwards.
    """

    return {
        "swing_structure_enabled": swing_mode_enabled(),
        "lookback_bars": get_int_env("SWING_STOP_LOOKBACK_BARS", DEFAULT_LOOKBACK_BARS),
        "atr_buffer": get_float_env("SWING_STOP_ATR_BUFFER", DEFAULT_ATR_BUFFER),
        "min_stop_pct": min_stop_distance_pct(),
        "max_stop_pct": max_stop_distance_pct(),
        "target_rr": target_rr(),
        "headroom_multiple": headroom_multiple(),
        "momentum_exits_enabled": get_bool_env("EXIT_MOMENTUM_ENABLED", True),
    }
