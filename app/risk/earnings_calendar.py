"""Upcoming earnings dates, so the scanner stops selling premium into them.

The event blocker has always supported blackout dates, but only from
EVENT_BLOCKER_DATES -- a comma-separated string maintained by hand. It shipped
empty, so with EVENT_BLOCKER_ENABLED=true the blocker was on and blocking nothing.

Buying a 7-21 day option into an earnings release is a losing trade even when the
direction is right: implied volatility collapses on the print and takes the
premium with it. Across a 26-symbol watchlist several names sit inside the DTE
window through every reporting season, so this is not an edge case.

Source is Alpha Vantage EARNINGS_CALENDAR. Called without a `symbol` it returns the
entire upcoming calendar as one CSV, which turns the free tier's limits into
non-issues: one request a day against a cap of 25, and the "no multi-ticker
batching" restriction does not apply because the whole market arrives in a single
response and is filtered locally.

The 3-month horizon is four times the 30-day maximum DTE, so nothing tradable
falls outside it. There is no before/after-market flag in the free feed; that is
why the blackout covers the day of the release and EVENT_BLOCKER_DAYS_BEFORE days
ahead of it rather than trying to reason about the session.

Cached in Postgres rather than on disk because the Streamlit filesystem does not
survive a container recycle. A cached calendar covers three months, so a failed
refresh is harmless -- and a fetch failure never blocks a trade, because refusing
to trade on missing data would turn an outage into a flat day.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime, timedelta

import requests


ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
DEFAULT_HORIZON = "3month"
REFRESH_INTERVAL_HOURS = 20
REQUEST_TIMEOUT_SECONDS = 30

_cache = {"fetched_at": None, "events": {}}


def _api_key():
    return str(os.getenv("ALPHAVANTAGE_API_KEY", "") or "").strip()


def _horizon():
    horizon = str(os.getenv("EARNINGS_CALENDAR_HORIZON", DEFAULT_HORIZON) or "").strip()

    # The endpoint accepts exactly these three; anything else returns an error
    # payload that would parse as an empty calendar and silently block nothing.
    return horizon if horizon in {"3month", "6month", "12month"} else DEFAULT_HORIZON


def _parse_calendar_csv(text):
    """Map symbol -> sorted list of upcoming report dates.

    Alpha Vantage returns CSV with a `symbol` and `reportDate` column. A row whose
    date will not parse is skipped rather than defaulting to today, which would
    invent a blackout.
    """

    events = {}

    if not text or not text.strip():
        return events

    reader = csv.DictReader(io.StringIO(text))

    for row in reader:
        symbol = str(row.get("symbol") or "").strip().upper()
        raw_date = str(row.get("reportDate") or "").strip()

        if not symbol or not raw_date:
            continue

        try:
            report_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue

        events.setdefault(symbol, [])

        if report_date not in events[symbol]:
            events[symbol].append(report_date)

    for symbol in events:
        events[symbol].sort()

    return events


def fetch_earnings_calendar():
    """One bulk request for the whole upcoming calendar. Returns {} on any failure."""

    key = _api_key()

    if not key:
        print("[EARNINGS CALENDAR] ALPHAVANTAGE_API_KEY not set; no earnings blackout.")
        return {}

    try:
        response = requests.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "EARNINGS_CALENDAR",
                "horizon": _horizon(),
                "apikey": key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

    except Exception as exc:
        print(f"[EARNINGS CALENDAR WARNING] fetch failed: {exc}")
        return {}

    text = response.text or ""

    # Rate limiting and bad keys come back as 200 with a JSON note, not an HTTP
    # error, so a naive CSV parse would read them as an empty calendar and quietly
    # disable the blackout.
    if text.lstrip().startswith("{"):
        print(f"[EARNINGS CALENDAR WARNING] non-CSV response: {text[:200]}")
        return {}

    events = _parse_calendar_csv(text)
    print(f"[EARNINGS CALENDAR] loaded {sum(len(v) for v in events.values())} "
          f"upcoming reports across {len(events)} symbols.")

    return events


def _store_calendar(events):
    try:
        from app.db.earnings_calendar_repository import EarningsCalendarRepository

        EarningsCalendarRepository().replace_all(events)

    except Exception as exc:
        print(f"[EARNINGS CALENDAR DB WARNING] store failed: {exc}")


def _load_stored_calendar():
    try:
        from app.db.earnings_calendar_repository import EarningsCalendarRepository

        return EarningsCalendarRepository().fetch_all()

    except Exception as exc:
        print(f"[EARNINGS CALENDAR DB WARNING] load failed: {exc}")
        return {}


def earnings_calendar(force_refresh=False):
    """Cached calendar. Refreshes at most once every REFRESH_INTERVAL_HOURS.

    Falls back to the stored copy when a refresh returns nothing, so one failed
    request cannot silently remove every blackout at once.
    """

    now = datetime.now()
    fetched_at = _cache["fetched_at"]
    fresh = (
        fetched_at is not None
        and (now - fetched_at) < timedelta(hours=REFRESH_INTERVAL_HOURS)
    )

    if fresh and not force_refresh and _cache["events"]:
        return _cache["events"]

    if not _cache["events"]:
        stored = _load_stored_calendar()

        if stored:
            _cache["events"] = stored
            _cache["fetched_at"] = now

            if not force_refresh:
                return stored

    events = fetch_earnings_calendar()

    if events:
        _cache["events"] = events
        _cache["fetched_at"] = now
        _store_calendar(events)

    elif _cache["events"]:
        # Keep serving the previous calendar rather than dropping every blackout
        # because one request failed. It covers three months; a stale day is fine.
        _cache["fetched_at"] = now
        print("[EARNINGS CALENDAR] refresh empty; retaining previous calendar.")

    return _cache["events"]


def next_earnings_date(symbol, on_date=None, calendar=None):
    """The next report on or after `on_date`, or None."""

    symbol = str(symbol or "").strip().upper()

    if not symbol:
        return None

    on_date = on_date or date.today()
    calendar = calendar if calendar is not None else earnings_calendar()

    for report_date in calendar.get(symbol, []):
        if report_date >= on_date:
            return report_date

    return None
