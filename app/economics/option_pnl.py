from __future__ import annotations

import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.economics.trade_costs import (
    DEGRADED,
    OK,
    UNAVAILABLE,
    entry_cost,
    exit_proceeds,
    fill_price,
    get_cost_model,
    is_stop_exit,
    resolve_tick_size,
    worst_status,
)


MARKET_TZ = ZoneInfo("America/New_York")

EXPIRY_HOUR = 16
DAYS_PER_YEAR = 365.0
SECONDS_PER_YEAR = DAYS_PER_YEAR * 24 * 3600

MIN_TIME_TO_EXPIRY = 1e-6
IV_LOWER_BOUND = 1e-4
IV_UPPER_BOUND = 5.0
IV_MAX_ITERATIONS = 200
IV_PRICE_TOLERANCE = 1e-10
IV_VOL_TOLERANCE = 1e-12

DEFAULT_RISK_FREE_RATE = 0.04

SOURCE_ACTUAL = "ACTUAL_QUOTE"
SOURCE_ESTIMATE = "BS_ESTIMATE"


def _float(value, default=None):

    try:

        if value is None:

            return default

        return float(value)

    except Exception:

        return default


def _norm_cdf(x):

    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):

    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _field(source, *names, default=None):

    source = source or {}

    for name in names:

        value = source.get(name)

        if value is not None:

            return value

    return default


def _as_datetime(value):

    if isinstance(value, datetime):

        return value

    if isinstance(value, date):

        return datetime.combine(value, time(0, 0), tzinfo=MARKET_TZ)

    try:

        return datetime.fromisoformat(str(value))

    except Exception:

        return None


def parse_occ_ticker(option_ticker):

    unavailable = {
        "underlying": None,
        "expiry": None,
        "option_type": None,
        "strike": None,
        "status": UNAVAILABLE,
        "reason": "MALFORMED_OCC_TICKER",
    }

    text = str(option_ticker or "").strip().upper()

    if text.startswith("O:"):

        text = text[2:]

    if len(text) < 16:

        return unavailable

    strike_raw = text[-8:]
    kind_raw = text[-9]
    date_raw = text[-15:-9]
    root = text[:-15]

    if not root or not root.isalnum():

        return unavailable

    if not strike_raw.isdigit() or not date_raw.isdigit():

        return unavailable

    if kind_raw not in {"C", "P"}:

        return unavailable

    try:

        expiry = date(
            2000 + int(date_raw[0:2]),
            int(date_raw[2:4]),
            int(date_raw[4:6])
        )

    except ValueError:

        return unavailable

    strike = int(strike_raw) / 1000.0

    if strike <= 0:

        return unavailable

    return {
        "underlying": root,
        "expiry": expiry,
        "option_type": "CALL" if kind_raw == "C" else "PUT",
        "strike": strike,
        "status": OK,
        "reason": None,
    }


def time_to_expiry_years(expiry, as_of):

    if expiry is None:

        return None

    as_of = _as_datetime(as_of)

    if as_of is None:

        return None

    if as_of.tzinfo is None:

        as_of = as_of.replace(tzinfo=MARKET_TZ)

    moment = datetime.combine(
        expiry,
        time(EXPIRY_HOUR, 0),
        tzinfo=MARKET_TZ
    )

    return (moment - as_of).total_seconds() / SECONDS_PER_YEAR


def _intrinsic(spot, strike, option_type):

    if option_type == "CALL":

        return max(0.0, spot - strike)

    return max(0.0, strike - spot)


def _unavailable_price(reason):

    return {
        "price": None,
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "intrinsic": None,
        "extrinsic": None,
        "status": UNAVAILABLE,
        "reason": reason,
    }


