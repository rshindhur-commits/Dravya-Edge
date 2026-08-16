"""Would the app have traded yesterday's big movers?

The recurring question: a ticker moved 9% and the app was silent -- was that the
entry logic missing it, or the option chain refusing it? Those need completely
different responses and the answer is not guessable from the chart.

Symbols do **not** need to be in the watchlist. That is the point: this is for
testing names before deciding whether to add them.

    python tools/mover_check.py --days 2026-08-14 --symbols NBIS,RIOT,COIN
    python tools/mover_check.py --days 2026-08-14,2026-08-13 --symbols NBIS

## What each column means

    signals    times the entry engine wanted to trade. Zero here means the setup
               never appeared -- an entry problem, and nothing about options.
    bought     times a contract passed every gate. This is the trade count.
    best spr   the tightest spread among contracts that passed EVERYTHING ELSE.
               This is the number that decides tradeability, and the one a chart
               cannot show you.
    needs      the spread ceiling that would have been required to buy it.

## The verdict column

**TRADED** -- nothing to do.

**NO SETUP** -- the entry engine never signalled. Adding the ticker changes
nothing; the chart moved in a way the strategy does not recognise.

**CHAIN TOO WIDE** -- the app saw the setup and could not buy it. The `needs`
column says what ceiling it would have taken. Before reaching for that, note
that §7.3a of TRADE_QUALITY_PLAN measured looser ceilings as **losing** on
exactly these high-range days: at 5%+ session range, ceiling 6 returns −1.89%
across 85 trades where ceiling 2 returns +5.62%. A wide chain is usually the
market telling you the contract is expensive to get out of.

**TOO EXPENSIVE** -- contracts were tight but above the cost cap. This one is
fixable per symbol via `OPTION_MAX_CONTRACT_COST_BY_SYMBOL`, which is how AVGO,
SMH and GOOGL were brought back.

**TOO THIN** -- open interest or volume too low. Not fixable by settings; the
contract genuinely is not traded, and buying it means being the only one in it.

Costs Polygon quota: roughly 150 requests per entry signal. Post-market only.
"""

import argparse
import pathlib
import sys
import warnings
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=FutureWarning, module="app.indicators.*")

load_dotenv()

from app.backtesting.contract_selector import SelectionConfig
from app.backtesting.replay_engine import ReplayConfig, replay_days
from app.config.settings import get_float_env, settings
from app.options.affordability_config import get_affordability_config

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from replay_forward import make_recording_selector, scan_grid

CEILING_LADDER = [3.0, 4.0, 6.0, 10.0, 15.0, 25.0]


