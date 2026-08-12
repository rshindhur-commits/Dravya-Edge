"""Did the candidates we refused go on to win?

Nothing has ever been able to answer this. `Replay Outcome` is written only where
`risk_setup["trade_allowed"]` is true (`app/main.py`), so a candidate a gate
rejected never gets an outcome computed -- and the archive proves it: **NO_REPLAY
on 16,614 of 16,614 scanner_snapshot rows, every row ever written.**

Everything downstream inherits that hole:

* `candidate_outcomes.py` derives winner/loser by string-matching that column, so
  all 888 `candidate_outcome` rows are `became_neutral`.
* `candidate_evidence.winner` is false on all 2,600 rows.
* `learning_engine.build_feedback_loop` computes `winners_blocked` from that
  column, so it is structurally zero and `losses_prevented` is just the block
  count. Every rule scores positively, and the rule that blocks most ranks best.
  That is why LOW_RR sat at the top of rule ROI on 2026-08-11 -- the day it
  refused every trade.
* V2's `trades_compared` is 0.0 on all 15 shadow days.

So the app could not discover that a rule was too strict. This closes that by
replaying refused candidates against the bars that actually followed.

    python tools/resolve_candidate_outcomes.py --day 2026-08-11
    python tools/resolve_candidate_outcomes.py --backfill --write

Reads `scanner_snapshot`, which keeps 21 days and carries the entry, stop and
target of every candidate that got that far. Dry run unless `--write`.

**Intrabar ties resolve to the stop.** When one bar's range covers both the
target and the stop, the order inside it is unknowable, and assuming the target
came first is exactly how a replay manufactures an edge. `tools/ceiling_test.py`
took the same convention for the Gate 1 numbers; the two must agree or their
results cannot be compared.
"""

import argparse
import collections
import hashlib
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.backtesting.historical_market_data import fetch_bars  # noqa: E402
from app.db.connection import get_engine  # noqa: E402

LONG = {"CALL", "BULLISH", "LONG"}
SHORT = {"PUT", "BEARISH", "SHORT"}


def number(value):

    try:

        if value is None or str(value).strip().lower() in {"", "nan", "none"}:

            return None

        return float(value)

    except (TypeError, ValueError):

        return None


def candidate_id(trading_day, symbol, direction, setup):
    """Match `candidate_outcomes.build_candidate_outcomes` exactly.

    A different hash here would write rows that never join to the evidence they
    are supposed to resolve, and the mismatch would look like missing data.
    """

    return hashlib.sha256(
        f"{trading_day}|{symbol}|{direction}|{setup}".encode()
    ).hexdigest()[:24]


def load_candidates(day):
    """Earliest moment per (symbol, direction, setup) that carried a full triplet.

    A candidate is re-emitted on every scan while it persists, at drifting
    prices. The first sighting is the one to judge: it is when the app could
    first have acted, and later sightings are the same idea priced after the
    move it was trying to catch.
    """

    with get_engine().begin() as connection:

        rows = connection.execute(text("""
            SELECT symbol, scan_id, decision_payload
            FROM scanner_snapshot
            WHERE trading_day = CAST(:day AS DATE)
              AND decision_payload ->> 'Candidate Entry Price' IS NOT NULL
              AND decision_payload ->> 'Candidate Stop Price' IS NOT NULL
              AND decision_payload ->> 'Candidate Target Price' IS NOT NULL
            ORDER BY scan_id
        """), {"day": day}).mappings().all()

    seen = {}

    for row in rows:

        payload = row["decision_payload"] or {}
        direction = str(payload.get("Candidate Direction") or "").upper()
        setup = payload.get("Entry")

        if direction not in LONG | SHORT:

            continue

        entry = number(payload.get("Candidate Entry Price"))
        stop = number(payload.get("Candidate Stop Price"))
        target = number(payload.get("Candidate Target Price"))
        decided_at = pd.to_datetime(
            payload.get("Decision Candle Time ET"), utc=True, errors="coerce"
        )

        if None in (entry, stop, target) or pd.isna(decided_at):

            continue

        key = candidate_id(day, row["symbol"], direction, setup)

        if key in seen:

            continue

        seen[key] = {
            "candidate_id": key,
            "symbol": row["symbol"],
            "direction": direction,
            "setup": setup,
            "entry": entry,
            "stop": stop,
            "target": target,
            "decided_at": decided_at,
            "scan_id": row["scan_id"],
            "action_status": payload.get("Action Status"),
            "blocked_by": payload.get("Option Rejection Reason")
            or payload.get("Rejected Trade Reason"),
        }

    return list(seen.values())


