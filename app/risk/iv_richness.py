"""Is this contract's implied volatility expensive relative to how the stock moves?

contract_ranker already scores implied volatility, but on an absolute scale:
18-45 scores well, above 80 is penalised. Absolute IV is not comparable across
symbols. Forty percent is elevated for AAPL and cheap for SOXL or SMCI, so the
same number rewards one contract and punishes another for reasons that have
nothing to do with either being a good trade.

What matters to a premium buyer is IV against the movement the underlying actually
delivers. Paying 60% implied on a stock realising 25% means the position needs a
substantially larger move than the chart suggests just to break even -- on top of
the bid/ask it already has to clear. That is a systematic drag, not an occasional
one, because the strategy only ever buys.

The realised side is derived from ATR_PCT, which is already computed on every bar,
so this needs no new market data.

Deliberately a diagnostic with an optional gate rather than a ranking change:
contract_ranker's scoring is tuned and interacts with expiry, quality and spread
selection, and changing it blind would move which contract is chosen for reasons
that cannot be attributed afterwards.
"""

from __future__ import annotations

import math
import os


# Bars per year at 15 minutes: 26 per session, 252 sessions.
BARS_PER_YEAR_15M = 26 * 252

# Average true range over-states standard deviation, because a range spans both
# tails of the bar. For a random walk E[range] is close to 1.6 sigma, so ATR is
# divided by this before being annualised. Approximate on purpose -- the ratio is
# used against a threshold with wide tolerance, not reported as a volatility.
RANGE_TO_SIGMA = 1.6


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


def max_iv_rv_ratio():
    """Ratio above which the contract is treated as expensive. 0 disables."""

    try:
        return float(os.getenv("MAX_IV_RV_RATIO", "2.0") or 2.0)
    except ValueError:
        return 2.0


def enforce_iv_richness():
    """Whether a rich verdict blocks, or is only recorded.

    Ships observing, for the same reason the stop-viability gate does: the
    range-to-sigma conversion is an approximation and no archived day exists yet to
    calibrate the threshold against. The ratio is written to every row regardless,
    so one session of data settles it.
    """

    return str(
        os.getenv("IV_RICHNESS_ENFORCE", "false") or "false"
    ).strip().lower() in {"true", "1", "yes", "on"}


def annualised_realised_vol(atr_pct_15m):
    """Annualised realised volatility, in percent, from 15-minute ATR percent."""

    atr_pct = _number(atr_pct_15m)

    if atr_pct is None or atr_pct <= 0:
        return None

    sigma_per_bar = (atr_pct / 100.0) / RANGE_TO_SIGMA

    return sigma_per_bar * math.sqrt(BARS_PER_YEAR_15M) * 100.0


def evaluate_iv_richness(option_iv, atr_pct_15m, max_ratio=None):
    """Compare implied to realised volatility for one contract.

    `option_iv` is accepted either as a percentage (61) or a fraction (0.61);
    Polygon has returned both shapes across endpoints, and reading 0.61 as 0.61%
    would make every contract look absurdly cheap and silently disable the check.

    `rich` is None when either side is unknown. Missing data is not evidence that a
    contract is expensive, and blocking on it would drop trades whenever the greeks
    feed hiccuped.
    """

    ratio_limit = _number(max_ratio)

    if ratio_limit is None:
        ratio_limit = max_iv_rv_ratio()

    result = {
        "rich": None,
        "reason": None,
        "implied_vol": None,
        "realised_vol": None,
        "iv_rv_ratio": None,
        "max_ratio": ratio_limit,
    }

    implied = _number(option_iv)
    realised = annualised_realised_vol(atr_pct_15m)

    if implied is not None and 0 < implied <= 5:
        # A fraction, not a percentage.
        implied = implied * 100.0

    result["implied_vol"] = round(implied, 2) if implied is not None else None
    result["realised_vol"] = round(realised, 2) if realised is not None else None

    if ratio_limit <= 0:
        result["rich"] = False
        result["reason"] = "CHECK_DISABLED"
        return result

    if implied is None or implied <= 0:
        result["reason"] = "NO_IMPLIED_VOL"
        return result

    if realised is None or realised <= 0:
        result["reason"] = "NO_REALISED_VOL"
        return result

    ratio = implied / realised
    result["iv_rv_ratio"] = round(ratio, 2)
    result["rich"] = ratio > ratio_limit
    result["reason"] = "IV_RICH_VS_REALISED" if result["rich"] else "IV_FAIR_VS_REALISED"

    return result
