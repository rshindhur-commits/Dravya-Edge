from dataclasses import dataclass
from datetime import datetime
import math
import os

from app.gates.setup_quality import (
    MIN_SETUP_BASE,
    MIN_SETUP_ELEVATED,
    MIN_SETUP_RANGE_BOUND,
    MIN_SETUP_WEAK_BREADTH,
)


ACTIONABLE_STATUSES = {
    "ENTER",
    "ENTER_PAPER",
    "REVIEW_TV_CHART"
}


@dataclass
class EntryGateConfig:
    min_rr: float = 1.8
    # Setup thresholds are on the scale defined in app/gates/setup_quality.py and
    # were re-derived there at matched pass rates when the metric changed. They
    # are imported rather than restated so the two cannot drift apart.
    min_setup_percent: float = MIN_SETUP_BASE
    min_option_quality: float = 65.0
    max_spread_pct: float = 10.0


def scanner_entry_gate_config():
    """The thresholds the scanner actually enforces, read from configuration.

    The dataclass defaults above are a floor for callers with no configuration,
    not the deployed bar, and the two differ: `max_spread_pct` defaults to 10.0
    here while `OPTION_MAX_SPREAD_PCT` has been 6 since 2026-07-30.

    This lives beside the dataclass rather than in app/main.py because the gate
    *audit* needs it too. `build_rule_evaluations` had no config to pass and so
    constructed a bare `EntryGateConfig()`, which meant every persisted
    `Option Spread` row recorded `required_value 10.0` regardless of what the
    worker enforced -- on 2026-08-10 that was 50 of 50 rows, alongside 733
    `RR` rows at the 1.8 default sitting next to 801 at the real 2.0. An audit
    that reports its own defaults cannot answer "what did the gate enforce",
    which is the one question it exists to answer, and the research cycles in
    docs/DELIVERY_PLAN.md read exactly that column.

    A function rather than a module constant so a changed variable takes effect
    without a reimport; every lookup is a dict read.
    """

    from app.config.settings import get_float_env

    return EntryGateConfig(
        min_rr=get_float_env("SCANNER_GATE_MIN_RR", 2.0),
        min_setup_percent=get_float_env("SCANNER_GATE_MIN_SETUP", 70.0),
        min_option_quality=get_float_env("OPTION_MIN_QUALITY_SCORE", 65.0),
        max_spread_pct=get_float_env("OPTION_MAX_SPREAD_PCT", 6.0),
    )


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


def symbol_daily_cap_is_directional():
    """Whether MAX_TRADES_PER_SYMBOL_PER_DAY counts each direction separately.

    The pair to `cooldown_is_directional()`, and shipped without it, which made
    that fix inert. The cooldown was taught to let a reversal through on
    2026-08-22; six lines later `symbol_trade_count_today` counted every trade on
    the symbol regardless of direction, and with MAX_TRADES_PER_SYMBOL_PER_DAY at
    its default of 1 the reversal was refused anyway. The PLTR put at 10:01 on
    2026-08-21 still blocks the call that ran +$8.83 -- one gate now says yes and
    the next says no, for the same reason the first one was changed.

    Same default, and the same argument: a put and a call on one symbol are two
    trades, not one taken twice. What the cap is for -- not churning the same
    setup all day -- is untouched, because a second entry in the *same* direction
    is still refused.
    """

    from app.config.settings import get_bool_env

    return get_bool_env("AUTO_PAPER_SYMBOL_DAILY_CAP_DIRECTIONAL", True)


def symbol_trade_count_today(state: dict, symbol: str, now=None, direction=None) -> int:
    """Trades opened on `symbol` today, optionally only in one direction.

    `direction=None` keeps the whole-symbol count. Callers pass a direction only
    when `symbol_daily_cap_is_directional()` is on.
    """

    now = now or datetime.now()
    direction = str(direction).upper() if direction else None
    count = 0

    for trade in (state or {}).values():

        if trade.get("symbol") != symbol:

            continue

        if direction and str(trade.get("direction") or "").upper() != direction:

            continue

        opened_at = parse_timestamp(trade.get("opened_at"))

        if opened_at and opened_at.date() == now.date():

            count += 1

    return count


