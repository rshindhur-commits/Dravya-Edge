"""One day, assembled from Postgres, for review or for an incident.

Built after 2026-08-01, when finding out why subscribers received the same weekly
summary ten times took a day of hand-written SQL. Nothing was missing from the
database; there was simply no way to ask it a question shaped like "what happened
on this day". The answer, when it came, was a gap in `scanner_runs` -- scans that
ran while writing nothing, because the container could not reach Postgres.

`coverage_gaps` exists because of that. A blind container leaves no trace except
the shape of the hole it leaves, so the hole is worth computing rather than
eyeballing off a list of timestamps.

Every section is `None` when its read failed and `[]` when the day genuinely had
none. The UI must render those differently -- treating an unavailable read as
"nothing happened" is the exact fault this tool was built to catch.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")

# Below this a gap is just cadence. The regular session runs a 300s cadence and
# after-hours 900s, so anything under 20 minutes is normal at some point in the
# day and flagging it would bury the gaps that matter.
GAP_MINUTES = 20


def build_day_postmortem(trading_day, repository=None):
    """Every section for one trading day. Section values may be None."""

    if repository is None:

        from app.db.day_postmortem_repository import DayPostmortemRepository

        repository = DayPostmortemRepository()

    scans = _safe(repository.scans, trading_day)

    return {
        "trading_day": str(trading_day),
        "scans": scans,
        "coverage": scan_coverage(scans),
        "gaps": coverage_gaps(scans),
        "alerts": _safe(repository.alerts, trading_day),
        "trades": _safe(repository.trades, trading_day),
        "entry_decisions": _safe(repository.entry_decisions, trading_day),
        "blocking_rules": _safe(repository.blocking_rules, trading_day),
        "alert_suppressions": _safe(repository.alert_suppressions, trading_day),
    }


def _safe(method, trading_day):
    """A section that raises must not take the rest of the page with it."""

    try:
        return method(trading_day)

    except Exception as exc:
        print(f"[POSTMORTEM] {getattr(method, '__name__', method)} failed: {exc}")

        return None


def scan_coverage(scans):
    """Headline numbers for the day's scanning, or None if unreadable."""

    if scans is None:
        return None

    starts = [row.get("started_at") for row in scans if row.get("started_at")]
    durations = [
        float(row["duration_sec"]) for row in scans
        if row.get("duration_sec") is not None
    ]
    # STARTED is a scan that never wrote a completion, which is a different
    # thing from one that failed: it is the signature of a container killed
    # mid-scan, and a deploy during the session produces one every time.
    statuses = [str(row.get("status") or "").upper() for row in scans]
    incomplete = sum(1 for status in statuses if status == "STARTED")
    failures = sum(
        1 for status in statuses
        if status not in {"FINISHED", "OK", "COMPLETE", "STARTED", "TEST"}
    )

    return {
        "scans": len(scans),
        "failures": failures,
        "incomplete": incomplete,
        "first_at": _et(min(starts)) if starts else None,
        "last_at": _et(max(starts)) if starts else None,
        "slowest_seconds": round(max(durations), 1) if durations else None,
        "median_seconds": round(sorted(durations)[len(durations) // 2], 1)
                          if durations else None,
    }


def coverage_gaps(scans, threshold_minutes=GAP_MINUTES):
    """Stretches of the day with no recorded scan.

    A gap is not proof the scanner was down. It is equally the signature of a
    scanner that ran and could not write, which is what happened on 2026-08-01 --
    and the two are indistinguishable from this table alone, which is precisely
    why the gap is worth surfacing rather than assuming either one.
    """

    if scans is None:
        return None

    starts = sorted(row["started_at"] for row in scans if row.get("started_at"))
    gaps = []

    for earlier, later in zip(starts, starts[1:]):

        minutes = (later - earlier).total_seconds() / 60

        if minutes >= threshold_minutes:

            gaps.append({
                "from_at": _et(earlier),
                "to_at": _et(later),
                "minutes": round(minutes, 1),
            })

    return sorted(gaps, key=lambda gap: gap["minutes"], reverse=True)


def build_reconciliation(trading_day, repository=None):
    """Does the decision record agree with the book?

    Two independent writers describe the same event: `auto_paper_decision` says
    an entry was taken, `paper_trades` says a position exists. They are written
    by different code on different paths, so they can disagree -- and when they
    do, one of them is wrong about a real position.

    This was previously checked by `_render_validation_data_health` against three
    local files, which on a dashboard that never wrote them compared nothing to
    nothing and reported agreement.
    """

    if repository is None:

        from app.db.day_postmortem_repository import DayPostmortemRepository

        repository = DayPostmortemRepository()

    intended = _safe(repository.entries_intended, trading_day)
    recorded = _safe(repository.entries_recorded, trading_day)

    if intended is None or recorded is None:
        return None

    intended_keys = {row.get("trade_key") for row in intended if row.get("trade_key")}
    recorded_keys = {row.get("trade_key") for row in recorded if row.get("trade_key")}

    return {
        "intended": len(intended),
        "recorded": len(recorded),
        # Decided to enter, but no position carries that key. Either the open
        # failed after the decision was logged, or it opened under another key.
        "missing_positions": [
            row for row in intended
            if row.get("trade_key") and row["trade_key"] not in recorded_keys
        ],
        # A position with no decision behind it. Manual entries land here
        # legitimately; an AUTO_PAPER one does not.
        "unexplained_positions": [
            row for row in recorded
            if row.get("trade_key") not in intended_keys
        ],
    }


def alert_delivery(alerts):
    """Sent versus attempted-but-undelivered, or None if unreadable.

    Undelivered is called out on its own because a queued send that never landed
    looks like a send everywhere else: the row exists, the audit says ATTEMPTED,
    and nothing counts it as a failure.
    """

    if alerts is None:
        return None

    total = sum(int(row.get("dispatches") or 0) for row in alerts)
    delivered = sum(int(row.get("delivered") or 0) for row in alerts)
    failed = sum(
        int(row.get("dispatches") or 0) for row in alerts
        if str(row.get("status") or "").upper() == "FAILED"
    )

    return {
        "dispatches": total,
        "delivered": delivered,
        "failed": failed,
        "undelivered": total - delivered - failed,
    }


def trade_outcome(trades):
    """Closed-trade result for the day in R, or None if unreadable."""

    if trades is None:
        return None

    closed = [
        row for row in trades
        if row.get("closed_at") and row.get("r_multiple") is not None
    ]

    if not closed:
        return {"closed": 0, "wins": 0, "losses": 0, "total_r": 0.0}

    values = [float(row["r_multiple"]) for row in closed]

    return {
        "closed": len(closed),
        "wins": sum(1 for value in values if value > 0),
        "losses": sum(1 for value in values if value <= 0),
        "total_r": round(sum(values), 2),
    }


def _et(moment):
    """TIMESTAMPTZ comes back in UTC; every time on this page is ET."""

    if moment is None:
        return None

    try:
        if getattr(moment, "tzinfo", None) is None:
            from datetime import timezone

            moment = moment.replace(tzinfo=timezone.utc)

        return moment.astimezone(ET)

    except Exception:
        return moment


def previous_trading_days(count=10, reference=None):
    """Recent weekdays, newest first, for the day picker."""

    from datetime import datetime

    day = reference or datetime.now(ET).date()
    days = []

    while len(days) < count:

        if day.weekday() < 5:
            days.append(day)

        day = day - timedelta(days=1)

    return days
