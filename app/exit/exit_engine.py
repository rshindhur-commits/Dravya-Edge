from datetime import time
import os

import pandas as pd

from app.analytics.exit_waterfall import build_exit_waterfall
from app.exit.exit_confidence import evaluate_exit_confidence
from app.exit.trend_health_engine import evaluate_live_trend_health
from app.utils.runtime_logging import debug_print


EXIT_PRIORITY = {
    "HARD_STOP": 100,
    "HARD_TARGET": 95,
    "PROFIT_PROTECTION": 85,
    "EMA": 80,
    "VWAP": 70,
    "MACD": 60,
    "FAILED_BREAKOUT": 50,
    "TIME_EXIT": 40,
    "NEAR_CLOSE": 30
}


def resolve_exit_fill(exit_code, is_short, market_price, stop_loss, take_profit):
    """The price an exit actually fills at, given the rule that fired.

    Every exit was booked at the latest 5m close regardless of why it fired, which
    silently detached execution from the levels the trade was built on. A stop is
    detected on the 15m bar's High/Low but filled wherever price happened to be at
    the last 5m print, up to a full scan interval later.

    SPCX on 2026-07-20 is the worked example: stop 125.38 against a 0.58 risk,
    booked at 124.87. Half a point past the stop turned an intended -1R into
    -1.88R -- 88% more than the trade was ever sized to lose. NVDA on 2026-07-29
    filled 1.09 past a breakeven stop. Because the overshoot was folded into the
    fill price and never recorded, a stop that could not hold looked like a
    strategy that lost more than it risked.

    The model, mirrored for shorts:

    * HARD_STOP    -- a stop becomes a market order the moment it is touched, so
                      the fill is never *better* than the stop and is worse when
                      price has already run past it: `min(stop, market)` long.
    * HARD_TARGET  -- a limit resting at the target. The rule only fires once the
                      bar has traded through it, so it fills at the target rather
                      than wherever the bar happened to close.
    * everything else -- EMA, VWAP, MACD, failed breakout, time, near-close and
                      profit protection are discretionary market exits with no
                      resting level, so the close is the honest fill.

    This is deliberately not a slippage *model*: it adds no assumed cost and
    invents no price the market did not print. It stops crediting a stop with a
    fill better than a stop can get, and stops charging a limit for a fill worse
    than a limit would take.

    Returns `(fill, adverse_slippage)`. Slippage is measured against the rule's
    trigger level, not against the close: it answers "how much worse than the
    level I meant to exit at did I actually get", which is the quantity that
    turned SPCX's planned -1R into -1.88R. Positive is always adverse, it is zero
    when the level was honoured, and it is directly comparable to R once divided
    by risk per share. Market exits have no trigger level and so report zero.
    """

    market_price = _float_or_none(market_price)

    if market_price is None:
        return None, None

    trigger = None

    if exit_code == "HARD_STOP":
        trigger = _float_or_none(stop_loss)

    elif exit_code == "HARD_TARGET":
        trigger = _float_or_none(take_profit)

    if trigger is None:
        return market_price, 0.0

    if exit_code == "HARD_STOP":
        # A stop is a market order once touched: never better than the level,
        # worse when price has already traded through it.
        fill = max(trigger, market_price) if is_short else min(trigger, market_price)
    else:
        # A limit resting at the target, which the bar has already traded through.
        fill = trigger

    adverse = (fill - trigger) if is_short else (trigger - fill)

    return fill, round(max(0.0, adverse), 4)


PROFIT_LOCK_ELIGIBLE_EXITS = {"EMA", "VWAP", "MACD"}


def resolve_profit_lock(
    exit_code,
    exit_signal,
    mfe_r,
    trend_health_score,
    exit_confidence_score,
    entry_price,
    current_stop,
    risk_per_share,
    is_short,
):
    """Turn a doubtful soft exit on a profitable trade into a stop, not a close.

    A soft rule can say "momentum broke" while the trade is sitting on real
    banked profit and the trend still reads strong. NVDA on 2026-07-31 ran to
    +1.66R, printed "Partial profit threshold reached" three times over ten
    minutes, then closed at +0.60R on an EMA9 touch -- with the engine scoring
    its own confidence in that exit at 11.5 out of 100 and trend health at 95.
    Breakeven protection was already active and did nothing, because the exit
    came from a soft rule rather than from the stop.

    So the exit is neither honoured nor vetoed. It becomes a floor: hold the
    position and ratchet the stop to protect all but PROFIT_LOCK_MAX_GIVEBACK_R
    of the peak. The stop only ever moves in the trade's favour, so this cannot
    turn a winner into a loser -- worst case is exiting at the locked level
    instead of at the soft signal, best case is keeping a trend that had not
    actually ended.

    Deliberately narrow. It requires all three of: profit actually banked, a
    trend still reading healthy, and the engine's own low confidence in the
    exit. None of those describe a losing trade, so a loss can never be widened
    and a confident exit is always honoured.

    Returns `(locked_stop, locked_r)`, or `(None, None)` when it does not apply.
    """

    if not exit_signal or exit_code not in PROFIT_LOCK_ELIGIBLE_EXITS:
        return None, None

    mfe_r = _float_or_none(mfe_r)
    risk_per_share = _float_or_none(risk_per_share)
    entry_price = _float_or_none(entry_price)

    if not mfe_r or not risk_per_share or entry_price is None:
        return None, None

    if mfe_r < _env_float("PROFIT_LOCK_MIN_MFE_R", 1.0):
        return None, None

    health = _float_or_none(trend_health_score)
    if health is None or health < _env_float("PROFIT_LOCK_MIN_TREND_HEALTH", 70):
        return None, None

    confidence = _float_or_none(exit_confidence_score)
    if confidence is None or confidence >= _env_float(
        "PROFIT_LOCK_MAX_EXIT_CONFIDENCE", 25
    ):
        return None, None

    locked_r = mfe_r - _env_float("PROFIT_LOCK_MAX_GIVEBACK_R", 1.0)

    if locked_r <= 0:
        return None, None

    locked_stop = (
        entry_price - (locked_r * risk_per_share)
        if is_short
        else entry_price + (locked_r * risk_per_share)
    )
    current_stop = _float_or_none(current_stop)

    if current_stop is not None:
        # Ratchet only. Never widen risk.
        locked_stop = (
            min(current_stop, locked_stop)
            if is_short
            else max(current_stop, locked_stop)
        )

    return locked_stop, locked_r


