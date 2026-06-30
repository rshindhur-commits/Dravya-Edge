from datetime import datetime

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