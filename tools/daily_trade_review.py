"""What each of today's trades did, and what it would have done otherwise.

Built 2026-08-15 after four trades were reviewed by hand from chart screenshots.
That review took an hour and produced findings the app already had the data for:
that CRWD sold near the top of its range and NFLX near the bottom, that holding
NFLX would have been stopped out while holding CRWD would have tripled the
result, and that SPCX reached +0.70R and closed -0.33R having told the operator
"Trend: STRONG, Continue Holding" fifteen minutes earlier.

None of that needed a chart. It needs the bars, which are cached, and the trade
record, which is in Postgres. So it runs nightly instead.

Four questions per trade, because those are the four that were actually asked:

  PLACEMENT  where in the session's range did we enter? A put wants to be sold
             high in the range and a call bought low. Reported as a percentage
             so a bad entry is visible without a chart.

  DRIFT      which way was price moving in the 45 minutes before entry, and did
             we trade with it or against it.

  HOLD       how far the trade went our way and against us while we held it,
             against the risk taken -- the giveback that `mfe_r` understated
             until 966a3ef.

  COUNTERFACTUAL  what holding to the close would have produced, and whether the
             stop or the target would have been reached first. This is the only
             one that needs bars after the exit, and it is the one that settles
             whether an exit rule saved money or cost it.

Cash is always on honest fills -- bought the ask, sold the bid -- because the
Telegram alert reported mid-to-mid until a7baef2 and overstated the book by $280
across 19 trades.

    python tools/daily_trade_review.py                 # the last session traded
    python tools/daily_trade_review.py --day 2026-08-14
    python tools/daily_trade_review.py --days 5        # a week in one pass

Cached bars only. No option quotes, no Polygon spend beyond the bar cache.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.backtesting.historical_market_data import fetch_bars
from app.db.connection import get_engine

DRIFT_WINDOW_MINUTES = 45

_bars = {}


def bars(symbol, day):
    key = (symbol, day)
    if key not in _bars:
        try:
            frame = fetch_bars(symbol, day, day)
            frame.index = frame.index.tz_convert("America/New_York")
            _bars[key] = frame.between_time("09:30", "16:00")
        except Exception:
            _bars[key] = None
    return _bars[key]


def num(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def load_trades(day=None, days=1):

    with get_engine().begin() as connection:

        if day:
            where, params = "opened_at::date = CAST(:day AS DATE)", {"day": day}
        else:
            where, params = (
                "opened_at::date >= (SELECT MAX(opened_at::date) FROM paper_trades)"
                " - CAST(:days AS INTEGER)",
                {"days": days - 1},
            )

        return connection.execute(text(f"""
            SELECT symbol, direction, opened_at, closed_at, status,
                   entry_price::float AS entry_price,
                   close_price::float AS close_price,
                   r_multiple::float AS r_multiple,
                   payload
            FROM paper_trades
            WHERE {where}
            ORDER BY opened_at
        """), params).mappings().all()


def review(trade):
    """Every diagnostic for one trade, or None when the bars are unavailable."""

    payload = trade["payload"] or {}
    entry = num(trade["entry_price"])
    stop = num(payload.get("initial_stop_loss")) or num(payload.get("stop_loss"))
    target = num(payload.get("take_profit"))
    opened, closed = trade["opened_at"], trade["closed_at"]

    if None in (entry, stop, target) or not opened:
        return None

    risk = abs(entry - stop)
    if risk <= 0:
        return None

    # `opened_at` is stored UTC; the bar index is ET.
    entry_at = pd.Timestamp(opened).tz_convert("America/New_York")
    exit_at = (
        pd.Timestamp(closed).tz_convert("America/New_York") if closed else None
    )

    day = entry_at.strftime("%Y-%m-%d")
    frame = bars(trade["symbol"], day)

    if frame is None or not len(frame):
        return None

    is_short = str(trade["direction"] or "").upper() in {"PUT", "SHORT"}

    before = frame[frame.index < entry_at]
    during = frame[(frame.index >= entry_at) & (frame.index <= (exit_at or frame.index[-1]))]
    after = frame[frame.index > (exit_at or frame.index[-1])]

    result = {
        "symbol": trade["symbol"],
        "direction": "PUT" if is_short else "CALL",
        "setup": payload.get("entry_type"),
        "entry_at": entry_at.strftime("%H:%M"),
        "exit_at": exit_at.strftime("%H:%M") if exit_at is not None else "open",
        "reason": payload.get("exit_reason"),
        "r": num(trade["r_multiple"]),
        "risk_pct": risk / entry * 100,
    }

    # PLACEMENT -- a put wants to be sold high in the range, a call bought low.
    if len(before) > 5:
        low, high = before["Low"].min(), before["High"].max()
        if high > low:
            position = (entry - low) / (high - low) * 100
            result["placement"] = position if is_short else 100 - position

    # DRIFT -- which way price was going as we entered.
    window = before[before.index >= entry_at - pd.Timedelta(minutes=DRIFT_WINDOW_MINUTES)]
    if len(window) > 2:
        drift = window["Close"].iloc[-1] - window["Close"].iloc[0]
        result["drift"] = drift
        result["with_drift"] = (drift < 0) == is_short

    # HOLD -- how far it went either way while we held it.
    if len(during):
        best = (entry - during["Low"].min()) if is_short else (during["High"].max() - entry)
        worst = (during["High"].max() - entry) if is_short else (entry - during["Low"].min())
        result["mfe_r"] = best / risk
        result["mae_r"] = worst / risk
        if result.get("r") is not None:
            result["giveback_r"] = result["mfe_r"] - result["r"]

    # COUNTERFACTUAL -- what holding to the close would have done.
    if len(after):
        favourable = (entry - after["Low"].min()) if is_short else (after["High"].max() - entry)
        result["available_after_r"] = favourable / risk
        result["stop_would_hit"] = bool(
            after["High"].max() >= stop if is_short else after["Low"].min() <= stop
        )
        result["target_would_hit"] = bool(
            after["Low"].min() <= target if is_short else after["High"].max() >= target
        )
        # Holding is scored honestly: a stop reached first ends the trade at -1R
        # whatever the day did afterwards.
        result["held_r"] = -1.0 if result["stop_would_hit"] else result["available_after_r"]

    # TARGET REACHABILITY -- was the target inside the day's range at all.
    span = frame["High"].max() - frame["Low"].min()
    if span > 0:
        result["target_vs_day_range"] = abs(target - entry) / span * 100

    # CASH, on honest fills.
    ask, bid = num(payload.get("option_entry_ask")), num(payload.get("option_close_bid"))
    contracts = num(payload.get("option_contracts")) or 1
    if ask and bid:
        result["cash"] = (bid - ask) * 100 * contracts
        result["cash_pct"] = (bid - ask) / ask * 100

    return result


def fmt(value, spec, blank="-"):
    return blank if value is None else format(value, spec)


def print_report(rows, day_label):

    print(f"\n{'=' * 78}")
    print(f"  TRADE REVIEW  {day_label}    {len(rows)} trades")
    print(f"{'=' * 78}\n")

    for r in rows:

        print(f"  {r['symbol']:<6} {r['direction']:<5} {r['entry_at']}->{r['exit_at']}"
              f"   {str(r.get('setup') or ''):<22} {fmt(r.get('r'), '+.2f')}R"
              f"   {fmt(r.get('cash'), '+.0f')}$")

        placement = r.get("placement")
        if placement is not None:
            verdict = ("well placed" if placement >= 65
                       else "poorly placed" if placement <= 35 else "mid-range")
            print(f"         entry sat {placement:>3.0f}% into the range  ({verdict})")

        if r.get("drift") is not None:
            print(f"         price was {r['drift']:+.2f} over the prior "
                  f"{DRIFT_WINDOW_MINUTES}m -- traded "
                  f"{'WITH' if r.get('with_drift') else 'AGAINST'} it")

        if r.get("mfe_r") is not None:
            line = (f"         while held: best {r['mfe_r']:+.2f}R  "
                    f"worst {r['mae_r']:+.2f}R")
            if r.get("giveback_r") is not None:
                line += f"  gave back {r['giveback_r']:+.2f}R"
            print(line)

        if r.get("held_r") is not None:
            gain = r["held_r"] - (r.get("r") or 0)
            note = ("stop would have hit" if r.get("stop_would_hit")
                    else "target reached" if r.get("target_would_hit") else "")
            print(f"         holding to the close: {r['held_r']:+.2f}R "
                  f"({gain:+.2f}R vs booked){'  ' + note if note else ''}")

        tvr = r.get("target_vs_day_range")
        if tvr is not None and tvr > 80:
            print(f"         WARNING target needed {tvr:.0f}% of the whole day's range")

        print()

    booked = [r["r"] for r in rows if r.get("r") is not None]
    held = [r["held_r"] for r in rows if r.get("held_r") is not None]
    cash = [r["cash"] for r in rows if r.get("cash") is not None]

    print(f"  {'-' * 74}")
    if booked:
        print(f"  booked            {sum(booked):+.2f}R across {len(booked)} trades")
    if cash:
        print(f"  cash (fills)      ${sum(cash):+,.0f}   "
              f"{sum(1 for x in cash if x > 0)}/{len(cash)} paid")
    if held:
        print(f"  held to close     {sum(held):+.2f}R   "
              f"-> exits were worth {sum(booked) - sum(held):+.2f}R")

    placed = [r["placement"] for r in rows if r.get("placement") is not None]
    if placed:
        print(f"  mean placement    {sum(placed) / len(placed):.0f}%  "
              f"(higher is better; 50% is a coin flip)")

    against = [r for r in rows if r.get("with_drift") is False]
    if against:
        print(f"  traded against the drift: {len(against)} of {len(rows)}")

    print()


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--day", help="YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()

    trades = load_trades(args.day, args.days)

    if not trades:
        print("\n  no trades found for that window.\n")
        return

    by_day = {}
    for trade in trades:
        key = pd.Timestamp(trade["opened_at"]).tz_convert("America/New_York").strftime("%Y-%m-%d")
        by_day.setdefault(key, []).append(trade)

    for day in sorted(by_day):
        rows = [r for r in (review(t) for t in by_day[day]) if r]
        if rows:
            print_report(rows, day)
        else:
            print(f"\n  {day}: {len(by_day[day])} trades, none reviewable "
                  f"(missing geometry or bars)\n")


if __name__ == "__main__":
    main()