def _float_or_none(value):

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    return None if result != result else result


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):

    raw = os.getenv(name)

    if raw is None:

        return default

    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _is_short_entry(entry_type):

    entry_type = str(entry_type or "").upper()

    return (
        "SHORT" in entry_type
        or "BEARISH" in entry_type
        or "BREAKDOWN" in entry_type
        or "REJECTION" in entry_type
    )


def _get_timestamp_et(latest):

    timestamp = getattr(
        latest,
        "name",
        None
    )

    if timestamp is None:

        return None

    try:

        if timestamp.tzinfo is None:

            timestamp = timestamp.tz_localize("UTC")

        return timestamp.tz_convert("America/New_York")

    except Exception:

        return None


def _bars_since_entry(df, trade_state, fallback):
    """Bars of the evaluation timeframe elapsed since entry.

    The previous count was `trade_state["bars_in_trade"] + 1`, incremented once
    per `evaluate_exit` call. That counts *scans*, not bars, and the scanner's
    cadence is not the bar interval: `SESSION_INTERVALS` is 300s in REGULAR and
    120s in OPENING_RANGE, while exits are evaluated on 15m bars. So the same
    forming bar was counted three times in a regular session and seven times at
    the open, and every threshold expressed in bars fired that much earlier:

        rule                              old value   was firing at   now
        time exit                         24 scans    ~2h             8 bars
        MULTIDAY_MOMENTUM_EXIT_MIN_BARS   6 scans     ~30m            2 bars
        _should_guard_early_exit          3 scans     ~15m            1 bar

    Worse, it moved whenever the cadence did, so an A/B run against an archived
    day could not reproduce a live session's exits.

    Each constant was restated in 15m bars to hold the wall-clock behaviour the
    system has actually been running, rather than the longer one its old value
    implied. That keeps this a unit fix: the timings are unchanged and only their
    dependence on scan cadence is gone. All three are env-tunable from here.

    Counting bars off the frame's own index makes the measure cadence-independent
    and matches what every threshold already claims to express. The bar holding
    the entry counts as bar 1, so the first evaluation after entry still reads 1
    and nothing shifts at the start of a trade.

    Falls back to the incrementing counter when the entry timestamp is missing or
    unparseable, which keeps legacy trades and synthetic frames working.
    """

    opened_at = (trade_state or {}).get("opened_at_et") or (trade_state or {}).get("opened_at")

    if not opened_at or df is None or df.empty:
        return fallback

    try:
        entry_ts = pd.Timestamp(opened_at)
    except (TypeError, ValueError):
        return fallback

    try:
        if entry_ts.tzinfo is None:
            entry_ts = entry_ts.tz_localize("America/New_York")

        index = df.index

        if index.tz is None:
            index = index.tz_localize("UTC")

        bars = int((index.tz_convert("America/New_York") >= entry_ts).sum())

    except Exception:
        return fallback

    # A trade opened after the last bar's timestamp is still in its first bar.
    return max(1, bars)


def resolve_risk_per_share(entry_price, initial_stop_loss, current_stop_loss):
    """Risk per share as fixed at entry — the only valid R denominator.

    R must be measured against the risk the trade was opened with. Deriving it
    from the *current* stop breaks the moment a stop is moved: a breakeven move
    makes `abs(entry - stop)` zero, so rr_progress and mfe_r collapse to 0 and
    never recover. That silently disables partial profit, the ATR trailing stop,
    and multiday profit protection, all of which are gated on R thresholds.

    Falls back to the current stop for trades opened before `initial_stop_loss`
    was persisted.
    """

    for candidate in (initial_stop_loss, current_stop_loss):

        if candidate is None or entry_price is None:
            continue

        try:
            risk = abs(float(entry_price) - float(candidate))
        except (TypeError, ValueError):
            continue

        if risk > 0:
            return risk

    return 0.0


def _calculate_rr_progress(
    current_price,
    entry_price,
    stop_loss,
    is_short,
    risk_per_share=None
):

    risk = (
        risk_per_share
        if risk_per_share is not None
        else abs(entry_price - stop_loss)
    )

    if risk <= 0:

        return 0

    if is_short:

        reward = entry_price - current_price

    else:

        reward = current_price - entry_price

    return reward / risk


def _round_float(value, digits=2):

    try:

        return round(float(value), digits)

    except (TypeError, ValueError):

        return value


def trend_still_valid(df, direction, ignore=None):
    """Whether the trend behind the position is otherwise intact.

    `ignore` names the component whose rule just fired, and is excluded from the
    verdict. Without it the question is self-defeating: a VWAP exit fires because
    price crossed VWAP, so asking "is the trend intact, including price being on
    the right side of VWAP?" can only answer no. That made the VWAP entry in
    `_should_guard_early_exit`'s weak_exit_reasons unreachable -- the rule was
    listed as guardable and never once could be guarded.

    Excluding the triggering component asks the question that was intended: the
    rule fired, but does the rest of the evidence still support the trade?
    """

    latest = df.iloc[-1]
    direction = str(direction or "").upper()
    ignore = str(ignore or "").upper()

    vwap_ok = ignore == "VWAP" or (
        latest["Close"] > latest["VWAP"]
        if direction == "CALL"
        else latest["Close"] < latest["VWAP"]
    )
    ema_ok = ignore == "EMA" or (
        latest["EMA9"] > latest["EMA20"]
        if direction == "CALL"
        else latest["EMA9"] < latest["EMA20"]
    )
    rsi_ok = (
        latest["RSI"] > 55
        if direction == "CALL"
        else latest["RSI"] < 45
    )

    return bool(vwap_ok and ema_ok and rsi_ok)