def resolve(candidate, bars):
    """Which came first for this candidate -- its target or its stop.

    Only bars strictly after the decision candle count. The decision bar has
    already closed when the scanner sees it, so including it would resolve a
    candidate on the very move that produced it.
    """

    forward = bars[bars.index > candidate["decided_at"]]

    if forward.empty:

        return "NO_BARS", None

    long_side = candidate["direction"] in LONG

    for stamp, bar in forward.iterrows():

        if long_side:

            hit_target = bar["High"] >= candidate["target"]
            hit_stop = bar["Low"] <= candidate["stop"]

        else:

            hit_target = bar["Low"] <= candidate["target"]
            hit_stop = bar["High"] >= candidate["stop"]

        # The tie goes to the stop. See the module docstring: intrabar order is
        # unknowable and guessing it in the target's favour invents the edge.
        if hit_stop:

            return "STOP_FIRST", stamp

        if hit_target:

            return "TARGET_FIRST", stamp

    return "NEITHER", None


def outcome_rows(candidates, results):
    """Shape the resolutions the way `candidate_outcome` expects them."""

    rows = []

    for candidate, (verdict, stamp) in zip(candidates, results):

        won = verdict == "TARGET_FIRST"
        lost = verdict == "STOP_FIRST"

        rows.append({
            "candidate_id": candidate["candidate_id"],
            # Nothing here entered: these are the refused ones. An entered
            # candidate's real fill lives in `paper_trades` and is not guessed at.
            "entered": False,
            "profitable": None,
            "trend_developed": won,
            "target_hit": won,
            "stop_hit": lost,
            "became_winner": won,
            "became_loser": lost,
            "became_neutral": not won and not lost,
            "symbol": candidate["symbol"],
            "setup": candidate["setup"],
            "direction": candidate["direction"],
            "verdict": verdict,
            "resolved_at": str(stamp) if stamp is not None else None,
            "entry": candidate["entry"],
            "stop": candidate["stop"],
            "target": candidate["target"],
            "action_status": candidate["action_status"],
            "resolved_by": "resolve_candidate_outcomes",
        })

    return rows


def run_day(day, write=False):

    candidates = load_candidates(day)

    if not candidates:

        print(f"{day}: no candidates carrying a full entry/stop/target")
        return None

    frames = {}
    results = []

    for candidate in candidates:

        symbol = candidate["symbol"]

        if symbol not in frames:

            frames[symbol] = fetch_bars(
                symbol, day, day, multiplier=5, timespan="minute"
            )

        results.append(resolve(candidate, frames[symbol]))

    tally = collections.Counter(verdict for verdict, _ in results)
    resolved = tally["TARGET_FIRST"] + tally["STOP_FIRST"]

    print(
        f"{day}: {len(candidates)} candidates  "
        f"target_first={tally['TARGET_FIRST']}  "
        f"stop_first={tally['STOP_FIRST']}  "
        f"neither={tally['NEITHER']}  no_bars={tally['NO_BARS']}"
    )

    if resolved:

        print(
            f"          of {resolved} resolved, "
            f"{tally['TARGET_FIRST'] / resolved:.0%} reached target first"
        )

    rows = outcome_rows(candidates, results)

    if write:

        from app.db.candidate_outcome_repository import CandidateOutcomeRepository

        CandidateOutcomeRepository().batch_insert(rows)
        print(f"          wrote {len(rows)} rows to candidate_outcome")

    return rows


def archived_days():

    with get_engine().begin() as connection:

        return [
            str(row[0]) for row in connection.execute(text("""
                SELECT DISTINCT trading_day FROM scanner_snapshot
                WHERE decision_payload ->> 'Candidate Entry Price' IS NOT NULL
                ORDER BY trading_day
            """))
        ]


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="trading day, YYYY-MM-DD")
    parser.add_argument(
        "--backfill", action="store_true",
        help="every archived day carrying candidates",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="persist to candidate_outcome (otherwise dry run)",
    )
    args = parser.parse_args()

    if not args.day and not args.backfill:

        parser.error("pass --day or --backfill")

    days = archived_days() if args.backfill else [args.day]

    if not args.write:

        print("dry run -- nothing is written; pass --write to persist\n")

    total = collections.Counter()

    for day in days:

        rows = run_day(day, write=args.write) or []

        for row in rows:

            total["target" if row["target_hit"] else
                  "stop" if row["stop_hit"] else "neither"] += 1

    if len(days) > 1:

        resolved = total["target"] + total["stop"]
        print(
            f"\nacross {len(days)} sessions: target_first={total['target']} "
            f"stop_first={total['stop']} neither={total['neither']}"
        )

        if resolved:

            print(
                f"refused candidates that reached target first: "
                f"{total['target'] / resolved:.0%} of {resolved} resolved"
            )


if __name__ == "__main__":
    main()