def _number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def diagnose(attempts, symbol):
    """Why nothing was bought, testing each gate independently.

    The `code` on each attempt is a FIRST-failure marker -- whichever gate runs
    first absorbs the blame -- so it cannot answer this. Every contract is
    re-tested against each gate on its own.
    """

    cap = get_affordability_config(symbol)["max_contract_cost"]
    min_cost = get_affordability_config(symbol)["min_contract_cost"]
    min_oi = settings.option_min_open_interest
    min_volume = settings.option_min_volume
    min_dte, max_dte = settings.option_min_dte, settings.option_max_dte

    survivors = []
    passed_cost = passed_liquidity = 0

    for raw in attempts:

        spread = _number(raw.get("spread_pct"))
        cost = _number(raw.get("contract_cost"))
        dte = _number(raw.get("dte"))
        oi = _number(raw.get("open_interest")) or 0
        volume = _number(raw.get("volume")) or 0

        if None in (spread, cost, dte):
            continue

        if not (min_dte <= dte <= max_dte):
            continue

        affordable = min_cost <= cost <= cap
        liquid = oi >= min_oi and volume >= min_volume

        passed_cost += affordable
        passed_liquidity += liquid

        # Everything except spread. What remains is what the ceiling refused.
        if affordable and liquid:
            survivors.append((spread, cost))

    if survivors:
        best_spread, best_cost = min(survivors)
        needed = next(
            (c for c in CEILING_LADDER if best_spread <= c), None
        )
        return "CHAIN TOO WIDE", best_spread, best_cost, needed

    if passed_liquidity and not passed_cost:
        return "TOO EXPENSIVE", None, None, None

    if passed_cost and not passed_liquidity:
        return "TOO THIN", None, None, None

    return "TOO THIN / TOO WIDE", None, None, None


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", required=True, help="comma-separated YYYY-MM-DD")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--cadence", type=int, default=5)
    parser.add_argument(
        "--together",
        action="store_true",
        help="replay all symbols in one book, so they compete for position "
        "slots as they would live. Off by default: this tool answers "
        "'would THIS ticker have traded', and a shared book suppresses "
        "signals for reasons that have nothing to do with the ticker.",
    )
    args = parser.parse_args()

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    ceiling = get_float_env("OPTION_MAX_SPREAD_PCT", 3.0)

    print(f"\n  {len(symbols)} symbols over {len(days)} session(s)")
    print(f"  live rules: spread<={ceiling:g}%, OI>={settings.option_min_open_interest}, "
          f"vol>={settings.option_min_volume}, DTE {settings.option_min_dte}-"
          f"{settings.option_max_dte}\n", flush=True)

    # One book per symbol unless asked otherwise. Run together, NBIS lost a
    # signal on 2026-08-14 because NVDA was holding a position slot -- which is
    # true to live behaviour and false to the question being asked here.
    groups = [symbols] if args.together else [[s] for s in symbols]

    log = []
    trades = []

    for group in groups:

        replay_config = ReplayConfig()
        replay_config.contract_selector = make_recording_selector(
            replay_config, SelectionConfig(), log
        )

        result = replay_days(
            group,
            days,
            lambda day: scan_grid(day, args.cadence),
            config=replay_config,
        )

        trades.extend(result["closed"] + result["open"])

    signals = defaultdict(int)
    bought = defaultdict(int)
    attempts = defaultdict(list)

    for entry in log:
        signals[entry["symbol"]] += 1
        if entry.get("ticker"):
            bought[entry["symbol"]] += 1
        attempts[entry["symbol"]].extend(
            (entry.get("diagnostics") or {}).get("liquidity_attempts") or []
        )

    print(f"\n  {'symbol':8}{'signals':>9}{'bought':>8}{'best spr':>10}"
          f"{'cost':>8}{'needs':>8}   verdict")
    print(f"  {'':-<74}")

    for symbol in symbols:

        found = signals[symbol]

        if not found:
            print(f"  {symbol:8}{0:>9}{0:>8}{'-':>10}{'-':>8}{'-':>8}   NO SETUP")
            continue

        if bought[symbol]:
            realised = [
                t for t in trades
                if t.symbol == symbol and t.option_entry_fill
            ]
            note = ""
            if realised:
                priced = [
                    (t.option_exit_fill - t.option_entry_fill)
                    / t.option_entry_fill * 100.0
                    for t in realised
                    if t.option_exit_fill is not None
                ]
                if priced:
                    note = f" ({sum(priced)/len(priced):+.1f}% mean)"
            print(f"  {symbol:8}{found:>9}{bought[symbol]:>8}{'-':>10}"
                  f"{'-':>8}{'-':>8}   TRADED{note}")
            continue

        verdict, spread, cost, needed = diagnose(attempts[symbol], symbol)

        print(f"  {symbol:8}{found:>9}{0:>8}"
              f"{(f'{spread:.2f}%' if spread else '-'):>10}"
              f"{(f'${cost:.0f}' if cost else '-'):>8}"
              f"{(f'{needed:g}%' if needed else '-'):>8}   {verdict}")

    print("\n  NO SETUP        entry logic never signalled -- nothing to do with options")
    print("  CHAIN TOO WIDE  the app saw it and could not buy it. See `needs`,")
    print("                  and read TRADE_QUALITY_PLAN 7.3a before loosening")
    print("  TOO EXPENSIVE   fixable per symbol via OPTION_MAX_CONTRACT_COST_BY_SYMBOL")
    print("  TOO THIN        nobody trades that contract; no setting fixes it\n")


if __name__ == "__main__":
    main()