def black_scholes_price(spot, strike, time_to_expiry_years, volatility,
                        risk_free_rate, option_type):

    kind = str(option_type or "").strip().upper()

    if kind not in {"CALL", "PUT"}:

        return _unavailable_price("INVALID_OPTION_TYPE")

    spot = _float(spot)
    strike = _float(strike)
    sigma = _float(volatility)
    rate = _float(risk_free_rate, DEFAULT_RISK_FREE_RATE)
    expiry_years = _float(time_to_expiry_years)

    if spot is None or spot <= 0:

        return _unavailable_price("INVALID_SPOT")

    if strike is None or strike <= 0:

        return _unavailable_price("INVALID_STRIKE")

    if sigma is None or sigma <= 0:

        return _unavailable_price("INVALID_VOLATILITY")

    if expiry_years is None:

        return _unavailable_price("INVALID_TIME_TO_EXPIRY")

    intrinsic = _intrinsic(spot, strike, kind)

    if expiry_years <= MIN_TIME_TO_EXPIRY:

        return {
            "price": intrinsic,
            "delta": (
                (1.0 if spot > strike else 0.0)
                if kind == "CALL"
                else (-1.0 if spot < strike else 0.0)
            ),
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "intrinsic": intrinsic,
            "extrinsic": 0.0,
            "status": DEGRADED,
            "reason": "TIME_TO_EXPIRY_BELOW_FLOOR",
        }

    sqrt_t = math.sqrt(expiry_years)
    d1 = (
        math.log(spot / strike)
        + (rate + 0.5 * sigma * sigma) * expiry_years
    ) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    discount = math.exp(-rate * expiry_years)

    if kind == "CALL":

        price = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = (
            -spot * _norm_pdf(d1) * sigma / (2 * sqrt_t)
            - rate * strike * discount * _norm_cdf(d2)
        )

    else:

        price = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -spot * _norm_pdf(d1) * sigma / (2 * sqrt_t)
            + rate * strike * discount * _norm_cdf(-d2)
        )

    return {
        "price": price,
        "delta": delta,
        "gamma": _norm_pdf(d1) / (spot * sigma * sqrt_t),
        "theta": theta,
        "vega": spot * _norm_pdf(d1) * sqrt_t,
        "intrinsic": intrinsic,
        "extrinsic": price - intrinsic,
        "status": OK,
        "reason": None,
    }


def implied_volatility(market_price, spot, strike, time_to_expiry_years,
                       risk_free_rate, option_type):

    unavailable = {
        "iv": None,
        "iterations": 0,
        "converged": False,
        "status": UNAVAILABLE,
    }

    price = _float(market_price)

    if price is None or price <= 0:

        return {**unavailable, "reason": "INVALID_MARKET_PRICE"}

    def priced(sigma):

        return black_scholes_price(
            spot,
            strike,
            time_to_expiry_years,
            sigma,
            risk_free_rate,
            option_type
        )

    low = priced(IV_LOWER_BOUND)
    high = priced(IV_UPPER_BOUND)

    if low["status"] == UNAVAILABLE or high["status"] == UNAVAILABLE:

        return {**unavailable, "reason": low["reason"] or high["reason"]}

    if price < low["price"] - IV_PRICE_TOLERANCE:

        return {**unavailable, "reason": "PRICE_BELOW_INTRINSIC"}

    if price > high["price"] + IV_PRICE_TOLERANCE:

        return {**unavailable, "reason": "PRICE_ABOVE_MODEL_RANGE"}

    lo = IV_LOWER_BOUND
    hi = IV_UPPER_BOUND
    iterations = 0

    while iterations < IV_MAX_ITERATIONS:

        iterations += 1
        mid = (lo + hi) / 2
        candidate = priced(mid)

        if candidate["status"] == UNAVAILABLE:

            return {**unavailable, "iterations": iterations, "reason": candidate["reason"]}

        converged_on_price = abs(candidate["price"] - price) < IV_PRICE_TOLERANCE
        converged_on_vol = (hi - lo) < IV_VOL_TOLERANCE

        if converged_on_price or converged_on_vol:

            return {
                "iv": mid,
                "iterations": iterations,
                "converged": True,
                "status": OK,
                "reason": None,
            }

        if candidate["price"] > price:

            hi = mid

        else:

            lo = mid

    resolved = (lo + hi) / 2
    final = priced(resolved)
    converged = (
        final["status"] != UNAVAILABLE
        and abs(final["price"] - price) < IV_PRICE_TOLERANCE
    )

    if not converged:

        return {
            **unavailable,
            "iterations": iterations,
            "reason": "IV_DID_NOT_CONVERGE",
        }

    return {
        "iv": resolved,
        "iterations": iterations,
        "converged": True,
        "status": OK,
        "reason": None,
    }


