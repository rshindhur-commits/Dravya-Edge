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


OPEN_STATUSES = {"OPEN", "PAUSED"}


def active_positions():
    """Open positions, from Postgres first and the local file second.

    `paper_trade_state.json` is written by whichever process opened the trade.
    Once scanning moved to the Render worker that process is on another host, so
    the dashboard was reading a file nobody in its container had ever written --
    it would have shown "No active paper positions" all Monday while the worker
    held a book. The scanner recovers from Postgres for the same reason
    (`restore_open_trades_from_db`); this is the read-only half of it.

    Local wins on conflict, matching that restore: the database mirror is
    written through a background queue and can lag, so a position closed locally
    must not reappear because the mirror has not caught up.
    """

    local = {
        key: trade
        for key, trade in _local_open_positions().items()
    }
    remote = _remote_open_positions()

    for trade_key, payload in (remote or {}).items():

        if trade_key not in local and payload.get("symbol"):

            local[trade_key] = payload

    return list(local.values())


def _local_open_positions():

    try:
        from app.state.paper_trade_manager import load_paper_trades

        return {
            key: trade
            for key, trade in (load_paper_trades() or {}).items()
            if str(trade.get("status") or "").upper() in OPEN_STATUSES
        }

    except Exception:
        return {}


def _remote_open_positions():
    """`{trade_key: payload}` for open rows, or `{}` when unreadable."""

    try:
        from app.db.paper_trade_repository import PaperTradeRepository

        rows = PaperTradeRepository().fetch_open()

    except Exception as exc:
        print(f"[RENDER CONTEXT] open positions unavailable: {exc}")

        return {}

    if rows is None:
        return {}

    return {
        row.get("trade_key"): dict(row.get("payload") or {},
                                   trade_key=row.get("trade_key"))
        for row in rows
        if row.get("trade_key")
    }


def telegram_rows(trading_day):
    """Today's dispatch events, from the local audit file or Postgres.

    The audit file is written by whichever container sent the alert. With
    alerting on the Render worker it never exists on Streamlit, so this returned
    nothing and the health cell read `0 sent / 0 failed` while alerts were going
    out. Worse, the file was tracked in git until 2026-08-01, so what it did show
    was a developer machine's July history presented as today's delivery health.
    """

    prefix = str(trading_day or "")
    local = [
        row for row in read_jsonl(TELEGRAM_AUDIT_FILE)
        if str(row.get("observed_at_utc") or "").startswith(prefix)
        and not str(row.get("message_type") or "").startswith("TEST_")
    ]

    if local:
        return local

    return _remote_telegram_rows(trading_day) or []


