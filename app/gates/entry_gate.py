from dataclasses import dataclass
from datetime import datetime
import math
import os


ACTIONABLE_STATUSES = {
    "ENTER",
    "ENTER_PAPER",
    "REVIEW_TV_CHART"
}


@dataclass
class EntryGateConfig:
    min_rr: float = 1.8
    min_setup_percent: float = 70.0
    min_option_quality: float = 65.0
    max_spread_pct: float = 10.0


def _row_get(row, *names, default=None):

    for name in names:

        try:

            value = row.get(name)

        except Exception:

            value = None

        if value is not None and str(value).strip().lower() not in {
            "",
            "nan",
            "none"
        }:

            return value

    return default


def safe_float(value, default=0.0):

    try:

        if value is None:

            return default

        if isinstance(value, float) and math.isnan(value):

            return default

        text = str(value).strip()

        if text.lower() in {"", "nan", "none"}:

            return default

        return float(text)

    except Exception:

        return default


def _geometry_float(value):

    try:

        if value is None:

            return None

        numeric_value = float(value)

        if math.isnan(numeric_value) or math.isinf(numeric_value):

            return None

        return numeric_value

    except (TypeError, ValueError):

        return None


def normalize_candidate_direction(direction):

    direction = str(direction or "").strip().upper()

    if direction in {"CALL", "LONG", "BULLISH", "HIGH CONVICTION BULLISH"}:

        return "CALL"

    if direction in {"PUT", "SHORT", "BEARISH", "HIGH CONVICTION BEARISH"}:

        return "PUT"

    return "UNKNOWN"


def _row_price_geometry_values(row):

    direction = _row_get(
        row,
        "Candidate Direction",
        "direction",
        "final_signal"
    )
    entry = _row_get(
        row,
        "Candidate Entry Price",
        "entry_price",
        "Entry Price",
        "entry",
        "Price"
    )
    stop = _row_get(
        row,
        "Candidate Stop Price",
        "stop_price",
        "stop_loss",
        "Stop Price",
        "Stop Loss",
        "stop"
    )
    target = _row_get(
        row,
        "Candidate Target Price",
        "target_price",
        "take_profit",
        "Target Price",
        "Take Profit",
        "target"
    )

    return direction, entry, stop, target


def price_geometry_error(
    direction=None,
    entry_price=None,
    stop_loss=None,
    take_profit=None,
    **kwargs
):

    if hasattr(direction, "get") and entry_price is None and stop_loss is None and take_profit is None:

        direction, entry_price, stop_loss, take_profit = _row_price_geometry_values(
            direction
        )

    if entry_price is None:

        entry_price = (
            kwargs.get("entry")
            or kwargs.get("Entry Price")
            or kwargs.get("Candidate Entry Price")
        )

    if stop_loss is None:

        stop_loss = (
            kwargs.get("stop")
            or kwargs.get("Stop Price")
            or kwargs.get("Stop Loss")
            or kwargs.get("Candidate Stop Price")
        )

    if take_profit is None:

        take_profit = (
            kwargs.get("target")
            or kwargs.get("Target Price")
            or kwargs.get("Take Profit")
            or kwargs.get("Candidate Target Price")
        )

    direction = normalize_candidate_direction(direction)
    entry = _geometry_float(entry_price)
    stop = _geometry_float(stop_loss)
    target = _geometry_float(take_profit)

    if direction not in {"CALL", "PUT"}:

        return None

    if entry is None or stop is None or target is None:

        return "MISSING_PRICE_GEOMETRY"

    if direction == "CALL":

        if not (stop < entry < target):

            return "INVALID_PRICE_GEOMETRY_CALL_REQUIRES_STOP_LT_ENTRY_LT_TARGET"

    if direction == "PUT":

        if not (target < entry < stop):

            return "INVALID_PRICE_GEOMETRY_PUT_REQUIRES_TARGET_LT_ENTRY_LT_STOP"

    return None


def validate_price_geometry(
    direction,
    entry_price=None,
    stop_loss=None,
    take_profit=None,
    **kwargs
):

    return price_geometry_error(
        direction,
        entry_price,
        stop_loss,
        take_profit,
        **kwargs
    ) is None


def has_valid_price_geometry(
    direction,
    entry_price=None,
    stop_loss=None,
    take_profit=None,
    **kwargs
):

    return validate_price_geometry(
        direction,
        entry_price,
        stop_loss,
        take_profit,
        **kwargs
    )


def _bool_false(value):

    if value is False or value == 0:

        return True

    return str(value).strip().lower() in {
        "false",
        "0",
        "no",
        "n"
    }


