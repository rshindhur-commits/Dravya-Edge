r"""Pull the day's real movers, ask whether the app could have traded them, and keep the answers.

Built after 2026-08-14, when NBIS moved 9.3% and the app was silent. Answering
that took an afternoon by hand. This makes it a nightly job whose output
accumulates, so the weekend question -- *which tickers keep getting refused, and
by what* -- is answered from a log rather than from memory.

    python tools/mover_watch.py                 # today's movers, check and log
    python tools/mover_watch.py --day 2026-08-14
    python tools/mover_watch.py --review        # what the log says so far
    python tools/mover_watch.py --top 12 --dry-run   # just the shortlist

Runs quietly and exits on weekends and holidays, so a daily trigger is safe.
To schedule it on this machine, once, from an elevated prompt:

    schtasks /create /tn "DravyaMoverWatch" ^
      /tr "d:\Dravya_Trade_Works\tools\mover_watch_daily.bat" /sc daily /st 16:45

16:45 local is after the 15:30 ET entry window and the close. Deliberately NOT
on the Render worker: this shares Polygon quota with the live scanner, and a
research job should not compete with the thing that places trades.

## Why the gainers endpoint is not used

`/v2/snapshot/.../gainers` returns warrants and sub-dollar tickers -- on the day
this was written its top five were a $0.012 stock up 2066%, two warrants and a
$0.97 name. None of them has a tradeable option chain. The full snapshot is
filtered instead, on the two things that decide whether an option chain can exist
at all:

    price       a contract has to land inside the subscriber bands, and price
                sets the floor on what a near-the-money option costs
    $ volume    thin underlyings have thin chains, and a chain nobody trades
                quotes wide -- which is exactly what refused NBIS

Names already in the watchlist are excluded: the question is what to ADD.

## Cost, counted rather than estimated

Every entry signal prices the chain, and that is **72 option-quote requests per
signal** -- `SelectionConfig.max_priced_contracts`. Measured end to end on AAOI,
2026-08-14:

    cadence  signals  requests   verdict
    5m            15      1081   CHAIN TOO WIDE, best 4.40%, needs 6%
    15m            4       288   CHAIN TOO WIDE, best 4.55%, needs 6%

**The 15-minute grid costs a quarter as much and returns the same answer**, so it
is the default here. `mover_check.py` keeps the 5-minute grid for when a single
symbol is being examined closely and precision is worth the quota.

Budget roughly **300 requests per mover that signals**, and none for one that
does not. `--top 5` is typically 500-1,500 requests a night. `--dry-run` costs
exactly one.

**Requests are not billed.** The data plan (massive.com Options Advanced, $199/mo,
verified 2026-08-16) is *Unlimited API Calls* with real-time data; the only
constraint is `POLYGON_RATE_LIMIT_PER_MINUTE`, set to 1200. So the reason to keep
this small is wall-clock time and not competing with the live scanner -- **not
cost**. Do not "optimise" this job for request count.
"""

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import warnings

from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=FutureWarning, module="app.indicators.*")

load_dotenv()

from app.config.watchlist import WATCHLIST
from app.utils.polygon_client import (
    get_polygon_api_key,
    get_polygon_base_url,
    safe_request,
)

LOG_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "mover_watch"

# A near-the-money option runs roughly 2-5% of share price. Below $15 the
# contract tends to fall under the $100 minimum; above $600 it clears the cap
# before it clears anything else.
MIN_PRICE, MAX_PRICE = 15.0, 600.0

# Dollar volume in millions. Thin underlyings have thin chains.
MIN_DOLLAR_VOLUME_M = 200.0

MIN_MOVE_PCT = 4.0


def _is_trading_day(now=None):
    """Weekday and not a market holiday, in market time."""

    from zoneinfo import ZoneInfo

    from app.runtime.market_calendar import is_market_holiday

    now = now or datetime.now(ZoneInfo("America/New_York"))

    if now.weekday() >= 5:
        return False

    try:
        return not is_market_holiday(now)
    except Exception:
        # A calendar that cannot answer must not silently skip a real session.
        return True


def _clean_ticker(symbol):
    """Ordinary common stock only.

    Warrants, units and rights carry option chains that either do not exist or
    are quoted so wide that pricing them is a waste of quota.
    """

    symbol = str(symbol or "").strip().upper()

    if not symbol or not symbol.isalpha() or len(symbol) > 5:
        return None

    return symbol