def reprice_option(option_ticker, spot, as_of, entry_iv,
                   risk_free_rate=DEFAULT_RISK_FREE_RATE):

    contract = parse_occ_ticker(option_ticker)

    if contract["status"] == UNAVAILABLE:

        return {
            "price": None,
            "greeks": None,
            "time_to_expiry_years": None,
            "source": SOURCE_ESTIMATE,
            "status": UNAVAILABLE,
            "reason": contract["reason"],
        }

    expiry_years = time_to_expiry_years(contract["expiry"], as_of)

    if expiry_years is None:

        return {
            "price": None,
            "greeks": None,
            "time_to_expiry_years": None,
            "source": SOURCE_ESTIMATE,
            "status": UNAVAILABLE,
            "reason": "INVALID_AS_OF",
        }

    if expiry_years <= 0:

        return {
            "price": None,
            "greeks": None,
            "time_to_expiry_years": expiry_years,
            "source": SOURCE_ESTIMATE,
            "status": UNAVAILABLE,
            "reason": "EXPIRED_BEFORE_AS_OF",
        }

    priced = black_scholes_price(
        spot,
        contract["strike"],
        expiry_years,
        entry_iv,
        risk_free_rate,
        contract["option_type"]
    )

    return {
        "price": priced["price"],
        "greeks": {
            "delta": priced["delta"],
            "gamma": priced["gamma"],
            "theta": priced["theta"],
            "vega": priced["vega"],
        },
        "time_to_expiry_years": expiry_years,
        "source": SOURCE_ESTIMATE,
        "status": priced["status"],
        "reason": priced["reason"],
    }


def option_pnl_realized(entry_quote, exit_quote, contracts, model=None,
                        exit_reason=None):

    from app.economics.trade_costs import round_trip_costs

    model = model or get_cost_model()

    costs = round_trip_costs(
        entry_quote,
        exit_quote,
        contracts,
        model,
        exit_reason
    )

    if costs["status"] == UNAVAILABLE:

        return {
            "pnl_option_gross": None,
            "pnl_option_net": None,
            "cost_total": None,
            "source": SOURCE_ACTUAL,
            "status": UNAVAILABLE,
            "reason": costs["reason"],
        }

    contracts = int(_float(contracts, 0) or 0)
    notional = model.contract_multiplier * contracts

    entry_mid = costs["entry"]["fill"]["mid"]
    exit_mid = costs["exit"]["fill"]["mid"]

    return {
        "pnl_option_gross": (exit_mid - entry_mid) * notional,
        "pnl_option_net": (
            costs["exit"]["net_proceeds"] - costs["entry"]["total_cost"]
        ),
        "cost_total": costs["total_friction"],
        "cost_spread_component": costs["spread_component"],
        "cost_commission_component": (
            costs["commission_component"] + costs["fee_component"]
        ),
        "source": SOURCE_ACTUAL,
        "status": costs["status"],
        "reason": costs["reason"],
    }


def _unavailable_assembly(reason):

    return {
        "r_multiple_net": None,
        "r_multiple_gross": None,
        "pnl_option_est": None,
        "pnl_underlying_est": None,
        "cost_total": None,
        "cost_spread_component": None,
        "cost_commission_component": None,
        "premium_at_stop_est": None,
        "implied_stop_loss_pct": None,
        "risk_dollars_net": None,
        "risk_dollars_gross": None,
        "entry_iv": None,
        "source": SOURCE_ESTIMATE,
        "confidence": "LOW",
        "status": UNAVAILABLE,
        "reason": reason,
    }


