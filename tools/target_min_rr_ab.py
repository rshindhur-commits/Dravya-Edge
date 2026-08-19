"""Does extending the target to a minimum R actually get the target REACHED?

    python tools/target_min_rr_ab.py --all
    python tools/target_min_rr_ab.py --date 2026-08-13

`TARGET_MIN_RR` has been committed and switched off since 2026-08-13, awaiting
exactly this. It is the only lever that addresses a structural refusal:

## The arithmetic it exists to fix

Every target in `calculate_risk` is an absolute distance -- 1.8 ATR for
EMA_PULLBACK, `target_atr_multiplier` elsewhere -- while the stop floats with
structure. RR therefore reduces to `1.8 / stop_in_ATR`, and clearing the 2.0 bar
in `SCANNER_GATE_MIN_RR` needs a stop under 0.9 ATR. That only happens when price
is sitting on the EMA.

It shows up in the decision ledger as a value pinned to the decimal. AMZN on
2026-08-04 was refused `RR_BELOW_THRESHOLD` on eleven consecutive scans from
09:46 to 11:56 with entry, stop and target all moving and `rr` reading **exactly
1.80 every time**. Those setups could not have passed at any hour of that day.
The gate was not mistiming them; it was arithmetically unable to admit them.

## Why this is not free

Moving a target further away converts a refusal into a position that must now
travel further to pay. The stated failure mode has a named instance: NFLX
2026-08-13 12:37, entry 77.29, stop 76.86, target 77.96 (RR 1.56). At
`TARGET_MIN_RR=2.0` the target becomes 78.15 -- and the stop is taken out at
13:40, hours before 78.15 is touched. A refusal became a full -1.00R.

So the pass criterion is **not** "more trades" and **not** "better RR" -- the RR
is a number the target was just adjusted to satisfy, which is why the extension
is capped by `TARGET_MAX_REWARD_ATR`. The criterion is that the trades this
admits are profitable in premium, and that it survives dropping its best few.

## Method

Both arms recompute indicators, entry and risk from the archived market bars, so
the real production `calculate_risk` runs in each; the only difference is the
environment variable. The scanner's RR gate is then applied at
`SCANNER_GATE_MIN_RR`, which is the point -- an arm that manufactures RR admits
candidates the shipped arm refuses, and that difference is the entire effect.

Exits run through the live exit engine, not to stop-or-target, because a
counterfactual entry judged against its own fixed stop ignores that the
breakeven move rewrites it.

Days are split into two halves and reported separately. A lever that only wins
on the half containing the case that motivated it has told us nothing.

Read-only: no baseline, paper row, database write, Telegram or Polygon call.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_float_env  # noqa: E402
from app.regression.historical_scanner import (  # noqa: E402
    RegressionContext,
    _baseline_folder,
    _metrics,
    _results_folder,
    _snapshot_folder,
    reconstruct_trades,
)
from app.risk.risk_manager import calculate_risk  # noqa: E402
from app.strategies.entry_engine import detect_entry  # noqa: E402
from app.strategies.momentum_strategy import analyze_setup  # noqa: E402
from tools.regression_ab import _bars_to_frame, resolve_days  # noqa: E402

# 0.0 is the shipped value and must stay first: every delta is measured from it.
ARMS = (0.0, 2.0, 2.2)


def make_evaluator(target_min_rr, min_rr):
    """Re-derive entry and risk from archived bars under one TARGET_MIN_RR."""

    stats = {
        "rows": 0, "no_bars": 0, "no_entry": 0,
        "risk_blocked": 0, "rr_refused": 0, "actionable": 0,
    }

    def evaluate(row, _context):
        stats["rows"] += 1
        snapshot = row.get("__Regression Market Snapshot") or {}
        symbol = row.get("Symbol") or row.get("symbol") or "UNKNOWN"

        if not snapshot:
            stats["no_bars"] += 1
            return {"action": "WAIT"}

        df_5m = _bars_to_frame(snapshot.get("bars_5m"), "5m", symbol)
        df_15m = _bars_to_frame(snapshot.get("bars_15m"), "15m", symbol)

        if df_15m.empty or df_5m.empty:
            stats["no_bars"] += 1
            return {"action": "WAIT"}

        analysis_15m = analyze_setup(df_15m)
        entry_setup = detect_entry(df_15m, analysis_15m, symbol=symbol)

        if not entry_setup or str(entry_setup.get("entry_type")) in {
            "NO_ENTRY", "NO_SETUP", "None", ""
        }:
            stats["no_entry"] += 1
            return {"action": "WAIT"}

        # calculate_risk reads TARGET_MIN_RR from the environment on every call,
        # so the arm is set here rather than passed -- the production path has no
        # parameter for it, and inventing one would stop this exercising the real
        # code.
        previous = os.environ.get("TARGET_MIN_RR")
        os.environ["TARGET_MIN_RR"] = str(target_min_rr)

        try:
            risk_setup = calculate_risk(df_15m, analysis_15m, entry_setup)
        finally:
            if previous is None:
                os.environ.pop("TARGET_MIN_RR", None)
            else:
                os.environ["TARGET_MIN_RR"] = previous

        if not risk_setup or not risk_setup.get("trade_allowed"):
            stats["risk_blocked"] += 1
            return {"action": "WAIT"}

        entry = risk_setup.get("entry_price")
        stop = risk_setup.get("stop_loss")
        target = risk_setup.get("take_profit")

        # The gate the scanner runs. Without it both arms take identical trades
        # and only the target level differs, which measures half the change.
        if None not in (entry, stop, target) and entry != stop:
            if abs(target - entry) / abs(entry - stop) < min_rr:
                stats["rr_refused"] += 1
                return {"action": "WAIT"}

        signal = str(analysis_15m.get("signal") or "").upper()
        stats["actionable"] += 1

        return {
            "action": "ENTER_PAPER",
            "holding_profile": None,
            "setup": entry_setup.get("entry_type"),
            "entry": entry,
            "stop": stop,
            "target": target,
            "direction": "PUT" if "BEAR" in signal else "CALL",
        }

    evaluate.stats = stats
    return evaluate


def run_arm(trading_day, target_min_rr, min_rr):
    context = RegressionContext(
        trading_day=trading_day,
        snapshot_folder=_snapshot_folder(trading_day),
        baseline_folder=_baseline_folder(trading_day),
        results_folder=_results_folder(trading_day),
        current_strategy_version=f"tminrr-{target_min_rr}",
        baseline_version="frozen",
        readonly=True,
    )
    evaluator = make_evaluator(target_min_rr, min_rr)
    trades = reconstruct_trades(context, evaluator=evaluator)
    closed = trades[trades.get("status") == "CLOSED"] if not trades.empty else trades

    return {
        "arm": target_min_rr,
        "metrics": _metrics(closed),
        "funnel": dict(evaluator.stats),
        "trades": closed,
    }


def _net_pct(trades):
    """Premium return, which is what the account actually receives."""

    if trades is None or trades.empty:
        return []

    values = pd.to_numeric(
        trades.get("option_pnl_pct_net", pd.Series(dtype=float)), errors="coerce"
    ).dropna()
    return [float(v) for v in values]


def _report(label, results):
    print("\n" + "=" * 78)
    print(label)
    print("=" * 78)

    header = "".join("TARGET_MIN_RR={0}".format(a).rjust(18) for a in ARMS)
    print("\n  " + " " * 22 + header)

    for key, title in (
        ("actionable", "entries admitted"),
        ("rr_refused", "refused on RR"),
        ("risk_blocked", "refused by risk mgr"),
    ):
        print("  " + title.ljust(22)
              + "".join(str(r["funnel"][key]).rjust(18) for r in results))

    for key, title in (
        ("trades", "closed trades"),
        ("win_rate", "win rate %"),
        ("total_r", "total R"),
        ("average_r", "mean R"),
    ):
        print("  " + title.ljust(22)
              + "".join(str(r["metrics"][key]).rjust(18) for r in results))

    # R flatters a book whose losses are wide, so premium sits beside it and the
    # verdict is read off this line, not off total R.
    print("  " + "priced trades".ljust(22)
          + "".join(str(len(_net_pct(r["trades"]))).rjust(18) for r in results))
    print("  " + "total premium %".ljust(22)
          + "".join("{0:+.1f}".format(sum(_net_pct(r["trades"]))).rjust(18)
                    for r in results))

    for r in results:
        nets = _net_pct(r["trades"])

        if not nets:
            continue

        trimmed = sorted(nets)[:-5] if len(nets) > 5 else []
        line = "\n  TARGET_MIN_RR={0}:  mean premium {1:+.2f}%".format(
            r["arm"], statistics.mean(nets)
        )

        if trimmed:
            line += "   without its best 5: {0:+.2f}%".format(
                statistics.mean(trimmed)
            )
        else:
            line += "   (too few to trim)"

        print(line)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--date", action="append")
    selector.add_argument("--all", action="store_true")
    selector.add_argument("--since", metavar="YYYY-MM-DD")
    args = parser.parse_args()

    min_rr = get_float_env("SCANNER_GATE_MIN_RR", 2.0)
    print("Read-only A/B. Nothing is written.")
    print("RR gate applied to both arms: SCANNER_GATE_MIN_RR = {0}".format(min_rr))

    per_day = {}

    for trading_day in resolve_days(args):

        try:
            per_day[trading_day] = [
                run_arm(trading_day, arm, min_rr) for arm in ARMS
            ]
        except Exception as exc:
            print("  {0}: FAILED -- {1}: {2}".format(
                trading_day, type(exc).__name__, exc
            ))
            continue

        print("  {0}: ".format(trading_day) + "  ".join(
            "{0}->{1}t/{2:+.1f}R".format(
                a, r["metrics"]["trades"], r["metrics"]["total_r"]
            )
            for a, r in zip(ARMS, per_day[trading_day])
        ))

    if not per_day:
        return

    ordered = sorted(per_day)
    half = len(ordered) // 2
    halves = [
        ("FIRST HALF  {0} .. {1}".format(ordered[0], ordered[half - 1]),
         ordered[:half]),
        ("SECOND HALF {0} .. {1}".format(ordered[half], ordered[-1]),
         ordered[half:]),
        ("ALL {0} DAYS".format(len(ordered)), ordered),
    ]

    for label, days_in in halves:
        merged = []

        for index, arm in enumerate(ARMS):
            frames = [per_day[d][index]["trades"] for d in days_in
                      if not per_day[d][index]["trades"].empty]
            funnel = {
                key: sum(per_day[d][index]["funnel"][key] for d in days_in)
                for key in ("actionable", "rr_refused", "risk_blocked")
            }
            trades = pd.concat(frames) if frames else pd.DataFrame()
            merged.append({
                "arm": arm, "funnel": funnel,
                "metrics": _metrics(trades), "trades": trades,
            })

        _report(label, merged)

    print("\nAdopt only if the premium line is positive on BOTH halves and")
    print("survives dropping its best 5 trades. More entries at a worse mean is")
    print("the failure this lever is most likely to produce.")


if __name__ == "__main__":
    main()
