"""Apply database migrations through DATABASE_DIRECT_URL. No scan executes DDL.

    python tools/apply_db_migration.py 012_holding_profiles.sql   # one file
    python tools/apply_db_migration.py --all                      # rebuild: every migration in order
    python tools/apply_db_migration.py --all --dry-run            # list what would run
    python tools/apply_db_migration.py --all --schema scratch     # rebuild into a scratch schema

Every migration is idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS), so
`--all` is safe against an existing database and is the supported way to build a
fresh one. Migrations apply in lexical order; `000_baseline_core_tables.sql` sorts
first by design because later migrations ALTER the tables it creates.
"""

from pathlib import Path
import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "app" / "db" / "migrations"
load_dotenv(ROOT / ".env", override=True)


def _statements(path):
    sql = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    return [statement for statement in sql.split(";") if statement.strip()]


def migration_files():
    return sorted(MIGRATIONS.glob("*.sql"), key=lambda path: path.name)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("filename", nargs="?",
                        default="000_baseline_core_tables.sql",
                        help="migration filename under app/db/migrations/")
    parser.add_argument("--all", action="store_true",
                        help="apply every migration in order (full rebuild)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list migrations and statement counts without executing")
    parser.add_argument("--schema",
                        help="apply into this schema instead of public (creates it)")
    args = parser.parse_args()

    url = os.getenv("DATABASE_DIRECT_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_DIRECT_URL or DATABASE_URL is required")

    scripts = migration_files() if args.all else [MIGRATIONS / args.filename]

    for script in scripts:
        if not script.exists():
            raise SystemExit(f"Migration not found: {script.name}")

    if args.dry_run:
        print(f"Would apply {len(scripts)} migration(s)"
              + (f" into schema {args.schema}" if args.schema else ""))
        for script in scripts:
            print(f"  {script.name:46} {len(_statements(script)):3} statement(s)")
        return

    engine = create_engine(url, pool_pre_ping=True)
    applied = 0

    with engine.begin() as connection:

        if args.schema:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{args.schema}"'))
            # SET LOCAL, not SET: a plain SET is session-scoped and leaks through a
            # transaction-pooling proxy onto whatever connection is handed out next,
            # leaving unrelated sessions resolving unqualified names against this
            # schema. SET LOCAL is reverted when the transaction ends.
            connection.execute(text(f'SET LOCAL search_path TO "{args.schema}"'))
            print(f"Applying into schema {args.schema}")

        for script in scripts:
            for statement in _statements(script):
                connection.execute(text(statement))
            applied += 1
            print(f"  applied {script.name}")

    print(f"{applied} migration(s) applied successfully.")


if __name__ == "__main__":
    main()
