"""Read-only A/B of two risk configurations over an archived trading day.

Why this exists
---------------
`run_historical_regression()` answers "does current code still reproduce the day's
frozen truth?" It is a drift detector, and its default evaluator *replays* the
archived `Candidate Stop Price` / `Candidate Target Price` straight out of
`decision_payload`. That makes it blind to a risk-manager change: the stop it
compares is the stop the archive already recorded.

`reconstruct_trades(context, evaluator=...)` accepts a custom evaluator, and
`_snapshot_frames()` attaches each snapshot's `market_payload` (5m/15m/1h OHLCV
bars) to every row for exactly this purpose. This tool supplies an evaluator that
*recomputes* indicators, strategy, entry, and risk from those bars, so both arms
exercise the real production code and the only difference is `stop_anchor`.

Both arms are compared against each other, not against the frozen baseline: the
baseline was built with the replay evaluator, so comparing to it would measure
the evaluator swap rather than the code change.

Read-only. Never writes to the database, never freezes or mutates a baseline,
never touches paper state, Telegram, or Polygon.

    python tools/regression_ab.py --date 2026-07-29
    python tools/regression_ab.py --date 2026-07-29 --date 2026-07-28
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.indicators.technical_indicators import compute_indicators  # noqa: E402
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

ARMS = ("SWING", "STRUCTURE")
BAR_KEYS = {"5m": "bars_5m", "15m": "bars_15m", "1h": "bars_1h"}


def _bars_to_frame(bars, interval, symbol):
    """Rebuild an indicator-ready frame from archived OHLCV bars."""

    if not bars:
        return pd.DataFrame()

    frame = pd.DataFrame(bars)

    if frame.empty:
        return frame

    time_column = next(
        (c for c in ("timestamp", "time", "t", "Datetime", "index") if c in frame.columns),
        None,
    )
    if time_column:
        frame[time_column] = pd.to_datetime(frame[time_column], errors="coerce", utc=True)
        frame = frame.dropna(subset=[time_column]).set_index(time_column)
        try:
            frame.index = frame.index.tz_convert("America/New_York")
        except (TypeError, AttributeError):
            pass

    frame = frame.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
        "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume",
    })

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()

    return compute_indicators(frame.sort_index(), interval=interval, symbol=symbol)


def make_recomputing_evaluator(stop_anchor):
    """Evaluator that re-derives entry and risk from archived market bars."""

    stats = {"rows": 0, "no_bars": 0, "thin_bars": 0, "no_entry": 0, "risk_blocked": 0, "actionable": 0}

    def evaluate(row, _context):
        stats["rows"] += 1
        snapshot = row.get("__Regression Market Snapshot") or {}
        symbol = row.get("Symbol") or row.get("symbol") or "UNKNOWN"

        if not snapshot:
            stats["no_bars"] += 1
            return {"action": "WAIT"}

        df_5m = _bars_to_frame(snapshot.get(BAR_KEYS["5m"]), "5m", symbol)
        df_15m = _bars_to_frame(snapshot.get(BAR_KEYS["15m"]), "15m", symbol)

        if df_15m.empty or df_5m.empty:
            stats["thin_bars"] += 1
            return {"action": "WAIT"}

        analysis_15m = analyze_setup(df_15m)
        entry_setup = detect_entry(df_15m, analysis_15m, symbol=symbol)

        if not entry_setup or str(entry_setup.get("entry_type")) in {"NO_ENTRY", "NO_SETUP", "None", ""}:
            stats["no_entry"] += 1
            return {"action": "WAIT"}

        risk_setup = calculate_risk(
            df_15m,
            analysis_15m,
            entry_setup,
            stop_anchor=stop_anchor,
        )

        if not risk_setup or not risk_setup.get("trade_allowed"):
            stats["risk_blocked"] += 1
            return {"action": "WAIT"}

        signal = str(analysis_15m.get("signal") or "").upper()
        direction = "PUT" if "BEAR" in signal else "CALL"
        stats["actionable"] += 1

        return {
            "action": "ENTER_PAPER",
            "holding_profile": None,
            "setup": entry_setup.get("entry_type"),
            "entry": risk_setup.get("entry_price"),
            "stop": risk_setup.get("stop_loss"),
            "target": risk_setup.get("take_profit"),
            "direction": direction,
        }

    evaluate.stats = stats
    return evaluate


def run_arm(trading_day, stop_anchor):
    context = RegressionContext(
        trading_day=trading_day,
        snapshot_folder=_snapshot_folder(trading_day),
        baseline_folder=_baseline_folder(trading_day),
        results_folder=_results_folder(trading_day),
        current_strategy_version=f"ab-{stop_anchor.lower()}",
        baseline_version="frozen",
        readonly=True,
    )
    evaluator = make_recomputing_evaluator(stop_anchor)
    trades = reconstruct_trades(context, evaluator=evaluator)
    closed = trades[trades.get("status") == "CLOSED"] if not trades.empty else trades
    return {
        "arm": stop_anchor,
        "metrics": _metrics(closed),
        "open_trades": 0 if trades.empty else int((trades.get("status") == "OPEN").sum()),
        "funnel": dict(evaluator.stats),
        "trades": closed,
    }


def _print_day(trading_day, results):
    print(f"\n{'=' * 74}\n{trading_day}\n{'=' * 74}")

    print(f"\n  {'funnel':<18}" + "".join(f"{r['arm']:>14}" for r in results))
    for key in ("rows", "no_bars", "thin_bars", "no_entry", "risk_blocked", "actionable"):
        print(f"  {key:<18}" + "".join(f"{r['funnel'][key]:>14}" for r in results))

    print(f"\n  {'outcome':<18}" + "".join(f"{r['arm']:>14}" for r in results))
    for key, label in [
        ("trades", "closed trades"), ("wins", "wins"), ("losses", "losses"),
        ("win_rate", "win rate %"), ("total_r", "total R"),
        ("average_r", "average R"), ("profit_factor", "profit factor"),
    ]:
        cells = "".join(
            f"{('-' if r['metrics'].get(key) is None else r['metrics'][key]):>14}"
            for r in results
        )
        print(f"  {label:<18}{cells}")
    print(f"  {'still open':<18}" + "".join(f"{r['open_trades']:>14}" for r in results))

    base, proposed = results[0]["metrics"], results[1]["metrics"]
    delta_r = round((proposed.get("total_r") or 0) - (base.get("total_r") or 0), 2)
    delta_trades = (proposed.get("trades") or 0) - (base.get("trades") or 0)
    delta_win = round((proposed.get("win_rate") or 0) - (base.get("win_rate") or 0), 1)

    print(f"\n  STRUCTURE vs SWING: {delta_r:+.2f}R  "
          f"trades {delta_trades:+d}  win rate {delta_win:+.1f}%")
    if delta_r > 0 and delta_win >= 0:
        print("  -> STRUCTURE produced more R without a worse win rate on this day.")
    elif delta_r > 0:
        print("  -> STRUCTURE produced more R but a worse win rate; check trade count.")
    elif delta_r < 0:
        print("  -> STRUCTURE produced less R. Do not adopt on this evidence.")
    else:
        print("  -> No measurable difference on this day.")
    return delta_r


def archived_days(since=None):
    """Trading days that actually have snapshots, newest last."""

    from sqlalchemy import text

    from app.db.connection import get_engine

    sql = "select distinct trading_day from scanner_snapshot"
    params = {}

    if since:
        sql += " where trading_day >= :since"
        params["since"] = since

    engine = get_engine().execution_options(isolation_level="AUTOCOMMIT")

    with engine.connect() as connection:
        return [
            str(row[0])
            for row in connection.execute(text(sql + " order by 1"), params)
        ]


def resolve_days(args):

    if args.date:
        return args.date

    days = archived_days(args.since)

    if not days:
        print("No archived days found in scanner_snapshot."
              + (f" (since {args.since})" if args.since else ""))
        return []

    scope = f"since {args.since}" if args.since else "all archived days"
    print(f"Discovered {len(days)} archived day(s) [{scope}]: {', '.join(days)}")
    return days


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  # every archived day (what to run after two weeks of recording)\n"
            "  python tools/regression_ab.py --all\n\n"
            "  # only days from a start date onward\n"
            "  python tools/regression_ab.py --since 2026-07-29\n\n"
            "  # specific days\n"
            "  python tools/regression_ab.py --date 2026-07-29 --date 2026-07-28\n"
        ),
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--date", action="append",
                          help="a single trading day YYYY-MM-DD; repeat the flag per day")
    selector.add_argument("--all", action="store_true",
                          help="every trading day present in scanner_snapshot")
    selector.add_argument("--since", metavar="YYYY-MM-DD",
                          help="every archived trading day on or after this date")
    parser.add_argument("--csv", help="optional directory for per-arm trade CSVs")
    args = parser.parse_args()

    print("Read-only A/B. No baseline, paper state, or database row is modified.")
    print("Both arms recompute indicators, entry, and risk from archived market bars.")

    totals = {}
    for trading_day in resolve_days(args):
        try:
            results = [run_arm(trading_day, arm) for arm in ARMS]
        except Exception as exc:
            print(f"\n{trading_day}: FAILED -- {type(exc).__name__}: {exc}")
            continue

        totals[trading_day] = _print_day(trading_day, results)

        if args.csv:
            out = Path(args.csv)
            out.mkdir(parents=True, exist_ok=True)
            for result in results:
                if not result["trades"].empty:
                    path = out / f"ab_{trading_day}_{result['arm'].lower()}.csv"
                    result["trades"].to_csv(path, index=False)
                    print(f"  wrote {path}")

    if len(totals) > 1:
        print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
        for day, delta in totals.items():
            print(f"  {day}: {delta:+.2f}R")
        print(f"  net across {len(totals)} day(s): {sum(totals.values()):+.2f}R")

    print("\nAdopt only if the R difference is positive across multiple days and\n"
          "market regimes. A single day is not evidence.")


if __name__ == "__main__":
    main()
