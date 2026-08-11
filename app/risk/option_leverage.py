"""How much the contract amplifies the move you are paying to catch.

Leverage here is the underlying price divided by the premium per share: how many
percent the premium moves for each percent the underlying moves. It is the term
that decides whether a 0.5% move is worth a 3.4% round-trip toll, and nothing in
the app looked at it.

Measured on the 291-trade forward archive (data/forward_runs/phase1_21day_*),
grouped by leverage at entry:

    < 20x    n=31    -5.10% premium   -0.312R    3% win
    20-40x   n=124   -2.93%           +0.064R   19%
    40-70x   n=101   -2.69%           +0.036R   25%
    > 70x    n=35    -3.51%           +0.006R   26%

A 3% win rate over 31 trades is not noise, and it has a mechanism: at low
leverage the underlying has to travel much further to move the premium at all,
while the spread is paid in full either way. The high end sags too, but gently
and on a thinner sample, so only the floor is expressed here.

Deliberately separate from `stop_viability` even though that module receives the
same two prices. Folding this into its verdict would file a leverage rejection
as STOP_INSIDE_OPTION_SPREAD and make neither rule's rejection rate readable --
and both thresholds are being A/B'd, which needs them attributable apart.

Default 0.0, which disables it: this threshold was derived on the same archive it
would be measured against, so it ships off and is turned on by
tools/gate_ab.py confirming it on sessions it was not fitted to.
"""

from __future__ import annotations

from app.config.settings import get_bool_env, get_float_env


# 0 disables the check. 20 is the floor the archive supports; see the module
# docstring for the counts behind it.
DEFAULT_MIN_OPTION_LEVERAGE = 0.0


def min_option_leverage():
    """Premium amplification a contract must offer, or 0 to not ask."""

    return get_float_env("OPTION_MIN_LEVERAGE", DEFAULT_MIN_OPTION_LEVERAGE)


def enforce_option_leverage():
    """Whether a failing verdict blocks the trade, or is only recorded.

    Off by default, for the reason in the module docstring: the number came from
    the archive, so it has to earn production on sessions it never saw. The
    observe-only fields are written either way.
    """

    return get_bool_env("OPTION_LEVERAGE_ENFORCE", False)


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


def evaluate_option_leverage(entry_price, option_price, min_multiple=None):
    """Does this contract amplify the underlying enough to be worth its toll?

    Returns a dict rather than a bool so the number reaches the artifacts.
    `viable` is None where the inputs are unknown -- an unpriced contract is a
    data gap, not a verdict, and blocking on one would quietly drop trades
    whenever the quote feed hiccuped.
    """

    required = _number(min_multiple)

    if required is None:
        required = min_option_leverage()

    result = {
        "viable": None,
        "reason": None,
        "leverage": None,
        "required_leverage": required,
    }

    entry_price = _number(entry_price)
    option_price = _number(option_price)

    if entry_price is None or entry_price <= 0:
        result["reason"] = "NO_ENTRY_PRICE"
        return result

    if option_price is None or option_price <= 0:
        result["reason"] = "NO_OPTION_PRICE"
        return result

    # Both are quoted per share, so the 100x contract multiplier cancels.
    leverage = entry_price / option_price
    result["leverage"] = round(leverage, 2)

    # Measured always, enforced only when a floor is set. Recording the number
    # under a disabled floor is the whole point: the archive this was derived
    # from had to reconstruct leverage from entry and fill prices because
    # nothing ever wrote it down.
    if required <= 0:
        result["viable"] = True
        result["reason"] = "CHECK_DISABLED"
        return result

    result["viable"] = leverage >= required
    result["reason"] = (
        "LEVERAGE_SUFFICIENT" if result["viable"] else "LEVERAGE_BELOW_FLOOR"
    )

    return result