def cooldown_is_directional():
    """Whether the cooldown only blocks the direction that just lost.

    PLTR, 2026-08-21. A put opened at 10:01 at 173.61 -- twelve minutes after the
    session low of 172.55 -- and closed at 10:05 for -0.55R. The cooldown then
    locked the symbol until 10:50, and PLTR ran to 182.44 by 11:58: +$8.83, the
    largest move on the board that day, with the app watching.

    The put was wrong because the stock reversed. A cooldown that also blocks the
    reversal makes the app punish itself for being wrong by refusing to be right,
    and it does so for exactly the window in which the opposite trade is best.

    Same direction still cools off, which is what the rule is for -- re-entering
    the idea that just failed, at a worse price, is the behaviour worth stopping.

    Set AUTO_PAPER_COOLDOWN_DIRECTIONAL=false to restore the blanket block.
    """

    from app.config.settings import get_bool_env

    return get_bool_env("AUTO_PAPER_COOLDOWN_DIRECTIONAL", True)


def is_symbol_in_cooldown(
    symbol,
    closed_trades,
    now,
    cooldown_minutes,
    direction=None,
):
    """Whether `symbol` closed a trade too recently to re-enter.

    `direction` is the direction being considered now. Passing it lets a reversal
    through while still cooling the losing idea; omitting it keeps the old
    symbol-wide behaviour, so callers that do not care are unaffected.
    """

    last_close_dt = None

    directional = direction is not None and cooldown_is_directional()

    for trade in (closed_trades or []):

        if trade.get("symbol") != symbol:

            continue

        # Only the direction that just lost cools off. A put closing does not
        # bar the call that its own reversal just created.
        if directional and str(trade.get("direction") or "").upper() != str(direction).upper():

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

        min_setup = max(min_setup, MIN_SETUP_RANGE_BOUND)
        min_rr = max(min_rr, 2.0)
        max_spread = min(max_spread, 5.0)

    if breadth < -20 or above_ema20 < 40:

        min_setup = max(min_setup, MIN_SETUP_WEAK_BREADTH)
        min_rr = max(min_rr, 2.0)

    # A candidate fighting its own sector complex. Not blocked outright -- the
    # strongest names lead reversals -- but it has to be clearly better than one
    # that has the tape behind it.
    if reference_regime == "TRENDING_BEAR" and direction == "CALL":

        min_setup = max(min_setup, MIN_SETUP_WEAK_BREADTH)
        min_rr = max(min_rr, 2.0)

    if reference_regime == "TRENDING_BULL" and direction == "PUT":

        min_setup = max(min_setup, MIN_SETUP_WEAK_BREADTH)
        min_rr = max(min_rr, 2.0)

    if reference_regime == "HIGH_VOLATILITY":

        min_setup = max(min_setup, MIN_SETUP_ELEVATED)
        min_rr = max(min_rr, 2.0)

    # A VIX spike raises every option premium at once and widens the quotes you
    # have to cross twice. Long calls suffer most: the move that was supposed to
    # pay for the position is now competing with a higher entry price.
    if vix_move is not None and vix_move >= _vix_spike_pct():

        min_setup = max(min_setup, MIN_SETUP_WEAK_BREADTH)
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

        min_setup = max(min_setup, MIN_SETUP_ELEVATED)
        min_rr = max(min_rr, 2.0)

    if daily_trend == "BULL" and direction == "PUT":

        min_setup = max(min_setup, MIN_SETUP_ELEVATED)
        min_rr = max(min_rr, 2.0)

    # The setup score does not predict outcomes, so it no longer blocks.
    #
    # Measured 2026-08-12 over 244 resolved candidates, banded by the score this
    # gate reads:
    #
    #     setup <50   n=136   33.8% win   +0.233R
    #     setup 50-70 n= 47   29.8% win   -0.171R
    #     setup 70+   n= 61   21.3% win   -0.300R
    #
    # It is inverted -- the candidates the app rates worst win most -- at
    # z = 1.89, p = 0.059. The obvious confound is ruled out: median RR is flat
    # across the bands (1.98 / 1.81 / 1.99), so it is not that high-scoring
    # candidates carry further targets.
    #
    # What it costs is not theoretical. On 2026-08-12 SPCX was flagged CALL five
    # times on an EMA_PULLBACK, refused at setup 68 against a bar of 81, and the
    # contract it would have bought returned **+22.26%**. SMCI was refused the
    # same way at setup 40 on a day it moved 18.86%. Meanwhile the RR gate that
    # evening refused NVDA at 1.87 (-19.83%) and PLTR at 1.97 (-4.00%) -- so the
    # RR bar earned its keep on the same funnel where this one did harm.
    #
    # Ranking on the score is untouched and the threshold is still returned and
    # recorded, so the bar remains measurable; only the refusal is removed, at
    # the two enforcement sites below. Set SETUP_GATE_ENABLED=true to restore
    # blocking, and note the escalation above only ever tightens, so restoring it
    # brings the 81-85 bar back with it.
    return min_setup, min_rr, max_spread