def option_giveback_arm_pct():
    """Gain on the option, in percent, before give-back protection engages.

    **25.** This is a floor on noise, not a profit target, and the number was
    arrived at by getting it wrong first. Armed at 10 it looked excellent on the
    round-trip count and was useless: on a real PLTR call it exited at +4.8% out
    of a trade that went on to +69.3%, because giving back half of a 16% peak
    means selling at 8%, and 16% on an option is one bad print rather than a
    move.

    Measured on the 39 single-day trades in the live book, priced on the traded
    contract's own bars:

        arm at     round-trip     big winners kept
        (app)          48%              9%
        10%             5%             18%
        25%            24%             55%
        40%            29%             64%

    Round-trip is, of trades ever up 10%, the share finishing at or below zero.
    Big-winners-kept is, of trades that reached +25%, how often the rule was
    still holding at +25% or better. They pull against each other -- exiting
    instantly scores a perfect round-trip and keeps nothing -- so 25 is chosen as
    the balance, halving the give-back while keeping six times as many winners
    running as the book does today.
    """

    return _env_float("EXIT_OPTION_GIVEBACK_ARM_PCT", 25.0)


def option_giveback_keep():
    """Share of the peak option gain the rule tries to hold on to. 0.5."""

    return _env_float("EXIT_OPTION_GIVEBACK_KEEP", 0.5)


def option_breakeven_arm_pct():
    """Gain at which the position is no longer allowed to become a loss. 10."""

    return _env_float("EXIT_OPTION_BREAKEVEN_ARM_PCT", 10.0)


def _giveback_floor(peak_gain):
    """The lowest gain this trade may fall to, or None while unprotected.

    Two levels, because one cannot do both jobs.

    Arming a *proportional* give-back at a small gain destroys winners: at 15%
    with half kept it books -7.3% total across the live trades and keeps only 18%
    of the trades that reached +25%, because an option wobbling between +12% and
    +20% is noise and half of a small peak sits inside it.

    Leaving small gains unprotected entirely is the hole the operator found: a
    trade up 20% that reverses has nothing but the hard stop beneath it.

    A **breakeven floor** resolves it. It is far looser than a proportional one --
    it only fires if the whole gain is gone -- so it survives the wobble while
    still refusing to let a winner become a loser.

        rule                  total    round-trip   kept >= 25%
        the book today       +52.4%       48%            9%
        arm 15, keep half     -7.3%       19%           18%
        arm 25, keep half    +98.7%       24%           55%
        two-tier 10 / 25    +143.4%       29%           45%

    So: past 10%, never let it go red. Past 25%, never give back more than half.
    The second floor always sits above the first by the time it engages, so they
    ratchet rather than fight.

    39 trades is thin and the profit figures are not established -- the top-5
    strip is still negative at -1.56%. The round-trip and winners-kept columns are
    counts rather than means and are the part worth trusting.
    """

    if peak_gain is None:
        return None

    if peak_gain >= option_giveback_arm_pct():
        return peak_gain * option_giveback_keep()

    if peak_gain >= option_breakeven_arm_pct():
        return 0.0

    return None


def _option_giveback_exit(trade_state, option_peak_mid):
    """(should_exit, peak_gain_pct, floor_pct) for the option give-back rule.

    Reads the option directly rather than converting through R. The subscriber
    watches premium, and the conversion is contract-specific enough that a rule
    expressed in R would arm at a different real gain on every trade.

    Silent no-op when the option prices are absent, so a caller that does not
    carry them is unaffected rather than broken.
    """

    if not trade_state:
        return False, None, None

    entry_mid = _float_or_none(
        trade_state.get("option_entry_mid")
        or trade_state.get("option_mid")
    )
    current_mid = _float_or_none(
        trade_state.get("option_current_mid")
        or trade_state.get("option_mid_price")
    )

    if not entry_mid or entry_mid <= 0 or not current_mid or current_mid <= 0:
        return False, None, None

    peak = option_peak_mid if option_peak_mid and option_peak_mid > 0 else current_mid
    peak_gain = (peak - entry_mid) / entry_mid * 100.0
    current_gain = (current_mid - entry_mid) / entry_mid * 100.0

    floor_gain = _giveback_floor(peak_gain)

    if floor_gain is None:
        return False, peak_gain, None

    return current_gain <= floor_gain, peak_gain, floor_gain


def volume_flush_enabled():
    return _env_bool("EXIT_VOLUME_FLUSH_ENABLED", True)


def volume_flush_arm_pct():
    """Gain the trade must already show before a reversal may close it. 10.

    The flush is a reversal detector and it was switched off once, correctly,
    because armed on every trade it books below the book it was meant to improve.
    The damage was entirely on positions that never gained: it cut them early,
    where the hard stop would have handled them.

    Arming it only once there is profit to protect removes that failure and keeps
    the benefit. 39 intraday trades, each on its own recorded stop:

        arm                    total   w/o best 3   gave back   kept>=25%
        the book             +443.67      +0.00        53%         36%
        floor only           +818.00     +31.83        32%         82%
        floor + flush @10    +902.00     +75.46        32%         82%
        floor + flush @25    +873.00     +46.46        32%         82%
        floor + flush @40    +812.00     +12.83        32%         82%
        floor + flush always +757.00     -69.54        37%         82%

    Armed at 10 it wins every column, and more than doubles floor-only on the
    strip that removes the best three trades -- the test that has killed most
    results in this project.

    **This is what answers "the trend reversed, get me out".** The floor alone
    requires a trade up 40% to fall back to 20% before it acts. With the flush
    armed, a genuine reversal -- a bar closing against the position on heavy
    volume with real range -- ends it at once, at any profit above 10%, without
    waiting for half the gain to disappear.
    """

    return _env_float("EXIT_FLUSH_ARM_PCT", 10.0)


