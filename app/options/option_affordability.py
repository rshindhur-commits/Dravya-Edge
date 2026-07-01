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
    contract_cost = mid * 100.0

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

    max_cost_by_risk = (
        current_capital * max_risk_pct / stop_loss_pct
        if stop_loss_pct > 0
        else hard_max_cost
    )
    max_allowed_cost = min(
        hard_max_cost,
        max_cost_by_risk
    )
    risk_at_stop = contract_cost * stop_loss_pct
    delta = abs(_float_value(contract.get("delta")))
    delta_ok = delta >= min_delta
    cost_ok = min_cost <= contract_cost <= max_allowed_cost

    contract["contract_cost"] = round(contract_cost, 2)
    contract["risk_at_stop"] = round(risk_at_stop, 2)
    contract["current_capital"] = round(current_capital, 2)
    contract["max_allowed_contract_cost"] = round(max_allowed_cost, 2)
    contract["preferred_max_contract_cost"] = round(preferred_max_cost, 2)
    contract["affordability_mode"] = config.get("mode", "HARD")
    contract["capital_profile"] = config.get("profile_name", "SMALL_ACCOUNT")
    contract["affordable"] = cost_ok and delta_ok
    contract["preferred_affordable"] = (
        min_cost <= contract_cost <= preferred_max_cost
        and delta_ok
    )

    if contract_cost <= 0:

        status = "NO_OPTION_PRICE"

    elif contract_cost < min_cost:

        status = "TOO_CHEAP_LOW_QUALITY_RISK"

    elif not delta_ok:

        status = "DELTA_TOO_LOW_FOR_AFFORDABLE_TRADE"

    elif contract_cost <= preferred_max_cost:

        status = "PREFERRED_AFFORDABLE"

    elif contract_cost <= max_allowed_cost:

        status = "AFFORDABLE"

    else:

        status = "OPTION_TOO_EXPENSIVE"

    contract["affordability_status"] = status

    return contract