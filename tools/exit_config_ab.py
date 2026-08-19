"""What the 2026-08-19 exit changes would have done to the trades actually taken.

Drives the **real** `evaluate_exit` over recorded trades, once under the settings
that were live before and once under the ones that shipped, and prices each exit
from the **option contract's own minute bars**. No synthetic contract, no assumed
spread: the traded ticker's real prints.

    python tools/exit_config_ab.py --days 3

Both arms start from the same recorded entry and the same option entry fill, so
every difference between them is the exit and nothing else.

## What this can and cannot settle

It answers "did the changes behave as intended on the book that actually
existed". On a handful of trades it cannot establish that they are profitable --
the stop-floor hypothesis looked decisive on 12 trades and died on 310. Read the
per-trade rows, not the total.

Option cash is quoted beside R deliberately. R has flattered this book before:
2026-08-19 closed +0.332R mean and -$5.00 in premium.
"""

from __future__ import annotations

import argparse
import importlib
import os
import pathlib
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

load_dotenv()

from app.db.connection import get_engine
from app.utils.polygon_client import get_polygon_api_key, get_polygon_base_url

# What was live before the exit work, and what shipped with it.
BEFORE = {
    "EXIT_TRAIL_ARM_R": "2.0",
    "EXIT_PROFIT_LADDER": "",
    "SOFT_EXIT_HOLD_ENABLED": "false",
    "EXIT_STRUCTURE_TRAIL_ENABLED": "false",
    "EXIT_TARGET_EXTEND_ENABLED": "false",
}

AFTER = {
    "EXIT_TRAIL_ARM_R": "1.0",
    "EXIT_PROFIT_LADDER": "1.0:0.25,1.5:0.75,2.0:1.25,2.5:1.75,3.0:2.25",
    "SOFT_EXIT_HOLD_ENABLED": "true",
    "EXIT_STRUCTURE_TRAIL_ENABLED": "true",
    "EXIT_TARGET_EXTEND_ENABLED": "false",
}


def option_bars(ticker, day):
    """The traded contract's own minute prints for that session."""

    key, base = get_polygon_api_key(), get_polygon_base_url()

    try:
        payload = requests.get(
            f"{base}/v2/aggs/ticker/{ticker}/range/1/minute/{day}/{day}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
            timeout=30,
        ).json()
    except Exception:
        return []

    return payload.get("results") or []


def option_price_at(bars, when):
    """The contract's close on the last bar at or before `when`."""

    if not bars or when is None:
        return None

    last = None

    for bar in bars:
        stamp = datetime.fromtimestamp(bar["t"] / 1000, tz=when.tzinfo)
        if stamp <= when:
            last = bar["c"]
        else:
            break

    return last


def load_trades(days):

    cutoff = (datetime.now() - timedelta(days=days)).date()

    with get_engine().connect() as conn:
        return list(conn.execute(text("""
            select symbol, direction, entry_price::float ep,
                   (payload->>'initial_stop_loss')::float stop,
                   (payload->>'take_profit')::float tgt,
                   option_ticker, option_entry_mid::float oem,
                   (payload->>'option_entry_ask')::float oea,
                   opened_at, closed_at, r_multiple::float booked,
                   payload->>'exit_rule' rule,
                   coalesce(payload->>'entry_type',
                            payload->'scanner_context'->>'Entry') etype,
                   coalesce(payload->>'holding_profile', 'INTRADAY') profile
            from paper_trades
            where closed_at is not null
              and opened_at::date >= :cutoff
              and option_ticker is not null
              and (payload->>'initial_stop_loss') is not null
            order by opened_at
        """), {"cutoff": cutoff}))


