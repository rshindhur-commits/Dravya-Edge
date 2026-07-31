"""Price charts for the operator console.

The scanner already writes normalized 5m candles to `candles_5m.csv` every scan,
so the app can draw its own chart with the engine's own entry, stop, target and
exit marked on the bars. TradingView cannot do that -- it does not know a trade
exists -- so the two are used for different jobs: this chart for post-trade
review, a TradingView deep link for live discretionary review.

Pure helpers here stay import-safe without streamlit so they remain testable.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

from app.storage.daily_paths import daily_path

ET_TZ = ZoneInfo("America/New_York")
CANDLE_FILE = "candles_5m.csv"

# Every scan appends the window it fetched, so the same bar is written many times
# over a session -- 4,879 of 19,496 rows on 2026-07-31. Dedupe is not optional.
_CANDLE_CACHE: dict[tuple[str, float], pd.DataFrame] = {}


def tradingview_url(symbol, interval="5"):
    """Deep link to TradingView with the symbol and interval already selected.

    `REVIEW_TV_CHART` is the most common action status the engine emits, and
    until now there was no way to act on it without leaving the app and typing
    the ticker by hand.
    """
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return None
    return f"https://www.tradingview.com/chart/?symbol={symbol}&interval={interval}"


def _load_day_candles(trading_day):
    path = daily_path(trading_day, CANDLE_FILE)
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()

    cache_key = (str(path), path.stat().st_mtime)
    cached = _CANDLE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()

    if frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame()

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["timestamp"])
    # Keep the last write of a bar: a later scan sees a more complete candle.
    frame = frame.drop_duplicates(["symbol", "timestamp"], keep="last")
    frame = frame.sort_values("timestamp").reset_index(drop=True)

    _CANDLE_CACHE.clear()
    _CANDLE_CACHE[cache_key] = frame
    return frame


def _select_grid(bars, window):
    """Restrict to one scan's bars so the chart shows a single 5m grid.

    Each scan re-fetches the whole session, and Polygon anchors its aggregates to
    the request, so scan A returns bars on :00/:05/:10 while scan B returns
    :02/:07/:12. Both are valid 5m bars and neither is a duplicate of the other
    by timestamp, so the file holds several interleaved grids -- NVDA on
    2026-07-31 carried three, 578 bars 1-2 minutes apart. Drawing them together
    would overlay candles for overlapping periods and misstate every wick.
    """
    if "scan_id" not in bars.columns:
        return bars

    groups = list(bars.groupby("scan_id"))
    if len(groups) <= 1:
        return bars

    def rank(group):
        scan_id, frame = group
        covers = bool(
            window
            and frame["timestamp"].min() <= min(window)
            and frame["timestamp"].max() >= max(window)
        )
        return (covers, len(frame), str(scan_id))

    return max(groups, key=rank)[1]


def load_candles(trading_day, symbol, limit=120, around=None):
    """5m bars for one symbol on a single consistent grid, oldest first, in ET.

    `around` is an (entry, exit) pair of moments. When supplied the window is
    centred on the trade with context either side, which is what makes an exit
    judgeable -- a blind tail() of the file can land entirely after the trade.
    """
    frame = _load_day_candles(trading_day)
    if frame.empty:
        return pd.DataFrame()

    symbol = str(symbol or "").strip().upper()
    bars = frame[frame["symbol"].astype(str).str.upper() == symbol].copy()
    if bars.empty:
        return pd.DataFrame()

    bars["timestamp"] = bars["timestamp"].dt.tz_convert(ET_TZ)
    window = [moment for moment in (around or ()) if moment is not None]
    bars = _select_grid(bars, window).sort_values("timestamp")

    if window:
        pad = pd.Timedelta(minutes=5 * 20)
        selected = bars[
            (bars["timestamp"] >= min(window) - pad)
            & (bars["timestamp"] <= max(window) + pad)
        ]
        if not selected.empty:
            return selected.reset_index(drop=True)

    return bars.tail(int(limit)).reset_index(drop=True)


def _price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _moment(value):
    if value in (None, "", "None"):
        return None
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(stamp):
        return None
    return stamp.tz_convert(ET_TZ)


def build_markers(trade):
    """Chart overlays for one position or closed trade.

    Accepts either a live paper-trade record or a `paper_trade_events` /
    `trade_exit_snapshots` row -- the field names differ between them, so both
    spellings are checked.
    """
    trade = trade or {}
    return {
        "entry_price": _price(trade.get("entry_price")),
        "stop_price": _price(trade.get("stop_loss") or trade.get("stop_price")),
        "target_price": _price(trade.get("take_profit") or trade.get("target_price")),
        "exit_price": _price(trade.get("exit_price") or trade.get("close_price")),
        "entry_time": _moment(
            trade.get("opened_at_et")
            or trade.get("entry_time")
            or trade.get("opened_at")
        ),
        "exit_time": _moment(
            trade.get("closed_at_et")
            or trade.get("exit_time")
            or trade.get("closed_at")
            or trade.get("event_time_et")
        ),
        "exit_reason": trade.get("exit_reason"),
    }


def _figure(bars, symbol, markers):
    import plotly.graph_objects as go

    markers = markers or {}
    figure = go.Figure(
        data=[go.Candlestick(
            x=bars["timestamp"],
            open=bars["open"],
            high=bars["high"],
            low=bars["low"],
            close=bars["close"],
            name=symbol,
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        )]
    )

    levels = (
        ("entry_price", "Entry", "#42a5f5", "solid"),
        ("stop_price", "Stop", "#ef5350", "dash"),
        ("target_price", "Target", "#26a69a", "dash"),
    )
    for field, label, colour, dash in levels:
        price = markers.get(field)
        if price is None:
            continue
        figure.add_hline(
            y=price,
            line_color=colour,
            line_dash=dash,
            line_width=1,
            annotation_text=f"{label} {price:g}",
            annotation_position="right",
            annotation_font_size=10,
        )

    points = (
        ("entry_time", "entry_price", "Entry", "triangle-up", "#42a5f5"),
        ("exit_time", "exit_price", "Exit", "x", "#ffa726"),
    )
    for time_field, price_field, label, shape, colour in points:
        moment = markers.get(time_field)
        price = markers.get(price_field)
        if moment is None or price is None:
            continue
        figure.add_trace(go.Scatter(
            x=[moment],
            y=[price],
            mode="markers",
            marker={"symbol": shape, "size": 13, "color": colour,
                    "line": {"width": 1, "color": "#ffffff"}},
            name=label,
            hovertemplate=f"{label} %{{y}}<br>%{{x}}<extra></extra>",
        ))

    figure.update_layout(
        height=380,
        margin={"l": 10, "r": 60, "t": 10, "b": 10},
        xaxis_rangeslider_visible=False,
        showlegend=False,
        hovermode="x unified",
    )
    # Only weekends are skipped. An hour rangebreak would silently erase the
    # premarket and after-hours bars the scanner does record and does act on.
    figure.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}])
    return figure


def render_chart(symbol, trading_day, markers=None, limit=120, key=None):
    """Draw one symbol's session with the engine's own levels on it."""
    import streamlit as st

    symbol = str(symbol or "").strip().upper()
    markers = markers or {}
    bars = load_candles(
        trading_day,
        symbol,
        limit=limit,
        around=(markers.get("entry_time"), markers.get("exit_time")),
    )
    if bars.empty:
        st.caption(f"No 5m candles recorded for {symbol} on {trading_day}.")
        render_tradingview_link(symbol)
        return

    try:
        st.plotly_chart(
            _figure(bars, symbol, markers),
            width="stretch",
            key=key or f"chart_{symbol}_{trading_day}",
        )
    except ImportError:
        st.caption("Install plotly to enable price charts.")
        return

    render_tradingview_link(symbol)


def render_tradingview_link(symbol, interval="5", label=None):
    """Inline TradingView link, for the live look this chart cannot replace."""
    import streamlit as st

    url = tradingview_url(symbol, interval=interval)
    if not url:
        return
    st.markdown(
        f"[{label or f'Open {symbol} on TradingView'}]({url})",
        unsafe_allow_html=False,
    )
