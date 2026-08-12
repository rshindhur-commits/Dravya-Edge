"""What each gate costs and earns, measured in the option, not the underlying.

Every number reported on 2026-08-12 -- the 29.5% hit rate, the 0.000R
expectancy, the per-gate comparisons, the conclusion that the candidate pool held
no edge -- was computed from whether the underlying reached its target or its
stop. The strategy does not buy the underlying. It buys a contract, crosses the
spread twice and pays theta, and those numbers said nothing about any of that.

The same day showed how far apart the two answers sit:

    SPCX  underlying TARGET_FIRST   option  +22.26%
    NVDA  underlying STOP_FIRST     option  -19.83%
    PLTR  underlying STOP_FIRST     option   -4.00%

Reaching the target is not the same as making money and missing it is not the
same as losing it. A gate that looks protective on underlying R can be refusing
the only trades that pay.

So this reads `option_leg_replay` -- honest fills, ask to enter and bid to exit --
and reports each gate by what the account would actually have felt.

    python tools/gate_economics.py
    python tools/gate_economics.py --since 2026-08-01

Only priced candidates appear. Coverage is printed first, because a gate scored
on three contracts is not scored.
"""

import argparse
import pathlib
import statistics
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.connection import get_engine  # noqa: E402

QUERY = """
SELECT o.trading_day::text AS day,
       o.symbol, o.direction, o.verdict,
       o.option_return_pct, o.underlying_rr, o.contract_cost,
       (s.payload ->> 'setup') ::float AS setup_pct,
       s.payload ->> 'gate' AS gate
FROM option_leg_replay o
LEFT JOIN LATERAL (
    SELECT jsonb_build_object(
        'setup', ss.decision_payload ->> 'Setup %',
        'gate',  coalesce(ss.decision_payload ->> 'Rejected Trade Reason',
                          ss.decision_payload ->> 'Action Reason', 'UNKNOWN')
    ) AS payload
    FROM scanner_snapshot ss
    WHERE ss.trading_day = o.trading_day
      AND ss.symbol = o.symbol
      AND ss.decision_payload ->> 'Candidate Target Price' IS NOT NULL
    ORDER BY ss.scan_id
    LIMIT 1
) s ON TRUE
WHERE o.trading_day >= CAST(:since AS DATE)
"""


def summarise(label, rows):

    if not rows:

        print(f"   {label:<34} n=0")
        return

    returns = [r["option_return_pct"] for r in rows]
    profitable = sum(1 for value in returns if value > 0)
    underlying_wins = sum(1 for r in rows if r["verdict"] == "TARGET_FIRST")

    print(
        f"   {label:<34} n={len(rows):<4} "
        f"mean {statistics.mean(returns):+7.2f}%  "
        f"median {statistics.median(returns):+7.2f}%  "
        f"profitable {profitable}/{len(rows)}  "
        f"(underlying said {underlying_wins} won)"
    )


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2000-01-01", help="earliest trading day")
    args = parser.parse_args()

    with get_engine().begin() as connection:

        rows = [
            dict(row) for row in
            connection.execute(text(QUERY), {"since": args.since}).mappings()
        ]

    if not rows:

        print(
            "no priced option legs yet -- run tools/replay_option_leg.py, or wait "
            "for the nightly backlog pass to reach these days"
        )
        return

    days = sorted({r["day"] for r in rows})
    print(f"{len(rows)} priced legs across {len(days)} sessions "
          f"({days[0]} .. {days[-1]})\n")

    # The headline: the two measurements disagree, and only one is the account.
    underlying_wins = sum(1 for r in rows if r["verdict"] == "TARGET_FIRST")
    profitable = sum(1 for r in rows if r["option_return_pct"] > 0)
    print(f"underlying reached target : {underlying_wins}/{len(rows)}")
    print(f"option actually profitable: {profitable}/{len(rows)}")
    print()

    print("== by what the underlying did ==")
    for verdict in ("TARGET_FIRST", "STOP_FIRST"):
        summarise(verdict, [r for r in rows if r["verdict"] == verdict])

    print("\n== by direction (drift guard) ==")
    for side in ("CALL", "PUT"):
        summarise(side, [r for r in rows if r["direction"] == side])

    print("\n== by setup score band ==")
    for lo, hi, label in ((0, 50, "setup <50"), (50, 70, "setup 50-70"),
                          (70, 201, "setup 70+")):
        summarise(label, [
            r for r in rows
            if r["setup_pct"] is not None and lo <= r["setup_pct"] < hi
        ])

    print("\n== by underlying RR band ==")
    for lo, hi, label in ((0, 2.0, "RR <2.0 (gate refuses)"),
                          (2.0, 3.0, "RR 2.0-3.0"),
                          (3.0, 99, "RR 3.0+")):
        summarise(label, [
            r for r in rows
            if r["underlying_rr"] is not None and lo <= r["underlying_rr"] < hi
        ])

    print("\n== per session ==")
    for day in days:
        summarise(day, [r for r in rows if r["day"] == day])


if __name__ == "__main__":
    main()
