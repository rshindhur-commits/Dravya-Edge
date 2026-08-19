"""A streamed price for every symbol the book is currently holding.

Stage 1 (``position_monitor``) polls REST every 20s. That already closes most of
the hole the 300s scan left, but it is still a clock: a reversal inside the 20s
is invisible the same way SPCX's was invisible inside 300s. This replaces the
clock with a subscription -- Polygon pushes a second aggregate per held symbol
and the monitor reacts to arrivals.

Only the symbols in the book are subscribed, and the subscription is re-synced
whenever that set changes. A universe stream would be a different, much larger
thing; this is deliberately the smallest surface that solves the problem.

## Why a price can never simply be trusted here

A socket can stop delivering without closing. If the monitor kept evaluating
stops against the last thing it heard, a dead feed would look exactly like a
motionless market -- and every protective rule would quietly stop working while
appearing healthy. That failure is worse than polling, because polling at least
raises an exception when it breaks.

So every read carries an **age**, and the caller is expected to reject a stale
one and fall back to REST. ``latest()`` never hides how old its answer is.

## Degradation

Streaming depends on the Polygon plan. If the socket cannot be established the
stream reports itself unhealthy and the monitor keeps polling; nothing raises
and nothing stops managing positions. Off unless
``POSITION_MONITOR_STREAM_ENABLED`` is true.
"""

from __future__ import annotations

import os
import threading
import time

# Beyond this a streamed price is treated as unusable and the caller polls
# instead. Second aggregates arrive ~1/s while a symbol trades, so 15s means
# roughly fifteen consecutive missed messages -- comfortably past jitter and far
# inside the 20s poll it replaces.
DEFAULT_MAX_AGE_SECONDS = 15.0


def _log(message):
    """Print, always flushed.

    Render pipes stdout, so Python block-buffers it at 8KB. The scan worker
    prints enough per cycle to keep filling that buffer; this process prints one
    line and then sleeps for twenty seconds, so an unflushed line sits there
    indefinitely and the service reads as dead in the dashboard while running
    perfectly. A monitor whose log is invisible is not a monitor.

    Belt and braces with PYTHONUNBUFFERED rather than instead of it -- the env
    var is a property of the service and the next one will not have it.
    """

    print(message, flush=True)


def stream_enabled():
    """Read at call time so the switch moves without a redeploy."""

    return str(os.getenv("POSITION_MONITOR_STREAM_ENABLED", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def max_age_seconds():
    try:
        return max(2.0, float(os.getenv("POSITION_MONITOR_STREAM_MAX_AGE", "")))
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_SECONDS


class PriceStream:
    """Latest streamed price per symbol, with the age of each.

    One background thread owns the socket. Everything the monitor touches goes
    through the lock, because the monitor reads on its own thread and a torn
    read here is a stop evaluated against half an update.
    """

    def __init__(self, api_key=None):
        self._api_key = api_key or os.getenv("POLYGON_API_KEY")
        self._lock = threading.Lock()
        self._prices = {}
        self._subscribed = set()
        self._client = None
        self._thread = None
        self._healthy = False
        self._last_error = None
        self._stopping = False

    # -- state -------------------------------------------------------------

    def healthy(self):
        with self._lock:
            return self._healthy

    def last_error(self):
        with self._lock:
            return self._last_error

    def latest(self, symbol):
        """(price, age_seconds), or (None, None) when nothing has arrived.

        Age is returned rather than applied so the staleness policy lives with
        the caller that knows what it is about to do with the number.
        """

        with self._lock:
            entry = self._prices.get(symbol)

        if not entry:
            return None, None

        price, at = entry
        return price, max(0.0, time.monotonic() - at)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return True

        if not self._api_key:
            with self._lock:
                self._healthy = False
                self._last_error = "POLYGON_API_KEY is not set"
            return False

        self._stopping = False
        self._thread = threading.Thread(
            target=self._run, name="position-price-stream", daemon=True
        )
        self._thread.start()
        return True

    def stop(self):
        self._stopping = True

        client = self._client

        if client is not None:
            try:
                client.close()
            except Exception:
                pass

        with self._lock:
            self._healthy = False

    def sync(self, symbols):
        """Subscribe to exactly this set, unsubscribing anything else.

        Called every pass. A position that closed must stop consuming a slot,
        and a position that opened must be watched without waiting for a
        reconnect.
        """

        wanted = {s for s in symbols if s}

        with self._lock:
            current = set(self._subscribed)

        if wanted == current or self._client is None:
            return

        add = wanted - current
        drop = current - wanted

        try:
            if add:
                self._client.subscribe(*[f"A.{s}" for s in sorted(add)])

            if drop:
                self._client.unsubscribe(*[f"A.{s}" for s in sorted(drop)])
        except Exception as exc:
            with self._lock:
                self._last_error = f"subscription change failed: {exc}"
            return

        with self._lock:
            self._subscribed = wanted

            for symbol in drop:
                self._prices.pop(symbol, None)

    # -- the socket --------------------------------------------------------

    def _handle(self, messages):
        now = time.monotonic()

        with self._lock:

            for message in messages or []:

                symbol = getattr(message, "symbol", None)
                close = getattr(message, "close", None)

                if symbol and close:
                    self._prices[symbol] = (float(close), now)

            self._healthy = True

    def _run(self):
        from polygon import WebSocketClient

        while not self._stopping:

            try:
                self._client = WebSocketClient(
                    api_key=self._api_key,
                    subscriptions=[f"A.{s}" for s in sorted(self._subscribed)] or None,
                )
                self._client.run(self._handle)

            except Exception as exc:
                with self._lock:
                    self._healthy = False
                    self._last_error = str(exc)

                _log(f"[PRICE STREAM] disconnected: {exc}")

            if self._stopping:
                break

            # A tight reconnect loop against a plan that does not allow
            # streaming would hammer the endpoint and log a wall of identical
            # failures. The monitor is still polling underneath either way.
            time.sleep(5)

        with self._lock:
            self._healthy = False


_stream = None


def get_stream():
    """Process-wide stream. One socket, however many callers."""

    global _stream

    if _stream is None:
        _stream = PriceStream()

    return _stream