def parse_timestamp(value):

    if not value:

        return None

    if isinstance(value, datetime):

        return value

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z"
    ):

        try:

            return datetime.strptime(str(value), fmt)

        except Exception:

            continue

    try:

        return datetime.fromisoformat(str(value))

    except Exception:

        return None


def env_int(name, default):

    try:

        return int(os.getenv(name, default))

    except Exception:

        return default


def build_trade_key(symbol: str, option_ticker: str, opened_at: str) -> str:

    return f"{symbol}|{option_ticker or 'NO_CONTRACT'}|{opened_at}"


def has_active_symbol_trade(state: dict, symbol: str) -> bool:

    return any(
        trade.get("symbol") == symbol
        and trade.get("status") in {"OPEN", "PAUSED"}
        for trade in (state or {}).values()
    )


def active_symbol_trade(state: dict, symbol: str):

    for key, trade in (state or {}).items():

        if (
            trade.get("symbol") == symbol
            and trade.get("status") in {"OPEN", "PAUSED"}
        ):

            return key, trade

    return None, None


def symbol_trade_count_today(state: dict, symbol: str, now=None) -> int:

    now = now or datetime.now()
    count = 0

    for trade in (state or {}).values():

        if trade.get("symbol") != symbol:

            continue

        opened_at = parse_timestamp(trade.get("opened_at"))

        if opened_at and opened_at.date() == now.date():

            count += 1

    return count


def is_symbol_in_cooldown(
    symbol,
    closed_trades,
    now,
    cooldown_minutes
):

    last_close_dt = None

    for trade in (closed_trades or []):

        if trade.get("symbol") != symbol:

            continue

        closed_at = parse_timestamp(trade.get("closed_at"))

        if closed_at and (
            last_close_dt is None
            or closed_at > last_close_dt
        ):

            last_close_dt = closed_at

    if not last_close_dt:

        return False

    return (
        now.replace(tzinfo=None) - last_close_dt.replace(tzinfo=None)
    ).total_seconds() < cooldown_minutes * 60


def _vix_spike_pct():
    """Intraday VIX move, in percent, that counts as a volatility spike.

    8 is deliberately not sensitive. The purpose is to catch the days when
    premium repriced under you, not to react to ordinary noise -- the VIX moves
    more than 3% on a quiet afternoon.
    """

    try:
        return float(os.getenv("VIX_SPIKE_PCT", "8") or 8)
    except ValueError:
        return 8.0


def apply_regime_entry_thresholds(row, config: EntryGateConfig):
    """Raise the entry bar when conditions are hostile.

    "Market Regime" is not a market regime. _classify_explicit_regime() derives it
    from the candidate's own 15m frame -- its ATR_PCT, EMAs, VWAP, RSI and MACD --
    so it describes one symbol's trend state. A name can read TRENDING_BULL while
    the sector it belongs to is breaking down, and only the first was consulted.

    "Reference Regime" is the market-level view, built in _fetch_reference_context()
    by taking the majority regime across XLK, SMH and SOXX. For a watchlist that is
    almost entirely semiconductors and large-cap tech those three are a better
    market proxy than SPY. It was computed on every scan, written to every row, and
    read by nothing.

    VIX is the same story: fetched, given a move, an ATR and a regime, recorded on
    the row as "VIX Move %", and never consulted. A volatility spike is the single
    clearest signal that premium is expensive and that stops get run -- both of
    which matter more to a premium buyer than to anyone else.

    Every threshold here only ever tightens, never loosens, so a hostile reading
    can raise the bar but no combination can wave through a trade the base config
    would have refused.
    """

    market_regime = str(
        _row_get(row, "Market Regime", "market_regime", default="")
    ).upper()
    reference_regime = str(
        _row_get(row, "Reference Regime", "reference_regime", default="")
    ).upper()
    breadth = safe_float(
        _row_get(row, "Watchlist Breadth Score", "watchlist_breadth_score"),
        0.0
    )
    above_ema20 = safe_float(
        _row_get(row, "Above EMA20 %", "above_ema20_pct"),
        100.0
    )
    vix_move = safe_float(
        _row_get(row, "VIX Move %", "vix_move_pct"),
        None
    )
    direction = str(
        _row_get(row, "Candidate Direction", "candidate_direction", default="")
    ).upper()

    min_setup = config.min_setup_percent
    min_rr = config.min_rr
    max_spread = config.max_spread_pct

    if market_regime == "RANGE_BOUND":

        min_setup = max(min_setup, 90.0)
        min_rr = max(min_rr, 2.0)
        max_spread = min(max_spread, 5.0)

    if breadth < -20 or above_ema20 < 40:

        min_setup = max(min_setup, 88.0)
        min_rr = max(min_rr, 2.0)

    # A candidate fighting its own sector complex. Not blocked outright -- the
    # strongest names lead reversals -- but it has to be clearly better than one
    # that has the tape behind it.
    if reference_regime == "TRENDING_BEAR" and direction == "CALL":

        min_setup = max(min_setup, 88.0)
        min_rr = max(min_rr, 2.0)

    if reference_regime == "TRENDING_BULL" and direction == "PUT":

        min_setup = max(min_setup, 88.0)
        min_rr = max(min_rr, 2.0)

    if reference_regime == "HIGH_VOLATILITY":

        min_setup = max(min_setup, 85.0)
        min_rr = max(min_rr, 2.0)

    # A VIX spike raises every option premium at once and widens the quotes you
    # have to cross twice. Long calls suffer most: the move that was supposed to
    # pay for the position is now competing with a higher entry price.
    if vix_move is not None and vix_move >= _vix_spike_pct():

        min_setup = max(min_setup, 88.0)
        min_rr = max(min_rr, 2.2)
        max_spread = min(max_spread, 5.0)

    # Trading against the daily trend. Every frame the scanner builds is resampled
    # from the same 5-minute pull, so until daily_context existed a candidate could
    # be a clean 15-minute pullback inside a three-week daily downtrend and nothing
    # could tell the difference.
    #
    # Not a block. Counter-trend entries are where reversals begin and the strongest
    # names turn first; they just have to be better than a setup the higher
    # timeframe agrees with.
    daily_trend = str(
        _row_get(row, "Daily Trend", "daily_trend", default="")
    ).upper()

    if daily_trend == "BEAR" and direction == "CALL":

        min_setup = max(min_setup, 85.0)
        min_rr = max(min_rr, 2.0)

    if daily_trend == "BULL" and direction == "PUT":

        min_setup = max(min_setup, 85.0)
        min_rr = max(min_rr, 2.0)

    return min_setup, min_rr, max_spread