def setup_gate_blocks():
    """Whether a setup score below the bar should refuse the trade.

    Off by default -- see the evidence above `return` in
    `apply_regime_entry_thresholds`. Read at call time rather than import so the
    switch can be flipped without a restart, and so tests can exercise both.
    """

    from app.config.settings import get_bool_env

    return get_bool_env("SETUP_GATE_ENABLED", False)


def timing_gate_blocks():
    """Whether a high entry-timing score should refuse the trade.

    `evaluate_entry_timing` calls itself observational, and it was never checked
    against outcomes. Measured over 122 resolved candidates on 2026-08-13 it runs
    inverted, and unlike the setup score it survives every control:

        score   0-55   n=52   EV -0.099    median RR 1.80
        score  55-70   n=46   EV -0.251    median RR 2.00
        score 70-101   n=24   EV -0.521    median RR 2.00

    Median RR is flat across the bands, so this is not target distance. It holds
    inside both RR bands (-0.105 vs -0.310 below RR 2, -0.082 vs -0.732 above),
    holds in both directions, and the GOOD grade's bootstrap CI [-1.000, -0.116]
    excludes zero. Dropping the five best outcomes takes the >=70 band from
    -0.521 to -1.000 -- it strengthens without its outliers, which no other edge
    tested on this data does.

    The mechanism is visible in the scoring: 20% of it rewards `trend_age <= 2`
    and another 20% rewards `pullback_number == 1`, so a top score means a trend
    two bars old on its first pullback with price still on the EMA. That is a
    move that has not yet proven anything. PLTR on 2026-08-13 scored 83.66 as
    FIRST_PULLBACK and was the first green bar of a five-bar slide; it closed
    -0.44R and would have been -1.00R held.

    Off by default. 122 candidates is enough to act on but not enough to hard-code,
    and the nightly resolution pass keeps growing it.
    """

    from app.config.settings import get_bool_env

    return get_bool_env("ENTRY_TIMING_GATE_ENABLED", False)


def timing_gate_max_score():
    """Scores at or above this are refused when the timing gate is on.

    **55, lowered from 70 on 2026-08-15.** The threshold is a ceiling, not a pass
    mark: the score predicts inversely, so a high one is a reason to refuse.

    70 was fitted to 122 resolved candidates. Re-measured against **22,954** --
    every resolved candidate joined to the timing score recorded at its decision
    -- the cliff is at 55 and not at 70:

        score   0-55   n=10,437   36% win   (35% first half, 36% second)
        score  55-70   n= 7,190   26% win   (23% / 27%)
        score 70-101   n= 5,327   25% win   (25% / 25%)

    Monotonic, and stable across both halves, which almost nothing else measured
    on this project is. Above 55 the win rate is flat at 25-26%; the 55-70 band
    is no better than the band above it and was being admitted.

    At 70 the gate refused 5,327 of 22,954. At 55 it refuses 12,517 -- about half
    the book rather than a quarter -- so expect materially fewer trades. That is
    the trade being made: the 7,190 additional refusals win 26% against 36% for
    what survives.

    Checked against the eight trades of 2026-08-13/14, which is the sample the
    operator had been reading from charts: 55 blocks four of the five losers,
    including PLTR at 83.7 and NFLX at 64.0 -- the two identified as badly placed
    by hand -- and keeps CRWD at 44.4, the only trade that made money. The one it
    blocks wrongly on R is SMCI at 57.6, +0.14R, which lost 2.8% in cash.
    """

    from app.config.settings import get_float_env

    return get_float_env("ENTRY_TIMING_MAX_SCORE", 55.0)


