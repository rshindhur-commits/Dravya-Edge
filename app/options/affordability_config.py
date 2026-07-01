from app.config.capital_profiles import CAPITAL_PROFILES
from app.config.settings import get_bool_env, get_float_env, get_int_env, get_secret_env


def get_affordability_config():

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

    return config