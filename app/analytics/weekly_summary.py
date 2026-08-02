"""Weekly subscriber results.

The channel tells subscribers what to do and never tells them how it went. That
asymmetry is what makes a signal channel hard to trust: every message is a claim,
none is a scorecard. This module produces the scorecard.

It reports in **both** R and premium, for the reason `build_performance_statistics`
documents at length -- R is measured on the underlying while the position held is
an option, and the option's round-trip spread is routinely wider than the move the
stop allows. On 2026-07-30 five closed trades summed to -0.76R and showed two
winners; priced ask-to-bid, all five lost. A summary that published only R would
be publishing the flattering half.

Source of truth is Postgres, not the daily CSVs. `data/daily/` is ephemeral on
Streamlit Cloud and is wiped on every container restart, which is exactly how
`completed_trades: 0` kept getting reported for days that had real trades.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from app.analytics.performance_statistics import build_performance_statistics


ET = ZoneInfo("America/New_York")

# Below this, the week's numbers describe what happened but do not support any
# claim about what to expect. The message says so explicitly rather than leaving
# a subscriber to infer it from a small denominator.
MEANINGFUL_SAMPLE_TRADES = 30


def weekly_summary_window(reference=None):
    """The Monday-to-Friday trading week containing `reference`.

    Anchored on the ISO week, so the intended Friday-evening or weekend run
    reports the week that just ended rather than an empty one starting Monday.
    Run mid-week it returns a partial week, which is correct for an ad-hoc run
    and is why the message prints the window it covers.
    """

    today = reference or _now_et().date()

    if isinstance(today, str):

        today = date.fromisoformat(today)

    monday = today - timedelta(days=today.weekday())

    return monday, monday + timedelta(days=4)


def _now_et():

    from datetime import datetime

    return datetime.now(ET)


def load_weekly_trades(start_day, end_day, repository=None):
    """Closed trades for the window, straight from Postgres.

    Returns `None` when the query could not be run, distinct from `[]` for a week
    that genuinely had no closes. `BestEffortRepository._fetch` warns about
    exactly this: "reporting zero completed trades when the query simply failed
    is how a broken pipeline disguises itself as a quiet day." Swallowing the
    failure here published that disguise -- subscribers were told "No positions
    were closed this week" for a week with seven closed trades.
    """

    if repository is None:

        from app.db.paper_trade_repository import PaperTradeRepository

        repository = PaperTradeRepository()

    try:

        return repository.fetch_closed_between(start_day, end_day)

    except Exception as exc:

        print(f"[WEEKLY SUMMARY WARNING] could not load trades: {exc}")

        return None


def build_weekly_summary_stats(trades):
    """Performance for the week, in R and in premium.

    Delegates to `build_performance_statistics` so the weekly number and the daily
    number can never disagree about what a win is -- including honouring
    `include_in_strategy_stats`, which excludes index-validation entries that
    otherwise flatter the record.
    """

    frame = pd.DataFrame(trades or [])
    stats = dict(build_performance_statistics(frame))
    stats["meaningful_sample"] = (
        int(stats.get("completed_trades") or 0) >= MEANINGFUL_SAMPLE_TRADES
    )

    return stats


def due_weekly_summary_window(now=None):
    """The week whose summary is due right now, or None if none is.

    Due from Friday's close through the weekend. The window stays open rather
    than firing on a single instant because the scanner is not guaranteed to run
    at any particular minute, and a summary that depends on catching one scan
    would be missed routinely. Repeats are harmless: `_weekly_summary_alert_key`
    keys on the ISO week, so the second attempt is a DUPLICATE_ALERT rather than
    a second message.

    Friday 16:00-20:00 ET is AFTERHOURS, so scans are still running when this
    first becomes due. The Saturday and Sunday branches only fire if something is
    scanning over the weekend (`skip_closed=False`); otherwise a missed Friday is
    recovered with `python -m tools.send_weekly_summary`.
    """

    now = now or _now_et()
    weekday = now.weekday()

    if weekday == 4 and now.hour >= 16:

        return weekly_summary_window(now.date())

    if weekday in {5, 6}:

        return weekly_summary_window(now.date())

    return None


def dispatch_weekly_summary_if_due(scan_id=None, now=None, force=False, window=None):
    """Send the weekly results if they are due and not already sent.

    Safe to call on every scan: the due-window check and the once-per-ISO-week
    dedup both have to pass before anything is sent.
    """

    from app.alerts.telegram_alerts import maybe_send_weekly_outcome_summary

    if window is None:

        window = due_weekly_summary_window(now)

    if window is None:

        return {"sent": False, "reason": "NOT_DUE"}

    start_day, end_day = window
    summary = build_weekly_summary(start_day, end_day)

    # A week we could not read is not a week with nothing in it. Silence is the
    # only honest output here: the alternative published "No positions were
    # closed this week" over a week that had seven.
    if summary["trades"] is None:

        return {"sent": False, "reason": "TRADES_UNAVAILABLE"}

    return maybe_send_weekly_outcome_summary(
        summary["stats"],
        start_day,
        end_day,
        scan_id=scan_id,
        force=force,
    )


def build_weekly_summary(start_day=None, end_day=None, repository=None, reference=None):
    """Window, trades and stats in one call, for a caller that just wants to send."""

    if start_day is None or end_day is None:

        start_day, end_day = weekly_summary_window(reference)

    trades = load_weekly_trades(start_day, end_day, repository=repository)

    return {
        "start_day": start_day,
        "end_day": end_day,
        # None when the read failed, so callers can tell it from a quiet week.
        "trades": trades,
        "stats": build_weekly_summary_stats(trades or []),
    }
