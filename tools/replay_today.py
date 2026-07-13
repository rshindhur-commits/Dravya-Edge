from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:

    sys.path.insert(0, str(ROOT_DIR))

from app.diagnostics import (  # noqa: E402
    build_entry_diagnostics_from_snapshot,
    classify_entry_gate_failure_stage,
    diagnostics_to_json,
    summarize_entry_diagnostics,
)


def _read_scanner_snapshot(path: Path) -> pd.DataFrame:

    suffix = path.suffix.lower()

    if suffix in {".xlsx", ".xls"}:

        return pd.read_excel(path)

    if suffix == ".csv":

        return pd.read_csv(path)

    raise ValueError(f"Unsupported input file type: {path.suffix}")


def _load_existing_diagnostics(row: dict):

    raw_payload = row.get("ENTRY_DIAGNOSTICS_JSON")

    if not raw_payload or str(raw_payload).strip().lower() in {"", "nan", "none"}:

        return None

    try:

        return json.loads(raw_payload)

    except Exception:

        return None


def replay_scanner_snapshot(path: Path) -> tuple[pd.DataFrame, dict]:

    snapshot = _read_scanner_snapshot(path)
    replay_rows = []

    for _, series in snapshot.iterrows():

        row = series.to_dict()
        diagnostics = _load_existing_diagnostics(row)
        source = "persisted_json"

        if diagnostics is None:

            diagnostics = build_entry_diagnostics_from_snapshot(row)
            source = "replayed_snapshot"

        replay_row = {
                "Symbol": row.get("Symbol") or row.get("symbol"),
                "Final Signal": row.get("Final Signal") or row.get("Signal"),
                "Action Status": row.get("Action Status") or row.get("action_status"),
                "Entry": row.get("Entry") or row.get("entry"),
                "Market Regime": row.get("Market Regime") or row.get("market_regime"),
            "ENTRY_GATE_FAILURE_STAGE": row.get("ENTRY_GATE_FAILURE_STAGE"),
                "ENTRY_SETUP_CANDIDATE": diagnostics.get("candidate_setup"),
                "ENTRY_READINESS": diagnostics.get("readiness"),
                "FAILED_ENTRY_CONDITIONS": ", ".join(diagnostics.get("failed_conditions") or []),
                "PASSED_ENTRY_CONDITIONS": ", ".join(diagnostics.get("passed_conditions") or []),
                "ENTRY_DECISION_TIMELINE": " -> ".join(diagnostics.get("timeline") or []),
                "REPLAY_SOURCE": source,
                "ENTRY_DIAGNOSTICS_JSON": diagnostics_to_json(diagnostics),
            }

        if not replay_row["ENTRY_GATE_FAILURE_STAGE"]:

            replay_row["ENTRY_GATE_FAILURE_STAGE"] = classify_entry_gate_failure_stage(
                {
                    **row,
                    **replay_row,
                }
            )

        replay_rows.append(replay_row)

    replay = pd.DataFrame(replay_rows)
    summary = summarize_entry_diagnostics(
        [
            {
                **row,
                "ENTRY_DIAGNOSTICS_JSON": row.get("ENTRY_DIAGNOSTICS_JSON"),
            }
            for row in replay_rows
        ]
    )
    return replay, summary


def build_replay_summary(replay: pd.DataFrame) -> pd.DataFrame:

    if replay is None or replay.empty:

        return pd.DataFrame(
            columns=[
                "Symbol",
                "Closest Setup",
                "Readiness",
                "Failed Conditions",
                "Passed Conditions",
                "Final Decision",
                "Gate Failure Stage",
                "First Failed Rule",
                "Recommendation",
                "Replay Source",
            ]
        )

    output = replay.copy()
    failed = output.get("FAILED_ENTRY_CONDITIONS", pd.Series("", index=output.index)).fillna("").astype(str)
    output["FIRST_FAILED_RULE"] = failed.apply(
        lambda value: value.split(",")[0].strip() if value.strip() else "None"
    )
    output["RECOMMENDATION"] = output["FIRST_FAILED_RULE"].apply(_recommendation_for_failure)

    return output.rename(
        columns={
            "ENTRY_SETUP_CANDIDATE": "Closest Setup",
            "ENTRY_READINESS": "Readiness",
            "FAILED_ENTRY_CONDITIONS": "Failed Conditions",
            "PASSED_ENTRY_CONDITIONS": "Passed Conditions",
            "Action Status": "Final Decision",
            "ENTRY_GATE_FAILURE_STAGE": "Gate Failure Stage",
            "FIRST_FAILED_RULE": "First Failed Rule",
            "RECOMMENDATION": "Recommendation",
            "REPLAY_SOURCE": "Replay Source",
        }
    )[
        [
            "Symbol",
            "Closest Setup",
            "Readiness",
            "Failed Conditions",
            "Passed Conditions",
            "Final Decision",
            "Gate Failure Stage",
            "First Failed Rule",
            "Recommendation",
            "Replay Source",
        ]
    ]