def _volume_flush_reversal(df, latest, is_short):
    """A bar closing against the position, on conviction volume, with real range.

    The reversal signal that actually fires in time. Every structural definition
    tested -- swing break, lower low, EMA9 and EMA20 crosses, a 15-minute EMA --
    fires *after* the profit has gone, because they all confirm a turn that has
    already happened. Measured on the live book by the share of trades that were
    up 10% and finished at or below zero:

        swing break                48%
        lower low                  52%
        EMA9 cross (15m)           62%
        the book as it runs today  48%
        volume flush               33%
        the two-tier P&L floor     33%

    Volume is what makes the difference. Heavy volume against the position prints
    on the bar the turn happens, not three bars later, so it is the one pattern
    that catches a reversal while there is still profit to protect.

    Three conditions, all required, so ordinary drift cannot trigger it:

        direction   the bar closes against us -- red on a call, green on a put
        conviction  volume above EXIT_FLUSH_VOLUME_MULT times its own average
        size        the bar's range exceeds one ATR

    **Measured on 5-minute bars; the engine runs this on the 15-minute frame.**
    Both tests are relative to the bar's own history rather than absolute, so
    they translate, but a 15-minute bar is a rarer and larger event and this will
    fire less often than the measurement implies. That difference is unmeasured
    and is the first thing to check against Monday's exits.
    """

    if not volume_flush_enabled():
        return False

    close = _float_or_none(latest.get("Close"))
    open_ = _float_or_none(latest.get("Open"))
    high = _float_or_none(latest.get("High"))
    low = _float_or_none(latest.get("Low"))
    volume = _float_or_none(latest.get("Volume"))
    atr = _float_or_none(latest.get("ATR"))

    if None in (close, open_, high, low, volume, atr) or atr <= 0:
        return False

    against = (close > open_) if is_short else (close < open_)

    if not against:
        return False

    if (high - low) <= atr:
        return False

    lookback = int(_env_float("EXIT_FLUSH_VOLUME_LOOKBACK", 20))

    try:
        recent = df["Volume"].tail(lookback + 1).head(lookback)
        average = float(recent.mean())
    except (KeyError, TypeError, ValueError):
        return False

    if not average or average != average or average <= 0:
        return False

    return volume > _env_float("EXIT_FLUSH_VOLUME_MULT", 1.5) * average


def _momentum_exits_allowed(holding_profile, bars_in_trade):
    """Whether minute-scale invalidation may close this position yet.

    EMA9, VWAP, MACD and failed-breakout are momentum signals measured on the scan
    timeframe. Applied to an INTRADAY position that is the intent. Applied to a
    MULTIDAY position on its entry bar it is a category error: the trade was opened
    to be held for days and is being judged by a nine-period EMA.

    On 2026-07-30 every paper trade was tagged MULTIDAY and every one exited on a
    momentum rule with `bars_in_trade` between 0 and 2 -- average hold 6.8 minutes,
    against 14 and 48 minutes on 2026-07-27 before the R denominator was corrected
    and the early-exit guard stopped firing. Replaying that day held to stop or
    target instead produced +8.91R versus the -0.76R actually booked. That is a
    structural comparison rather than a priced one, but the direction is stark.

    So a MULTIDAY position gets a minimum leash before momentum may close it.
    Protective exits are deliberately untouched and still fire from bar zero: hard
    stop, target, profit protection, confirmed trend failure, time stagnation and
    the near-close rule. This defers the twitchy signals, it does not remove risk
    control.

    Tunable for A/B against archived days. Zero restores the previous behaviour.

    `EXIT_MOMENTUM_ENABLED=false` removes all four at once, which is the only
    version of this experiment that measures anything. Testing them one at a
    time does not: disabling the EMA rule over 21 sessions moved return on
    capital by -0.40sd, because EMA exits went 75 -> 0 while MACD went 88 -> 129
    and VWAP 16 -> 37. They are substitutes, so removing one renames the exit
    rather than preventing it. Removing the class leaves stop, target, time and
    end-of-day -- a different strategy rather than a relabelling.
    """

    if not _env_bool("EXIT_MOMENTUM_ENABLED", True):

        return False

    if str(holding_profile or "").upper() != "MULTIDAY":
        return True

    # Restated in true bars. This was 6 while `bars_in_trade` counted scans, so
    # at the 300s REGULAR cadence it was a 30-minute leash, not the 90 minutes
    # "6 bars" implies. 2 bars of 15m keeps the 30 minutes that was actually
    # observed and tuned against, now independent of how often we scan.
    minimum_bars = _env_float("MULTIDAY_MOMENTUM_EXIT_MIN_BARS", 2)

    try:
        return float(bars_in_trade or 0) >= minimum_bars
    except (TypeError, ValueError):
        return True


def _should_guard_early_exit(df, exit_reason, bars_in_trade, rr_progress, is_short):

    # Was `> 3` against a scan counter, i.e. the first ~15 minutes at the REGULAR
    # cadence. One 15m bar preserves that; the guard is meant to cover the noise
    # immediately after entry, not the first three quarters of an hour.
    if bars_in_trade > _env_float("EARLY_EXIT_GUARD_MAX_BARS", 1):

        return False

    if abs(rr_progress) >= 0.25:

        return False

    # Mapped to the trend component each rule tests, so that component can be
    # excluded from the verdict below. "Failed breakout" and MACD test neither
    # VWAP nor the EMA stack, so nothing is excluded for them.
    weak_exit_reasons = {
        "EMA9 invalidation": "EMA",
        "VWAP invalidation": "VWAP",
        "MACD": None,
        "Failed breakout": None,
    }

    triggered = next(
        (
            (reason, component)
            for reason, component in weak_exit_reasons.items()
            if reason in str(exit_reason)
        ),
        None,
    )

    if triggered is None:

        return False

    return trend_still_valid(
        df,
        "PUT" if is_short else "CALL",
        ignore=triggered[1],
    )


