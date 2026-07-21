from __future__ import annotations

import json
from pathlib import Path

from app.storage.daily_paths import daily_path


def append_trade_timeline_event(trading_day, trade_id, event_type, occurred_at, payload=None):
    event = {"trade_id": trade_id, "event_type": event_type, "occurred_at": str(occurred_at), "payload": payload or {}}
    path = daily_path(trading_day, "trade_timeline.jsonl")
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str) + "\n")
    return event
