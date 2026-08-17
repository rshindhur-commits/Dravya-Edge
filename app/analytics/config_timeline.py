"""Which enforced settings changed inside a measurement window.

Averaging across a config change is the trap `CONFIG_CHANGELOG.md` exists to
prevent, and the Validation page's window is thirty days wide -- long enough to
straddle several. A 30-day average taken over a spread ceiling of 2 and then 3 is
not a measurement of either.

The source is `scanner_runs.payload->'config'`, the snapshot of the thresholds
each scan actually enforced. That is deliberately not the changelog and not the
environment: both describe what someone intended, and the whole point of the
snapshot is that it records what the scanner was really running. See
`app/runtime/config_snapshot.py`.
"""

from __future__ import annotations

from datetime import timedelta


# Reported in the operator's language rather than as raw keys, and restricted to
# levers that move which trades exist. A change in `max_daily_entries` explains a
# thinner window; a change in a debug flag does not, and listing everything would
# bury the ones that do.
WATCHED = {
    "option_max_spread_pct": "Option spread ceiling",
    "option_max_contract_cost": "Contract cost cap",
    "option_min_contract_cost": "Contract cost floor",
    "option_min_dte": "Min DTE",
    "option_max_dte": "Max DTE",
    "option_min_open_interest": "Min open interest",
    "option_min_volume": "Min volume",
    "option_min_quality_score": "Min option quality",
    "gate_min_setup_percent": "Setup bar",
    "gate_min_rr": "Min RR",
    "gate_max_spread_pct": "Gate spread ceiling",
    "gate_min_option_quality": "Gate option quality",
    "auto_paper_min_setup": "Auto-paper setup bar",
    "auto_paper_min_rr": "Auto-paper min RR",
    "auto_paper_enabled": "Auto-paper enabled",
    "max_daily_entries": "Daily entry cap",
    "min_stop_spread_multiple": "Stop vs spread multiple",
    "option_allow_0dte": "Allow 0DTE",
    "option_allow_1dte": "Allow 1DTE",
}


def _daily_configs(connection, start_day, end_day):
    """The last config each day enforced, plus the day before the window.

    The extra day is the baseline. Without it the first day of the window can
    never show a change, which is the day most likely to have one -- an operator
    who just moved a lever is exactly who opens this page.
    """

    from sqlalchemy import text

    rows = connection.execute(
        text(
            """
            SELECT DISTINCT ON (started_at::date)
                   started_at::date AS day, payload->'config' AS config
            FROM scanner_runs
            WHERE payload->'config' IS NOT NULL
              AND started_at >= CAST(:start_day AS date)
              AND started_at <  CAST(:end_day AS date) + INTERVAL '1 day'
            ORDER BY started_at::date, started_at DESC
            """
        ),
        {"start_day": str(start_day - timedelta(days=14)), "end_day": str(end_day)},
    ).mappings().all()

    return [(row["day"], row["config"] or {}) for row in rows]


def config_changes_between(start_day, end_day, connection=None):
    """Settings that changed between two dates, or None when unreadable.

    None is not an empty list. "No config changed in this window" is a finding
    that makes an average trustworthy; "the archive could not be read" is not,
    and a caller that cannot tell them apart will publish the first while meaning
    the second.
    """

    if connection is not None:
        return _changes(_daily_configs(connection, start_day, end_day), start_day)

    try:
        from app.db.connection import get_engine

        with get_engine().connect() as opened:
            return _changes(_daily_configs(opened, start_day, end_day), start_day)

    except Exception as exc:
        print(f"[CONFIG TIMELINE] unreadable: {exc}")

        return None


def _changes(daily, start_day):

    changes = []

    for position in range(1, len(daily)):
        day, config = daily[position]
        _, previous = daily[position - 1]

        # Changes before the window are the baseline, not news. They are read to
        # establish what the window started from and then dropped.
        if day < start_day:
            continue

        for key, label in WATCHED.items():
            was, now = previous.get(key), config.get(key)

            if was is None or now is None or was == now:
                continue

            changes.append({
                "day": day.isoformat(),
                "setting": label,
                "key": key,
                "from": was,
                "to": now,
            })

    return changes