def run_arm(trade, env, entry_type, profile):
    """Walk the 5-minute scan grid from entry to the close under one config."""

    for key, value in env.items():
        os.environ[key] = value

    # Reloaded so module-level reads of the switches pick up this arm.
    import app.exit.exit_engine as engine
    importlib.reload(engine)
    import app.backtesting.replay_engine as replay
    importlib.reload(replay)
    import app.backtesting.historical_market_data as hmd

    # `opened_at` is UTC-aware. Stripping tzinfo from it yields a naive UTC
    # time, which the replay grid reads as ET -- TSLA's 14:08 UTC became "14:08
    # ET" and replayed a different part of the session entirely, which is why the
    # BEFORE arm did not reproduce the booked result.
    opened = trade.opened_at.astimezone(ZoneInfo("America/New_York"))
    day = opened.date().isoformat()
    raw = hmd.load_replay_frames(trade.symbol, day, lookback_days=3)
    config = replay.ReplayConfig()

    naive_open = opened.replace(tzinfo=None)

    rt = replay.ReplayTrade(
        symbol=trade.symbol,
        direction=trade.direction,
        entry_type=entry_type,
        scan_id=opened.strftime("%Y-%m-%d_%H%M%S"),
        entry_time=replay._et(naive_open),
        entry_price=trade.ep,
        stop_loss=trade.stop,
        initial_stop_loss=trade.stop,
        take_profit=trade.tgt,
    )
    rt.state = {
        "symbol": trade.symbol,
        "status": "OPEN",
        "direction": trade.direction,
        "entry_type": entry_type,
        "entry_price": trade.ep,
        "stop_loss": trade.stop,
        "initial_stop_loss": trade.stop,
        "take_profit": trade.tgt,
        "highest_price": trade.ep,
        "lowest_price": trade.ep,
        "bars_in_trade": 0,
        "partial_profit_taken": False,
        "holding_profile": profile,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "option_contracts": 1,
    }

    cursor = naive_open + timedelta(minutes=5)
    end = naive_open.replace(hour=15, minute=55, second=0, microsecond=0)

    while cursor <= end and rt.is_open:

        frames = replay.build_frames(raw, cursor, trade.symbol, config)

        if frames[0] is not None:
            df_5m, df_15m, _one_h, _daily, analysis_15m, _ctx = frames
            replay._manage_trade(rt, cursor, df_5m, df_15m, analysis_15m, config)

        cursor += timedelta(minutes=5)

    for key in env:
        os.environ.pop(key, None)

    return rt


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    trades = load_trades(args.days)

    print(f"\nreplaying {len(trades)} closed trades from the last {args.days} days")
    print("both arms share the entry and the option entry fill; only exits differ\n")
    print(f"  {'sym':5s} {'booked':>7s} | {'BEFORE':>7s} {'AFTER':>7s} {'dR':>7s} | "
          f"{'$before':>8s} {'$after':>8s} {'d$':>8s}  rules")

    tot_b = tot_a = cash_b = cash_a = 0.0
    counted = 0

    for trade in trades:

        # The recorded setup, not an assumed one. `_is_short_entry` reads the
        # setup NAME while `direction` holds PUT/CALL, so hardcoding a long
        # entry type replays every PUT as a CALL -- which is exactly what the
        # first run of this tool did, reporting SPCX (a PUT) reaching a profit
        # target and TSLA booking +5.48R against the +0.00R it actually made.
        entry_type = trade.etype or (
            "EMA_REJECTION_SHORT" if str(trade.direction).upper() == "PUT"
            else "EMA_PULLBACK"
        )
        bars = option_bars(
            trade.option_ticker,
            trade.opened_at.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
        )
        entry_fill = trade.oea or trade.oem
        arms = {}

        for label, env in (("BEFORE", BEFORE), ("AFTER", AFTER)):
            try:
                rt = run_arm(trade, env, entry_type, trade.profile)
            except Exception as exc:
                arms[label] = None
                print(f"  {trade.symbol:5s} replay failed under {label}: "
                      f"{type(exc).__name__}: {exc}")
                continue
            arms[label] = rt

        before, after = arms.get("BEFORE"), arms.get("AFTER")

        if before is None or after is None or before.is_open or after.is_open:
            print(f"  {trade.symbol:5s} {trade.booked:+7.2f} | incomplete replay")
            continue

        pb = option_price_at(bars, before.exit_time)
        pa = option_price_at(bars, after.exit_time)
        cb = (pb - entry_fill) * 100 if (pb and entry_fill) else 0.0
        ca = (pa - entry_fill) * 100 if (pa and entry_fill) else 0.0

        tot_b += before.r_multiple
        tot_a += after.r_multiple
        cash_b += cb
        cash_a += ca
        counted += 1

        print(f"  {trade.symbol:5s} {trade.booked:+7.2f} | {before.r_multiple:+7.2f} "
              f"{after.r_multiple:+7.2f} {after.r_multiple - before.r_multiple:+7.2f} | "
              f"{cb:+8.2f} {ca:+8.2f} {ca - cb:+8.2f}  "
              f"{str(before.exit_reason)[:16]} -> {str(after.exit_reason)[:16]}")

    if counted:
        print(f"\n  {counted} trades    R {tot_b:+.2f} -> {tot_a:+.2f} "
              f"({tot_a - tot_b:+.2f})    cash ${cash_b:+.2f} -> ${cash_a:+.2f} "
              f"(${cash_a - cash_b:+.2f})")
        print("\n  Read the per-trade rows, not the total. A handful of trades cannot")
        print("  establish that a change is profitable -- the stop-floor hypothesis")
        print("  looked decisive on 12 trades and died on 310.")


if __name__ == "__main__":
    main()