def fetch_movers(top, min_move=MIN_MOVE_PCT):

    key = get_polygon_api_key()

    if not key:
        raise SystemExit("POLYGON_API_KEY is not set")

    response = safe_request(
        f"{get_polygon_base_url()}/v2/snapshot/locale/us/markets/stocks/tickers",
        params={"apiKey": key},
        timeout=40,
    )
    tickers = response.json().get("tickers") or []

    rows = []

    for item in tickers:

        symbol = _clean_ticker(item.get("ticker"))

        if not symbol or symbol in WATCHLIST:
            continue

        day = item.get("day") or {}
        close = day.get("c") or 0
        volume = day.get("v") or 0

        try:
            change = float(item.get("todaysChangePerc") or 0.0)
            close = float(close)
            volume = float(volume)
        except (TypeError, ValueError):
            continue

        if not (MIN_PRICE <= close <= MAX_PRICE):
            continue

        dollar_volume = close * volume / 1e6

        if dollar_volume < MIN_DOLLAR_VOLUME_M:
            continue

        if abs(change) < min_move:
            continue

        rows.append({
            "symbol": symbol,
            "change_pct": change,
            "price": close,
            "dollar_volume_m": dollar_volume,
        })

    rows.sort(key=lambda r: -abs(r["change_pct"]))

    return rows[:top]


def check(symbols, day, cadence):
    """Run the live rules over each symbol, one book each."""

    from app.backtesting.contract_selector import SelectionConfig
    from app.backtesting.replay_engine import ReplayConfig, replay_days
    from mover_check import diagnose
    from replay_forward import make_recording_selector, scan_grid

    results = {}

    for symbol in symbols:

        log = []
        config = ReplayConfig()
        config.contract_selector = make_recording_selector(
            config, SelectionConfig(), log
        )

        try:
            outcome = replay_days(
                [symbol], [day], lambda d: scan_grid(d, cadence), config=config
            )
        except Exception as exc:
            results[symbol] = {"verdict": "ERROR", "detail": str(exc)[:120]}
            continue

        trades = outcome["closed"] + outcome["open"]
        signals = len(log)
        bought = sum(1 for entry in log if entry.get("ticker"))
        attempts = [
            a
            for entry in log
            for a in (entry.get("diagnostics") or {}).get("liquidity_attempts") or []
        ]

        if not signals:
            results[symbol] = {"verdict": "NO SETUP", "signals": 0, "bought": 0}
            continue

        if bought:
            priced = [
                (t.option_exit_fill - t.option_entry_fill) / t.option_entry_fill * 100.0
                for t in trades
                if t.option_entry_fill and t.option_exit_fill is not None
            ]
            results[symbol] = {
                "verdict": "TRADED",
                "signals": signals,
                "bought": bought,
                "mean_pct": round(sum(priced) / len(priced), 2) if priced else None,
            }
            continue

        verdict, spread, cost, needed = diagnose(attempts, symbol)
        results[symbol] = {
            "verdict": verdict,
            "signals": signals,
            "bought": 0,
            "best_spread": round(spread, 2) if spread else None,
            "best_cost": round(cost) if cost else None,
            "needs_ceiling": needed,
        }

    return results


def _deliver(text):
    """Send the review to the OPERATOR, never to the subscriber channel.

    `TELEGRAM_CHAT_ID` is where signals go. A research summary posted there
    would be an internal note delivered to paying subscribers, so it is not used
    as a fallback -- deliberately, and this must stay that way. Set
    `MOVER_WATCH_TELEGRAM_CHAT_ID` to a private chat to get the review pushed;
    leave it unset and the file on disk is the only output.

    Never raises. This runs unattended from Task Scheduler, and a delivery
    failure must not lose the review that was already written to disk.
    """

    import os

    chat_id = (os.getenv("MOVER_WATCH_TELEGRAM_CHAT_ID") or "").strip()

    if not chat_id:
        return None

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

    if not token:
        return "MOVER_WATCH_TELEGRAM_CHAT_ID is set but TELEGRAM_BOT_TOKEN is not"

    if chat_id == (os.getenv("TELEGRAM_CHAT_ID") or "").strip():
        return "refused: that is the subscriber channel, not an operator chat"

    try:
        import requests

        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "<b>Mover watch review</b>\n<pre>" + text + "</pre>",
                "parse_mode": "HTML",
            },
            timeout=20,
        )
        return None if response.ok else f"telegram said {response.status_code}"
    except Exception as exc:
        return f"delivery failed: {str(exc)[:100]}"


def build_review():
    """The accumulated log as text. Returns None when nothing is logged yet."""

    files = sorted(LOG_DIR.glob("*.json"))

    if not files:
        return None

    verdicts = defaultdict(Counter)
    needs = defaultdict(list)
    seen_days = set()

    for path in files:
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        seen_days.add(payload.get("day"))
        for symbol, row in (payload.get("results") or {}).items():
            verdicts[symbol][row.get("verdict")] += 1
            if row.get("needs_ceiling"):
                needs[symbol].append(row["needs_ceiling"])

    if not verdicts:
        return None

    lines = [
        f"{len(seen_days)} sessions logged, {len(verdicts)} distinct movers",
        "",
        f"{'symbol':8}{'seen':>6}{'traded':>8}{'too wide':>10}"
        f"{'no setup':>10}{'needs':>8}",
        "-" * 50,
    ]

    ranked = sorted(
        verdicts.items(),
        key=lambda kv: -(kv[1]["CHAIN TOO WIDE"] + kv[1]["TOO EXPENSIVE"]),
    )

    for symbol, counts in ranked[:30]:
        total = sum(counts.values())
        ceiling = f"{min(needs[symbol]):g}%" if needs.get(symbol) else "-"
        lines.append(
            f"{symbol:8}{total:>6}{counts['TRADED']:>8}"
            f"{counts['CHAIN TOO WIDE']:>10}{counts['NO SETUP']:>10}{ceiling:>8}"
        )

    lines += [
        "",
        "'needs' is the LOWEST ceiling that would have bought it on any day",
        "logged. A symbol appearing often with a low needs is the case for a",
        "per-symbol spread exception -- and TRADE_QUALITY_PLAN 7.3a is the",
        "evidence against loosening globally. Bring the list, not one day.",
    ]

    return "\n".join(lines)


