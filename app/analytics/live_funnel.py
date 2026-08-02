"""The mid-session question: nothing has fired, is that the market or is it us?

Nothing in the dashboard answered it. Every diagnostic panel was retrospective --
closed trades, archived days, decision logs -- so the only way to tell a quiet
tape from a rule eating everything was to wait for the day to end and read the
postmortem, by which point the answer no longer changes anything.

`decision_waterfall` has held the answer all along, at 52k rows. This shapes a
trailing window of it into a funnel and, more usefully, into one sentence.
"""

from __future__ import annotations


# Stage names are data, so the funnel cannot assume an order beyond `stage_order`.
DEFAULT_WINDOW_MINUTES = 60

# Beyond this the funnel is describing a scan that has not happened. The regular
# session runs a 300s cadence, so three missed cycles is already a problem worth
# naming ahead of whatever the rules say.
STALE_SCAN_MINUTES = 20


def build_live_funnel(minutes=DEFAULT_WINDOW_MINUTES, repository=None):

    if repository is None:
        from app.db.decision_funnel_repository import DecisionFunnelRepository

        repository = DecisionFunnelRepository()

    stages = _safe(repository.stage_funnel, minutes)

    return {
        "minutes": int(minutes),
        "stages": funnel_rows(stages),
        "blocking_rules": _safe(repository.blocking_rules, minutes),
        "near_misses": _safe(repository.near_misses, minutes),
        "entry_decisions": _safe(repository.entry_decisions, minutes),
        "freshness": _safe(repository.freshness),
    }


def _safe(method, *args):

    try:
        return method(*args)

    except Exception as exc:
        print(f"[LIVE FUNNEL] {getattr(method, '__name__', method)} failed: {exc}")

        return None


def funnel_rows(stages):
    """Add passed and pass rate per stage, ordered by `stage_order`."""

    if stages is None:
        return None

    rows = []

    for stage in stages:

        seen = int(stage.get("symbols_seen") or 0)
        blocked = int(stage.get("symbols_blocked") or 0)
        passed = max(seen - blocked, 0)

        rows.append({
            "stage": stage.get("stage"),
            "stage_order": int(stage.get("stage_order") or 0),
            "seen": seen,
            "passed": passed,
            "blocked": blocked,
            "pass_rate": round(100 * passed / seen, 1) if seen else None,
            "blocks": int(stage.get("blocks") or 0),
        })

    return sorted(rows, key=lambda row: row["stage_order"])


def narrative(report):
    """One sentence answering the question the page exists for.

    A table of stages and counts still leaves the operator to work out what it
    means. The distinction that matters is narrow: nothing scanned, nothing was
    evaluated, everything was blocked at one stage, or candidates are getting
    through and the entry layer is declining them -- and each points somewhere
    different.
    """

    freshness = report.get("freshness")

    if freshness is None:
        return "Unavailable — the database could not be read. This is not the same as a quiet market."

    age = float((freshness or {}).get("age_minutes") or 0)

    if not freshness:
        return "No scan has ever been recorded."

    if age > STALE_SCAN_MINUTES:
        return (
            f"The last scan was {age:.0f} minutes ago. Nothing is firing because "
            "nothing is scanning — this is an engine question, not a rules question."
        )

    stages = report.get("stages")

    if stages is None:
        return "Stage data unavailable — the database could not be read."

    if not stages:
        return (
            f"Scanning is current, but no candidate was evaluated in the last "
            f"{report['minutes']} minutes."
        )

    wall = next((row for row in stages if row["seen"] and not row["passed"]), None)

    if wall:
        return (
            f"Every one of the {wall['seen']} symbols evaluated was stopped at "
            f"{wall['stage']}. Nothing reached the stages after it."
        )

    final = stages[-1]

    if final["passed"]:
        return (
            f"{final['passed']} of {final['seen']} symbols cleared every stage, so "
            "the scanner is not what is holding entries back — check the entry "
            "decisions below."
        )

    return (
        f"Candidates are reaching {final['stage']} and being stopped there "
        f"({final['blocked']} of {final['seen']})."
    )