def _recommendation_for_failure(failure: str) -> str:

    failure = str(failure or "").upper()

    if failure in {"", "NONE"}:

        return "Would enter / no failed entry rule"

    if "RR" in failure or "RISK" in failure:

        return "Review stop/target distance"

    if "REL_VOLUME" in failure:

        return "Wait for volume confirmation"

    if "BODY_STRENGTH" in failure:

        return "Wait for stronger candle body"

    if "VWAP" in failure:

        return "Wait for VWAP confirmation"

    if "EMA" in failure:

        return "Wait for EMA alignment/trigger"

    if "SIGNAL" in failure:

        return "Wait for directional momentum"

    if "MISSING REPLAY INDICATORS" in failure:

        return "Run a fresh scanner with replay snapshots"

    return "Inspect setup diagnostics"


def replay_coverage(replay: pd.DataFrame, scanner_rows: int | None = None) -> dict:

    replay_rows = len(replay) if replay is not None else 0
    missing_indicators = 0

    if replay is not None and not replay.empty and "FAILED_ENTRY_CONDITIONS" in replay.columns:

        missing_indicators = int(
            replay["FAILED_ENTRY_CONDITIONS"]
            .astype(str)
            .str.contains("Missing replay indicators", na=False)
            .sum()
        )

    scanner_rows = scanner_rows if scanner_rows is not None else replay_rows

    return {
        "scanner_rows": scanner_rows,
        "replay_rows": replay_rows,
        "missing_indicators": missing_indicators,
        "partial_replay": missing_indicators,
        "coverage_pct": round((replay_rows / scanner_rows) * 100, 2) if scanner_rows else 0,
    }


def _print_report(replay: pd.DataFrame, summary: dict) -> None:

    for _, row in replay.iterrows():

        print("\n" + "=" * 72)
        print(f"Ticker: {row.get('Symbol')}")
        print(f"Analysis: {row.get('Final Signal')}")
        print(f"Market Regime: {row.get('Market Regime')}")
        print(f"Candidate: {row.get('ENTRY_SETUP_CANDIDATE')}")
        print(f"Readiness: {row.get('ENTRY_READINESS')}%")
        print(f"Action: {row.get('Action Status')}")
        print(f"Failed: {row.get('FAILED_ENTRY_CONDITIONS') or 'none'}")
        print(f"Passed: {row.get('PASSED_ENTRY_CONDITIONS') or 'none'}")
        print(f"Timeline: {row.get('ENTRY_DECISION_TIMELINE')}")
        print(f"Replay Source: {row.get('REPLAY_SOURCE')}")

    print("\nENTRY FAILURE SUMMARY")
    print("---------------------")

    failure_counts = summary.get("failure_counts") or {}

    if not failure_counts:

        print("No entry condition failures recorded")

    else:

        for failure, count in sorted(
            failure_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        ):

            print(f"{failure}: {count}")

    print("\nMARKET REGIME SUMMARY")
    print("---------------------")

    for regime, stats in sorted((summary.get("regime_summary") or {}).items()):

        print(
            f"{regime}: "
            f"candidates={stats.get('candidates', 0)} "
            f"bullish={stats.get('bullish_candidates', 0)} "
            f"bearish={stats.get('bearish_candidates', 0)} "
            f"generated={stats.get('generated', 0)} "
            f"top_failure={stats.get('top_failure') or 'NONE'}"
        )


def main() -> int:

    parser = argparse.ArgumentParser(
        description="Replay entry diagnostics from a saved scanner_output CSV/XLSX snapshot."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to scanner_output CSV/XLSX file.",
    )
    parser.add_argument(
        "--output",
        help="Optional CSV path for replay output.",
    )
    parser.add_argument(
        "--summary-output",
        help="Optional CSV path for concise replay summary. Defaults next to --output as offline_replay_summary.csv.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Skip console ticker-by-ticker report.",
    )
    args = parser.parse_args()
    input_path = Path(args.input)

    if not input_path.exists():

        raise FileNotFoundError(input_path)

    scanner_rows = len(_read_scanner_snapshot(input_path))
    replay, summary = replay_scanner_snapshot(input_path)
    concise_summary = build_replay_summary(replay)
    coverage = replay_coverage(replay, scanner_rows=scanner_rows)

    if args.output:

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        replay.to_csv(output_path, index=False)
        print(f"Replay output written to {output_path}")

        summary_path = Path(args.summary_output) if args.summary_output else output_path.with_name("offline_replay_summary.csv")
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        concise_summary.to_csv(summary_path, index=False)
        print(f"Replay summary written to {summary_path}")

    elif args.summary_output:

        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        concise_summary.to_csv(summary_path, index=False)
        print(f"Replay summary written to {summary_path}")

    if not args.quiet:

        _print_report(replay, summary)

    print(
        "\nREPLAY COVERAGE "
        f"scanner_rows={coverage['scanner_rows']} "
        f"replay_rows={coverage['replay_rows']} "
        f"missing_indicators={coverage['missing_indicators']} "
        f"partial_replay={coverage['partial_replay']} "
        f"coverage_pct={coverage['coverage_pct']}"
    )

    if coverage["missing_indicators"]:

        print(
            "\nReplay was partial: "
            f"{coverage['missing_indicators']} rows are missing replay indicator columns. "
            "Run a fresh scanner after the latest diagnostics changes to capture replay-ready snapshots."
        )

    return 0


if __name__ == "__main__":

    raise SystemExit(main())