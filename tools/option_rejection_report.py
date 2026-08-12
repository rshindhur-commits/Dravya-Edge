"""What the option liquidity walk rejected on a trading day, and at what number.

The question this exists to answer is the one 2026-08-03 could not: when a
contract is refused, was the threshold set too high, or was the selector
reaching for a strike that deserved refusing?

That day recorded 18,026 attempts across 242 rejected rows and not one carried
an open interest, a cost or a delta -- `_select_liquid_option_from_bundle` built
each attempt from a fixed seven keys and dropped the evidence
`evaluate_option_liquidity` had attached. With that fixed, every attempt names
its contract and the threshold it was measured against, so the counterfactuals
below are reads rather than estimates.

Two cautions the 2026-08-03 analysis had to learn the hard way:

* **`Option Rejection Reason` is the last attempt's reason, not the cause.** It
  said "Low open interest, 162" while 209 of 242 rows had hit
  OPTION_TOO_EXPENSIVE along the way. Always count per attempt.
* **Rows are not opportunities.** 2,990 rows is 26 symbols x 115 scans; a symbol
  blocked all day counts 115 times. The symbol columns are the honest ones.

* **Read `tradeable`, not `by label`.** The threshold tables print both. A code
  is only an attempt's *first* failure -- the filter short-circuits -- so
  `by label` counts contracts that were never measured against the later bars,
  and it inflates the earliest gate most. On 2026-08-11 the OI floor looked
  worth 1,982 recovered contracts at 250 and was worth 0: they failed cost,
  spread and volume too. Thresholds come from the day's own
  `scanner_runs.payload->config`, never from the local `.env`, which on that
  same day held a spread ceiling of 6.0% against the 2.0% actually enforced.

    python tools/option_rejection_report.py --day 2026-08-04

Reads the database. Days before the evidence fix land in the `unknown` column
rather than being silently counted as passing.
"""

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

# What the thresholds fall back to only when a day recorded no config at all.
FALLBACK_CONFIG = {
    "option_min_open_interest": 500,
    "option_min_volume": 100,
    "option_max_spread_pct": 2.0,
    "option_max_contract_cost": 500.0,
    "option_min_contract_cost": 100.0,
    "option_allow_0dte": False,
    "option_allow_1dte": False,
}

COST_CAPS = (500, 750, 1000, 1200, 1500, 2000)
OI_FLOORS = (100, 250, 500, 750, 1000)


def number(value):

    try:

        if value is None or str(value).strip().lower() in {"", "nan", "none"}:

            return None

        return float(value)

    except (TypeError, ValueError):

        return None


def attempts_of(payload):

    raw = payload.get("Option Liquidity Attempts")

    if not raw:

        return []

    if isinstance(raw, str):

        try:

            raw = json.loads(raw)

        except json.JSONDecodeError:

            return []

    return raw if isinstance(raw, list) else []


def load_config(day):
    """The thresholds that day actually enforced, from its own scan records.

    Never read these from `settings`/`.env`: a local checkout is not what the
    worker ran. On 2026-08-11 the local `.env` carried
    `option_max_spread_pct=6.0` while every scan that day enforced 2.0, which
    let 45 contracts count as recoverable that the live filter had refused.
    `scanner_runs.payload->config` is the record of what was in force.
    """

    with get_engine().begin() as connection:

        row = connection.execute(text("""
            SELECT payload -> 'config' AS config FROM scanner_runs
            WHERE payload ->> 'trading_day' = :day
              AND payload -> 'config' IS NOT NULL
            ORDER BY started_at DESC LIMIT 1
        """), {"day": day}).mappings().first()

    config = dict(FALLBACK_CONFIG)

    if row and row["config"]:

        config.update(
            {k: v for k, v in row["config"].items() if v is not None}
        )

        return config, True

    return config, False


