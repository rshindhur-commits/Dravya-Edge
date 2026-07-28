from __future__ import annotations

import math
import os
from dataclasses import dataclass


OK = "OK"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"

CONTRACT_MULTIPLIER = 100

PENNY_TICK_BOUNDARY = 3.00
TICK_BELOW_BOUNDARY = 0.01
TICK_ABOVE_BOUNDARY = 0.05

TICK_EPSILON = 1e-9
MONEY_PRECISION = 6


@dataclass(frozen=True)
class CostModel:

    commission_per_contract: float = 0.65
    commission_min_per_leg: float = 0.0
    commission_max_pct_of_premium: float = 0.0
    occ_clearing_per_contract: float = 0.02
    orf_per_contract: float = 0.02685
    finra_taf_per_contract_sell: float = 0.00279
    sec_fee_rate_on_sell_proceeds: float = 0.0000278
    entry_fill_aggression: float = 1.0
    exit_fill_aggression: float = 1.0
    stop_exit_spread_multiplier: float = 1.5
    tick_size: float = 0.0
    contract_multiplier: int = CONTRACT_MULTIPLIER


def _float(value, default=None):

    try:

        if value is None:

            return default

        return float(value)

    except Exception:

        return default


def _env_float(name, default):

    return _float(os.getenv(name), default)


def get_cost_model():

    return CostModel(
        commission_per_contract=_env_float("COST_COMMISSION_PER_CONTRACT", 0.65),
        commission_min_per_leg=_env_float("COST_COMMISSION_MIN_PER_LEG", 0.0),
        commission_max_pct_of_premium=_env_float("COST_COMMISSION_MAX_PCT_OF_PREMIUM", 0.0),
        occ_clearing_per_contract=_env_float("COST_OCC_CLEARING_PER_CONTRACT", 0.02),
        orf_per_contract=_env_float("COST_ORF_PER_CONTRACT", 0.02685),
        finra_taf_per_contract_sell=_env_float("COST_FINRA_TAF_PER_CONTRACT", 0.00279),
        sec_fee_rate_on_sell_proceeds=_env_float("COST_SEC_FEE_RATE", 0.0000278),
        entry_fill_aggression=_env_float("COST_ENTRY_FILL_AGGRESSION", 1.0),
        exit_fill_aggression=_env_float("COST_EXIT_FILL_AGGRESSION", 1.0),
        stop_exit_spread_multiplier=_env_float("COST_STOP_EXIT_SPREAD_MULTIPLIER", 1.5),
        tick_size=_env_float("COST_TICK_SIZE", 0.0),
        contract_multiplier=int(_env_float("COST_CONTRACT_MULTIPLIER", CONTRACT_MULTIPLIER)),
    )


def worst_status(*statuses):

    if UNAVAILABLE in statuses:

        return UNAVAILABLE

    if DEGRADED in statuses:

        return DEGRADED

    return OK


def is_stop_exit(exit_reason):

    text = str(exit_reason or "").strip().upper()

    if not text:

        return None

    return "STOP" in text


def resolve_tick_size(price, model=None):

    model = model or get_cost_model()

    if model.tick_size and model.tick_size > 0:

        return {
            "tick_size": model.tick_size,
            "status": OK,
            "reason": None,
        }

    price = _float(price)

    if price is None or price <= 0:

        return {
            "tick_size": TICK_ABOVE_BOUNDARY,
            "status": DEGRADED,
            "reason": "TICK_SIZE_INFERRED_NO_PRICE",
        }

    tick = (
        TICK_BELOW_BOUNDARY
        if price < PENNY_TICK_BOUNDARY
        else TICK_ABOVE_BOUNDARY
    )

    return {
        "tick_size": tick,
        "status": DEGRADED,
        "reason": "TICK_SIZE_INFERRED",
    }


def round_to_tick(price, tick, round_up):

    price = _float(price)
    tick = _float(tick)

    if price is None:

        return None

    if tick is None or tick <= 0:

        return price

    steps = price / tick
    nearest = round(steps)

    if abs(steps - nearest) < TICK_EPSILON:

        steps = nearest

    else:

        steps = math.ceil(steps) if round_up else math.floor(steps)

    return round(steps * tick, MONEY_PRECISION)