def review():
    """Print the review, keep a dated copy, and deliver it if configured."""

    text = build_review()

    if text is None:
        print(f"\n  nothing logged yet in {LOG_DIR}\n")
        return

    print("\n" + "\n".join("  " + line for line in text.splitlines()) + "\n")

    destination = LOG_DIR / "review" / f"{datetime.now():%Y-%m-%d}.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)
    print(f"  written to {destination}")

    problem = _deliver(text)

    if problem:
        print(f"  telegram: {problem}")

    print()


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=None, help="YYYY-MM-DD, default today")
    parser.add_argument("--top", type=int, default=5)
    # 15 rather than 5: a quarter of the quota for the same verdict, measured
    # on AAOI above. This runs nightly and unattended, so the default should
    # be the cheap one.
    parser.add_argument("--cadence", type=int, default=15)
    parser.add_argument("--min-move", type=float, default=MIN_MOVE_PCT)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the shortlist and stop, costing one request")
    parser.add_argument("--review", action="store_true",
                        help="summarise everything logged so far")
    args = parser.parse_args()

    if args.review:
        review()
        return

    day = args.day or datetime.now().strftime("%Y-%m-%d")

    # Scheduled daily, this fires on weekends and holidays too. Exiting quietly
    # is the right behaviour there: the snapshot would return the previous
    # session's numbers and log them under a date with no bars, quietly
    # corrupting the history the weekend review reads.
    if args.day is None and not _is_trading_day():
        print(f"\n  {day} is not a trading day -- nothing to do\n")
        return

    movers = fetch_movers(args.top, args.min_move)

    if not movers:
        print(f"\n  no movers cleared the filter "
              f"(>={args.min_move:g}%, ${MIN_PRICE:.0f}-{MAX_PRICE:.0f}, "
              f">${MIN_DOLLAR_VOLUME_M:.0f}M)\n")
        return

    print(f"\n  {len(movers)} movers on {day}, "
          f"outside the watchlist, priced ${MIN_PRICE:.0f}-{MAX_PRICE:.0f}, "
          f">${MIN_DOLLAR_VOLUME_M:.0f}M traded\n")
    print(f"  {'symbol':8}{'move':>9}{'price':>9}{'$vol M':>9}")
    print(f"  {'':-<35}")

    for row in movers:
        print(f"  {row['symbol']:8}{row['change_pct']:>+8.2f}%"
              f"{row['price']:>9.2f}{row['dollar_volume_m']:>9.0f}")

    if args.dry_run:
        print("\n  --dry-run, stopping before the chain walk\n")
        return

    symbols = [row["symbol"] for row in movers]
    print(f"\n  walking {len(symbols)} chains under the live rules "
          f"(this is the expensive part)\n", flush=True)

    results = check(symbols, day, args.cadence)

    print(f"\n  {'symbol':8}{'signals':>9}{'bought':>8}{'best spr':>10}"
          f"{'needs':>8}   verdict")
    print(f"  {'':-<72}")

    for row in movers:

        symbol = row["symbol"]
        outcome = results.get(symbol, {})
        spread = outcome.get("best_spread")
        needed = outcome.get("needs_ceiling")
        extra = ""

        if outcome.get("verdict") == "TRADED" and outcome.get("mean_pct") is not None:
            extra = f" ({outcome['mean_pct']:+.1f}% mean)"

        print(f"  {symbol:8}{outcome.get('signals', 0):>9}{outcome.get('bought', 0):>8}"
              f"{(f'{spread:.2f}%' if spread else '-'):>10}"
              f"{(f'{needed:g}%' if needed else '-'):>8}   "
              f"{outcome.get('verdict', '?')}{extra}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    destination = LOG_DIR / f"{day}.json"
    destination.write_text(json.dumps({
        "day": day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "filter": {
            "min_move_pct": args.min_move,
            "price": [MIN_PRICE, MAX_PRICE],
            "min_dollar_volume_m": MIN_DOLLAR_VOLUME_M,
        },
        "movers": movers,
        "results": results,
    }, indent=2))

    print(f"\n  logged to {destination}")
    print(f"  run `python tools/mover_watch.py --review` at the weekend\n")


if __name__ == "__main__":
    main()