def evaluate_entry_gate(
    row,
    config: EntryGateConfig,
    mode: str = "paper"
):

    action_status = str(
        _row_get(row, "Action Status", "action_status", default="")
    ).strip().upper()

    if action_status not in ACTIONABLE_STATUSES:

        return False, "NOT_ACTIONABLE_STATUS"

    geometry_error = price_geometry_error(row)

    if geometry_error:

        return False, "INVALID_PRICE_GEOMETRY"

    min_setup, min_rr, max_spread = apply_regime_entry_thresholds(
        row,
        config
    )

    setup = safe_float(
        _row_get(row, "Setup %", "setup_percent"),
        0.0
    )
    rr = safe_float(
        _row_get(row, "Candidate RR", "Risk Reward", "RR", "rr"),
        0.0
    )
    option_quality = safe_float(
        _row_get(row, "Option Quality Score", "option_quality_score"),
        0.0
    )
    spread = _row_get(
        row,
        "Option Spread %",
        "option_spread_pct",
        "spread_pct",
        default=None
    )
    quote_freshness = _row_get(
        row,
        "Option Quote Freshness",
        "option_quote_freshness",
        "quote_freshness",
        default=None
    )
    affordable = _row_get(
        row,
        "Affordable",
        "affordable",
        default=True
    )

    if rr < min_rr:

        return False, "RR_BELOW_THRESHOLD"

    if setup < min_setup:

        return False, "SETUP_BELOW_THRESHOLD"

    if option_quality < config.min_option_quality:

        return False, "OPTION_QUALITY_BELOW_THRESHOLD"

    if quote_freshness != "LIVE_QUOTE":

        return False, "OPTION_QUOTE_NOT_LIVE"

    if _bool_false(affordable):

        return False, "OPTION_NOT_AFFORDABLE"

    spread_is_unknown = (
        spread is None
        or str(spread).strip().lower() in {"", "nan", "none"}
    )

    if spread_is_unknown:

        if mode in {"telegram", "real"}:

            return False, "UNKNOWN_SPREAD_FOR_ALERT"

        if mode == "paper" and option_quality >= 80:

            return True, "ELIGIBLE_WITH_UNKNOWN_SPREAD"

        return False, "UNKNOWN_SPREAD"

    if safe_float(spread, max_spread + 1) > max_spread:

        return False, "SPREAD_TOO_WIDE"

    return True, "ELIGIBLE"


