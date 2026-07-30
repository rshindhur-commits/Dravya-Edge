import os
import time
import threading
import random
import logging
from typing import Optional, Dict, Any
from collections import deque

from datetime import datetime
import pytz

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from datetime import timedelta

from app.utils.runtime_logging import debug_print

load_dotenv(override=True)

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()


def get_polygon_api_key():

    return os.getenv(
        "POLYGON_API_KEY",
        POLYGON_API_KEY
    ).strip()


def get_polygon_base_url():

    return os.getenv(
        "POLYGON_BASE_URL",
        "https://api.polygon.io"
    ).rstrip("/")


def redact_polygon_url(url, api_key):

    if api_key:

        return url.replace(
            api_key,
            "<redacted>"
        )

    return url.replace(
        "apiKey=",
        "apiKey=<missing>"
    )
# Configurable rate limit (requests per minute). Set via env var.
_RATE_LIMIT_PER_MINUTE = int(os.getenv("POLYGON_RATE_LIMIT_PER_MINUTE", "1200"))


class TokenBucket:
    """Simple token-bucket rate limiter (thread-unsafe, but fine for single-threaded script).

    Refill tokens at a rate of `rate_per_minute`. Call `consume(1)` to take a token,
    which will sleep until a token is available.
    """

    def __init__(self, rate_per_minute: int):
        self.capacity = rate_per_minute
        self.tokens = rate_per_minute
        self.rate_per_sec = rate_per_minute / 60.0
        self.timestamp = time.monotonic()
        self._lock = threading.Lock()

    def set_rate_per_minute(self, rate_per_minute: int):
        """Adjust the bucket capacity and refill rate (thread-safe)."""
        with self._lock:
            # preserve some tokens proportional to new capacity
            old_capacity = self.capacity
            old_tokens = self.tokens
            self.capacity = max(1, int(rate_per_minute))
            self.rate_per_sec = self.capacity / 60.0
            # scale current tokens to new capacity proportionally
            try:
                scale = self.capacity / float(old_capacity)
            except Exception:
                scale = 1.0
            self.tokens = min(self.capacity, max(0.0, old_tokens * scale))

    def get_rate_per_minute(self) -> int:
        with self._lock:
            return int(self.capacity)

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.timestamp
        self.timestamp = now
        refill = elapsed * self.rate_per_sec
        if refill > 0:
            self.tokens = min(self.capacity, self.tokens + refill)

    def consume(self, tokens: float = 1.0):
        """Consume tokens, blocking until available. Adds small random jitter to waits.

        This method is thread-safe.
        """
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                # compute required wait (seconds) without sleeping while holding lock
                needed = tokens - self.tokens
                wait = needed / max(1e-6, self.rate_per_sec)
            # add jitter to avoid synchronized wakes
            jitter_factor = 1.0 + random.uniform(0, 0.2)
            sleep_time = max(0.01, wait * jitter_factor)
            time.sleep(sleep_time)


# module-level rate limiter
_rate_limiter = TokenBucket(_RATE_LIMIT_PER_MINUTE)


def acquire_rate_limit():
    """Block until a request token is available according to configured rate limit."""
    _rate_limiter.consume(1)


def _auto_tune_from_headers(headers: Dict[str, str]):
    """Use Polygon X-RateLimit headers to gently adjust the local token-bucket rate.

    Expected headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
    `X-RateLimit-Reset` is expected to be an epoch seconds timestamp.
    This computes an estimated safe per-minute rate from remaining/time-until-reset
    and applies a smoothed update to the token-bucket to avoid spikes.
    """
    if not headers:
        return

    rl_rem = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
    rl_reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
    if not rl_rem or not rl_reset:
        return

    try:
        rem = int(rl_rem)
        reset = int(rl_reset)
    except Exception:
        return

    now = int(time.time())
    seconds_to_reset = reset - now
    if seconds_to_reset <= 0:
        return

    # estimate allowed requests per minute until reset
    estimated_per_min = int(rem * 60.0 / float(seconds_to_reset))
    if estimated_per_min < 1:
        estimated_per_min = 1

    current = _rate_limiter.get_rate_per_minute()
    # smooth changes: blend current and estimated
    new_rate = int(current * 0.7 + estimated_per_min * 0.3)
    # avoid huge jumps upward: cap to 2x current unless estimated is larger
    new_rate = max(1, min(new_rate, max(current * 2, estimated_per_min)))

    try:
        _rate_limiter.set_rate_per_minute(new_rate)
        logger.info(
            "Auto-tuned local rate per minute to %s based on headers Limit=%s Remaining=%s Reset=%s",
            new_rate,
            headers.get("X-RateLimit-Limit"),
            rl_rem,
            rl_reset,
        )
    except Exception:
        logger.exception("Failed to auto-tune rate limiter from headers")