def _unavailable_fill(reason):

    return {
        "fill": None,
        "mid": None,
        "half_spread": None,
        "slippage_per_share": None,
        "status": UNAVAILABLE,
        "reason": reason,
    }


def fill_price(mid, bid, ask, side, aggression, tick_size, spread_multiplier=1.0):

    side = str(side or "").strip().upper()

    if side not in {"BUY", "SELL"}:

        return _unavailable_fill("INVALID_SIDE")

    bid = _float(bid)
    ask = _float(ask)

    if bid is None or ask is None or bid <= 0 or ask <= 0:

        return _unavailable_fill("MISSING_QUOTE")

    if ask < bid:

        return _unavailable_fill("CROSSED_MARKET")

    mid = _float(mid)

    if mid is None:

        mid = (bid + ask) / 2

    aggression = _float(aggression, 1.0)
    spread_multiplier = _float(spread_multiplier, 1.0)

    half_spread = (ask - bid) / 2 * spread_multiplier

    if side == "BUY":

        fill = round_to_tick(mid + aggression * half_spread, tick_size, True)

    else:

        fill = round_to_tick(mid - aggression * half_spread, tick_size, False)

        if fill is not None:

            fill = max(0.0, fill)

    return {
        "fill": fill,
        "mid": mid,
        "half_spread": half_spread,
        "slippage_per_share": abs(fill - mid) if fill is not None else None,
        "status": OK,
        "reason": None,
    }


def _commission(gross, contracts, model):

    commission = model.commission_per_contract * contracts
    commission = max(commission, model.commission_min_per_leg)

    if model.commission_max_pct_of_premium > 0:

        commission = min(
            commission,
            model.commission_max_pct_of_premium * gross
        )

    return commission


def _unavailable_leg(reason, keys):

    payload = {key: None for key in keys}
    payload["breakdown"] = None
    payload["status"] = UNAVAILABLE
    payload["reason"] = reason

    return payload


def entry_cost(fill_price_per_share, contracts, model=None):

    model = model or get_cost_model()

    keys = ["gross_debit", "commission", "regulatory_fees", "total_cost", "cash_outlay"]

    price = _float(fill_price_per_share)
    contracts = int(_float(contracts, 0) or 0)

    if price is None or price <= 0:

        return _unavailable_leg("MISSING_FILL_PRICE", keys)

    if contracts <= 0:

        return _unavailable_leg("INVALID_CONTRACTS", keys)

    gross_debit = price * model.contract_multiplier * contracts
    commission = _commission(gross_debit, contracts, model)
    occ = model.occ_clearing_per_contract * contracts
    orf = model.orf_per_contract * contracts
    regulatory_fees = occ + orf
    total_cost = gross_debit + commission + regulatory_fees

    return {
        "gross_debit": gross_debit,
        "commission": commission,
        "regulatory_fees": regulatory_fees,
        "total_cost": total_cost,
        "cash_outlay": total_cost,
        "breakdown": {
            "occ_clearing": occ,
            "orf": orf,
        },
        "status": OK,
        "reason": None,
    }


def exit_proceeds(fill_price_per_share, contracts, model=None):

    model = model or get_cost_model()

    keys = ["gross_credit", "commission", "regulatory_fees", "net_proceeds"]

    price = _float(fill_price_per_share)
    contracts = int(_float(contracts, 0) or 0)

    if price is None or price < 0:

        return _unavailable_leg("MISSING_FILL_PRICE", keys)

    if contracts <= 0:

        return _unavailable_leg("INVALID_CONTRACTS", keys)

    gross_credit = price * model.contract_multiplier * contracts
    commission = _commission(gross_credit, contracts, model)
    occ = model.occ_clearing_per_contract * contracts
    orf = model.orf_per_contract * contracts
    taf = model.finra_taf_per_contract_sell * contracts
    sec = model.sec_fee_rate_on_sell_proceeds * gross_credit
    regulatory_fees = occ + orf + taf + sec
    net_proceeds = gross_credit - commission - regulatory_fees

    return {
        "gross_credit": gross_credit,
        "commission": commission,
        "regulatory_fees": regulatory_fees,
        "net_proceeds": net_proceeds,
        "breakdown": {
            "occ_clearing": occ,
            "orf": orf,
            "finra_taf": taf,
            "sec_fee": sec,
        },
        "status": OK,
        "reason": None,
    }


