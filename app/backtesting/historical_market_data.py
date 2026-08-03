"""Point-in-time market data for replays.

The live scanner gets its frames from
``app/indicators/technical_indicators.py::get_polygon_data``, which is anchored
to ``datetime.now()`` and therefore cannot be pointed at a past moment. This
module produces the *same frame contract* for an arbitrary historical instant:

    UTC ``DatetimeIndex``, columns ``Open/High/Low/Close/Volume``, sorted.

Two properties are what make a replay honest, and both are easy to get wrong.

**Grid alignment.** Polygon anchors aggregate windows to the ``from_``
timestamp, so an unrounded anchor returns bars on :02/:07/:12 instead of
:00/:05/:10 -- see ``floor_to_bar``'s docstring for the damage that did to
``candles_5m.csv``. Day-bounded date requests return the epoch-aligned grid,
which is the grid live settled on, so that is what is used here.

**Decision lag.** The scanner does not decide on the bar that is forming when
it runs. Measured over the nine live trades of 2026-07-30 and 2026-07-31, the
bar it actually decided on started 7.8-13.0 minutes (mean 9.5) before the scan,
and in all nine cases ``entry_price`` equalled that bar's close exactly. A
replay that read the forming bar would hand the strategy up to five minutes of
information it never had, and would do so invisibly -- the results simply come
out better.

``frame_as_of`` therefore truncates on bar *completion*: a bar is readable only
once its whole interval is in the past. On the nine fixtures that rule alone
reproduces live's decision candle 7/9, close matching to the cent. The other
two are live reading one bar staler still -- the 30s aggregate cache in
``polygon_client`` plus Polygon's own publication latency -- which is jitter
rather than a systematic offset, so it is not modelled by default.
``decision_lag_minutes`` adds delay beyond completion and exists so a
sensitivity run can show whether an edge survives the staler end of that
jitter. An edge that only appears at low lag is a latency artefact, not an
edge.
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from app.utils.runtime_logging import debug_print

POLYGON_BASE_URL = "https://api.polygon.io"

MARKET_TZ = "America/New_York"

# Extra delay, beyond bar completion, before a scan may read a bar. Zero
# reproduces live's decision candle on 7 of the 9 parity fixtures and is not
# optimistic: a bar that has completed is already up to five minutes stale by
# the time the next scan reads it. Raise it to probe latency sensitivity.
DEFAULT_DECISION_LAG_MINUTES = 0.0

_CACHE_ROOT = Path(
    os.getenv(
        "BACKTEST_CACHE_DIR",
        "data/backtest_cache",
    )
)


class HistoricalDataError(RuntimeError):
    """Raised when Polygon cannot supply a window the replay requires.

    Deliberately fatal. A replay that silently proceeds on a short frame
    produces numbers that look like results, and there is no way to tell them
    apart from real ones after the fact.
    """


# Statuses that are not answers. 429 is the rate limiter and 408 is the request
# timing out server-side; a 5xx is a gateway or backend fault, which means
# Polygon never looked at the question, so the identical request can succeed
# moments later. Every other 4xx *is* an answer -- about the key, the plan, or
# the URL -- and will say the same thing however long we wait.
_RETRYABLE_STATUSES = frozenset({408, 429})


def _is_retryable(status_code):

    return status_code in _RETRYABLE_STATUSES or status_code >= 500


def request_with_retry(url, params, timeout=30, max_retries=4, context=""):
    """GET with backoff over rate limits, gateway faults and transport faults.

    Retrying only on 429 is not enough at replay volume. A single scan can
    price seventy contracts, each needing bars and a quote, so a year-long run
    makes millions of requests and will meet dropped connections, read timeouts
    and gateway errors regardless of how well-behaved it is.

    This used to treat every non-429 status as final, on the reasoning that an
    HTTP status is an answer. That holds for 4xx and not for 5xx: a 502 is the
    edge saying it could not reach the backend, which is the *absence* of an
    answer and is exactly the failure a long run meets most. One such 502 -- a
    single quote, mid-run -- aborted a nine-trade parity check, and the same
    request succeeded immediately afterwards. At a year and 26 symbols, refusing
    to retry those means no long replay ever finishes.
    """

    last_error = None

    for attempt in range(max_retries):

        try:

            response = requests.get(url, params=params, timeout=timeout)

        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:

            last_error = exc
            time.sleep(2 ** attempt)
            continue

        if response.status_code == 200:

            return response

        if not _is_retryable(response.status_code):

            raise HistoricalDataError(
                f"Polygon returned {response.status_code} for {context or url}: "
                f"{response.text[:200]}"
            )

        # Kept so exhausting the retries reports what actually kept failing.
        # Reporting `None` here -- which is what a run that exhausted its 429s
        # used to say -- turns a rate limit into an unexplained outage.
        last_error = (
            f"HTTP {response.status_code}: {response.text[:200]}"
        )

        time.sleep(2 ** attempt)

    raise HistoricalDataError(
        f"Polygon unreachable for {context or url} after {max_retries} "
        f"attempts: {last_error}"
    )


def _api_key():

    key = os.getenv("POLYGON_API_KEY")

    if not key:

        raise HistoricalDataError(
            "POLYGON_API_KEY is not set; historical replay cannot run"
        )

    return key


def _cache_path(symbol, multiplier, timespan, start_day, end_day):

    safe_symbol = symbol.replace(":", "_")

    return _CACHE_ROOT / f"{safe_symbol}_{multiplier}{timespan}_{start_day}_{end_day}.parquet"


def _empty_frame():

    frame = pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"]
    )
    frame.index = pd.DatetimeIndex([], tz="UTC", name="Datetime")

    return frame


def fetch_bars(
    ticker,
    start_day,
    end_day,
    multiplier=5,
    timespan="minute",
    use_cache=True,
    max_retries=4,
):
    """Fetch aggregates for ``ticker`` over an inclusive day range.

    Works for both equities (``NVDA``) and option contracts
    (``O:NVDA260807C00197500``) -- Polygon serves them from the same endpoint.

    Results are cached to parquet because a single replay day re-reads the same
    window once per scan, and a year-long run over 26 symbols would otherwise
    spend its entire wall time re-fetching bars that never change.
    """

    cache_file = _cache_path(ticker, multiplier, timespan, start_day, end_day)

    if use_cache and cache_file.exists():

        cached = pd.read_parquet(cache_file)

        if not cached.empty:

            cached.index = pd.to_datetime(cached.index, utc=True)

        return cached

    url = (
        f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}"
        f"/range/{multiplier}/{timespan}/{start_day}/{end_day}"
    )

    results = []
    params = {
        "apiKey": _api_key(),
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
    }
    next_url = url

    while next_url:

        payload = request_with_retry(
            next_url,
            params,
            max_retries=max_retries,
            context=f"{ticker} {start_day}..{end_day}",
        ).json()

        results.extend(payload.get("results") or [])

        next_url = payload.get("next_url")

        # next_url already carries the query string; only the key is needed.
        params = {"apiKey": _api_key()} if next_url else params

    if not results:

        frame = _empty_frame()

    else:

        frame = pd.DataFrame(results)
        frame["Datetime"] = pd.to_datetime(frame["t"], unit="ms", utc=True)
        frame = frame.rename(
            columns={
                "o": "Open",
                "h": "High",
                "l": "Low",
                "c": "Close",
                "v": "Volume",
            }
        )
        frame = frame[
            ["Datetime", "Open", "High", "Low", "Close", "Volume"]
        ]
        frame = frame.set_index("Datetime").sort_index()

    if use_cache:

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(cache_file)

    return frame


def frame_as_of(
    frame,
    as_of,
    bar_minutes=5,
    decision_lag_minutes=DEFAULT_DECISION_LAG_MINUTES,
):
    """Truncate ``frame`` to what a scan at ``as_of`` could legitimately see.

    A bar stamped 11:25 covers 11:25:00-11:29:59 and is not knowable until
    11:30; it becomes readable ``decision_lag_minutes`` after that. The default
    of zero is "readable as soon as it has completed", which matched live on 7
    of the 9 parity fixtures.

    ``as_of`` may be naive (interpreted as ET, matching the ``scan_id`` format)
    or tz-aware.
    """

    if frame is None or frame.empty:

        return _empty_frame()

    moment = pd.Timestamp(as_of)

    if moment.tzinfo is None:

        moment = moment.tz_localize(MARKET_TZ)

    moment = moment.tz_convert("UTC")

    # A bar is readable once it has closed and the lag has elapsed:
    #     bar_start + bar_minutes + lag <= as_of
    cutoff = moment - timedelta(
        minutes=bar_minutes + decision_lag_minutes
    )

    return frame[frame.index <= cutoff].copy()


def load_replay_frames(
    symbol,
    trading_day,
    lookback_days=5,
    use_cache=True,
):
    """Load the 5m history a replay of ``trading_day`` needs.

    ``lookback_days`` exists because indicators are not computable from a
    single session: ``compute_indicators`` refuses frames shorter than its
    per-interval minimum and returns an EMPTY DataFrame rather than raising, so
    a short window degrades into "no signal all morning" rather than an error.
    Five calendar days clears the 1h minimum with room for holidays.
    """

    day = pd.Timestamp(trading_day).date()
    start = day - timedelta(days=lookback_days)

    frame = fetch_bars(
        symbol,
        start.isoformat(),
        day.isoformat(),
        multiplier=5,
        timespan="minute",
        use_cache=use_cache,
    )

    if frame.empty:

        debug_print(
            f"[REPLAY DATA] {symbol} {trading_day}: no bars returned"
        )

    return frame


def scan_times_for_day(trading_day, first="09:30", last="16:00", every_minutes=5):
    """Synthesise an evenly spaced ET scan clock for ``trading_day``.

    Used for historical days that predate the archive. Where real scan times
    exist -- ``scanner_runs.started_at``, or a trade's ``scan_id`` -- prefer
    those: the live cadence is not actually uniform, and parity runs must use
    the real timestamps rather than a synthetic grid.
    """

    day = pd.Timestamp(trading_day).date()

    start = datetime.combine(
        day, datetime.strptime(first, "%H:%M").time()
    )
    end = datetime.combine(
        day, datetime.strptime(last, "%H:%M").time()
    )

    moments = []
    cursor = start

    while cursor <= end:

        moments.append(cursor)
        cursor += timedelta(minutes=every_minutes)

    return moments
