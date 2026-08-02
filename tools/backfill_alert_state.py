"""Seed `telegram_alert_state` from the local JSON dedup file.

Migration 027 gave the Telegram dedup keys a durable home, but only keys written
*after* it landed reach Postgres — `mark_alert_sent` writes through from that
point on. Keys already in `app/state/telegram_alert_state.json` are invisible to
a fresh container until this runs, which means the alerts they suppress could go
out a second time on the next restart.

Idempotent: every row is an upsert keyed on `alert_key`, so re-running is safe
and is the right thing to do before a Render deploy.

    python -m tools.backfill_alert_state --dry-run
    python -m tools.backfill_alert_state
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:

    sys.path.insert(0, str(ROOT_DIR))

from app.alerts.telegram_alerts import ALERT_STATE_FILE  # noqa: E402
from app.db.telegram_alert_state_repository import TelegramAlertStateRepository  # noqa: E402


def main(argv=None):

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report, write nothing.")
    args = parser.parse_args(argv)

    path = Path(str(ALERT_STATE_FILE))

    if not path.exists():

        print(f"[BACKFILL] no local state at {path}; nothing to do.")

        return 0

    sent = (json.loads(path.read_text(encoding="utf-8")) or {}).get("sent") or {}
    repository = TelegramAlertStateRepository()
    existing = repository.fetch_recent()

    # None means the read failed. Treating it as "nothing stored yet" would
    # rewrite every key and stamp a fresh `updated_at` across the table, which is
    # the opposite of what a backfill is for.
    if existing is None:

        print("[BACKFILL] could not read existing keys; aborting rather than "
              "assuming the table is empty.")

        return 1

    missing = [key for key in sent if key not in existing]

    print(f"[BACKFILL] local keys: {len(sent)}")
    print(f"[BACKFILL] already in Postgres: {len(existing)}")
    print(f"[BACKFILL] to write: {len(missing)}")

    for key in missing:

        print(f"    {key}")

    if args.dry_run:

        print("[BACKFILL] dry run; nothing written.")

        return 0

    written = sum(bool(repository.upsert(key, sent[key])) for key in missing)
    print(f"[BACKFILL] wrote {written} of {len(missing)} key(s).")

    if written < len(missing):

        # Writes are gated on DB_WRITE_ENABLED and swallow failures by design.
        print("[BACKFILL] some rows did not persist — check DB_WRITE_ENABLED.")

        return 1

    return 0


if __name__ == "__main__":

    raise SystemExit(main())