def round_trip_costs(entry_quote, exit_quote, contracts, model=None, exit_reason=None):

    model = model or get_cost_model()

    entry_quote = entry_quote or {}
    exit_quote = exit_quote or {}

    stop_exit = is_stop_exit(exit_reason)

    reasons = []

    if stop_exit is None:

        reasons.append("EXIT_REASON_UNCLASSIFIED")

    exit_multiplier = (
        model.stop_exit_spread_multiplier
        if stop_exit
        else 1.0
    )

    entry_tick = resolve_tick_size(entry_quote.get("ask"), model)
    exit_tick = resolve_tick_size(exit_quote.get("bid"), model)

    entry_fill = fill_price(
        entry_quote.get("mid"),
        entry_quote.get("bid"),
        entry_quote.get("ask"),
        "BUY",
        model.entry_fill_aggression,
        entry_tick["tick_size"]
    )

    exit_fill = fill_price(
        exit_quote.get("mid"),
        exit_quote.get("bid"),
        exit_quote.get("ask"),
        "SELL",
        model.exit_fill_aggression,
        exit_tick["tick_size"],
        exit_multiplier
    )

    if entry_fill["status"] == UNAVAILABLE or exit_fill["status"] == UNAVAILABLE:

        return {
            "entry": entry_fill,
            "exit": exit_fill,
            "total_friction": None,
            "friction_per_contract": None,
            "friction_pct_of_premium": None,
            "spread_component": None,
            "commission_component": None,
            "fee_component": None,
            "status": UNAVAILABLE,
            "reason": entry_fill["reason"] or exit_fill["reason"],
        }

    entry_leg = entry_cost(entry_fill["fill"], contracts, model)
    exit_leg = exit_proceeds(exit_fill["fill"], contracts, model)

    if entry_leg["status"] == UNAVAILABLE or exit_leg["status"] == UNAVAILABLE:

        return {
            "entry": entry_leg,
            "exit": exit_leg,
            "total_friction": None,
            "friction_per_contract": None,
            "friction_pct_of_premium": None,
            "spread_component": None,
            "commission_component": None,
            "fee_component": None,
            "status": UNAVAILABLE,
            "reason": entry_leg["reason"] or exit_leg["reason"],
        }

    contracts = int(_float(contracts, 0) or 0)
    notional = model.contract_multiplier * contracts

    spread_component = (
        (entry_fill["fill"] - entry_fill["mid"]) * notional
        + (exit_fill["mid"] - exit_fill["fill"]) * notional
    )
    commission_component = entry_leg["commission"] + exit_leg["commission"]
    fee_component = entry_leg["regulatory_fees"] + exit_leg["regulatory_fees"]
    total_friction = spread_component + commission_component + fee_component

    status = worst_status(
        entry_tick["status"],
        exit_tick["status"],
        OK if stop_exit is not None else DEGRADED
    )

    for candidate in [entry_tick["reason"], exit_tick["reason"]]:

        if candidate and candidate not in reasons:

            reasons.append(candidate)

    return {
        "entry": {**entry_leg, "fill": entry_fill},
        "exit": {**exit_leg, "fill": exit_fill},
        "total_friction": total_friction,
        "friction_per_contract": total_friction / contracts if contracts else None,
        "friction_pct_of_premium": (
            total_friction / entry_leg["gross_debit"] * 100
            if entry_leg["gross_debit"]
            else None
        ),
        "spread_component": spread_component,
        "commission_component": commission_component,
        "fee_component": fee_component,
        "stop_exit": stop_exit,
        "exit_spread_multiplier": exit_multiplier,
        "status": status,
        "reason": ";".join(reasons) if reasons else None,
    }
