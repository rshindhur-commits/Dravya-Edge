from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load the repo .env explicitly. Relying on find_dotenv() from inside app.config
# left DATABASE_URL unset when this tool ran, so every DB-backed check here failed
# with "DATABASE_URL is not configured".
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from app.analytics.candidate_evidence import write_candidate_evidence  # noqa: E402
from app.analytics.learning_engine import write_daily_learning_summary
from app.db.connection import get_engine
from app.db.persistence import db_writes_enabled
from app.storage.daily_paths import DAILY_DIR, daily_path


def _file_rows(path):
    try:
        return max(0, len(path.read_text(encoding="utf-8").splitlines()) - 1)
    except Exception:
        return 0


def reconcile_day(trading_day, backfill=False):
    if backfill:
        write_candidate_evidence(trading_day)
        write_daily_learning_summary(trading_day)
    result = {"trading_day": trading_day, "candidate_file_rows": _file_rows(daily_path(trading_day, "candidate_evidence.csv")), "db_active": db_writes_enabled()}
    if not result["db_active"]:
        result["status"] = "FILE_ONLY"
        return result
    with get_engine().connect() as connection:
        result["candidate_db_rows"] = connection.execute(text("SELECT COUNT(*) FROM candidate_evidence WHERE trading_day = CAST(:day AS DATE)"), {"day": trading_day}).scalar_one()
        result["summary_db_rows"] = connection.execute(text("SELECT COUNT(*) FROM daily_engine_summary WHERE trading_day = CAST(:day AS DATE)"), {"day": trading_day}).scalar_one()
    if result["candidate_file_rows"] == 0 and result["candidate_db_rows"] > 0:
        result["status"] = "DB_AUTHORITATIVE"
    elif result["candidate_file_rows"] == result["candidate_db_rows"] and result["summary_db_rows"]:
        result["status"] = "MATCH"
    else:
        result["status"] = "REVIEW"
    return result


def reconcile_trade_ledgers():
    """Compare the two trade ledgers, which serve different purposes but must agree.

    `paper_trades` is the live state mirror (upserted on every open/update/close).
    `trade` holds immutable completed-trade facts (entry/exit/outcome snapshots).
    Both are written from the same close path, so every completed `trade` row must
    have a matching `paper_trades` row. On 2026-07-29 they overlapped on only one
    row out of 13 and 14 -- paper_trades stopped receiving rows after 2026-07-20
    while `trade` kept recording through 07-27, and nothing surfaced it. Read-only.
    """

    from sqlalchemy import text

    from app.db.connection import get_engine

    engine = get_engine().execution_options(isolation_level="AUTOCOMMIT")

    with engine.connect() as connection:
        paper = {
            (str(row[0]), str(row[1])[:10])
            for row in connection.execute(text(
                "select symbol, coalesce(closed_at, opened_at) from paper_trades"))
        }
        facts = {
            (str(row[0]), str(row[1])[:10])
            for row in connection.execute(text(
                "select symbol, coalesce(completed_at, created_at) from trade"))
        }

    missing_paper = sorted(facts - paper)
    missing_facts = sorted(paper - facts)
    status = "MATCH" if not missing_paper else "REVIEW"

    return {
        "check": "trade_ledgers",
        "status": status,
        "paper_trades_rows": len(paper),
        "trade_rows": len(facts),
        "completed_without_paper_state": [f"{s} {d}" for s, d in missing_paper],
        "paper_state_without_completed_fact": [f"{s} {d}" for s, d in missing_facts],
        "note": (
            "completed_without_paper_state is the real defect: a completed trade "
            "fact exists but the live-state upsert never landed. "
            "paper_state_without_completed_fact is expected for still-open trades "
            "and for closes where trend-capture analysis did not run."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--ledgers", action="store_true",
                        help="only reconcile paper_trades against trade (read-only)")
    args = parser.parse_args()

    if args.ledgers:
        print(json.dumps(reconcile_trade_ledgers(), indent=2, default=str))
        return

    days = [args.date] if args.date else [path.name for path in DAILY_DIR.iterdir() if path.is_dir()]
    report = [reconcile_day(day, args.backfill) for day in sorted(days)]
    report.append(reconcile_trade_ledgers())
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()