def _timing_score_refused(row):
    """True when this candidate's entry-timing score is high enough to refuse."""

    if not timing_gate_blocks():
        return False

    raw = _row_get(row, "Entry Timing Score", "entry_timing_score")

    if raw is None or str(raw).strip().lower() in {"", "nan", "none"}:
        return False

    score = safe_float(raw, default=None)

    if score is None:
        return False

    return score >= timing_gate_max_score()


def late_session_cutoff_et():
    """No new position after this wall-clock time, ET. Empty string disables it.

    **14:05**, chosen by the operator over the 13:05 the data prefers. Measured
    on 1,231 archived candidates, scored on whether the option ever offered a 10%
    gain before the protective stop:

        10:25-11:25   21.5%
        11:25-12:15   24.8%
        12:15-13:05   19.1%
        13:05-14:05    8.1%
        14:05-15:35    4.5%

    A random entry on the same contract with the same horizon reaches 10% about
    20.7% of the time, so both afternoon bands sit far below chance. Late in the
    session the day's move has already happened and there is nothing left for a
    bought option to capture before the bell.

    The two candidate cutoffs, and the trade-off between them:

        cutoff    kept              discarded
        13:05     755 at 21.7%      476 at 5.9%
        14:05     988 at 18.4%      243 at 4.1%

    **13:05 is the better number and 14:05 is the shipped one.** It blocks the
    4.5% band, which is the worst of the session, while keeping the 8.1% band and
    233 more candidates. At 18.4% the book still sits *below* the 20.7% random
    baseline, so this setting narrows the deficit rather than closing it -- 13:05
    is what closes it.

    That is the operator's call and it is a reasonable one: 13:05 refuses new
    positions for the last three hours of a six-and-a-half hour session, which is
    a large amount of the day to give up on seven days of evidence. Monday adds
    an eighth. If the afternoon band keeps underperforming, 13:05 is one variable
    away.

    A range-used filter measured slightly better in combination with 13:05 (714
    kept at 22.3%) but needs the session high and low in the decision payload,
    which are recorded now but not yet available for the archive period. The
    clock carries most of the effect on its own.
    """

    # Deliberately `os.getenv` rather than `get_secret_env`, which folds an
    # empty value into the default. Here empty is a *choice* -- it is how the
    # operator turns the cutoff off -- and folding it into "13:05" would
    # silently re-enable the rule someone had just disabled.
    import os

    value = os.getenv("ENTRY_LATE_SESSION_CUTOFF_ET")

    return "14:05" if value is None else value