def build_entry_gate_diagnostics(
    row,
    config: EntryGateConfig,
    mode: str = "paper"
):

    action_status = str(
        _row_get(row, "Action Status", "action_status", default="")
    ).strip().upper()
    geometry_error = price_geometry_error(row)
    min_setup, min_rr, max_spread = apply_regime_entry_thresholds(
        row,
        config
    )
    setup = safe_float(
        _row_get(row, "Setup %", "setup_percent"),
        0.0
    )
    rr = safe_float(
        _row_get(row, "Candidate RR", "Risk Reward", "RR", "rr"),
        0.0
    )
    option_quality = safe_float(
        _row_get(row, "Option Quality Score", "option_quality_score"),
        0.0
    )
    spread = _row_get(
        row,
        "Option Spread %",
        "option_spread_pct",
        "spread_pct",
        default=None
    )
    quote_freshness = _row_get(
        row,
        "Option Quote Freshness",
        "option_quote_freshness",
        "quote_freshness",
        default=None
    )
    affordable = _row_get(
        row,
        "Affordable",
        "affordable",
        default=True
    )
    spread_is_unknown = (
        spread is None
        or str(spread).strip().lower() in {"", "nan", "none"}
    )
    result = "PASS"
    failure = None

    if action_status not in ACTIONABLE_STATUSES:

        result = "FAIL"
        failure = "NOT_ACTIONABLE_STATUS"

    elif geometry_error:

        result = "FAIL"
        failure = "INVALID_PRICE_GEOMETRY"

    elif rr < min_rr:

        result = "FAIL"
        failure = "RR_BELOW_THRESHOLD"

    elif setup < min_setup:

        result = "FAIL"
        failure = "SETUP_BELOW_THRESHOLD"

    elif option_quality < config.min_option_quality:

        result = "FAIL"
        failure = "OPTION_QUALITY_BELOW_THRESHOLD"

    elif quote_freshness != "LIVE_QUOTE":

        result = "FAIL"
        failure = "OPTION_QUOTE_NOT_LIVE"

    elif _bool_false(affordable):

        result = "FAIL"
        failure = "OPTION_NOT_AFFORDABLE"

    elif spread_is_unknown:

        if mode in {"telegram", "real"}:

            result = "FAIL"
            failure = "UNKNOWN_SPREAD_FOR_ALERT"

        elif mode == "paper" and option_quality >= 80:

            result = "PASS"
            failure = None

        else:

            result = "FAIL"
            failure = "UNKNOWN_SPREAD"

    elif safe_float(spread, max_spread + 1) > max_spread:

        result = "FAIL"
        failure = "SPREAD_TOO_WIDE"

    return {
        "setup": setup,
        "min_setup": min_setup,
        "rr": rr,
        "min_rr": min_rr,
        "option_quality": option_quality,
        "min_option_quality": config.min_option_quality,
        "spread": None if spread_is_unknown else safe_float(spread),
        "max_spread": max_spread,
        "quote_freshness": quote_freshness,
        "affordable": affordable,
        "action_status": action_status,
        "geometry_error": geometry_error,
        "result": result,
        "failure": failure,
    }


def build_entry_gate_rule_evaluations(row, config: EntryGateConfig, scan_id: str, mode: str = "paper"):
    """The entry validator's native structured audit output."""
    from app.gates.rule_evaluation import RuleEvaluation

    diagnostics = build_entry_gate_diagnostics(row, config, mode=mode)
    symbol = str(_row_get(row, "Symbol", "symbol", default=""))
    setup_name = _row_get(row, "Entry", "setup", "setup_type")

    def item(name, group, actual, required, passed, priority):
        return RuleEvaluation(scan_id, symbol, setup_name, name, group, actual, required, bool(passed), not bool(passed), priority)

    evaluations = [
        item("Setup", "Entry", diagnostics["setup"], diagnostics["min_setup"], diagnostics["setup"] >= diagnostics["min_setup"], 80),
        item("RR", "Risk", diagnostics["rr"], diagnostics["min_rr"], diagnostics["rr"] >= diagnostics["min_rr"], 90),
        item("Option Quality", "Option", diagnostics["option_quality"], diagnostics["min_option_quality"], diagnostics["option_quality"] >= diagnostics["min_option_quality"], 80),
        item("Quote Freshness", "Realtime", diagnostics["quote_freshness"], "LIVE_QUOTE", diagnostics["quote_freshness"] == "LIVE_QUOTE", 80),
        item("Affordability", "Affordability", diagnostics["affordable"], True, not _bool_false(diagnostics["affordable"]), 70),
    ]
    if diagnostics["spread"] is not None:
        evaluations.append(item("Option Spread", "Option", diagnostics["spread"], diagnostics["max_spread"], diagnostics["spread"] <= diagnostics["max_spread"], 70))
    return evaluations