def _remote_telegram_rows(trading_day):

    try:
        from app.db.telegram_dispatch_repository import TelegramDispatchRepository

        rows = TelegramDispatchRepository().fetch_for_day(trading_day)

    except Exception as exc:
        print(f"[RENDER CONTEXT] telegram history unavailable: {exc}")

        return None

    if rows is None:
        return None

    return [
        row for row in rows
        if not str(row.get("message_type") or "").startswith("TEST_")
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
        local = 0

    else:
        day = str(trading_day)
        local = len({
            key for key in events["trade_key"].dropna().astype(str)
            if key.rsplit("|", 1)[-1].strip().startswith(day)
        })

    # The CSV lives in whichever container opened the trade, so on the dashboard
    # it is routinely absent and this read 0 while the worker had used its
    # allowance. Both sources undercount in different ways -- the file misses
    # another host, the mirror lags -- so the larger is the safer number to show
    # against a cap.
    remote = _remote_entries_used(trading_day)

    return max(local, remote or 0)


def _remote_entries_used(trading_day):

    try:
        from app.db.paper_trade_repository import PaperTradeRepository

        return PaperTradeRepository().count_opened_on(trading_day)

    except Exception as exc:
        print(f"[RENDER CONTEXT] entry count unavailable: {exc}")

        return None


def closed_trades(events):
    """Completed trades with entry and exit stitched back into one row.

    `paper_trade_events.csv` is append-only and one-sided: the OPEN row carries
    the entry time, the close row carries the exit. Charting a trade needs both.
    """
    if events is None or events.empty or "event_type" not in events.columns:
        return []

    events = events.copy()
    return _stitch_closed(events)


def closed_trades_for_day(events, trading_day):
    """Closed trades for the day, from the CSV when present and Postgres when not.

    `data/daily/<day>/paper_trade_events.csv` is written by the process that ran
    the scan. With scanning on Render the dashboard's container never has it, so
    Today's Result rendered empty on days that had trades.
    """

    stitched = closed_trades(events)

    if stitched:
        return stitched

    return _remote_closed_trades(trading_day)


def _remote_closed_trades(trading_day):

    try:
        from app.db.paper_trade_repository import PaperTradeRepository

        rows = PaperTradeRepository().fetch_closed(trading_day) or []

    except Exception as exc:
        print(f"[RENDER CONTEXT] closed trades unavailable: {exc}")

        return []

    return [
        {
            "trade_key": row.get("trade_key"),
            "symbol": row.get("symbol"),
            "direction": row.get("direction"),
            "entry_price": row.get("entry_price"),
            "exit_price": row.get("close_price"),
            "r_multiple": row.get("r_multiple"),
            "exit_reason": row.get("exit_reason"),
            "entry_time": row.get("opened_at"),
            "exit_time": row.get("closed_at"),
            "closed_how": row.get("trade_status") or row.get("status"),
        }
        for row in rows
    ]


def _stitch_closed(events):
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
    """The scan engine serving this deployment, wherever it is running.

    Falls back to the Postgres heartbeat when there is no supervisor thread in
    this process. Once scanning moves to the Render worker there never will be
    one, and every consumer that only asked `thread_alive` reported a healthy
    system as down -- the Operator Console went further and told the operator to
    restart Streamlit, which would not have helped and would have wiped the
    container's state.

    `running` is the key callers should read. `thread_alive` still means what it
    always did: a supervisor thread inside *this* process.
    """

    try:
        from app.runtime.scan_supervisor import status

        local = status() or {}

    except Exception:
        local = {}

    if local.get("thread_alive"):

        local.setdefault("running", True)
        local.setdefault("owner", "dashboard")

        return local

    try:
        from app.runtime.scan_engine_heartbeat import heartbeat_to_engine_status

        summary = scan_engine_heartbeats() or {}
        live = summary.get("live") or []

        if live:

            return heartbeat_to_engine_status(live[0])

    except Exception as exc:
        print(f"[ENGINE STATUS WARNING] heartbeat unavailable: {exc}")

    local.setdefault("running", False)

    return local


ENGINE_LABELS = {
    "worker": ("Worker engine (Render)", "WORKER ENGINE"),
    "dashboard": ("Dashboard engine (Streamlit)", "DASHBOARD ENGINE"),
}


def engine_label(engine, short=False):
    """Name the engine being reported, instead of trailing the owner behind it.

    `Engine SLEEPING_WEEKEND · worker · every 30 min` packed three unrelated
    facts into one line and left `worker` reading as part of the status rather
    than as the thing being described. Since scanning moved to Render there are
    two engines this could be and the panel shows exactly one of them, so which
    one it is has to lead, not qualify.

    Falls back to the raw owner rather than to a guess: a name nobody recognises
    is a better prompt to investigate than a confident "Dashboard".
    """

    engine = engine or {}
    owner = str(engine.get("owner") or "").strip().lower()

    if not owner and engine.get("thread_alive"):
        owner = "dashboard"

    long_form, short_form = ENGINE_LABELS.get(
        owner,
        (f"{owner or 'unknown'} engine".capitalize(), f"{owner or 'unknown'} engine".upper()),
    )

    return short_form if short else long_form


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


_DB_STATE_CACHE = {"checked_at": 0.0, "state": None}
_DB_STATE_TTL_SECONDS = 30


def database_state():
    """`ON`, `OFF`, or `UNREACHABLE`, cached so a rerun is not a round trip.

    `db_writes_active()` reports intent -- a flag and a non-empty URL -- and the
    sidebar rendered that as "DB writes on". A container holding a URL it cannot
    reach looked identical to a healthy one, so nothing on screen contradicted a
    process that was reading empty results and dropping every write.
    """

    import time

    now = time.monotonic()

    if (_DB_STATE_CACHE["state"] is not None
            and now - _DB_STATE_CACHE["checked_at"] < _DB_STATE_TTL_SECONDS):

        return _DB_STATE_CACHE["state"]

    try:
        from app.db.persistence import database_status

        state = database_status()

    except Exception:
        state = "UNREACHABLE"

    _DB_STATE_CACHE.update({"checked_at": now, "state": state})

    return state


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
        return closed_trades_for_day(self.paper_events, self.trading_day)

    @cached_property
    def engine(self):
        return engine_status()

    @cached_property
    def db_writes_active(self):
        return db_writes_active()

    @cached_property
    def db_state(self):
        """`ON`, `OFF` or `UNREACHABLE`. Prefer this to `db_writes_active`.

        The sidebar was taught reachability and the Operator Console was not, so
        the page's most prominent trust signal went on reporting a flag. That is
        the same split that had the sidebar reporting the Render worker while the
        console two feet away said ENGINE DOWN.
        """

        return database_state()

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