def _late_session_refused(row):
    """True when this candidate arrives too late in the session to be worth taking.

    **MULTIDAY candidates are exempt.** The cutoff was measured on one question --
    does the option reach +10% before its stop, *inside the session* -- and the
    mechanism is that late in the day there is no session left to move in. A
    position held for days does not have that problem, so the evidence does not
    transfer to it.

    This is not hypothetical. Five of the nine MULTIDAY trades in the book opened
    between 14:19 and 14:48 on 2026-07-30, and without this exemption all five
    would be refused on evidence that says nothing about them.
    """

    profile = str(
        _row_get(row, "Holding Profile", "holding_profile", default="")
    ).strip().upper()

    if profile == "MULTIDAY":
        return False

    cutoff = (late_session_cutoff_et() or "").strip()

    if not cutoff:
        return False

    raw = _row_get(row, "Data Timestamp ET", "data_timestamp_et")

    if raw is None or str(raw).strip().lower() in {"", "nan", "none"}:
        return False

    text = str(raw).strip()

    # "2026-07-28 19:56:00 EDT" -- take the clock field only. A timestamp we
    # cannot parse must not silently refuse every candidate, so anything
    # unexpected falls through to allowed.
    parts = text.split()

    if len(parts) < 2:
        return False

    clock = parts[1]

    try:
        hour, minute = int(clock[:2]), int(clock[3:5])
        limit_hour, limit_minute = int(cutoff[:2]), int(cutoff[3:5])
    except (ValueError, IndexError):
        return False

    return (hour * 60 + minute) > (limit_hour * 60 + limit_minute)


def max_range_placement_pct():
    """Retained for analysis only. **This filter does not work and is not wired in.**

    The observation looked damning: across 41 live trades the median entry sat
    **68% of the way into the session's range** in the trade's own direction --
    buying near the high on calls, near the low on puts. That is the textbook
    description of chasing, and it was expected to be the entry defect.

    It is not. Scored on whether the option ever offered a 10% gain, placement
    carries no signal at all:

        32-62%   15.4%      78-84%   16.3%
        62-71%   18.7%      84-100%  14.2%
        71-78%   13.4%

    Refusing above 80% keeps 825 candidates at 15.5% and discards 406 at
    **15.8%** -- the ones thrown away do slightly *better*. Combined with the
    13:05 cutoff it actively hurts: 20.8% against 21.7% for the clock alone,
    while discarding 216 more candidates to get there.

    So "the app buys at 68% of the range" is true and is not a cause. It
    describes where entries land without predicting what they do, and a filter
    built on it would cost trades and buy nothing. `Session High` and
    `Session Low` are still recorded in the payload, because the measurement
    should be repeatable on a larger sample later.

    Kept as a function so the refutation has somewhere to live. Wiring it into
    `evaluate_entry_gate` would make the book worse.
    """

    from app.config.settings import get_float_env

    return get_float_env("ENTRY_MAX_RANGE_PLACEMENT_PCT", 100.0)


def min_trade_quality_score():
    """Trade Quality Score a candidate must reach. **48.** Zero disables it.

    TQS has been computed and published on every candidate for months and has
    never gated anything. It is the weighted blend already in `trade_ranker`:
    setup 0.25, timing 0.20, trend 0.20, relative strength 0.10, plus option
    quality, liquidity and leverage. Every term is known at the scan, so there is
    no lookahead in using it.

    **It is the only entry signal in this project that survives a holdout split.**
    Seven experiments -- 56 single features, 2,278 pairs, a regularised model,
    trigger inversion, entry delay, limit-order pullback, and eleven bar and
    market-context features -- all returned null. 1,315 candidates scored on
    whether the option ever offered a 10% gain before its stop:

        quintile          option +10%   underlying 2R   median spread
        Q1  26-37             7.6%          20.9%           2.43%
        Q2  37-40             6.8%          25.5%           1.92%
        Q3  40-42            12.9%          31.2%           2.17%
        Q4  42-48             9.9%          25.5%           1.62%
        Q5  48-83            41.4%          36.9%           1.32%

    **Part of that is a contract effect and is stated rather than hidden.** Median
    spread halves across the quintiles, so a high score partly means a better
    contract was available, and the option column overstates how much better the
    *entry* is. The underlying-2R column is the control -- contract quality cannot
    touch it -- and it still rises, monotonically in the holdout half: 16.0, 17.2,
    24.5, 30.7, 40.2. Both effects are real and both are known at scan time, so a
    gate on the blend captures both legitimately.

    On the trades actually taken it is far less brutal than the quintiles imply,
    because the option gates already reject 98% of candidates. Of 27 trades
    matched to a score it drops three -- NVDA at -$79.50 and two that booked
    exactly zero -- keeps all eight winners, and takes the total from +$118.50 to
    +$198.00.

    **48 is not a tuned number.** Thresholds of 44, 46, 48 and 52 give identical
    results on the live book: nothing sits between 40.6 and 48, so the cut lands
    in a gap rather than on a fitted edge.
    """

    from app.config.settings import get_float_env

    return get_float_env("ENTRY_MIN_TRADE_QUALITY", 48.0)