def _exit_diagnostic(code, reason):

    return {
        "code": code,
        "reason": reason,
        "priority": EXIT_PRIORITY.get(code, 0)
    }


def _select_primary_exit(exit_reasons):

    if not exit_reasons:

        return None

    return sorted(
        exit_reasons,
        key=lambda item: item.get("priority", 0),
        reverse=True
    )[0]


def _ema_invalidation(price, bar, is_short):
    """Is price on the wrong side of a turning EMA9, on this bar."""

    ema = bar.get("EMA9")

    if not pd.notna(ema) or price is None or not pd.notna(price):

        return False

    slope = bar.get("EMA9_SLOPE", 0)

    if is_short:

        return price > ema and slope > 0

    return price < ema and slope < 0


def _ema_exit_signalled(df, latest, current_price, is_short):
    """EMA9 invalidation, optionally required to persist before it is acted on.

    Across 601 archived trades this rule fires 151 times against the hard
    stop's 75, and costs -6.81% of premium against the stop's -7.62%. That
    makes it an earlier second stop rather than protection: it closes at a
    mean of -0.42R, and the spread turns that into most of the book's losses.

    Whether it is preventing something worse is exactly what cannot be settled
    by reading it, so both knobs exist to be A/B'd against the archive.
    `EXIT_EMA_ENABLED=false` removes the rule; `EXIT_EMA_CONFIRM_BARS=n`
    requires the same invalidation on the n bars before this one, which keeps
    the rule but stops it acting on a single bar's excursion.
    """

    if not _env_bool("EXIT_EMA_ENABLED", True):

        return False

    if not _ema_invalidation(current_price, latest, is_short):

        return False

    confirm = int(_env_float("EXIT_EMA_CONFIRM_BARS", 0))

    for offset in range(2, confirm + 2):

        if len(df) < offset:

            return False

        bar = df.iloc[-offset]

        if not _ema_invalidation(bar.get("Close"), bar, is_short):

            return False

    return True


