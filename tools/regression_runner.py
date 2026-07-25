from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.regression import run_historical_regression


def _print_metrics(label, metrics):
    print(f"\n{label}")
    print(f"Trades      {metrics['trades']}")
    print(f"Wins        {metrics['wins']}")
    print(f"Losses      {metrics['losses']}")
    print(f"Win Rate    {metrics['win_rate']:.1f}%")
    print(f"Average R   {metrics['average_r']:.2f}")
    print(f"Total R     {metrics['total_r']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Run read-only Historical Scanner Regression")
    parser.add_argument("--date", required=True, help="Trading day in YYYY-MM-DD format")
    parser.add_argument("--strategy-version", default="current", help="Label for the current evaluator")
    args = parser.parse_args()

    summary = run_historical_regression(
        args.date,
        current_strategy_version=args.strategy_version,
    )
    comparison = summary["comparison"]
    print("=" * 56)
    print("Historical Scanner Regression")
    print(f"Trading Day  {summary['trading_day']}")
    print("=" * 56)
    _print_metrics("Baseline", summary["baseline"])
    _print_metrics("Current Code", summary["current"])
    print("\nDifference")
    print(f"New Trades      {len(comparison['new'])}")
    print(f"Removed Trades  {len(comparison['removed'])}")
    print(f"Changed Trades  {len(comparison['changed'])}")
    print(f"Net Gain        {summary['net_gain_r']:+.2f}R")
    print("\nVerdict")
    print(summary["verdict"])
    print(f"\nArtifacts: {summary['context']['results_folder']}")


if __name__ == "__main__":
    main()