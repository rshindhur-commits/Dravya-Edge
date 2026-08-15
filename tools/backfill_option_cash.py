"""Compute the cash P&L of closed trades that already carry both option legs.

`option_pl_dollars` exists on **2 of 42** closed trades, so every profitability
statement about the live book has been made in R or in percent. That is not a
cosmetic gap. R measures the underlying and is blind to the spread crossed twice;
percent is blind to position size. On 2026-08-14 the book was +0.10R and -13.0%
of premium on the same five trades, and on 2026-08-05 an SMCI put booked -1.92%
against a real -99.5%.

Nothing here is fetched. Where a trade records `option_entry_ask`,
`option_close_bid` and `option_contracts`, the cash is arithmetic on numbers the
row already holds:

    cash = (close_bid - entry_ask) x 100 x contracts

Honest fills, matching the rest of the codebase: the position bought the ask and
sold the bid. That is deliberately the pessimistic reading of a quote and it is
the one the account would have seen.

Trades opened before 2026-07-31 carry no option quotes at all -- neither leg was
persisted then -- and are reported as unrecoverable rather than estimated. A
guessed cash figure is worse than a missing one, because only the missing one is
visibly missing.

    python tools/backfill_option_cash.py           # report only
    python tools/backfill_option_cash.py --apply   # write

Reversible: the original payload keys are never overwritten, only added to, and
`option_pl_dollars_basis` records where the number came from.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

# The percent already on the row and the percent implied by the two legs must
# agree, or the legs are not the ones the percent was computed from and the cash
# would be attributed to the wrong trade. 0.05 absorbs rounding, nothing more.
PCT_TOLERANCE = 0.05


def _number(value):
    try:
        if value is None:
            return None
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def classify(row):
    """What can be done with this trade, and why."""

    payload = row["payload"] or {}

    if payload.get("option_pl_dollars") is not None:
        return "already_priced", None, None

    entry_ask = _number(payload.get("option_entry_ask"))
    close_bid = _number(payload.get("option_close_bid"))
    contracts = _number(payload.get("option_contracts"))

    if entry_ask is None or close_bid is None:
        return "no_quotes", None, None

    if not entry_ask > 0:
        return "bad_entry_ask", None, None

    contracts = contracts if contracts and contracts > 0 else 1.0

    cash = round((close_bid - entry_ask) * 100 * contracts, 2)
    implied_pct = round((close_bid - entry_ask) / entry_ask * 100, 2)

    recorded_pct = _number(payload.get("option_pnl_pct_net"))

    if recorded_pct is not None and abs(recorded_pct - implied_pct) > PCT_TOLERANCE:
        # Do not write. Either the legs or the percent is wrong, and this tool
        # cannot tell which -- reporting the disagreement is the useful act.
        return "pct_mismatch", cash, implied_pct

    return "computable", cash, implied_pct


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the results")
    args = parser.parse_args()

    with get_engine().begin() as conn:

        rows = conn.execute(text("""
            SELECT id, symbol, opened_at, option_ticker, r_multiple, payload
            FROM paper_trades WHERE status = 'CLOSED' ORDER BY opened_at
        """)).mappings().all()

        buckets = {}
        writes = []

        for row in rows:
            verdict, cash, implied = classify(row)
            buckets.setdefault(verdict, []).append((row, cash, implied))

            if verdict == "computable":
                writes.append((row, cash))

        print(f"\nclosed trades: {len(rows)}\n")

        for verdict in sorted(buckets):
            print(f"  {verdict:<16} {len(buckets[verdict])}")

        for row, cash, implied in buckets.get("pct_mismatch", []):
            recorded = (row["payload"] or {}).get("option_pnl_pct_net")
            print(
                f"\n  MISMATCH {row['symbol']} {row['option_ticker']}: "
                f"recorded {recorded}% vs legs implying {implied}% -- not written"
            )

        total = sum(cash for _row, cash in writes)

        print(f"\n  computable cash total: ${total:,.2f} across {len(writes)} trades")

        if not args.apply:
            print("\n  dry run. re-run with --apply to write.\n")
            return

        for row, cash in writes:
            payload = dict(row["payload"] or {})
            payload["option_pl_dollars"] = cash
            payload["option_pl_dollars_basis"] = (
                "backfilled from recorded option_entry_ask and option_close_bid; "
                "honest fill, bought the ask and sold the bid"
            )

            conn.execute(text("""
                UPDATE paper_trades
                SET payload = CAST(:p AS JSONB), updated_at = NOW()
                WHERE id = :id
            """), {"p": json.dumps(payload, default=str), "id": row["id"]})

        print(f"\n  wrote option_pl_dollars on {len(writes)} trades.\n")


if __name__ == "__main__":
    main()
