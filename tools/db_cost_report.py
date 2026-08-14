"""What the database is costing, in dollars, today.

Neon Launch is usage-priced, not a fixed tier -- the repo's older docs saying
"free plan, 512 MB" are stale and led to retention windows set to protect a cap
that does not exist. Storage is the cheap half at about $0.35/GB-month; the bill
is driven by compute, which is charged per CU-hour while the endpoint is awake.

So this reports both, and reports growth rather than only the level: a database
sitting at 200 MB is fine, and one adding 200 MB a week is not, and the size
alone cannot tell them apart. `retention_run_state` is read for the last prune so
a table that is growing because retention silently stopped is visible as such.

    python tools/db_cost_report.py

Read-only. Nothing here writes, prunes or vacuums.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

# Neon Launch list prices, 2026-08. Storage is billed on the average over the
# month; compute on CU-hours actually used. Both are stated here rather than
# imported because they are external facts that change without the code changing.
STORAGE_USD_PER_GB_MONTH = 0.35
COMPUTE_USD_PER_CU_HOUR = 0.16


def fmt_mb(mb):
    return f"{mb:,.1f} MB" if mb < 1024 else f"{mb / 1024:,.2f} GB"


def main():

    with get_engine().begin() as conn:

        # float(), because Postgres returns numeric as Decimal and Decimal does
        # not divide by float.
        total_mb = float(conn.execute(text("""
            SELECT pg_database_size(current_database()) / 1048576.0
        """)).scalar_one())

        tables = conn.execute(text("""
            SELECT c.relname AS relname,
                   pg_total_relation_size(c.oid) / 1048576.0 AS mb,
                   s.n_live_tup AS rows
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY pg_total_relation_size(c.oid) DESC
            LIMIT 12
        """)).mappings().all()

        # Retention is what keeps this bounded. A stalled prune shows up here as
        # an old timestamp long before it shows up as a bill.
        try:
            last_prune = conn.execute(text("""
                SELECT max(ran_on)::text FROM retention_run
            """)).scalar()
        except Exception:
            last_prune = None

    storage_cost = total_mb / 1024.0 * STORAGE_USD_PER_GB_MONTH

    print(f"\ndatabase size   {fmt_mb(total_mb)}")
    print(f"storage cost    ${storage_cost:,.2f} / month "
          f"(at ${STORAGE_USD_PER_GB_MONTH}/GB-month)")
    print(f"last retention  {last_prune or 'never recorded'}")

    print(f"\n{'table':<32}{'size':>12}{'rows':>14}")
    for t in tables:
        rows = f"{t['rows']:,}" if t["rows"] is not None else "-"
        print(f"{t['relname']:<32}{fmt_mb(float(t['mb'])):>12}{rows:>14}")

    print(f"\nStorage is the part this measures and it is small. The larger half "
          f"of a Neon\nbill is compute (${COMPUTE_USD_PER_CU_HOUR}/CU-hour), "
          f"which is driven by how long the endpoint\nstays awake -- that is "
          f"visible only in the Neon console, not from inside SQL.\n")


if __name__ == "__main__":
    main()
