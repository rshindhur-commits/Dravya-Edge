from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.analytics.candidate_evidence import write_candidate_evidence
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()
    days = [args.date] if args.date else [path.name for path in DAILY_DIR.iterdir() if path.is_dir()]
    print(json.dumps([reconcile_day(day, args.backfill) for day in sorted(days)], indent=2, default=str))


if __name__ == "__main__":
    main()