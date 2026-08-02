"""Archive the Neon database to local disk, before retention ages rows out.

Retention runs on the Render worker, whose disk is ephemeral, so it cannot
archive anything itself -- the copy has to be pulled to a real machine. The
shortest retention window is 7 days, so exporting less often than that loses
data permanently.

    python tools/export_db.py            # full export
    python tools/export_db.py --check    # is the last export stale? exit 1 if so

CSV+gzip rather than Parquet on purpose: COPY streams at constant memory, is
lossless for every Postgres type without a type-mapping layer, restores with
COPY FROM, and gzip compresses the JSONB payloads that dominate this database.
"""
import argparse
import csv
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2

from app.db.connection import get_engine  # noqa: F401  (loads .env)
import os

DEFAULT_ROOT = Path(os.getenv("DB_EXPORT_ROOT", "D:/Dravya_Trade_Works_backup"))

# The JSONB payload columns exceed csv's default 128 KB field limit.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def shortest_retention_window():
    from app.db.retention import RETENTION_RULES

    return min(rule.resolved_keep_days() for rule in RETENTION_RULES)


def resolve_dsn():
    """SQLAlchemy URL -> libpq DSN on the direct (non-pooled) endpoint.

    `+psycopg2` is SQLAlchemy syntax libpq rejects, and PgBouncer's transaction
    pooling can cut long COPY streams, so the `-pooler` host is swapped out.
    """
    url = os.getenv("DATABASE_URL", "").strip().strip('"').strip("'")

    if not url:
        raise SystemExit("DATABASE_URL is not set")

    return url.replace("postgresql+psycopg2://", "postgresql://").replace("-pooler.", ".")


def human(n):
    n = float(n or 0)

    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024

    return f"{n:.1f} TB"


def existing_exports(root):
    if not root.exists():
        return []

    return sorted(
        (p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").exists()),
        key=lambda p: p.name,
    )


def last_export_age_days(root):
    exports = existing_exports(root)

    if not exports:
        return None

    try:
        stamp = datetime.strptime(exports[-1].name, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None

    return (datetime.now(timezone.utc) - stamp).total_seconds() / 86400


def check(root):
    window = shortest_retention_window()
    age = last_export_age_days(root)

    if age is None:
        print(f"No export found in {root}.")
        print(f"Shortest retention window is {window}d -- export now.")
        return 1

    print(f"Last export: {age:.1f} days ago")
    print(f"Shortest retention window: {window} days")

    if age >= window:
        print(f"\nSTALE. Rows may already have aged out since the last export.")
        return 1

    print(f"\nOK -- {window - age:.1f} days of margin.")
    return 0


def fetch_schema_sql(cur):
    """Best-effort DDL: columns, defaults, PKs, indexes.

    Not a pg_dump substitute -- omits foreign keys, check constraints, sequence
    ownership and privileges. Enough to recreate the tables and reload the CSVs.
    """
    cur.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable, column_default,
               character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
    )
    cols = {}

    for table, col, typ, nullable, default, maxlen in cur.fetchall():
        if maxlen and "character" in typ:
            typ = f"{typ}({maxlen})"

        piece = f"    {col} {typ}"

        if default is not None:
            piece += f" DEFAULT {default}"

        if nullable == "NO":
            piece += " NOT NULL"

        cols.setdefault(table, []).append(piece)

    cur.execute(
        "SELECT tablename, indexdef FROM pg_indexes WHERE schemaname='public' "
        "ORDER BY tablename, indexname"
    )
    idx = {}

    for table, definition in cur.fetchall():
        idx.setdefault(table, []).append(definition + ";")

    out = []

    for table in sorted(cols):
        out.append(f"CREATE TABLE IF NOT EXISTS {table} (")
        out.append(",\n".join(cols[table]))
        out.append(");")
        out.extend(idx.get(table, []))
        out.append("")

    return "\n".join(out)


def export(root):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = root / stamp
    (out_dir / "tables").mkdir(parents=True, exist_ok=True)

    conn = psycopg2.connect(resolve_dsn())
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    cur.execute("SELECT current_database(), version(), pg_database_size(current_database())")
    dbname, version, db_bytes = cur.fetchone()
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    tables = [r[0] for r in cur.fetchall()]

    print(f"database : {dbname}  ({human(db_bytes)}, {len(tables)} tables)")
    print(f"output   : {out_dir}\n")

    (out_dir / "schema.sql").write_text(fetch_schema_sql(cur), encoding="utf-8")

    manifest = {
        "exported_at_utc": stamp,
        "database": dbname,
        "server_version": version,
        "database_bytes": db_bytes,
        "format": "csv.gz (COPY ... TO STDOUT WITH CSV HEADER)",
        "tables": {},
    }
    total_rows = 0
    total_bytes = 0

    print(f"{'TABLE':<40}{'ROWS':>10}{'FILE':>11}")
    print("-" * 61)

    for table in tables:
        cur.execute(f'SELECT count(*) FROM "{table}"')
        expected = cur.fetchone()[0]
        path = out_dir / "tables" / f"{table}.csv.gz"

        with gzip.open(path, "wb", compresslevel=6) as fh:
            cur.copy_expert(f'COPY (SELECT * FROM "{table}") TO STDOUT WITH CSV HEADER', fh)

        size = path.stat().st_size
        manifest["tables"][table] = {
            "expected_rows": expected,
            "file": f"tables/{table}.csv.gz",
            "file_bytes": size,
        }
        total_rows += expected
        total_bytes += size
        print(f"{table:<40}{expected:>10,}{human(size):>11}")

    manifest["total_rows"] = total_rows
    manifest["total_file_bytes"] = total_bytes

    print("-" * 61)
    print(f"{'TOTAL':<40}{total_rows:>10,}{human(total_bytes):>11}\n")

    # Verify by parsing records back, not counting lines: the JSONB payloads
    # contain embedded newlines that a line count reads as extra rows.
    print("verifying...")
    failures = []

    for table, meta in manifest["tables"].items():
        with gzip.open(out_dir / meta["file"], "rt", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            actual = sum(1 for _ in reader)

        meta["verified_rows"] = actual
        meta["columns"] = len(header) if header else 0

        if actual != meta["expected_rows"]:
            failures.append(f"  {table}: expected {meta['expected_rows']:,}, got {actual:,}")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    cur.close()
    conn.close()

    if failures:
        print("VERIFICATION FAILED")
        print("\n".join(failures))
        return 1

    print(f"OK - all {len(tables)} tables match ({total_rows:,} rows verified)")
    print(f"\narchive: {out_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="report whether the last export is stale; exit 1 if it is",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    return check(args.root) if args.check else export(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
