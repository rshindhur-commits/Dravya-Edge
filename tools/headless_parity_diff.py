"""S2.4 -- diff the dashboard-driven and headless-driven paper-trade ledgers.

Compares two `paper_trade_state.json`-shaped files: the dashboard's live state
and the headless caller's shadow state (written to a separate file via
`PAPER_TRADE_STATE_FILE_OVERRIDE`, see app/state/paper_trade_manager.py and
docs/specs/S2.1-headless-extraction-plan.md §7).

Usage:
    python tools/headless_parity_diff.py \\
        --live app/state/paper_trade_state.json \\
        --shadow app/state/paper_trade_state.headless_shadow.json

Read-only. Writes nothing. Exit code is 0 iff there are zero diffs, so this
is safe to use in an automated daily check.

⚠️ Before pointing this at a real parallel run, read
docs/specs/S2.4-parallel-run-procedure.md -- the headless process MUST be
launched with its own `TELEGRAM_ALERTS_ENABLED=false` and
`DB_WRITE_ENABLED=false`, or every shadow decision double-sends a real
Telegram message and double-writes to Neon. The state-file separation this
tool relies on does not, by itself, prevent that.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


COMPARE_FIELDS = [
    "symbol",
    "direction",
    "status",
    "entry_price",
    "stop_loss",
    "take_profit",
    "option_ticker",
    "close_price",
    "exit_reason",
    "r_multiple",
    "outcome",
    "holding_profile",
]

# Timestamps and IDs are allowed to differ by construction: opening the "same"
# trade a few seconds apart on two independent processes is expected, not a
# divergence. A close-timestamp gap wider than this is still reported, since
# it usually means the two paths disagreed on *when* to exit, not just that
# they both ran.
CLOSE_TIME_TOLERANCE_SECONDS = 120


def load_state(path):

    path = Path(path)

    if not path.exists():

        return {}

    with path.open("r", encoding="utf-8") as handle:

        return json.load(handle)


def _closed_at_seconds(trade):

    from datetime import datetime

    value = trade.get("closed_at_et") or trade.get("closed_at")

    if not value:

        return None

    try:

        return datetime.fromisoformat(str(value)).timestamp()

    except Exception:

        return None


def diff_trade(live_trade, shadow_trade):

    """Field-level diff between two trades believed to be the same trade.
    Returns a list of (field, live_value, shadow_value) mismatches.
    """

    mismatches = []

    for field in COMPARE_FIELDS:

        live_value = live_trade.get(field)
        shadow_value = shadow_trade.get(field)

        if live_value != shadow_value:

            mismatches.append((field, live_value, shadow_value))

    live_closed = _closed_at_seconds(live_trade)
    shadow_closed = _closed_at_seconds(shadow_trade)

    if live_closed is not None and shadow_closed is not None:

        gap = abs(live_closed - shadow_closed)

        if gap > CLOSE_TIME_TOLERANCE_SECONDS:

            mismatches.append((
                "closed_at (gap > tolerance)",
                live_trade.get("closed_at_et"),
                shadow_trade.get("closed_at_et"),
            ))

    return mismatches


def diff_states(live_state, shadow_state):

    """Returns a report dict: {"only_in_live": [...], "only_in_shadow": [...],
    "mismatched": {trade_key: [(field, live, shadow), ...]}, "matched": n}.
    """

    live_keys = set(live_state.keys())
    shadow_keys = set(shadow_state.keys())

    only_in_live = sorted(live_keys - shadow_keys)
    only_in_shadow = sorted(shadow_keys - live_keys)
    shared = sorted(live_keys & shadow_keys)

    mismatched = {}
    matched = 0

    for key in shared:

        mismatches = diff_trade(live_state[key], shadow_state[key])

        if mismatches:

            mismatched[key] = mismatches

        else:

            matched += 1

    return {
        "only_in_live": only_in_live,
        "only_in_shadow": only_in_shadow,
        "mismatched": mismatched,
        "matched": matched,
    }


def format_report(report):

    lines = []

    lines.append(
        f"matched: {report['matched']}  "
        f"only_in_live: {len(report['only_in_live'])}  "
        f"only_in_shadow: {len(report['only_in_shadow'])}  "
        f"mismatched: {len(report['mismatched'])}"
    )

    if report["only_in_live"]:

        lines.append("\nTrades the dashboard opened/closed that the headless path did not:")

        for key in report["only_in_live"]:

            lines.append(f"  - {key}")

    if report["only_in_shadow"]:

        lines.append("\nTrades the headless path opened/closed that the dashboard did not:")

        for key in report["only_in_shadow"]:

            lines.append(f"  - {key}")

    if report["mismatched"]:

        lines.append("\nTrades both paths have, with differing fields:")

        for key, mismatches in report["mismatched"].items():

            lines.append(f"  - {key}:")

            for field, live_value, shadow_value in mismatches:

                lines.append(f"      {field}: live={live_value!r}  shadow={shadow_value!r}")

    return "\n".join(lines)


def is_clean(report):

    return (
        not report["only_in_live"]
        and not report["only_in_shadow"]
        and not report["mismatched"]
    )


def main():

    parser = argparse.ArgumentParser(
        description="Diff the dashboard-driven and headless-driven paper trade ledgers (S2.4)."
    )
    parser.add_argument(
        "--live",
        default="app/state/paper_trade_state.json",
        help="Path to the dashboard's live paper_trade_state.json"
    )
    parser.add_argument(
        "--shadow",
        required=True,
        help="Path to the headless caller's shadow state file "
             "(PAPER_TRADE_STATE_FILE_OVERRIDE target)"
    )

    args = parser.parse_args()

    live_state = load_state(args.live)
    shadow_state = load_state(args.shadow)

    report = diff_states(live_state, shadow_state)

    print(format_report(report))

    sys.exit(0 if is_clean(report) else 1)


if __name__ == "__main__":

    main()
