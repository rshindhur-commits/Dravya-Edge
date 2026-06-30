from app.utils.json_store import (
    load_json_file,
    save_json_file
)

TRADE_STATE_FILE = (
    "app/state/trade_state.json"
)


def load_trade_state():

    return load_json_file(
        TRADE_STATE_FILE,
        {}
    )


def save_trade_state(state):

    save_json_file(
        TRADE_STATE_FILE,
        state
    )


def get_trade(symbol):

    state = load_trade_state()

    return state.get(symbol)


def open_trade(

    symbol,
    entry_price,
    stop_loss,
    take_profit,
    entry_type=None,
    option_data=None,
    contracts=None

):

    state = load_trade_state()

    option_data = option_data or {}

    state[symbol] = {

        "status": "OPEN",

        "entry_price": entry_price,

        "stop_loss": stop_loss,

        "take_profit": take_profit,

        "entry_type": entry_type,

        "highest_price": entry_price,

        "lowest_price": entry_price,

        "bars_in_trade": 0,

        "partial_profit_taken": False,

        "rr_progress": 0,

        "option_ticker": option_data.get("ticker"),

        "option_entry_mid": option_data.get("mid_price"),

        "option_current_mid": option_data.get("mid_price"),

        "option_bid": option_data.get("bid"),

        "option_ask": option_data.get("ask"),

        "option_spread_pct": option_data.get("spread_pct"),

        "option_volume": option_data.get("volume"),

        "option_open_interest": option_data.get("open_interest"),

        "option_delta": option_data.get("delta"),

        "option_theta": option_data.get("theta"),

        "option_iv": option_data.get("iv"),

        "option_gamma": option_data.get("gamma"),

        "option_expiration": option_data.get("expiration"),

        "option_expiration_bucket": option_data.get("expiration_bucket"),

        "option_expiration_risk": option_data.get("expiration_risk"),

        "option_quality_score": option_data.get("option_quality_score"),

        "option_liquidity_grade": option_data.get("option_liquidity_grade"),

        "option_quality_reasons": option_data.get("option_quality_reasons"),

        "option_quote_freshness": option_data.get("quote_freshness"),

        "option_quote_age_minutes": option_data.get("quote_age_minutes"),

        "option_contracts": contracts

    }

    save_trade_state(state)


def close_trade(symbol):

    state = load_trade_state()

    if symbol in state:

        del state[symbol]

    save_trade_state(state)


def update_trade(

    symbol,
    highest_price,
    rr_progress,
    updated_stop,
    lowest_price=None,
    bars_in_trade=None,
    partial_profit_taken=None,
    option_data=None,
    option_pl=None

):

    state = load_trade_state()

    if symbol not in state:

        return

    state[symbol][
        "highest_price"
    ] = highest_price

    if lowest_price is not None:

        state[symbol][
            "lowest_price"
        ] = lowest_price

    if bars_in_trade is not None:

        state[symbol][
            "bars_in_trade"
        ] = bars_in_trade

    if partial_profit_taken is not None:

        state[symbol][
            "partial_profit_taken"
        ] = partial_profit_taken

    if option_data:

        state[symbol]["option_current_mid"] = option_data.get(
            "mid_price"
        )
        state[symbol]["option_bid"] = option_data.get("bid")
        state[symbol]["option_ask"] = option_data.get("ask")
        state[symbol]["option_spread_pct"] = option_data.get(
            "spread_pct"
        )
        state[symbol]["option_volume"] = option_data.get("volume")
        state[symbol]["option_open_interest"] = option_data.get(
            "open_interest"
        )
        state[symbol]["option_delta"] = option_data.get("delta")
        state[symbol]["option_theta"] = option_data.get("theta")
        state[symbol]["option_iv"] = option_data.get("iv")
        state[symbol]["option_gamma"] = option_data.get("gamma")
        state[symbol]["option_expiration_bucket"] = option_data.get(
            "expiration_bucket"
        )
        state[symbol]["option_expiration_risk"] = option_data.get(
            "expiration_risk"
        )
        state[symbol]["option_quality_score"] = option_data.get(
            "option_quality_score"
        )
        state[symbol]["option_liquidity_grade"] = option_data.get(
            "option_liquidity_grade"
        )
        state[symbol]["option_quality_reasons"] = option_data.get(
            "option_quality_reasons"
        )
        state[symbol]["option_quote_freshness"] = option_data.get(
            "quote_freshness"
        )
        state[symbol]["option_quote_age_minutes"] = option_data.get(
            "quote_age_minutes"
        )

    if option_pl:

        state[symbol]["option_pl_pct"] = option_pl.get(
            "option_pl_pct"
        )
        state[symbol]["option_pl_dollars"] = option_pl.get(
            "option_pl_dollars"
        )

    state[symbol][
        "rr_progress"
    ] = rr_progress

    state[symbol][
        "stop_loss"
    ] = updated_stop

    save_trade_state(state)