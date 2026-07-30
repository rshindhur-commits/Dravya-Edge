"""Reject entries whose stop sits inside the option's own trading friction.

The stop is placed on the underlying; the position is an option. Those are only
the same trade if the move to the stop is large enough to clear the contract's
bid/ask. When it is not, the option is stopped out by the spread rather than by
the thesis being wrong, and the R multiple that gets recorded measures friction.

2026-07-30 is the worked example. PLTR entered at 122.06 with a stop at 121.90 --
$0.16, or 0.13% of price, smaller than one 5-minute candle. It filled at 121.77
for -1.81R: the stop was so tight that ordinary overshoot cost most of another R
on top of the planned loss. Every EMA_PULLBACK entry that day carried a stop of
0.13%-0.36% against option round-trip spreads of 2.1%-8.0%.

MIN_STOP_DISTANCE_PCT (risk_manager) already puts a floor under this in price
terms. This adds the term the pathology actually lives in: the specific contract's
spread. A 0.50% stop is ample on a penny-wide contract and still unwinnable on one
quoted 8% wide, and a single price-term floor cannot tell those apart.

Deliberately a rejection rather than a stop widener. Widening the stop would move
it off the structure it was anchored to and quietly change the trade's thesis;
refusing the trade leaves the analysis honest and costs only a position that could
not have been won.
"""

from __future__ import annotations

from app.config.settings import get_float_env


DEFAULT_MIN_STOP_SPREAD_MULTIPLE = 1.5


def _number(value):
    if value is None or value == "":
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if result != result or result in (float("inf"), float("-inf")):
        return None

    return result


def min_stop_spread_multiple():
    """How many times the round-trip spread the move to stop must cover.

    0 disables the check. The default is deliberately modest: at 1.5 the trade
    still only needs the option to move half again the cost of getting in and out
    before the stop is reached, which is a low bar that 07-30's entries failed.
    """

    return get_float_env("MIN_STOP_SPREAD_MULTIPLE", DEFAULT_MIN_STOP_SPREAD_MULTIPLE)


def evaluate_stop_viability(
    entry_price,
    stop_price,
    option_price,
    delta,
    spread_pct,
    min_multiple=None,
):
    """Can the move to the stop clear this contract's round-trip spread?

    `spread_pct` is (ask - bid) / ask * 100, which is already the full round trip:
    the position is opened at the ask and closed at the bid, so the spread is paid
    once across the pair.

    Returns a dict rather than a bool so the reason and the numbers land in the
    artifacts. `viable` is None when the inputs are unknown -- an unpriced contract
    is not evidence of a bad stop, and blocking on missing data would silently
    discard trades whenever the quote feed hiccuped.
    """

    multiple = _number(min_multiple)

    if multiple is None:
        multiple = min_stop_spread_multiple()

    result = {
        "viable": None,
        "reason": None,
        "stop_distance": None,
        "option_move_at_stop": None,
        "move_pct_of_premium": None,
        "round_trip_spread_pct": _number(spread_pct),
        "spread_multiple": None,
        "required_multiple": multiple,
    }

    if multiple <= 0:
        result["viable"] = True
        result["reason"] = "CHECK_DISABLED"
        return result

    entry_price = _number(entry_price)
    stop_price = _number(stop_price)
    option_price = _number(option_price)
    delta = _number(delta)
    spread = _number(spread_pct)

    if entry_price is None or stop_price is None:
        result["reason"] = "NO_STOP_PRICE"
        return result

    stop_distance = abs(entry_price - stop_price)
    result["stop_distance"] = round(stop_distance, 4)

    if stop_distance <= 0:
        result["viable"] = False
        result["reason"] = "STOP_AT_ENTRY"
        return result

    if option_price is None or option_price <= 0:
        result["reason"] = "NO_OPTION_PRICE"
        return result

    if spread is None:
        result["reason"] = "NO_OPTION_SPREAD"
        return result

    if delta is None or abs(delta) <= 0:
        result["reason"] = "NO_OPTION_DELTA"
        return result

    # Delta is per share and the premium is quoted per share, so the two are
    # directly comparable without the 100x contract multiplier.
    option_move = stop_distance * abs(delta)
    move_pct = (option_move / option_price) * 100

    result["option_move_at_stop"] = round(option_move, 4)
    result["move_pct_of_premium"] = round(move_pct, 2)

    if spread <= 0:
        result["viable"] = True
        result["reason"] = "NO_SPREAD_COST"
        return result

    spread_multiple = move_pct / spread
    result["spread_multiple"] = round(spread_multiple, 2)
    result["viable"] = spread_multiple >= multiple
    result["reason"] = (
        "STOP_CLEARS_SPREAD"
        if result["viable"]
        else "STOP_INSIDE_OPTION_SPREAD"
    )

    return result