def load(day):

    with get_engine().begin() as connection:

        return [
            row["decision_payload"] or {}
            for row in connection.execute(text("""
                SELECT decision_payload FROM scanner_snapshot
                WHERE trading_day = CAST(:day AS DATE)
                ORDER BY scan_id, symbol
            """), {"day": day}).mappings()
        ]


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, help="trading day, YYYY-MM-DD")
    args = parser.parse_args()

    config, config_recorded = load_config(args.day)
    rows = load(args.day)

    if not rows:

        print(f"no scanner_snapshot rows for {args.day}")
        return

    rejected = [row for row in rows if row.get("Option Rejection Reason")]
    symbols = {str(row.get("Symbol")) for row in rows}

    print(f"{args.day}: {len(rows)} rows, {len(symbols)} symbols, "
          f"{len(rejected)} rejected")

    if config_recorded:

        print(f"   thresholds as enforced that day: "
              f"OI>={config['option_min_open_interest']} "
              f"vol>={config['option_min_volume']} "
              f"spread<={config['option_max_spread_pct']}% "
              f"cost {config['option_min_contract_cost']:.0f}-"
              f"{config['option_max_contract_cost']:.0f}\n")

    else:

        # Scans only began recording their own config on 2026-08-11. Earlier
        # days are re-tested against assumed thresholds, so the `tradeable`
        # column is an estimate -- and a wrong assumption moves it a long way.
        # Check CONFIG_CHANGELOG.md for what was in force before trusting it.
        print(f"   !! {args.day} recorded no config; assuming "
              f"OI>={config['option_min_open_interest']} "
              f"vol>={config['option_min_volume']} "
              f"spread<={config['option_max_spread_pct']}% "
              f"cost {config['option_min_contract_cost']:.0f}-"
              f"{config['option_max_contract_cost']:.0f}")
        print("      'tradeable' below is an estimate -- "
              "confirm against CONFIG_CHANGELOG.md\n")

    # ------------------------------------------------------------ per attempt
    per_attempt = collections.Counter()
    rows_hit = collections.Counter()
    symbols_hit = collections.defaultdict(set)
    evidence_seen = 0
    total_attempts = 0

    for row in rejected:

        symbol = str(row.get("Symbol"))
        codes = []

        for attempt in attempts_of(row):

            total_attempts += 1
            code = str(attempt.get("code") or "?")
            codes.append(code)

            if attempt.get("open_interest") is not None:

                evidence_seen += 1

        per_attempt.update(codes)

        for code in set(codes):

            rows_hit[code] += 1
            symbols_hit[code].add(symbol)

    print(f"== {total_attempts} attempts, "
          f"{evidence_seen} carrying contract evidence "
          f"({evidence_seen / max(total_attempts, 1):.0%}) ==")

    if evidence_seen == 0:

        print("   this day predates the evidence fix -- "
              "the counterfactuals below cannot be computed\n")

    print(f"\n   {'code':34}{'attempts':>10}{'rows':>7}{'symbols':>9}")

    for code, count in per_attempt.most_common(12):

        print(f"   {code[:32]:34}{count:>10}{rows_hit[code]:>7}"
              f"{len(symbols_hit[code]):>9}")

    if evidence_seen == 0:

        return

    # ------------------------------------------------------- counterfactuals
    # A contract only becomes tradeable if it clears every bar at once. The
    # `code` on an attempt is only its FIRST failure: `evaluate_option_liquidity`
    # is a short-circuit chain (OI -> volume -> spread -> DTE -> quality ->
    # cost), so a contract labelled LOW_OPEN_INTEREST was never measured against
    # the bars after it. Counting by label alone therefore assumes an all-clear
    # that was never tested, and it inflates the earliest gate the most.
    #
    # 2026-08-11 is the worked example: the label-only count said 1,982 of 8,036
    # OI rejections were recoverable at floor 250, across 18 symbols. Re-testing
    # the other bars put the honest number at 0, at every floor down to 100 --
    # they died on cost, spread and volume as well. Relaxing OI would have
    # bought nothing.
    #
    # So each row below moves one threshold and re-tests every other bar this
    # evidence can speak to. Quality score is not carried on an attempt, so it
    # cannot be re-tested; `clears_others` says so rather than assuming a pass,
    # which keeps these counts an upper bound on what a change would recover.
    def clears_others(attempt, ignore):

        checks = {
            "oi": (number(attempt.get("open_interest")),
                   config["option_min_open_interest"]),
            "volume": (number(attempt.get("volume")),
                       config["option_min_volume"]),
        }

        for name, (value, floor) in checks.items():

            if name == ignore:

                continue

            if value is None or value < floor:

                return False

        if ignore != "spread":

            spread = number(attempt.get("spread_pct"))

            if spread is None or spread > config["option_max_spread_pct"]:

                return False

        if ignore != "cost":

            cost = number(attempt.get("contract_cost"))

            if (
                cost is None
                or cost > config["option_max_contract_cost"]
                or cost < config["option_min_contract_cost"]
            ):

                return False

        # `option_min_dte`/`option_max_dte` only score in `contract_ranker`;
        # the sole hard expiry gates are the 0DTE and 1DTE blocks.
        dte = number(attempt.get("dte"))

        if dte is None:

            return False

        if dte <= 0 and not config["option_allow_0dte"]:

            return False

        if dte <= 1 and not config["option_allow_1dte"]:

            return False

        return True

    too_expensive = []
    low_oi = []

    for row in rejected:

        symbol = str(row.get("Symbol"))

        for attempt in attempts_of(row):

            code = str(attempt.get("code") or "")
            cost = number(attempt.get("contract_cost"))
            open_interest = number(attempt.get("open_interest"))

            if code == "OPTION_TOO_EXPENSIVE" and cost is not None:

                too_expensive.append(
                    (symbol, cost, clears_others(attempt, "cost"))
                )

            if code == "LOW_OPEN_INTEREST" and open_interest is not None:

                low_oi.append(
                    (symbol, open_interest, clears_others(attempt, "oi"))
                )

    def counterfactual(title, rows, thresholds, label, passes):

        print(f"\n== {len(rows)} contracts refused on {title} ==")
        print(f"   {label:>8}{'by label':>11}{'tradeable':>11}{'symbols':>9}")

        for threshold in thresholds:

            moved = [r for r in rows if passes(r[1], threshold)]
            real = [r for r in moved if r[2]]

            print(f"   {threshold:>8}{len(moved):>11}{len(real):>11}"
                  f"{len({s for s, _, _ in real}):>9}")

    # Cost is the last gate in the chain, so an OPTION_TOO_EXPENSIVE contract
    # has already cleared everything before it -- here `by label` and
    # `tradeable` should agree, and a gap means an evidence field went missing.
    if too_expensive:

        counterfactual("cost", too_expensive, COST_CAPS, "cap",
                       lambda value, cap: value <= cap)

    if low_oi:

        counterfactual("open interest", low_oi, OI_FLOORS, "floor",
                       lambda value, floor: value >= floor)

    # ------------------------------------------- what was blocked by cost only
    # The useful ceiling on the cost cap: past the point where the cheaper
    # contracts fail the quality bar anyway, raising it buys risk and nothing
    # else. 2026-08-03 put that ceiling near $1,500.
    print("\n== cheapest contract per symbol that failed ONLY on cost ==")
    cheapest = {}

    for row in rejected:

        symbol = str(row.get("Symbol"))

        for attempt in attempts_of(row):

            if str(attempt.get("code")) != "OPTION_TOO_EXPENSIVE":

                continue

            cost = number(attempt.get("contract_cost"))

            if cost is None:

                continue

            if symbol not in cheapest or cost < cheapest[symbol][0]:

                cheapest[symbol] = (cost, attempt.get("ticker"),
                                    number(attempt.get("delta")))

    print(f"   {'sym':8}{'cheapest':>10}{'delta':>8}  ticker")

    for symbol, (cost, ticker, delta) in sorted(
        cheapest.items(), key=lambda item: item[1][0]
    ):

        print(f"   {symbol:8}{cost:>10.0f}"
              f"{('-' if delta is None else f'{delta:.2f}'):>8}  {ticker or '-'}")


if __name__ == "__main__":

    main()