def _trade_quality_refused(row):
    """True when the candidate scores below the quality bar.

    A missing score never refuses. TQS is written by `rank_candidates`, and a
    caller that has not run the ranker would otherwise have its whole book
    rejected by a field it never populated.
    """

    minimum = min_trade_quality_score()

    if minimum <= 0:
        return False

    raw = _row_get(row, "Trade Quality Score", "trade_quality_score")

    if raw is None or str(raw).strip().lower() in {"", "nan", "none"}:
        return False

    score = safe_float(raw, default=None)

    if score is None:
        return False

    return score < minimum


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

    if setup < min_setup and setup_gate_blocks():

        return False, "SETUP_BELOW_THRESHOLD"

    if _timing_score_refused(row):

        return False, "ENTRY_TIMING_TOO_EARLY"

    if _late_session_refused(row):

        return False, "ENTRY_TOO_LATE_IN_SESSION"

    if _trade_quality_refused(row):

        return False, "TRADE_QUALITY_BELOW_THRESHOLD"

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

    elif setup < min_setup and setup_gate_blocks():

        result = "FAIL"
        failure = "SETUP_BELOW_THRESHOLD"

    elif _timing_score_refused(row):

        result = "FAIL"
        failure = "ENTRY_TIMING_TOO_EARLY"

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


# Every one of these is copied off the selected contract. The scanner writes the
# whole block as None when no `option_recommendation` was produced, so "all absent"
# is exactly "contract selection never reached this candidate".
_CONTRACT_FIELDS = (
    ("Option Mid Price", "option_mid_price"),
    ("Option Ask", "option_ask"),
    ("Option Bid", "option_bid"),
    ("Option Spread %", "option_spread_pct", "spread_pct"),
    ("Option Quote Freshness", "option_quote_freshness", "quote_freshness"),
    ("Option Ticker", "option_ticker"),
)


def _contract_was_priced(row):
    """Is anything at all known about a contract for this candidate?

    Not "is the contract good" -- that is the rule's own job. This only separates
    an evaluated contract from an absent one, so a candidate that died before
    selection stops being recorded as an option failure it never had the chance to
    have.
    """

    for field in _CONTRACT_FIELDS:

        value = _row_get(row, *field, default=None)

        if value is None:
            continue

        if str(value).strip().lower() not in {"", "nan", "none"}:
            return True

    return False


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
        item("Affordability", "Affordability", diagnostics["affordable"], True, not _bool_false(diagnostics["affordable"]), 70),
    ]

    # The contract rules are only emitted once a contract exists.
    #
    # A candidate that dies at Momentum or Setup never gets one priced, so
    # option_quality defaults to 0.0 and quote_freshness to None -- and recording
    # those as failures reports a rule that was never evaluated. On 2026-08-03 that
    # was 2,913 fabricated Option Quality failures against 77 real evaluations, all
    # of them non-blocking, which is how "Option Quality" came to head the rule
    # tables every day while costing nothing. `Option Spread` has always been
    # conditional here; this extends the same test to its two neighbours.
    if _contract_was_priced(row):
        evaluations.append(item("Option Quality", "Option", diagnostics["option_quality"], diagnostics["min_option_quality"], diagnostics["option_quality"] >= diagnostics["min_option_quality"], 80))
        evaluations.append(item("Quote Freshness", "Realtime", diagnostics["quote_freshness"], "LIVE_QUOTE", diagnostics["quote_freshness"] == "LIVE_QUOTE", 80))

    if diagnostics["spread"] is not None:
        evaluations.append(item("Option Spread", "Option", diagnostics["spread"], diagnostics["max_spread"], diagnostics["spread"] <= diagnostics["max_spread"], 70))

    return evaluations