# keep a short history of recent rate-limit header observations for manual tuning
_HEADER_HISTORY_MAX = int(os.getenv("POLYGON_HEADER_HISTORY_MAX", "60"))
_header_history = deque(maxlen=_HEADER_HISTORY_MAX)  # stores (ts, limit, remaining, reset)


def record_rate_limit_headers(headers: Dict[str, str]):
    try:
        rl_limit = headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit")
        rl_rem = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        rl_reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        if rl_limit is None and rl_rem is None and rl_reset is None:
            return

        limit = int(rl_limit) if rl_limit is not None else None
        rem = int(rl_rem) if rl_rem is not None else None
        reset = int(rl_reset) if rl_reset is not None else None

        _header_history.append((int(time.time()), limit, rem, reset))
    except Exception:
        logger.exception("Failed to record rate-limit headers")


# Simple live metrics for requests and headers
_metrics_lock = threading.Lock()
_metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "rate_limit_responses": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "api_time_total_sec": 0.0,
    "api_time_count": 0,
    "cache_read_time_total_sec": 0.0,
    "cache_read_count": 0,
    "last_headers": None,
}
# timestamps of recent requests to compute requests/min
_request_times = deque(maxlen=1000)


def _record_request(
    status: Optional[int],
    headers: Optional[Dict[str, str]] = None,
    success: bool = False,
    elapsed_seconds: float | None = None,
):
    with _metrics_lock:
        _metrics["total_requests"] += 1
        if success:
            _metrics["successful_requests"] += 1
        else:
            _metrics["failed_requests"] += 1
        if elapsed_seconds is not None:
            _metrics["api_time_total_sec"] += elapsed_seconds
            _metrics["api_time_count"] += 1
        if status == 429:
            _metrics["rate_limit_responses"] += 1
        if headers:
            _metrics["last_headers"] = {
                "X-RateLimit-Limit": headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit"),
                "X-RateLimit-Remaining": headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining"),
                "X-RateLimit-Reset": headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset"),
            }
        _request_times.append(time.time())


def _record_cache_lookup(hit: bool, elapsed_seconds: float):
    with _metrics_lock:
        if hit:
            _metrics["cache_hits"] += 1
        else:
            _metrics["cache_misses"] += 1
        _metrics["cache_read_time_total_sec"] += elapsed_seconds
        _metrics["cache_read_count"] += 1


def get_metrics():
    """Return a snapshot of metrics including requests per minute."""
    with _metrics_lock:
        now = time.time()
        # count timestamps within last 60 seconds
        rpm = sum(1 for t in _request_times if now - t <= 60)
        snapshot = dict(_metrics)
        snapshot["requests_per_minute"] = rpm
        snapshot["recent_request_count"] = len(_request_times)
        cache_total = snapshot["cache_hits"] + snapshot["cache_misses"]
        snapshot["cache_hit_rate"] = (
            round(snapshot["cache_hits"] / cache_total * 100, 1)
            if cache_total > 0
            else None
        )
        snapshot["average_api_time"] = (
            round(snapshot["api_time_total_sec"] / snapshot["api_time_count"], 4)
            if snapshot["api_time_count"] > 0
            else None
        )
        snapshot["average_cache_read_time"] = (
            round(snapshot["cache_read_time_total_sec"] / snapshot["cache_read_count"], 6)
            if snapshot["cache_read_count"] > 0
            else None
        )
        return snapshot


