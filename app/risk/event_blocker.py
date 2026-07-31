from datetime import datetime
import os

from app.config.settings import settings


def _parse_event_items(raw_events):

    events = []

    for item in str(raw_events or "").split(","):

        item = item.strip()

        if not item:

            continue

        parts = [
            part.strip()
            for part in item.split(":")
        ]

        if len(parts) < 2:

            continue

        symbol = parts[0].upper() or "*"
        event_date = parts[1]
        label = (
            parts[2]
            if len(parts) >= 3 and parts[2]
            else "Scheduled event"
        )

        events.append({
            "symbol": symbol,
            "date": event_date,
            "label": label
        })

    return events


def _earnings_blocker_enabled():
    return str(
        os.getenv("EARNINGS_BLOCKER_ENABLED", "true") or "true"
    ).strip().lower() not in {"false", "0", "no", "off"}


def _evaluate_earnings_blocker(symbol, current_date):
    """Block a symbol reporting today or within EVENT_BLOCKER_DAYS_BEFORE days.

    The free Alpha Vantage feed carries no before/after-market flag, so the day of
    the report is treated as unsafe in both directions rather than guessing which
    session the print lands in.

    Any failure here returns "not blocked". Refusing to trade because a calendar
    lookup raised would turn an API outage into a flat day, and the pre-existing
    behaviour was no earnings blocking at all -- so the worst case is exactly the
    status quo rather than a new way to lose the session.
    """

    if not _earnings_blocker_enabled():

        return {"blocked": False, "reason": None, "event_date": None, "event_label": None}

    try:
        from app.risk.earnings_calendar import next_earnings_date

        report_date = next_earnings_date(symbol, on_date=current_date)

    except Exception as exc:
        print(f"[EARNINGS BLOCKER WARNING] {symbol}: {exc}")
        return {"blocked": False, "reason": None, "event_date": None, "event_label": None}

    if report_date is None:

        return {"blocked": False, "reason": None, "event_date": None, "event_label": None}

    days_until = (report_date - current_date).days

    if 0 <= days_until <= settings.event_blocker_days_before:

        return {
            "blocked": True,
            "reason": (
                f"Earnings risk: {symbol} reports {report_date.isoformat()} "
                f"({days_until}d away); IV crush outweighs direction"
            ),
            "event_date": report_date.isoformat(),
            "event_label": "Earnings"
        }

    return {"blocked": False, "reason": None, "event_date": None, "event_label": None}


def evaluate_event_blocker(symbol, current_date=None):

    if not settings.event_blocker_enabled:

        return {
            "blocked": False,
            "reason": None,
            "event_date": None,
            "event_label": None
        }

    current_date = current_date or datetime.now().date()
    symbol = str(symbol or "").upper()

    # Earnings first: it is the event that actually recurs, and a manual entry for
    # the same symbol should not be able to mask it. EVENT_BLOCKER_DATES stays as
    # the override for everything the calendar does not cover -- FOMC, CPI, a known
    # product event -- which is what it was always meant to be, rather than the
    # sole source it had silently become while shipping empty.
    earnings_block = _evaluate_earnings_blocker(symbol, current_date)

    if earnings_block["blocked"]:

        return earnings_block

    for event in _parse_event_items(settings.event_blocker_dates):

        if event["symbol"] not in ["*", symbol]:

            continue

        try:

            event_date = datetime.strptime(
                event["date"],
                "%Y-%m-%d"
            ).date()

        except Exception:

            continue

        days_until = (
            event_date - current_date
        ).days

        if 0 <= days_until <= settings.event_blocker_days_before:

            label = event["label"]

            return {
                "blocked": True,
                "reason": (
                    f"Event risk: {label} on {event_date.isoformat()}"
                ),
                "event_date": event_date.isoformat(),
                "event_label": label
            }

    return {
        "blocked": False,
        "reason": None,
        "event_date": None,
        "event_label": None
    }