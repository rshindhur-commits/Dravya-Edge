from app.config.settings import get_float_env
from app.options.affordability_config import get_affordability_config


def _float_value(value, default=0.0):

    try:

        return float(value)

    except Exception:

        return default


def _option_mid_price(contract):

    for field in [
        "mid_price",
        "option_mid_price",
        "quote_midpoint",
        "midpoint",
        "close"
    ]:

        value = contract.get(field)

        if value is not None:

            return _float_value(value)

    bid = _float_value(contract.get("bid"))
    ask = _float_value(contract.get("ask"))

    if bid > 0 and ask > 0:

        return (bid + ask) / 2

    return 0.0


def _option_entry_price(contract):
    """The price a buy order would actually have to pay, not the midpoint.

    Affordability was measured against the mid, but a long option is bought at or
    near the ask. On a real MRVL $210 call 08/14 quoted 4.60 / 5.15, the mid is
    4.875 -- a $487.50 contract, inside a $500 limit -- while the order screen shows
    an estimated cost of $515.04. The gate approved a contract that cannot be
    bought within the limit, and nothing downstream re-checked it.

    That error scales with the spread, so it is largest exactly where it is most
    dangerous: this contract's round trip is 10.7% of the ask.

    AFFORDABILITY_FILL_FRACTION is where in the spread the fill is assumed. 1.0 is
    the ask and guarantees the contract is buyable inside the limit; 0.5 restores
    the previous mid-based behaviour. The default is deliberately the conservative
    end -- approving an unbuyable contract wastes a signal and an alert, while
    rejecting a marginal one costs a candidate that was at the edge of budget
    anyway.
    """

    mid = _option_mid_price(contract)
    ask = _float_value(contract.get("ask"))
    bid = _float_value(contract.get("bid"))

    if ask <= 0:
        return mid

    if bid <= 0 or ask < bid:
        return ask

    fraction = get_float_env("AFFORDABILITY_FILL_FRACTION", 1.0)
    fraction = min(max(fraction, 0.0), 1.0)

    return bid + (ask - bid) * fraction


def add_affordability_metrics(contract, current_capital=None, config=None):

    if not contract:

        return contract

    config = config or get_affordability_config()

    current_capital = (
        _float_value(current_capital)
        if current_capital is not None
        else _float_value(config.get("daily_start_capital"), 1000.0)
    )

    mid = _option_mid_price(contract)
    # Cost is what the order pays, which is the ask end of the spread, not the mid.
    entry_price = _option_entry_price(contract)
    contract_cost = entry_price * 100.0

    stop_loss_pct = _float_value(
        config.get("option_stop_loss_pct"),
        0.20
    )
    max_risk_pct = _float_value(
        config.get("max_risk_per_trade_pct"),
        0.12
    )
    hard_max_cost = _float_value(
        config.get("max_contract_cost"),
        650.0
    )
    preferred_max_cost = _float_value(
        config.get("preferred_max_contract_cost"),
        500.0
    )
    min_cost = _float_value(
        config.get("min_contract_cost"),
        100.0
    )
    min_delta = _float_value(
        config.get("min_affordable_delta"),
        0.25
    )

    max_allowed_risk = current_capital * max_risk_pct
    max_cost_by_risk = (
        max_allowed_risk / stop_loss_pct
        if stop_loss_pct > 0
        else hard_max_cost
    )
    max_allowed_cost = min(
        hard_max_cost,
        max_cost_by_risk
    )
    effective_preferred_max_cost = min(
        preferred_max_cost,
        max_allowed_cost
    )
    risk_at_stop = contract_cost * stop_loss_pct
    delta = abs(_float_value(contract.get("delta")))
    delta_ok = delta >= min_delta
    risk_ok = risk_at_stop <= max_allowed_risk
    cost_ok = min_cost <= contract_cost <= max_allowed_cost and risk_ok

    contract["contract_cost"] = round(contract_cost, 2)
    # Both are recorded so a rejection near the limit can be read directly: the
    # gap between them is the spread, and it is what decides borderline contracts.
    contract["contract_entry_price"] = round(entry_price, 4)
    contract["contract_mid_price"] = round(mid, 4)
    contract["contract_cost_at_mid"] = round(mid * 100.0, 2)
    contract["risk_at_stop"] = round(risk_at_stop, 2)
    contract["max_allowed_risk"] = round(max_allowed_risk, 2)
    contract["current_capital"] = round(current_capital, 2)
    contract["max_allowed_contract_cost"] = round(max_allowed_cost, 2)
    contract["risk_based_max_contract_cost"] = round(max_cost_by_risk, 2)
    contract["preferred_max_contract_cost"] = round(preferred_max_cost, 2)
    contract["affordability_mode"] = config.get("mode", "HARD")
    contract["capital_profile"] = config.get("profile_name", "SMALL_ACCOUNT")
    contract["affordable"] = cost_ok and delta_ok
    contract["preferred_affordable"] = (
        min_cost <= contract_cost <= effective_preferred_max_cost
        and risk_ok
        and delta_ok
    )

    if contract_cost <= 0:

        status = "NO_OPTION_PRICE"

    elif contract_cost < min_cost:

        status = "TOO_CHEAP_LOW_QUALITY_RISK"

    elif not delta_ok:

        status = "DELTA_TOO_LOW_FOR_AFFORDABLE_TRADE"

    elif contract_cost <= effective_preferred_max_cost:

        status = "PREFERRED_AFFORDABLE"

    elif contract_cost <= max_allowed_cost:

        status = "AFFORDABLE"

    else:

        status = "OPTION_TOO_EXPENSIVE"

    contract["affordability_status"] = status

    return contract


def build_affordability_rule_evaluations(contract, scan_id, symbol, setup=None):
    from app.gates.rule_evaluation import RuleEvaluation

    contract = contract or {}
    affordable = bool(contract.get("affordable"))
    return [
        RuleEvaluation(scan_id, symbol, setup, "Affordability", "Affordability", contract.get("contract_cost"), contract.get("max_allowed_contract_cost"), affordable, not affordable, 70),
        RuleEvaluation(scan_id, symbol, setup, "Option Delta", "Affordability", contract.get("delta"), get_affordability_config().get("min_affordable_delta"), contract.get("affordability_status") != "DELTA_TOO_LOW_FOR_AFFORDABLE_TRADE", contract.get("affordability_status") == "DELTA_TOO_LOW_FOR_AFFORDABLE_TRADE", 60),
    ]