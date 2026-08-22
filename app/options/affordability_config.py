import os

from app.config.capital_profiles import CAPITAL_PROFILES
from app.config.settings import get_bool_env, get_float_env, get_int_env, get_secret_env


# Preferred and aggressive move with the hard cap, at the same ratios the global
# settings use (800 / 1000 / 1500). Raising the hard cap alone leaves preference
# pointing at the cheap, decayed corner of the chain, which is the mistake
# recorded in CONFIG_CHANGELOG.md for the 2026-08-15 change.
PREFERRED_RATIO = 0.8
AGGRESSIVE_RATIO = 1.5


def per_symbol_cost_caps():
    """Symbols allowed a different contract cost cap. Empty disables it.

    `OPTION_MAX_CONTRACT_COST_BY_SYMBOL="AVGO:2500,SMH:2500,GOOGL:2500"`

    Some underlyings carry a good signal and no contract this account can buy.
    Measured over 21 sessions, AVGO and SMH produce the best entries in the
    universe -- **+1.098R and +1.070R on the underlying, better than every name
    currently tradeable except ORCL** -- and GOOGL is close behind. Priced with
    the cap lifted, the three return +7.44% mean and +5.86% after the top-5 strip
    at the shipped ceiling and liquidity floors, on 26 trades.

    They are unreachable rather than unprofitable: the tightest qualifying
    contract runs a **$1,430 median**, above the $1,000 cap that exists to keep
    alerts inside the subscriber bands. Raising the cap globally would push every
    other name's contract up with it, which is the opposite of what those bands
    are for -- so the exception is per symbol and nothing else moves.

    **This serves a tier that can afford ~$1,500 and no other.** An alert on
    these three is not actionable for a sub-$500 subscriber, and that is a
    product decision the operator took explicitly, not a side effect.

    Read live rather than frozen at import, so the operator can withdraw it
    without a deploy. Anything unparseable is skipped rather than raising: a
    malformed override must not take the option path down mid-session.

    ## Reviewed 2026-08-22 -- AVGO and SMH narrowed 2500 -> 1500

    The admission argument above is about the *signal*. What the headroom bought
    is a separate question, and the traded record answers it:

        AVGO 08-04  $1,115  +2.35R  +$195   <- the only winner
        AVGO 08-19  $1,938  -0.17R
        AVGO 08-20  $2,408  -1.00R
        AVGO 08-21  $2,200  -0.61R
        SMH  08-19  $2,245  -0.16R

    Every trade that actually needed the $2,500 band lost -- 4 of 4, -1.94R --
    and the single winner sat at $1,115, inside even the old $1,000 global cap.
    Four trades is not enough to withdraw an exception measured over 21 sessions,
    which is why this narrows the band instead of removing it.

    MSFT joined at 1500 on the same test the original three had to pass, run by
    `tools/cost_blocked_signal_quality.py`: median best +0.68R against a +0.41R
    baseline and median close +0.16R against -0.13R, over 18 candidates at a
    $1,235 median. Its interval on the close is [-0.02, +0.47] and touches zero,
    so it is a trial rather than a verdict -- unlike AVGO and SMH, whose blocked
    candidates now measure -0.51R and +0.43R with the top five removed leaving
    +0.01R. Raising *those* two further would buy nothing.
    """

    raw = os.getenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "") or ""
    caps = {}

    for item in raw.split(","):

        if ":" not in item:
            continue

        symbol, _, value = item.partition(":")

        try:
            cap = float(value.strip())
        except (TypeError, ValueError):
            continue

        symbol = symbol.strip().upper()

        if symbol and cap > 0:
            caps[symbol] = cap

    return caps


def get_affordability_config(symbol=None):

    profile_name = get_secret_env(
        "OPTION_CAPITAL_PROFILE",
        "SMALL_ACCOUNT"
    ).strip().upper()

    profile = CAPITAL_PROFILES.get(
        profile_name,
        CAPITAL_PROFILES["SMALL_ACCOUNT"]
    ).copy()

    config = {
        "mode": get_secret_env(
            "OPTION_AFFORDABILITY_MODE",
            "HARD"
        ).strip().upper(),
        "profile_name": profile_name,
        "capital_growth_mode": get_bool_env(
            "CAPITAL_GROWTH_MODE",
            True
        ),
        "show_best_quality_contract": get_bool_env(
            "OPTION_SHOW_BEST_QUALITY_CONTRACT",
            True
        ),
        "show_affordable_alternate": get_bool_env(
            "OPTION_SHOW_AFFORDABLE_ALTERNATE",
            True
        ),
        **profile,
    }

    config["daily_start_capital"] = get_float_env(
        "DAILY_START_CAPITAL",
        config["daily_start_capital"]
    )
    config["option_stop_loss_pct"] = get_float_env(
        "OPTION_STOP_LOSS_PCT",
        config["option_stop_loss_pct"]
    )
    config["max_risk_per_trade_pct"] = get_float_env(
        "OPTION_MAX_RISK_PER_TRADE_PCT",
        config["max_risk_per_trade_pct"]
    )
    config["min_contract_cost"] = get_float_env(
        "OPTION_MIN_CONTRACT_COST",
        config["min_contract_cost"]
    )
    config["preferred_max_contract_cost"] = get_float_env(
        "OPTION_PREFERRED_MAX_CONTRACT_COST",
        config["preferred_max_contract_cost"]
    )
    config["max_contract_cost"] = get_float_env(
        "OPTION_MAX_CONTRACT_COST",
        config["max_contract_cost"]
    )
    config["aggressive_max_contract_cost"] = get_float_env(
        "OPTION_AGGRESSIVE_MAX_CONTRACT_COST",
        config["aggressive_max_contract_cost"]
    )
    config["min_affordable_delta"] = get_float_env(
        "OPTION_MIN_AFFORDABLE_DELTA",
        config["min_affordable_delta"]
    )
    config["max_active_trades"] = get_int_env(
        "MAX_ACTIVE_PAPER_TRADES",
        config["max_active_trades"]
    )
    config["max_daily_entries"] = get_int_env(
        "MAX_DAILY_ENTRIES",
        config["max_daily_entries"]
    )

    if config["mode"] not in [
        "OFF",
        "SOFT",
        "HARD"
    ]:

        config["mode"] = "HARD"

    # Applied last, so it overrides the env vars above rather than being
    # overwritten by them. Only raises: an override below the global cap would
    # be a way to tighten a single name silently, and no measurement asks for
    # that. `cost_cap_symbol` is recorded so a trade can be attributed to the
    # exception afterwards -- an alert above the band that nothing explains is
    # indistinguishable from a bug.
    override = per_symbol_cost_caps().get(str(symbol or "").strip().upper())

    if override and override > config["max_contract_cost"]:

        config["max_contract_cost"] = override
        config["preferred_max_contract_cost"] = override * PREFERRED_RATIO
        config["aggressive_max_contract_cost"] = override * AGGRESSIVE_RATIO
        config["cost_cap_symbol"] = symbol

    return config