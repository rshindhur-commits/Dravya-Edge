"""Per-render data access for the operator console.

Every renderer used to fetch what it needed for itself, so one page render read
`paper_trade_state.json` four times (health cards, book, risk monitor, market
pulse), re-parsed the whole telegram audit JSONL two to three times, and read
`paper_trade_events.csv` twice -- roughly 25-30ms of duplicated disk work on a
page that auto-refreshes all session, on a container where a scan already takes
200-285s.

`RenderContext` reads each source at most once per render. Everything is a
`cached_property`, so a source that no renderer asks for is still never read:
the post-market card pays for closed trades, the intraday board does not.

Module-level functions stay importable and take an explicit trading day, so the
data rules are testable without constructing a context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
ET_TZ = ZoneInfo("America/New_York")
TELEGRAM_AUDIT_FILE = ROOT_DIR / "data" / "live" / "telegram_dispatch_audit.jsonl"
MARKET_CLOSE = time(16, 0)
STALE_SCAN_MINUTES = 15


def trading_day_of(state):
    explicit = str((state or {}).get("trading_day") or "")
    if explicit:
        return explicit
    scan_id = str((state or {}).get("scan_id") or "")
    if len(scan_id) >= 10 and scan_id[:10].count("-") == 2:
        return scan_id[:10]
    return datetime.now(ET_TZ).date().isoformat()


def is_post_market(now=None):
    now = now or datetime.now(ET_TZ)
    return now.weekday() >= 5 or now.time() >= MARKET_CLOSE


def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def active_positions():
    from app.state.paper_trade_manager import load_paper_trades

    return [
        trade for trade in load_paper_trades().values()
        if str(trade.get("status") or "").upper() in {"OPEN", "PAUSED"}
    ]


def telegram_rows(trading_day):
    prefix = str(trading_day or "")
    return [
        row for row in read_jsonl(TELEGRAM_AUDIT_FILE)
        if str(row.get("observed_at_utc") or "").startswith(prefix)
        and not str(row.get("message_type") or "").startswith("TEST_")
    ]


def paper_events(trading_day):
    path = ROOT_DIR / "data" / "daily" / str(trading_day) / "paper_trade_events.csv"
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def entries_used(events, trading_day):
    """Entries opened on this trading day, counted off the trade key.

    Counting OPEN events undercounts: the 2026-07-30 file holds an AUTO_EXIT for
    a trade whose OPEN row never landed, so the day reported zero entries while
    a position had plainly been taken. The key embeds its own open timestamp
    (`SYMBOL|OPTION|YYYY-MM-DD HH:MM:SS`), which survives a missing event row and
    also keeps a next-day close from counting as a fresh entry.
    """
    if events is None or events.empty or "trade_key" not in events.columns:
        return 0

    day = str(trading_day)
    return len({
        key for key in events["trade_key"].dropna().astype(str)
        if key.rsplit("|", 1)[-1].strip().startswith(day)
    })


def closed_trades(events):
    """Completed trades with entry and exit stitched back into one row.

    `paper_trade_events.csv` is append-only and one-sided: the OPEN row carries
    the entry time, the close row carries the exit. Charting a trade needs both.
    """
    if events is None or events.empty or "event_type" not in events.columns:
        return []

    events = events.copy()
    events["event_type"] = events["event_type"].astype(str).str.upper()
    opens = {
        str(row.get("trade_key")): row
        for _, row in events[events["event_type"] == "OPEN"].iterrows()
    }

    trades = []
    for _, close in events[events["event_type"].isin({"MANUAL_CLOSE", "AUTO_EXIT"})].iterrows():
        opened = opens.get(str(close.get("trade_key")), {})
        trades.append({
            "trade_key": close.get("trade_key"),
            "symbol": close.get("symbol"),
            "direction": close.get("direction"),
            "entry_price": close.get("entry_price"),
            "exit_price": close.get("exit_price"),
            "r_multiple": close.get("r_multiple"),
            "exit_reason": close.get("exit_reason"),
            "entry_time": opened.get("event_time_et") if len(opened) else None,
            "exit_time": close.get("event_time_et"),
            "closed_how": close.get("event_type"),
        })
    return trades


def archived_scan_count(trading_day):
    """Scans archived to Neon today, or None when the archive cannot be read.

    This is the number that was silently zero all of 2026-07-31: the engine ran
    120 scans and the archive stopped at 09:25 ET, which nothing surfaced until
    the day was over and the container's own files had been wiped. The archive is
    what makes a session reconstructable after a redeploy, so its health belongs
    on the page rather than in a query someone remembers to run.
    """
    try:
        from sqlalchemy import text

        from app.db.connection import get_engine

        with get_engine().connect() as connection:
            return int(connection.execute(text("""
                SELECT COUNT(DISTINCT scan_id)
                FROM scanner_snapshot
                WHERE trading_day = CAST(:trading_day AS DATE)
            """), {"trading_day": str(trading_day)}).scalar() or 0)
    except Exception:
        return None


def engine_status():
    try:
        from app.runtime.scan_supervisor import status

        return status()
    except Exception:
        return {}


def scan_engine_heartbeats(within_seconds=1800):
    """Scan engines reporting to Postgres, summarised.

    The local `engine_status()` above only knows about a supervisor thread in
    *this* process. Once scanning moves to the Render worker there is no such
    thread here, and the System panel would report "not running" about an engine
    that is running perfectly well on another host. This is how it finds out.
    """

    try:
        from app.db.scan_engine_heartbeat_repository import ScanEngineHeartbeatRepository
        from app.runtime.scan_engine_heartbeat import summarize_engines

        return summarize_engines(
            ScanEngineHeartbeatRepository().fetch_recent(within_seconds)
        )
    except Exception:
        return {}


def db_writes_active():
    try:
        from app.db.persistence import db_writes_enabled

        return bool(db_writes_enabled())
    except Exception:
        return False


def auto_paper_max_daily():
    try:
        from app.runtime.paper_automation_support import load_auto_paper_controls

        return int(load_auto_paper_controls().get("max_daily") or 0)
    except Exception:
        return 0


@dataclass
class RenderContext:
    """One render's view of the world. Each source is read at most once."""

    state: dict = field(default_factory=dict)
    df: pd.DataFrame = field(default_factory=pd.DataFrame)

    @cached_property
    def trading_day(self):
        return trading_day_of(self.state)

    @cached_property
    def post_market(self):
        return is_post_market()

    @cached_property
    def positions(self):
        return active_positions()

    @cached_property
    def telegram(self):
        return telegram_rows(self.trading_day)

    @cached_property
    def paper_events(self):
        return paper_events(self.trading_day)

    @cached_property
    def entries_used(self):
        return entries_used(self.paper_events, self.trading_day)

    @cached_property
    def closed_trades(self):
        return closed_trades(self.paper_events)

    @cached_property
    def engine(self):
        return engine_status()

    @cached_property
    def db_writes_active(self):
        return db_writes_active()

    @cached_property
    def max_daily_entries(self):
        return auto_paper_max_daily()

    @cached_property
    def delivered_trade_ids(self):
        return {
            str(row.get("trade_id"))
            for row in self.telegram
            if row.get("event") == "SENT" and row.get("trade_id")
        }

    @cached_property
    def archived_scans(self):
        return archived_scan_count(self.trading_day)

    @cached_property
    def scan_age_minutes(self):
        from app.ui.timestamps import minutes_since

        return minutes_since(
            ((self.state or {}).get("scanner_health") or {}).get("timestamp")
            or ((self.state or {}).get("metadata") or {}).get("created_at")
            or (self.state or {}).get("generated_at")
        )