def suggest_rate_from_history(safety_margin: float = 0.8) -> Optional[int]:
    """Compute a conservative suggested requests-per-minute based on recent header history.

    Uses the formula used in auto-tune: estimated_per_min = rem * 60 / seconds_to_reset.
    Returns the 25th percentile (conservative) estimate scaled by safety_margin.
    """
    estimates = []
    now = int(time.time())
    for ts, limit, rem, reset in list(_header_history):
        if rem is None or reset is None:
            continue
        seconds_to_reset = reset - now
        if seconds_to_reset <= 0:
            continue
        try:
            est = rem * 60.0 / float(seconds_to_reset)
            if est >= 1:
                estimates.append(est)
        except Exception:
            continue

    if not estimates:
        return None

    estimates.sort()
    # choose lower quartile for conservatism
    idx = max(0, int(len(estimates) * 0.25) - 1)
    q = estimates[idx]
    suggested = int(max(1, q * safety_margin))
    return suggested


# Simple in-memory TTL cache for identical aggregate requests
_CACHE_TTL = float(os.getenv("POLYGON_CACHE_TTL", "2"))
_cache: dict = {}


def _cache_get(key):
    entry = _cache.get(key)
    if not entry:
        return None
    ts, val = entry
    if time.monotonic() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    return val


def _cache_set(key, val):
    _cache[key] = (time.monotonic(), val)


def get_aggs_cached(symbol: str, multiplier: int, timespan: str, from_: int, to: int, limit: int = 200, force_refresh: bool = False):
    """Fetch aggregates with a short TTL cache to avoid duplicate calls.

    Returns a list of plain dicts with keys: timestamp, open, high, low, close, volume
    """
    key = (symbol, multiplier, timespan, from_, to, limit)

    cache_read_start = time.perf_counter()
    cached = None if force_refresh else _cache_get(key)
    cache_read_seconds = time.perf_counter() - cache_read_start

    if cached is not None:

        _record_cache_lookup(True, cache_read_seconds)

        debug_print(
            f"[POLYGON CACHE HIT] {symbol} {multiplier}{timespan}"
        )
        return cached

    _record_cache_lookup(False, cache_read_seconds)

    # acquire token before making the request
    acquire_rate_limit()

    from datetime import datetime

    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")

    debug_print(
        f"[FROM ET] "
        f"{datetime.fromtimestamp(from_ / 1000, tz=et)}"
    )

    debug_print(
        f"[TO ET] "
        f"{datetime.fromtimestamp(to / 1000, tz=et)}"
    )

    # Use HTTP aggregates endpoint so we can inspect response headers
    url = f"{get_polygon_base_url()}/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from_}/{to}"
    polygon_api_key = get_polygon_api_key()

    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 1000,
        "apiKey": polygon_api_key,
    }

    resp = safe_request(url, params=params)
    safe_url = redact_polygon_url(
        resp.url,
        polygon_api_key
    )
    debug_print(f"[POLYGON URL] {safe_url}")

    debug_print(
        f"[POLYGON RESPONSE STATUS CODE] "
        f"{resp.status_code}"
    )

    server_time = resp.headers.get("Date")

    debug_print(
        f"[POLYGON SERVER TIME] {server_time}"
    )

    try:
        payload = resp.json()

        polygon_status = payload.get("status")
        results_count = payload.get("resultsCount")

        debug_print(f"[POLYGON STATUS] {polygon_status}")
        debug_print(f"[POLYGON RESULTS COUNT] {results_count}")

        if polygon_status not in ["OK", "DELAYED"]:

            print(
                f"[POLYGON ERROR STATUS] {symbol}: {polygon_status}"
            )

            return []

        if not payload.get("results"):

            print(
                f"[NO RESULTS RETURNED] {symbol}"
            )

            return []

        if payload.get("results"):

            latest_raw = payload["results"][-1]

            latest_ts = latest_raw.get("t")

            eastern = pytz.timezone("America/New_York")

            latest_dt = (
                datetime.utcfromtimestamp(latest_ts / 1000)
                .replace(tzinfo=pytz.utc)
                .astimezone(eastern)
            )

            current_et = datetime.now(eastern)

            market_closed = (
                current_et.hour >= 20
                or current_et.hour < 4
            )            

            delay_minutes = (
                current_et - latest_dt
            ).total_seconds() / 60

            debug_print(
                f"[LATEST RAW ET] {latest_dt}"
            )

            debug_print(
                f"[CURRENT ET] {current_et}"
            )

            debug_print(
                f"[RAW DATA DELAY] {round(delay_minutes, 2)} min"
            )

            # =====================================
            # VALID TRADING DAY CHECK
            # =====================================

            if current_et.hour < 4:

                expected_date = (
                    current_et - timedelta(days=1)
                ).date()

            else:

                expected_date = current_et.date()

            debug_print("\n===== LAST 5 POLYGON CANDLES =====")

            for candle in payload["results"][-5:]:

                ts = candle["t"]

                dt = (
                    datetime.utcfromtimestamp(ts / 1000)
                    .replace(tzinfo=pytz.utc)
                    .astimezone(eastern)
                )

                debug_print(dt)

            debug_print("=================================\n")



            if (
                latest_dt.date() != expected_date
                and not market_closed
            ):
                print(
                    f"[STALE DAY ERROR] {symbol} "
                    f"latest={latest_dt.date()} "
                    f"expected={expected_date}"
                )

                return []

            debug_print(
                f"[LATEST RAW POLYGON TS] "
                f"{latest_raw.get('t')}"
            )

            ts = latest_raw.get("t")

            dt = (
                datetime.utcfromtimestamp(ts / 1000)
                .replace(tzinfo=pytz.utc)
                .astimezone(
                    pytz.timezone("America/New_York")
                )
            )

            debug_print(f"[LATEST RAW ET] {dt}")            


        #print(f"[RAW POLYGON PAYLOAD] {symbol}")
        #print(payload)
    except ValueError:
        logger.error("Non-JSON response from Polygon aggs for %s: %s", symbol, resp.text[:200])
        return []

    #print(f"[RAW POLYGON PAYLOAD] {symbol}")
    #print(payload)    
    raw_results = payload.get("results", [])

    results = []
    for agg in raw_results:
        # each agg is expected to be a dict with numeric fields
        ts = agg.get("t") or agg.get("timestamp") or agg.get("T")

        eastern = pytz.timezone("America/New_York")

        dt_et = (
            datetime.utcfromtimestamp(ts / 1000)
            .replace(tzinfo=pytz.utc)
            .astimezone(eastern)
        )

        # Keep ONLY regular session candles
        # if not (
        #     (dt_et.hour > 9 or (dt_et.hour == 9 and dt_et.minute >= 30))
        #     and
        #     (dt_et.hour < 16)
        # ):
        #     continue


        # polygon aggs keys: t (timestamp), o, h, l, c, v
        op = agg.get("o") or agg.get("open")
        hi = agg.get("h") or agg.get("high")
        lo = agg.get("l") or agg.get("low")
        cl = agg.get("c") or agg.get("close")
        vol = agg.get("v") or agg.get("volume")

        results.append({
            "timestamp": ts,
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": vol,
        })

    debug_print(f"[TOTAL CANDLES RETURNED] {symbol}: {len(results)}")
    _cache_set(key, results)
    return results


