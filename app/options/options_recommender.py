def recommend_option(symbol, price, final_signal):

    recommendation = {
        "strategy": "WAIT",
        "strike": None,
        "expiration": None,
        "style": None,
        "risk": "LOW"
    }

    rounded_price = round(price)

    # Strong bullish setup
    if final_signal == "HIGH CONVICTION BULLISH":

        recommendation["strategy"] = "CALL"

        strike = rounded_price

        recommendation["strike"] = f"{strike}C"

        recommendation["expiration"] = "1-2 weeks"

        recommendation["style"] = "ATM momentum continuation"

        recommendation["risk"] = "MEDIUM"

    # Moderate bullish setup
    elif final_signal == "BULLISH":

        recommendation["strategy"] = "CALL"

        strike = rounded_price + 2

        recommendation["strike"] = f"{strike}C"

        recommendation["expiration"] = "1 week"

        recommendation["style"] = "Slightly OTM momentum"

        recommendation["risk"] = "MEDIUM-HIGH"

    # Placeholder bearish setup
    elif final_signal == "HIGH CONVICTION BEARISH":

        recommendation["strategy"] = "PUT"

        strike = rounded_price

        recommendation["strike"] = f"{strike}P"

        recommendation["expiration"] = "1-2 weeks"

        recommendation["style"] = "ATM bearish continuation"

        recommendation["risk"] = "MEDIUM"

    return recommendation


from app.options.live_options_chain import (
    fetch_options_chain,
    refresh_contract_quote
)

from app.options.contract_ranker import (
    rank_option_contracts
)

from app.options.option_direction import (
    resolve_option_direction
)

from app.options.affordability_config import get_affordability_config
from app.options.option_affordability import add_affordability_metrics


def recommend_live_option(

    symbol,
    latest_price,
    final_signal,
    entry_type=None

):

    try:

        direction = resolve_option_direction(
            final_signal,
            entry_type
        )

        if direction == "NONE":

            return None

        contracts = fetch_options_chain(
            symbol,
            latest_price,
            direction=direction
        )

        ranked = rank_option_contracts(

            contracts,

            latest_price,

            direction,

            symbol=symbol

        )

        if not ranked:

            return None

        return ranked[0]

    except Exception as e:

        print(
            f"[OPTION RECOMMENDER ERROR] {e}"
        )

        return None


def _pick_first_by_dte(ranked, min_dte, max_dte, exclude_ticker=None):

    for contract in ranked:

        if contract.get("ticker") == exclude_ticker:

            continue

        dte = contract.get("dte")

        if dte is None:

            continue

        if min_dte <= dte <= max_dte:

            return contract

    return None


def _pick_best_affordable(ranked, config):

    if config.get("mode") == "OFF":

        return None

    for contract in ranked[:50]:

        candidate = add_affordability_metrics(
            dict(contract),
            config=config
        )

        if not candidate.get("affordable"):

            continue

        refreshed = refresh_contract_quote(candidate)
        refreshed = add_affordability_metrics(
            refreshed,
            config=config
        )

        if refreshed.get("affordable"):

            return refreshed

    return None


def recommend_live_option_bundle(
    symbol,
    latest_price,
    final_signal,
    entry_type=None,
    paper_mode=False
):

    try:

        direction = resolve_option_direction(
            final_signal,
            entry_type
        )

        if direction == "NONE":

            return {
                "primary": None,
                "active": None,
                "affordable": None,
                "short_dte": None,
                "longer_dte": None,
                "ranked": []
            }

        contracts = fetch_options_chain(
            symbol,
            latest_price,
            direction=direction
        )

        affordability_config = get_affordability_config(symbol)

        ranked = rank_option_contracts(
            contracts,
            latest_price,
            direction,
            paper_mode=paper_mode,
            symbol=symbol
        )

        if not ranked:

            return {
                "primary": None,
                "active": None,
                "affordable": None,
                "short_dte": None,
                "longer_dte": None,
                "ranked": []
            }

        primary = add_affordability_metrics(
            refresh_contract_quote(
                ranked[0]
            ),
            config=affordability_config
        )

        affordable = _pick_best_affordable(
            ranked,
            affordability_config
        )

        active = (
            primary
            if paper_mode
            else affordable
            if (
                affordability_config.get("mode") != "OFF"
                and affordable
            )
            else primary
        )

        primary_ticker = primary.get("ticker")

        short_dte = _pick_first_by_dte(
            ranked,
            2,
            13,
            exclude_ticker=primary_ticker
        )
        longer_dte = _pick_first_by_dte(
            ranked,
            31,
            45,
            exclude_ticker=primary_ticker
        )

        return {
            "primary": primary,
            "active": active,
            "affordable": affordable,
            "short_dte": add_affordability_metrics(
                refresh_contract_quote(short_dte),
                config=affordability_config
            ) if short_dte else None,
            "longer_dte": add_affordability_metrics(
                refresh_contract_quote(longer_dte),
                config=affordability_config
            ) if longer_dte else None,
            "ranked": ranked
        }

    except Exception as e:

        print(
            f"[OPTION BUNDLE ERROR] {e}"
        )

        return {
            "primary": None,
            "active": None,
            "affordable": None,
            "short_dte": None,
            "longer_dte": None,
            "ranked": []
        }