def _assemble(trade, exit_spot, exit_time, model=None, exit_reason=None):

    """Shared C-block / D-block assembly.

    The exit repricing and the stop repricing MUST use the same `as_of`,
    otherwise the -1R identity (a trade exiting exactly at its stop returns
    exactly -1.0) silently breaks.
    """

    model = model or get_cost_model()
    trade = trade or {}

    ticker = _field(trade, "option_ticker", "Option Ticker")
    contract = parse_occ_ticker(ticker)

    if contract["status"] == UNAVAILABLE:

        return _unavailable_assembly(contract["reason"])

    kind = contract["option_type"]

    entry_spot = _float(_field(trade, "entry_price", "Candidate Entry Price"))
    stop_spot = _float(_field(trade, "stop_loss", "Candidate Stop Price"))
    exit_spot = _float(exit_spot)

    if entry_spot is None or entry_spot <= 0:

        return _unavailable_assembly("MISSING_ENTRY_PRICE")

    if stop_spot is None or stop_spot <= 0:

        return _unavailable_assembly("MISSING_STOP_PRICE")

    if exit_spot is None or exit_spot <= 0:

        return _unavailable_assembly("MISSING_EXIT_SPOT")

    bid = _float(_field(trade, "option_bid", "Option Bid"))
    ask = _float(_field(trade, "option_ask", "Option Ask"))

    if bid is None or ask is None or bid <= 0 or ask <= 0:

        return _unavailable_assembly("MISSING_ENTRY_QUOTE")

    if ask < bid:

        return _unavailable_assembly("CROSSED_MARKET")

    contracts = int(_float(_field(trade, "contracts"), 1) or 1)

    if contracts <= 0:

        return _unavailable_assembly("INVALID_CONTRACTS")

    rate = _float(_field(trade, "risk_free_rate"), DEFAULT_RISK_FREE_RATE)

    opened_at = _field(trade, "opened_at", "entry_time")
    entry_years = time_to_expiry_years(contract["expiry"], opened_at)
    exit_years = time_to_expiry_years(contract["expiry"], exit_time)

    if entry_years is None or exit_years is None:

        return _unavailable_assembly("MISSING_TIMESTAMPS")

    if exit_years <= 0:

        return _unavailable_assembly("EXPIRED_BEFORE_EXIT")

    entry_mid = (bid + ask) / 2
    entry_half_spread = (ask - bid) / 2

    reasons = []

    stored_iv = _float(_field(trade, "entry_iv", "Option IV"))
    inverted = implied_volatility(
        entry_mid,
        entry_spot,
        contract["strike"],
        entry_years,
        rate,
        kind
    )

    if inverted["status"] == OK:

        entry_iv = inverted["iv"]

    elif stored_iv is not None and stored_iv > 0:

        entry_iv = stored_iv
        reasons.append("ENTRY_IV_FROM_STORED_FIELD")

    else:

        return _unavailable_assembly(inverted["reason"])

    entry_tick = resolve_tick_size(ask, model)
    entry_fill = fill_price(
        entry_mid,
        bid,
        ask,
        "BUY",
        model.entry_fill_aggression,
        entry_tick["tick_size"]
    )

    if entry_fill["status"] == UNAVAILABLE:

        return _unavailable_assembly(entry_fill["reason"])

    entry_leg = entry_cost(entry_fill["fill"], contracts, model)

    if entry_leg["status"] == UNAVAILABLE:

        return _unavailable_assembly(entry_leg["reason"])

    stop_exit = is_stop_exit(exit_reason)

    if stop_exit is None:

        reasons.append("EXIT_REASON_UNCLASSIFIED")

    exit_multiplier = (
        model.stop_exit_spread_multiplier
        if stop_exit
        else 1.0
    )

    exit_priced = black_scholes_price(
        exit_spot,
        contract["strike"],
        exit_years,
        entry_iv,
        rate,
        kind
    )

    stop_priced = black_scholes_price(
        stop_spot,
        contract["strike"],
        exit_years,
        entry_iv,
        rate,
        kind
    )

    if exit_priced["status"] == UNAVAILABLE or stop_priced["status"] == UNAVAILABLE:

        return _unavailable_assembly(
            exit_priced["reason"] or stop_priced["reason"]
        )

    exit_mid = exit_priced["price"]
    stop_mid = stop_priced["price"]

    exit_tick = resolve_tick_size(exit_mid, model)

    exit_fill = fill_price(
        exit_mid,
        exit_mid - entry_half_spread,
        exit_mid + entry_half_spread,
        "SELL",
        model.exit_fill_aggression,
        exit_tick["tick_size"],
        exit_multiplier
    )

    stop_fill = fill_price(
        stop_mid,
        stop_mid - entry_half_spread,
        stop_mid + entry_half_spread,
        "SELL",
        model.exit_fill_aggression,
        exit_tick["tick_size"],
        model.stop_exit_spread_multiplier
    )

    if exit_fill["status"] == UNAVAILABLE or stop_fill["status"] == UNAVAILABLE:

        return _unavailable_assembly(
            exit_fill["reason"] or stop_fill["reason"]
        )

    exit_leg = exit_proceeds(exit_fill["fill"], contracts, model)
    stop_leg = exit_proceeds(stop_fill["fill"], contracts, model)

    if exit_leg["status"] == UNAVAILABLE or stop_leg["status"] == UNAVAILABLE:

        return _unavailable_assembly(exit_leg["reason"] or stop_leg["reason"])

    notional = model.contract_multiplier * contracts

    risk_premium = (entry_fill["fill"] - stop_fill["fill"]) * notional
    risk_costs = (
        entry_leg["commission"]
        + entry_leg["regulatory_fees"]
        + stop_leg["commission"]
        + stop_leg["regulatory_fees"]
    )
    risk_dollars_net = risk_premium + risk_costs
    risk_dollars_gross = (entry_mid - stop_mid) * notional

    pnl_option_gross = (exit_mid - entry_mid) * notional
    pnl_option_net = exit_leg["net_proceeds"] - entry_leg["total_cost"]

    spread_component = (
        (entry_fill["fill"] - entry_mid) * notional
        + (exit_mid - exit_fill["fill"]) * notional
    )
    commission_component = entry_leg["commission"] + exit_leg["commission"]
    fee_component = entry_leg["regulatory_fees"] + exit_leg["regulatory_fees"]
    cost_total = spread_component + commission_component + fee_component

    underlying_move = (
        (exit_spot - entry_spot)
        if kind == "CALL"
        else (entry_spot - exit_spot)
    )

    status = worst_status(
        entry_tick["status"],
        exit_tick["status"],
        exit_priced["status"],
        stop_priced["status"],
        OK if stop_exit is not None else DEGRADED,
        OK if not reasons else DEGRADED
    )

    for candidate in [entry_tick["reason"], exit_tick["reason"]]:

        if candidate and candidate not in reasons:

            reasons.append(candidate)

    return {
        "r_multiple_net": (
            pnl_option_net / risk_dollars_net
            if risk_dollars_net
            else None
        ),
        "r_multiple_gross": (
            pnl_option_gross / risk_dollars_gross
            if risk_dollars_gross
            else None
        ),
        "pnl_option_est": pnl_option_net,
        "pnl_option_gross": pnl_option_gross,
        "pnl_underlying_est": underlying_move * notional,
        "cost_total": cost_total,
        "cost_spread_component": spread_component,
        "cost_commission_component": commission_component + fee_component,
        "premium_at_stop_est": stop_mid,
        "premium_at_exit_est": exit_mid,
        "implied_stop_loss_pct": (
            (entry_mid - stop_mid) / entry_mid * 100
            if entry_mid
            else None
        ),
        "risk_dollars_net": risk_dollars_net,
        "risk_dollars_gross": risk_dollars_gross,
        "entry_iv": entry_iv,
        "entry_fill": entry_fill["fill"],
        "exit_fill": exit_fill["fill"],
        "stop_fill": stop_fill["fill"],
        "contracts": contracts,
        "source": SOURCE_ESTIMATE,
        "confidence": "MEDIUM" if status == OK else "LOW",
        "status": status,
        "reason": ";".join(reasons) if reasons else None,
    }


