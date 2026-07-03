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


def normalize_candidate_direction(direction):

    direction = str(direction or "").strip().upper()

    if direction in {"CALL", "BULLISH", "HIGH CONVICTION BULLISH"}:

        return "CALL"

    if direction in {"PUT", "BEARISH", "HIGH CONVICTION BEARISH"}:

        return "PUT"

    return "UNKNOWN"


def validate_price_geometry(direction, entry, stop, target):

    direction = normalize_candidate_direction(direction)
    entry = safe_float(entry, None)
    stop = safe_float(stop, None)
    target = safe_float(target, None)

    if entry is None or stop is None or target is None:

        return False

    if direction == "CALL":

        return stop < entry < target

    if direction == "PUT":

        return target < entry < stop

    return False


def price_geometry_error(row):

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
        "Price"
    )
    stop = _row_get(
        row,
        "Candidate Stop Price",
        "stop_price",
        "stop_loss",
        "Stop Loss"
    )
    target = _row_get(
        row,
        "Candidate Target Price",
        "target_price",
        "take_profit",
        "Take Profit"
    )
    normalized_direction = normalize_candidate_direction(direction)

    if validate_price_geometry(
        normalized_direction,
        entry,
        stop,
        target
    ):

        return None

    if normalized_direction == "PUT":

        return "INVALID_PRICE_GEOMETRY: PUT requires target < entry < stop"

    if normalized_direction == "CALL":

        return "INVALID_PRICE_GEOMETRY: CALL requires stop < entry < target"

    return "INVALID_PRICE_GEOMETRY: unknown direction"


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
        and trade.get("status") == "OPEN"
        for trade in (state or {}).values()
    )


def active_symbol_trade(state: dict, symbol: str):

    for key, trade in (state or {}).items():

        if (
            trade.get("symbol") == symbol
            and trade.get("status") == "OPEN"
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


def apply_regime_entry_thresholds(row, config: EntryGateConfig):

    market_regime = str(
        _row_get(row, "Market Regime", "market_regime", default="")
    ).upper()
    breadth = safe_float(
        _row_get(row, "Watchlist Breadth Score", "watchlist_breadth_score"),
        0.0
    )
    above_ema20 = safe_float(
        _row_get(row, "Above EMA20 %", "above_ema20_pct"),
        100.0
    )

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