def evaluate_exit(
    df,
    analysis,
    risk_setup,
    entry_setup=None,
    trade_state=None
):

    latest = df.iloc[-1]

    entry_type = (
        entry_setup.get("entry_type")
        if entry_setup
        else None
    )

    if trade_state and trade_state.get("entry_type"):

        entry_type = trade_state.get("entry_type")

    is_short = _is_short_entry(entry_type)

    entry_price = risk_setup.get(
        "entry_price"
    )
    stop_loss = risk_setup.get(
        "stop_loss"
    )
    take_profit = risk_setup.get(
        "take_profit"
    )

    # The protective stop may have been moved to breakeven or trailed. R is
    # always measured against the risk frozen at entry, never the moved stop.
    initial_stop_loss = (
        risk_setup.get("initial_stop_loss")
        or (trade_state or {}).get("initial_stop_loss")
    )
    risk_per_share = resolve_risk_per_share(
        entry_price,
        initial_stop_loss,
        stop_loss
    )

    current_price = latest["Close"]
    atr = latest.get("ATR", 0) or 0

    highest_price = (
        trade_state.get("highest_price")
        if trade_state
        else entry_price
    )
    lowest_price = (
        trade_state.get("lowest_price")
        if trade_state
        else entry_price
    )

    if highest_price is None:

        highest_price = entry_price

    if lowest_price is None:

        lowest_price = entry_price

    # Ratcheted from the bar's own extremes, not from its close.
    #
    # These two lines used to read `current_price`, which is `latest["Close"]`.
    # So "highest price" meant "highest close" and every intrabar excursion the
    # position actually lived through was discarded. On a 5-minute frame that is
    # most of the excursion: measured over 191 replayed trades on 2026-08-15, the
    # recorded MFE averaged +0.434R against a true 10-minute peak of +0.524R, and
    # 73 trades recorded an MFE of exactly zero on bars whose highs were plainly
    # above the entry.
    #
    # It is not only a reporting defect. `mfe_r` gates `resolve_profit_lock`
    # (PROFIT_LOCK_MIN_MFE_R), the multiday profit rules, and breakeven-on-peak,
    # so an understated peak means each of those engages later than intended or
    # not at all. The 2026-08-15 archive shows 16% of trades booking a peak above
    # +1R while 34% of them actually reach +1R within an hour.
    #
    # The correction can only raise the peak and lower the trough, so every rule
    # reading them can only trigger earlier or protect more -- never later, never
    # less. That bounds the behaviour change to one direction.
    #
    # A stop is a resting order and executes intrabar, so protecting a level
    # derived from the true high is legitimate: the position genuinely was worth
    # that much. The locked level is a full giveback-R below the peak in any
    # case.
    bar_high = _float_or_none(latest.get("High"))
    bar_low = _float_or_none(latest.get("Low"))

    highest_price = max(
        highest_price,
        current_price if bar_high is None else max(current_price, bar_high),
    )
    lowest_price = min(
        lowest_price,
        current_price if bar_low is None else min(current_price, bar_low),
    )

    bars_in_trade = _bars_since_entry(
        df,
        trade_state,
        fallback=(
            trade_state.get("bars_in_trade", 0)
            if trade_state
            else 0
        ) + 1,
    )

    partial_profit_taken = (
        trade_state.get("partial_profit_taken", False)
        if trade_state
        else False
    )

    rr_progress = _calculate_rr_progress(
        current_price=current_price,
        entry_price=entry_price,
        stop_loss=stop_loss,
        is_short=is_short,
        risk_per_share=risk_per_share
    )
    direction = "PUT" if is_short else "CALL"
    trend_health = evaluate_live_trend_health(latest, direction)
    mfe_r = _calculate_rr_progress(
        lowest_price if is_short else highest_price,
        entry_price,
        stop_loss,
        is_short,
        risk_per_share=risk_per_share,
    )
    exit_confidence = evaluate_exit_confidence(
        latest,
        trend_health,
        rr_progress,
        mfe_r,
        bars_in_trade,
        is_short,
    )

    holding_profile = str((trade_state or {}).get("holding_profile") or "INTRADAY").upper()
    profit_lock_mfe_r = _env_float("MULTIDAY_PROFIT_LOCK_MFE_R", 2.0)
    profit_lock_r = _env_float("MULTIDAY_PROFIT_LOCK_R", 1.0)
    profit_exit_mfe_r = _env_float("MULTIDAY_PROFIT_EXIT_MFE_R", 3.0)
    profit_max_giveback_r = _env_float("MULTIDAY_PROFIT_MAX_GIVEBACK_R", 1.0)
    profit_protection_active = False
    profit_lock_stop = None
    profit_giveback_r = max(0.0, mfe_r - rr_progress)

    updated_stop = stop_loss
    trailing_stop = stop_loss
    exit_signal = False
    exit_reason = "Hold"
    exit_reasons = []
    trade_action = "HOLD"
    adjustment_reason = "Trend intact"

    # Hard stop and hard target are evaluated before softer invalidation rules.
    if is_short:

        if latest["High"] >= stop_loss:

            exit_reasons.append(_exit_diagnostic("HARD_STOP", "Hard stop hit (short)"))

        if latest["Low"] <= take_profit:

            exit_reasons.append(_exit_diagnostic("HARD_TARGET", "Profit target reached (short)"))

    else:

        if latest["Low"] <= stop_loss:

            exit_reasons.append(_exit_diagnostic("HARD_STOP", "Hard stop hit (long)"))

        if latest["High"] >= take_profit:

            exit_reasons.append(_exit_diagnostic("HARD_TARGET", "Profit target reached (long)"))

    primary_exit = _select_primary_exit(exit_reasons)

    if primary_exit:

        exit_signal = True
        exit_reason = primary_exit["reason"]

    # The breakeven move has been gated on a full 1R since it was written, and
    # over the 21-day economics run that made it very nearly inert: it fired on
    # 38 of 291 trades and saved 0.2R. The trades that need it never get near
    # 1R. Of the 145 that travelled at all, 108 peaked below 1R -- 68 of them
    # peaked between 0.1R and 0.5R and gave every bit of it back, closing at
    # -0.01R on average, and 15 of those ran on to worse than -0.25R.
    #
    # Simulated against that book, a 0.25R trigger recovers about 10R and takes
    # it from +2.1R to +12.2R. That simulation assumes no trade is cut at
    # breakeven that would have recovered, which is exactly the cost the replay
    # has to price, so the default stays at 1.0 and the knob is what moves.
    breakeven_trigger_r = _env_float("EXIT_BREAKEVEN_TRIGGER_R", 1.0)

    # Whether "got there" means the peak or only the current bar's close. A
    # trade can touch the trigger intrabar and close back under it; on peak the
    # stop still moves. Default is the close, which is what has always run.
    breakeven_progress = (
        max(rr_progress, mfe_r)
        if _env_bool("EXIT_BREAKEVEN_ON_PEAK", False)
        else rr_progress
    )

    if (
        not exit_signal
        and breakeven_trigger_r > 0
        and breakeven_progress >= breakeven_trigger_r
    ):

        if is_short:

            updated_stop = min(
                updated_stop,
                entry_price
            )

        else:

            updated_stop = max(
                updated_stop,
                entry_price
            )

        adjustment_reason = "Moved stop to breakeven"

    if not exit_signal and rr_progress >= 1.5:

        partial_profit_taken = True
        trade_action = "PARTIAL_PROFIT"
        adjustment_reason = "Partial profit threshold reached"

    if not exit_signal and rr_progress >= 2 and atr > 0:

        if is_short:

            trailing_stop = min(
                updated_stop,
                current_price + atr
            )
            updated_stop = trailing_stop

        else:

            trailing_stop = max(
                updated_stop,
                current_price - atr
            )
            updated_stop = trailing_stop

        adjustment_reason = "ATR trailing stop active"

    if not exit_signal and holding_profile == "MULTIDAY" and mfe_r >= profit_lock_mfe_r:

        risk = risk_per_share
        profit_lock_stop = (
            entry_price - (risk * profit_lock_r)
            if is_short
            else entry_price + (risk * profit_lock_r)
        )
        updated_stop = (
            min(updated_stop, profit_lock_stop)
            if is_short
            else max(updated_stop, profit_lock_stop)
        )
        trailing_stop = updated_stop
        profit_protection_active = True
        adjustment_reason = f"Multiday profit lock: {profit_lock_r:.1f}R protected"

        if mfe_r >= profit_exit_mfe_r and profit_giveback_r >= profit_max_giveback_r:
            exit_reasons.append(_exit_diagnostic(
                "PROFIT_PROTECTION",
                f"Multiday profit protection: {profit_giveback_r:.2f}R giveback from {mfe_r:.2f}R peak",
            ))
            primary_exit = _select_primary_exit(exit_reasons)
            exit_signal = True
            exit_reason = primary_exit["reason"]

    # The option's own high-water mark, ratcheted the same way `highest_price`
    # is, because the give-back rule needs a peak that survives between scans.
    option_peak_mid = _float_or_none(
        (trade_state or {}).get("option_peak_mid")
    )
    option_current_mid = _float_or_none(
        (trade_state or {}).get("option_current_mid")
        or (trade_state or {}).get("option_mid_price")
    )

    if option_current_mid and option_current_mid > 0:
        option_peak_mid = (
            option_current_mid if option_peak_mid is None
            else max(option_peak_mid, option_current_mid)
        )

    if not exit_signal:

        giveback_hit, peak_gain, floor_gain = _option_giveback_exit(
            trade_state,
            option_peak_mid,
        )

        if giveback_hit:

            exit_reasons.append(_exit_diagnostic(
                "PROFIT_PROTECTION",
                f"Option giveback: {peak_gain:.0f}% peak fell through {floor_gain:.0f}%",
            ))
            primary_exit = _select_primary_exit(exit_reasons)
            exit_signal = True
            exit_reason = primary_exit["reason"]
            profit_protection_active = True

    # The reversal half of the pair. The floor catches a slow bleed the flush
    # never sees; the flush catches a sharp turn before the floor is reached.
    # Neither covers the other's case, which is why both are on.
    if not exit_signal:

        _hit, flush_peak, _floor = _option_giveback_exit(trade_state, option_peak_mid)

        # Only once there is a gain to protect. Armed on every trade the flush
        # books below the book -- the damage is all on positions that never
        # gained, where the hard stop is the right tool.
        if (flush_peak is not None
                and flush_peak >= volume_flush_arm_pct()
                and _volume_flush_reversal(df, latest, is_short)):

            exit_reasons.append(_exit_diagnostic(
                "PROFIT_PROTECTION",
                f"Reversal on heavy volume, {flush_peak:.0f}% peak protected",
            ))
            primary_exit = _select_primary_exit(exit_reasons)
            exit_signal = True
            exit_reason = primary_exit["reason"]

    momentum_exits_allowed = _momentum_exits_allowed(holding_profile, bars_in_trade)

    if momentum_exits_allowed:

        if _ema_exit_signalled(df, latest, current_price, is_short):

            exit_reasons.append(_exit_diagnostic(
                "EMA",
                f"EMA9 invalidation ({'short' if is_short else 'long'})",
            ))

        primary_exit = _select_primary_exit(exit_reasons)

        if primary_exit:

            exit_signal = True
            exit_reason = primary_exit["reason"]

    if momentum_exits_allowed and pd.notna(latest.get("VWAP")):

        if is_short and current_price > latest["VWAP"]:

            exit_reasons.append(_exit_diagnostic("VWAP", "VWAP invalidation (short)"))

        elif not is_short and current_price < latest["VWAP"]:

            exit_reasons.append(_exit_diagnostic("VWAP", "VWAP invalidation (long)"))

        primary_exit = _select_primary_exit(exit_reasons)

        if primary_exit:

            exit_signal = True
            exit_reason = primary_exit["reason"]

    if momentum_exits_allowed:

        if (
            is_short
            and pd.notna(latest.get("MACD"))
            and pd.notna(latest.get("MACD_SIGNAL"))
            and latest["MACD"] > latest["MACD_SIGNAL"]
        ):

            exit_reasons.append(_exit_diagnostic("MACD", "MACD bullish crossover (short)"))

        elif (
            not is_short
            and pd.notna(latest.get("MACD"))
            and pd.notna(latest.get("MACD_SIGNAL"))
            and latest["MACD"] < latest["MACD_SIGNAL"]
        ):

            exit_reasons.append(_exit_diagnostic("MACD", "MACD bearish crossover (long)"))

        primary_exit = _select_primary_exit(exit_reasons)

        if primary_exit:

            exit_signal = True
            exit_reason = primary_exit["reason"]

    if momentum_exits_allowed and latest.get("FAILED_BREAKOUT", False):

        exit_reasons.append(_exit_diagnostic("FAILED_BREAKOUT", "Failed breakout"))
        primary_exit = _select_primary_exit(exit_reasons)
        exit_signal = True
        exit_reason = primary_exit["reason"]

    # 24 against a scan counter was ~2h at the REGULAR cadence, not the 6h that
    # "24 bars" reads as. 8 bars of 15m holds that 2h, which is the behaviour the
    # system has actually been running and the only one with evidence behind it.
    # A genuine 6h stagnation exit would barely fire inside a 9:45-15:55 session.
    if bars_in_trade >= _env_float("TIME_EXIT_BARS", 8) and rr_progress < 0.5:

        exit_reasons.append(_exit_diagnostic("TIME_EXIT", "Time exit: trade stagnation"))
        primary_exit = _select_primary_exit(exit_reasons)
        exit_signal = True
        exit_reason = primary_exit["reason"]

    latest_et = _get_timestamp_et(latest)

    if (
        latest_et is not None
        and latest_et.time() >= time(15, 45)
        and rr_progress < 1
    ):

        exit_reasons.append(_exit_diagnostic("NEAR_CLOSE", "Near-close exit without sufficient profit"))
        primary_exit = _select_primary_exit(exit_reasons)
        exit_signal = True
        exit_reason = primary_exit["reason"]

    if exit_signal and _should_guard_early_exit(
        df,
        exit_reason,
        bars_in_trade,
        rr_progress,
        is_short
    ):

        exit_signal = False
        exit_reason = "Hold"
        adjustment_reason = "Early weak exit guarded; trend intact"

    # The grace zone defers a lone momentum exit by one bar so a wick through the
    # level does not close the trade on a bar that closes back the right side.
    #
    # It was scoped to EMA only, but EMA is the one momentum rule that least needs
    # it: VWAP and MACD are bare state comparisons -- `price > VWAP`, `MACD <
    # signal` -- with no buffer, no slope condition and no confirmation, evaluated
    # against a still-forming bar. On 2026-07-29 nine of thirteen exits were soft
    # invalidations, two of them booking +0.04R and +0.10R.
    #
    # Every other condition is unchanged: the rule must be the *only* exit reason,
    # `grace_zone_eligible` still requires trend health >= 60, a position in profit
    # or MFE >= 1R, exactly one soft confirmation and no confirmed trend failure,
    # and the pending flag still allows the deferral only once per trade. This
    # widens which rules may be deferred, not how easily.
    #
    # The persisted flag keeps its `v1_ema_grace_pending` name: it is written into
    # live trade state by update_paper_trade(), so renaming it would strand any
    # position open across the change.
    GRACE_ELIGIBLE_EXITS = {"EMA", "VWAP", "MACD"}

    selected_exit = _select_primary_exit(exit_reasons) if exit_signal else None
    first_momentum_break = (
        selected_exit is not None
        and selected_exit.get("code") in GRACE_ELIGIBLE_EXITS
        and len(exit_reasons) == 1
        and not bool((trade_state or {}).get("v1_ema_grace_pending"))
    )
    grace_zone_active = first_momentum_break and exit_confidence["grace_zone_eligible"]
    if grace_zone_active:
        exit_signal = False
        exit_reason = "Hold"
        trade_action = "HOLD"
        adjustment_reason = (
            f"{selected_exit['code']} grace zone active; awaiting one-bar confirmation"
        )

    # Profit protection. A soft rule may say "momentum broke" while the trade is
    # sitting on real banked profit and the trend still reads strong. NVDA on
    # 2026-07-31 ran to +1.66R, printed "Partial profit threshold reached" three
    # times over ten minutes, then closed at +0.60R on an EMA9 touch -- with the
    # engine scoring its own confidence in that exit at 11.5 out of 100 and trend
    # health at 95. Breakeven protection was already active and did nothing,
    # because the exit came from a soft rule rather than the stop.
    #
    # So rather than honour or veto the exit outright, convert it into a floor:
    # keep the position and ratchet the stop up to protect all but
    # PROFIT_LOCK_MAX_GIVEBACK_R of the peak. The stop only ever moves in the
    # trade's favour, so this cannot turn a winner into a loser -- the worst case
    # is exiting at the locked level instead of at the soft signal, and the best
    # case is keeping a trend that had not actually ended.
    #
    # Deliberately narrow: it needs real profit banked, a trend still reading
    # healthy, AND the engine's own low confidence in the exit. None of those
    # describe a losing trade, so this never widens a loss.
    profit_lock_active = False
    locked_stop, locked_r = resolve_profit_lock(
        exit_code=selected_exit.get("code") if selected_exit else None,
        exit_signal=exit_signal,
        mfe_r=mfe_r,
        trend_health_score=trend_health["score"],
        exit_confidence_score=exit_confidence["exit_confidence_score"],
        entry_price=entry_price,
        current_stop=updated_stop,
        risk_per_share=risk_per_share,
        is_short=is_short,
    )

    if locked_stop is not None:

        updated_stop = locked_stop
        profit_lock_active = True
        exit_signal = False
        exit_reason = "Hold"
        trade_action = "HOLD"
        adjustment_reason = (
            f"{selected_exit['code']} exit held at confidence "
            f"{exit_confidence['exit_confidence_score']}; "
            f"stop locked at {locked_r:.2f}R of {mfe_r:.2f}R peak"
        )

    if exit_signal:

        trade_action = "EXIT"
        adjustment_reason = exit_reason

    selected_exit = (
        _select_primary_exit(exit_reasons)
        if exit_signal
        else None
    )
    exit_rule = selected_exit.get("code") if selected_exit else "HOLD"
    exit_waterfall = build_exit_waterfall(
        exit_reasons,
        selected_rule=exit_rule if exit_signal else None
    )

    # Priced against this frame's close. The caller may hold a fresher mark (the
    # scanner prefers the 5m close), so it re-resolves the fill with that price;
    # this keeps the exit engine's own result self-consistent for shadow runs,
    # replays and tests that call evaluate_exit() directly.
    exit_fill_price, exit_slippage = (
        resolve_exit_fill(
            exit_rule,
            is_short,
            current_price,
            stop_loss,
            take_profit,
        )
        if exit_signal
        else (None, None)
    )

    debug_print(
        f"[EXIT DEBUG] "
        f"is_short={is_short} "
        f"rr_progress={round(rr_progress, 2)} "
        f"bars={bars_in_trade} "
        f"exit_signal={exit_signal} "
        f"reason={exit_reason} "
        f"all_reasons={[item['reason'] for item in exit_reasons]}"
    )

    return {
        "exit_signal": exit_signal,
        "exit_reason": exit_reason,
        "exit_reasons": [item["reason"] for item in exit_reasons],
        "exit_diagnostics": exit_reasons,
        "primary_exit": exit_reason,
        "exit_waterfall": exit_waterfall,
        "exit_rule": exit_rule,
        "exit_fill_price": _round_float(exit_fill_price),
        "exit_slippage": exit_slippage,
        "exit_stage": (
            next(
                (
                    item["stage"]
                    for item in exit_waterfall
                    if item["rule"] == exit_rule
                ),
                None
            )
            if exit_signal
            else None
        ),
        "secondary_exits": [
            item["reason"]
            for item in exit_reasons
            if item["reason"] != exit_reason
        ],
        "ignored_exit_signals": [
            item["reason"]
            for item in exit_reasons
            if not exit_signal or item["reason"] != exit_reason
        ],
        "trailing_stop": _round_float(trailing_stop),
        "updated_stop": _round_float(updated_stop),
        "rr_progress": _round_float(rr_progress),
        # V1's own R measurements, against the risk frozen at entry. Exposed so
        # callers no longer have to borrow MFE from the V2 shadow, which carries
        # its own independent risk denominator.
        "mfe_r": _round_float(mfe_r),
        "risk_per_share": _round_float(risk_per_share),
        # The price this verdict was reached on. The caller fills against a
        # fresher mark, so without this the gap between deciding and filling is
        # unrecoverable -- and for soft exits `exit_slippage` is zero by
        # definition, so nothing else records it.
        "current_price": _round_float(current_price),
        "highest_price": _round_float(highest_price),
        "option_peak_mid": _round_float(option_peak_mid),
        "lowest_price": _round_float(lowest_price),
        "bars_in_trade": int(bars_in_trade),
        "partial_profit_taken": partial_profit_taken,
        "trade_action": trade_action,
        "adjustment_reason": adjustment_reason,
        "trend_health_score": trend_health["score"],
        "exit_confidence_score": exit_confidence["exit_confidence_score"],
        "profit_protection_active": profit_protection_active,
        "profit_lock_stop": _round_float(profit_lock_stop),
        "profit_giveback_r": _round_float(profit_giveback_r),
        "grace_zone_active": grace_zone_active,
        "profit_lock_active": profit_lock_active,
        "v1_ema_grace_pending": grace_zone_active,
    }