def option_pnl_estimated(trade, exit_spot, exit_time, model=None,
                         exit_reason=None):

    return _assemble(trade, exit_spot, exit_time, model, exit_reason)


def planned_risk_dollars(trade, model=None, as_of=None, exit_reason=None):

    stop_spot = _float(_field(trade or {}, "stop_loss", "Candidate Stop Price"))

    assembled = _assemble(
        trade,
        stop_spot,
        as_of,
        model,
        exit_reason or "HARD_STOP"
    )

    if assembled["status"] == UNAVAILABLE:

        return {
            "risk_dollars_net": None,
            "premium_at_stop": None,
            "premium_at_entry": None,
            "friction": None,
            "implied_stop_loss_pct": None,
            "status": UNAVAILABLE,
            "reason": assembled["reason"],
        }

    return {
        "risk_dollars_net": assembled["risk_dollars_net"],
        "premium_at_stop": assembled["premium_at_stop_est"],
        "premium_at_entry": (
            assembled["premium_at_stop_est"]
            + assembled["risk_dollars_gross"] / assembled["contracts"] / 100
        ),
        "friction": assembled["cost_total"],
        "implied_stop_loss_pct": assembled["implied_stop_loss_pct"],
        "status": assembled["status"],
        "reason": assembled["reason"],
    }


def r_multiple_net(trade, exit_spot, exit_time, model=None, exit_reason=None):

    return _assemble(trade, exit_spot, exit_time, model, exit_reason)