logger = logging.getLogger(__name__)


# configure a session with retries/backoff for transient errors (including 429)
_session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=1,
    # Let safe_request handle 429 specially; retry adapter handles server errors only
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=("GET",),
)
adapter = HTTPAdapter(max_retries=retries)
_session.mount("https://", adapter)
_session.mount("http://", adapter)


def safe_request(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> requests.Response:
    """Make a GET request using a session with retries.

    Handles 429 responses with exponential backoff and raises for other HTTP errors.
    """

    attempt = 0
    max_attempts = 2

    while attempt < max_attempts:
        try:
            request_start = time.perf_counter()
            resp = _session.get(url, params=params, timeout=timeout)
            elapsed_seconds = time.perf_counter() - request_start

            #print("\n================ POLYGON DEBUG ================")
            #print("URL:", resp.url)
            #print("STATUS:", resp.status_code)
            #print("RESPONSE:", resp.text)
            #print("================================================\n")

            if resp.status_code == 400:
                logger.error(
                    "Bad Polygon request: %s",
                    resp.text
                )
                resp.raise_for_status()

            if resp.status_code == 403:
                logger.error(
                    "Polygon Forbidden response: %s",
                    resp.text
                )
                resp.raise_for_status()


            if resp.status_code == 429:
                _record_request(
                    resp.status_code,
                    resp.headers,
                    success=False,
                    elapsed_seconds=elapsed_seconds,
                )
                # exponential backoff with jitter
                backoff = min(3, (2 ** attempt))
                jitter = random.uniform(0, backoff * 0.4)
                sleep_time = min(backoff + jitter, 3)
                logger.warning(
                    "Polygon API rate limit (429). Backing off %.1fs (base=%s, jitter=%.2f) attempt %s",
                    sleep_time,
                    backoff,
                    jitter,
                    attempt + 1,
                )
                time.sleep(sleep_time)
                attempt += 1
                continue

            resp.raise_for_status()
            _record_request(
                resp.status_code,
                resp.headers,
                success=True,
                elapsed_seconds=elapsed_seconds,
            )

            # Log Polygon rate-limit headers if present to help tuning
            try:
                headers = resp.headers
                rl_limit = headers.get("X-RateLimit-Limit")
                rl_rem = headers.get("X-RateLimit-Remaining")
                rl_reset = headers.get("X-RateLimit-Reset")
                if rl_limit or rl_rem or rl_reset:
                    logger.info(
                        "Polygon RateLimit headers for %s: Limit=%s Remaining=%s Reset=%s",
                        url,
                        rl_limit,
                        rl_rem,
                        rl_reset,
                    )
                    # record headers for later manual tuning
                    try:
                        record_rate_limit_headers(resp.headers)
                    except Exception:
                        logger.exception("Failed to record headers")
                    # attempt to auto-tune local rate limiter using headers
                    try:
                        _auto_tune_from_headers(resp.headers)
                    except Exception:
                        logger.exception("Auto-tune failed")
            except Exception:
                # never fail because of logging
                logger.exception("Failed to read rate-limit headers")

            return resp

        except requests.RequestException as e:
            try:
                _record_request(
                    getattr(e.response, "status_code", None),
                    getattr(e.response, "headers", None),
                    success=False,
                    elapsed_seconds=(time.perf_counter() - request_start),
                )
            except Exception:
                pass
            logger.exception("Request error on attempt %s: %s", attempt + 1, e)
            print(
                f"[REQUEST ERROR] "
                f"attempt={attempt+1} "
                f"error={e}"
            )
            # small backoff with jitter before retrying
            backoff = min(30, 2 ** attempt)
            jitter = random.uniform(0, backoff * 0.3)
            sleep_time = min(backoff + jitter, 3)
            print(
                f"[RETRY SLEEP] {sleep_time:.2f}s"
            )

            time.sleep(sleep_time)
            attempt += 1

    raise RuntimeError("Failed to get a successful response from Polygon after retries")


def get_last_price(symbol: str) -> Optional[float]:
    """Return the previous close price for `symbol` or None on error.

    Uses `safe_request` and performs robust JSON parsing with clear logging
    so errors in responses are visible for debugging.
    """

    polygon_api_key = get_polygon_api_key()

    if not polygon_api_key:
        logger.error("POLYGON_API_KEY not set in environment")
        return None

    # short TTL cache for last-price lookups to avoid calling /prev repeatedly
    LAST_PRICE_TTL = float(os.getenv("POLYGON_LAST_PRICE_TTL", "10"))
    if not hasattr(get_last_price, "_cache"):
        get_last_price._cache = {}

    cache_key = symbol
    entry = get_last_price._cache.get(cache_key)
    if entry:
        ts, val = entry
        if time.monotonic() - ts <= LAST_PRICE_TTL:
            return val

    url = f"{get_polygon_base_url()}/v2/aggs/ticker/{symbol}/prev"
    params = {
        "adjusted": "true",
        "apiKey": polygon_api_key
    }

    try:
        resp = safe_request(url, params=params)
        safe_url = redact_polygon_url(
            resp.url,
            polygon_api_key
        )
        debug_print(f"[FINAL URL] {safe_url}")
    except Exception as e:
        logger.error("Failed to fetch last price for %s: %s", symbol, e)
        return None

    try:
        data = resp.json()
    except ValueError:
        # log the raw body for debugging when response isn't valid JSON
        logger.error("Non-JSON response for %s: %s", symbol, resp.text[:1000])
        return None

    results = data.get("results")
    if not results:
        logger.debug("No results key in Polygon response for %s: %s", symbol, data)
        return None

    try:
        val = float(results[0]["c"])
        # cache result
        get_last_price._cache[cache_key] = (time.monotonic(), val)
        return val
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.exception("Unexpected results format for %s: %s", symbol, e)
        return None
