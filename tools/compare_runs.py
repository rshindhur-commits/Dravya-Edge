"""Compare forward-replay runs, in dollars first.

Built after a cost-cap A/B that raised total R from +2.1 to +16.5 and doubled
the money lost. R is measured against the underlying's stop distance, so it
neither knows nor cares what the contract cost; a change that buys a bigger,
nearer-the-money option improves R while earning the same negative percentage
on twice the stake. Judging that change on R would have been a $4,000 mistake
over 21 days.

So the ordering here is deliberate: total P&L and return on capital deployed
come first, R comes after, and a run whose R and dollars disagree in sign is
called out by name rather than left for the reader to notice.

    python tools/compare_runs.py data/forward_runs/*.json
    python tools/compare_runs.py base.json variant.json --label base --label 0.25R

The first run given is the baseline; every other is differenced against it.
"""

import argparse
import collections
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

DEAD_MFE = 0.10


def number(value, default=0.0):

    try:

        return float(value)

    except (TypeError, ValueError):

        return default


def load(path):

    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))

    return data["trades"] if isinstance(data, dict) else data


def summarise(trades):

    r_values = [number(t.get("r_multiple")) for t in trades]
    spent = []
    made = []

    for trade in trades:

        entry = number(trade.get("option_entry_fill"))

        if entry <= 0:

            continue

        spent.append(entry * 100.0)
        made.append((number(trade.get("option_exit_fill")) - entry) * 100.0)

    if not spent:

        return None

    peaks = [number(t.get("mfe_r")) for t in trades]
    live = [t for t in trades if number(t.get("mfe_r")) > DEAD_MFE]
    dead = [t for t in trades if number(t.get("mfe_r")) <= DEAD_MFE]

    captured = None

    if live:

        peak = statistics.mean(number(t.get("mfe_r")) for t in live)
        kept = statistics.mean(number(t.get("r_multiple")) for t in live)
        captured = kept / peak if peak else None

    return {
        "trades": len(trades),
        "pnl": sum(made),
        "deployed": sum(spent),
        "roc": sum(made) / sum(spent),
        "per_trade": sum(made) / len(made),
        "mean_pct": statistics.mean(m / s * 100.0 for m, s in zip(made, spent)),
        "cash_win": sum(1 for v in made if v > 0) / len(made),
        "total_r": sum(r_values),
        "mean_r": statistics.mean(r_values),
        "r_win": sum(1 for v in r_values if v > 0) / len(r_values),
        "median_cost": statistics.median(spent),
        "dead_share": len(dead) / len(trades),
        "dead_mean_r": (
            statistics.mean(number(t.get("r_multiple")) for t in dead)
            if dead else None
        ),
        "capture": captured,
        "mean_peak": statistics.mean(peaks),
        "exits": collections.Counter(
            str(t.get("exit_rule") or "-") for t in trades
        ),
    }


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", help="run JSONs; the first is baseline")
    parser.add_argument("--label", action="append", default=None)
    args = parser.parse_args()

    labels = args.label or []
    summaries = []

    for index, path in enumerate(args.runs):

        trades = load(path)
        summary = summarise(trades)

        if summary is None:

            print(f"{path}: no priced trades, skipped")
            continue

        summary["label"] = (
            labels[index] if index < len(labels)
            else pathlib.Path(path).stem[:22]
        )
        summaries.append(summary)

    if not summaries:

        return

    base = summaries[0]

    def row(label, key, fmt, width=14):

        cells = "".join(
            fmt.format(s[key]).rjust(width) if s.get(key) is not None
            else "-".rjust(width)
            for s in summaries
        )
        print(f"   {label:24}{cells}")

    print()
    header = "".join(s["label"][:13].rjust(14) for s in summaries)
    print(f"   {'':24}{header}")
    print(f"   {'-' * (24 + 14 * len(summaries))}")

    print("   MONEY")
    row("total P&L $", "pnl", "{:+,.0f}")
    row("per trade $", "per_trade", "{:+,.1f}")
    row("capital deployed $", "deployed", "{:,.0f}")
    row("return on capital", "roc", "{:+.2%}")
    row("mean return %", "mean_pct", "{:+.1f}")
    row("cash win rate", "cash_win", "{:.0%}")

    print("\n   R")
    row("total R", "total_r", "{:+.1f}")
    row("mean R", "mean_r", "{:+.3f}")
    row("R win rate", "r_win", "{:.0%}")

    print("\n   SHAPE")
    row("trades", "trades", "{:,.0f}")
    row("median contract $", "median_cost", "{:,.0f}")
    row("never travelled", "dead_share", "{:.0%}")
    row("  their mean R", "dead_mean_r", "{:+.2f}")
    row("mean peak (MFE)", "mean_peak", "{:+.2f}")
    row("peak captured", "capture", "{:.0%}")

    if len(summaries) < 2:

        return

    print(f"\n   vs {base['label']}")
    print(f"   {'-' * (24 + 14 * len(summaries))}")

    for summary in summaries[1:]:

        print(f"\n   {summary['label']}")
        print(f"      P&L        {summary['pnl'] - base['pnl']:+,.0f} "
              f"(${base['pnl']:+,.0f} -> ${summary['pnl']:+,.0f})")
        print(f"      per trade  {summary['per_trade'] - base['per_trade']:+,.1f}")
        print(f"      capital    {summary['deployed'] / base['deployed'] - 1:+.0%}")
        print(f"      total R    {summary['total_r'] - base['total_r']:+.1f}")

        # The trap this tool exists for.
        r_better = summary["total_r"] > base["total_r"]
        cash_better = summary["pnl"] > base["pnl"]

        if r_better != cash_better:

            print(f"      *** R and dollars DISAGREE: R says "
                  f"{'better' if r_better else 'worse'}, money says "
                  f"{'better' if cash_better else 'worse'}. "
                  f"Trust the money.")

        elif cash_better:

            print(f"      both agree: better")

        else:

            print(f"      both agree: worse")

    print("\n   exit mix")
    rules = sorted(
        {rule for s in summaries for rule in s["exits"]},
        key=lambda rule: -base["exits"].get(rule, 0),
    )
    print(f"   {'':24}" + "".join(s["label"][:13].rjust(14) for s in summaries))

    for rule in rules[:10]:

        cells = "".join(
            f"{s['exits'].get(rule, 0):,}".rjust(14) for s in summaries
        )
        print(f"   {rule[:22]:24}{cells}")

    print()


if __name__ == "__main__":

    main()
