from polygon import RESTClient
from dotenv import load_dotenv

import pandas as pd
import ta
import os
from datetime import datetime, timedelta

import time

from datetime import (
    datetime,
    timedelta,
    timezone
)

from app.utils.polygon_client import (
    get_aggs_cached
)

from app.mock.load_mock_aggs import (
    load_mock_aggs
)

from app.config.settings import settings
from app.utils.runtime_logging import debug_print

load_dotenv()

client = RESTClient(os.getenv("POLYGON_API_KEY"))
extended_hours = (

    os.getenv(
        "USE_EXTENDED_HOURS",
        "false"
    ).lower() == "true"

)

USE_MOCK_MARKET_DATA = settings.use_mock_market_data

# =====================================
# Live data stale thresholds
# =====================================

MAX_DELAY_REGULAR = 20

MAX_DELAY_EXTENDED = 25


def get_polygon_data(
    symbol,
    multiplier,
    timespan,
    days_back,
    force_refresh=False,
):
    

    # import pytz

    # eastern = pytz.timezone("US/Eastern")

    # Add safety buffer to avoid incomplete/future candles
    # end_date = (
    #     datetime.now(eastern)
    #     - timedelta(minutes=30)
    # )

    from zoneinfo import ZoneInfo
    from datetime import timezone

    market_tz = ZoneInfo("America/New_York")

    import time

    debug_print("=" * 60)
    debug_print("[SYSTEM DEBUG]")
    debug_print(f"time.time() = {time.time()}")
    debug_print(f"datetime.utcnow() = {datetime.utcnow()}")
    debug_print(f"datetime.now() = {datetime.now()}")
    debug_print("=" * 60)    

    now_market = datetime.now(market_tz)

    utc_now = datetime.now(timezone.utc)

    # =====================================
    # FIX TRADING DAY ROLLOVER
    # =====================================

    # If after midnight ET but before premarket,
    # still use PREVIOUS trading day

    if now_market.hour < 4:

        trading_date = (
            now_market - timedelta(days=1)
        ).date()

    else:

        trading_date = now_market.date()    

    # =====================================
    # SESSION-AWARE REQUEST WINDOWS
    # =====================================

    #PREMARKET_START_HOUR = 9

    start_hour = 4 if extended_hours else 9
    start_minute = 0 if extended_hours else 30

    # =====================================
    # Historical Lookback Window
    # =====================================

    # from_date = int(
    #     (
    #         utc_now - timedelta(days=days_back)
    #     ).timestamp() * 1000
    # )

    from_date = int(
        (
            now_market - timedelta(days=days_back)
        ).timestamp() * 1000
    )    

    # =====================================
    # ROUND TO COMPLETED MARKET CANDLE
    # =====================================

    if timespan == "minute":

        completed_minute = (
            now_market.minute // multiplier
        ) * multiplier

        rounded_market = now_market.replace(
            minute=completed_minute,
            second=0,
            microsecond=0
        )

    elif timespan == "hour":

        rounded_market = now_market.replace(
            minute=0,
            second=0,
            microsecond=0
        )

    else:

        rounded_market = now_market.replace(
            second=0,
            microsecond=0
        )

    to_date = int(
        rounded_market.timestamp() * 1000
    )

    # # Round to completed candle
    # if timespan == "minute":

    #     minute = (
    #         now_market.minute // multiplier
    #     ) * multiplier

    #     to_date = now_market.replace(
    #         minute=minute,
    #         second=0,
    #         microsecond=0
    #     )

    # elif timespan == "hour":

    #     to_date = now_market.replace(
    #         minute=0,
    #         second=0,
    #         microsecond=0
    #     )

    # else:

    #     to_date = now_market.replace(
    #         second=0,
    #         microsecond=0
    #     )

    # =====================================
    # Market Session Detection
    # =====================================

    market_hour = now_market.hour
    market_minute = now_market.minute

    market_time = (
        market_hour * 60
    ) + market_minute

    # =====================================
    # ET MARKET SESSION DETECTION
    # =====================================

    market_minutes = (
        now_market.hour * 60
    ) + now_market.minute

    PREMARKET_START = 4 * 60          # 4:00 AM ET
    REGULAR_START = 9 * 60 + 30      # 9:30 AM ET
    REGULAR_END = 16 * 60            # 4:00 PM ET
    AFTERHOURS_END = 20 * 60         # 8:00 PM ET

    if market_minutes < PREMARKET_START:

        market_session = "CLOSED"

    elif market_minutes < REGULAR_START:

        market_session = "PREMARKET"

    elif market_minutes < REGULAR_END:

        market_session = "REGULAR"

    elif market_minutes < AFTERHOURS_END:

        market_session = "AFTERHOURS"

    else:

        market_session = "CLOSED"

    #print(f"[MARKET SESSION] {symbol}: {market_session}")

    # =====================================
    # SESSION-SAFE END TIMES
    # =====================================

    if market_session == "PREMARKET":

        # allow live premarket candles up to current time
        completed_minute = (
            now_market.minute // multiplier
        ) * multiplier

        rounded_market = now_market.replace(
            minute=completed_minute,
            second=0,
            microsecond=0
        )

    elif market_session == "REGULAR":

        # allow live regular candles
        completed_minute = (
            now_market.minute // multiplier
        ) * multiplier

        rounded_market = now_market.replace(
            minute=completed_minute,
            second=0,
            microsecond=0
        )

    elif market_session == "AFTERHOURS":

        # allow live AH candles up to 8 PM ET only
        afterhours_close = datetime.combine(
            trading_date,
            datetime.min.time()
        ).replace(
            hour=20,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=market_tz
        )

        rounded_market = min(
            now_market,
            afterhours_close
        )

    else:

        # CLOSED session
        close_hour = 20 if extended_hours else 16

        rounded_market = datetime.combine(
            trading_date,
            datetime.min.time()
        ).replace(
            hour=close_hour,
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=market_tz
        )

    to_date = int(
        rounded_market.timestamp() * 1000
    )

    debug_print(
        f"[MARKET SESSION] "
        f"{symbol}: {market_session}"
    )    


    # # =====================================
    # # SIMPLE POLYGON TIME REQUEST
    # # =====================================

    # from_date = int(
    #     (
    #         datetime.utcnow()
    #         - timedelta(days=days_back)
    #     ).timestamp() * 1000
    # )

    # to_date = int(
    #     datetime.utcnow().timestamp() * 1000
    # )


    # from_str = start_date.strftime("%Y-%m-%d")
    # to_str = to_date.strftime("%Y-%m-%d")

    #print(start_date.tzinfo)
    #print(to_date.tzinfo)
    # print(start_date)
    # print(to_date)

    MAX_RETRIES = 5

    for attempt in range(MAX_RETRIES):

        try:

            # Respect local client-side rate limit before calling Polygon
            #acquire_rate_limit()

            debug_print(
                f"[REQUEST] {symbol} "
                f"FROM={from_date} "
                f"TO={to_date}"
            )            

            aggs = get_aggs_cached(
                symbol=symbol,
                multiplier=multiplier,
                timespan=timespan,
                from_=from_date,
                to=to_date,
                limit=1000,
                force_refresh=force_refresh,
            )

            debug_print(f"[TOTAL AGGS] {symbol}: {len(aggs)}")        

            debug_print(
                f"[RAW AGGS] {symbol} "
                f"count={len(aggs)}"
            )

            data = []

            for agg in aggs:

                # aggs may be normalized dicts from cache or polygon objects
                if isinstance(agg, dict):
                    ts = agg.get("timestamp", agg.get("t"))

                    op = agg.get("open", agg.get("o"))

                    hi = agg.get("high", agg.get("h"))

                    lo = agg.get("low", agg.get("l"))

                    cl = agg.get("close", agg.get("c"))

                    vol = agg.get("volume", agg.get("v"))
                else:
                    ts = getattr(agg, "timestamp", None)
                    op = getattr(agg, "open", None)
                    hi = getattr(agg, "high", None)
                    lo = getattr(agg, "low", None)
                    cl = getattr(agg, "close", None)
                    vol = getattr(agg, "volume", None)

                data.append({
                    "Datetime": pd.to_datetime(ts, unit="ms", utc=True),
                    "Open": op,
                    "High": hi,
                    "Low": lo,
                    "Close": cl,
                    "Volume": vol,
                })

            df = pd.DataFrame(data)

            # =====================================
            # Mock fallback for stale/weekend feeds
            # =====================================

            if df.empty:

                debug_print(
                    f"[NO RESULTS RETURNED] {symbol}"
                )

                if USE_MOCK_MARKET_DATA:

                    debug_print(
                        f"[USING MOCK AGGS] {symbol}"
                    )

                    mock_aggs = load_mock_aggs(symbol)

                    debug_print(
                        f"[MOCK AGGS COUNT] "
                        f"{len(mock_aggs)}"
                    )

                    mock_data = []

                    for agg in mock_aggs:

                        mock_data.append({

                            "Datetime": pd.to_datetime(
                                agg["t"],
                                unit="ms",
                                utc=True
                            ),

                            "Open": agg["o"],

                            "High": agg["h"],

                            "Low": agg["l"],

                            "Close": agg["c"],

                            "Volume": agg["v"]

                        })

                    df = pd.DataFrame(mock_data)

                else:

                    return df

            df.set_index("Datetime", inplace=True)

            df.sort_index(inplace=True)

            debug_print(
                f"[FIRST CANDLE] {symbol}: "
                f"{df.index[0]}"
            )

            latest_et = (
                df.index[-1]
                .tz_convert("America/New_York")
            )

            debug_print(
                f"[LAST CANDLE ET] "
                f"{symbol}: {latest_et}"
            )  

            debug_print(
                f"[CURRENT ET] "
                f"{symbol}: {now_market}"
            )        

            latest_candle_time = df.index[-1]

            current_utc = datetime.now(timezone.utc)

            latest_et = latest_candle_time.tz_convert(
                "America/New_York"
            )

            current_et = current_utc.astimezone(
                ZoneInfo("America/New_York")
            )

            # =====================================
            # SAME DAY VALIDATION
            # =====================================

            # =====================================
            # VALID TRADING DAY CHECK
            # =====================================

            expected_date = trading_date

            # =====================================
            # Skip stale-day validation for mocks
            # =====================================



            # =====================================
            # STALE DELAY VALIDATION
            # =====================================        

            delay_minutes = (
                current_utc - latest_candle_time
            ).total_seconds() / 60

            debug_print(
                f"[SESSION CHECK] "
                f"session={market_session} "
                f"delay={round(delay_minutes,2)}"
            )                

            # =====================================
            # Dynamic delay thresholds
            # =====================================

            # =====================================
            # Delay validation only during LIVE sessions
            # =====================================

            if market_session in ["PREMARKET", "REGULAR", "AFTERHOURS"]:

                if market_session == "REGULAR":
                    max_delay = MAX_DELAY_REGULAR
                else:
                    max_delay = MAX_DELAY_EXTENDED

                if (
                    not USE_MOCK_MARKET_DATA
                    and delay_minutes > max_delay
                ):

                    print(
                        f"[STALE DATA BLOCKED] "
                        f"{symbol} "
                        f"delay={round(delay_minutes,2)}m"
                    )

                    print(
                        f"[STALE LIVE DATA DETECTED] {symbol}"
                    )

                    return pd.DataFrame()

            debug_print(
                f"[DATA DELAY] "
                f"{symbol}: "
                f"{round(delay_minutes, 2)} min"
            )          

            debug_print(
                f"[DATA CHECK] {symbol} "
                f"latest candle: {latest_candle_time}"
            )

            debug_print(
                f"[DATA CHECK] {symbol} "
                f"latest close: "
                f"{df['Close'].iloc[-1]}"
            )          

            return df

        except Exception as e:

            import traceback

            traceback.print_exc()            

            error_message = str(e)

            if "400" in error_message:

                print(
                    f"[BAD REQUEST] {symbol}"
                )

                raise e            

            if "429" in error_message:

                backoff = (2 ** attempt)

                print(
                    f"[RATE LIMIT] "
                    f"{symbol} {timespan} "
                    f"retry {attempt + 1} backoff={backoff}s"
                )

                time.sleep(backoff)

                # try again
                continue

            else:
                raise e

    print(
        f"[FAILED AFTER RETRIES] "
        f"{symbol} {timespan}"
    )

    return pd.DataFrame()


def compute_indicators(
    df,
    interval = "5m",
    symbol="UNKNOWN"
):
    
    if df.empty:
        return df

    # Minimum candles required
    interval_minimums = {

        "5m": 25,

        "15m": 10,

        "1h": 5

    }

    MIN_CANDLES = interval_minimums.get(
        interval,
        20
    )

    if len(df) < MIN_CANDLES:

        print(
            f"[LOW DATA WARNING] "
            f"{symbol or 'UNKNOWN'} "
            f"candles={len(df)}"
        )

        return pd.DataFrame()


    # EMA
    df["EMA9"] = ta.trend.ema_indicator(
        df["Close"],
        window=9
    )

    df["EMA20"] = ta.trend.ema_indicator(
        df["Close"],
        window=20
    )

    # EMA Slope
    df["EMA9_SLOPE"] = (
        df["EMA9"] - df["EMA9"].shift(1)
    )

    # MACD
    macd_config = {

        "5m": {
            "slow": 26,
            "fast": 12,
            "signal": 9
        },

        "15m": {
            "slow": 18,
            "fast": 8,
            "signal": 6
        },

        "1h": {
            "slow": 10,
            "fast": 5,
            "signal": 3
        }

    }

    config = macd_config.get(
        interval,
        {
            "slow": 26,
            "fast": 12,
            "signal": 9
        }
    )

    macd = ta.trend.MACD(

        df["Close"],

        window_slow=config["slow"],

        window_fast=config["fast"],

        window_sign=config["signal"]

    )

    df["MACD"] = macd.macd()
    df["MACD_SIGNAL"] = macd.macd_signal()


    rsi_window_map = {

        "5m": 14,

        "15m": 10,

        "1h": 5

    }

    rsi_window = rsi_window_map.get(
        interval,
        14
    )


    # RSI
    df["RSI"] = ta.momentum.rsi(
        df["Close"],
        window=rsi_window
    )

    # RSI Slope
    df["RSI_SLOPE"] = (
        df["RSI"] - df["RSI"].shift(1)
    )

    # Volume
    df["AVG_VOLUME"] = (
        df["Volume"]
        .rolling(window=20)
        .mean()
    )

    df["REL_VOLUME"] = (
        df["Volume"] / df["AVG_VOLUME"]
    )


    # Volume Trend
    df["VOLUME_SMA5"] = (
        df["Volume"]
        .rolling(window=5)
        .mean()
    )

    df["VOLUME_SMA20"] = (
        df["Volume"]
        .rolling(window=20)
        .mean()
    )

    # Increasing participation
    df["VOLUME_TREND"] = (
        df["VOLUME_SMA5"]
        > df["VOLUME_SMA20"]
    )

    # Volume spike detection
    df["VOLUME_SPIKE"] = (
        df["REL_VOLUME"] > 2.0
    )

    # =========================
    # Session VWAP
    # =========================

    typical_price = (
        df["High"] +
        df["Low"] +
        df["Close"]
    ) / 3

    # Convert timestamps to ET
    et_index = df.index.tz_convert(
        "America/New_York"
    )

    # Trading day
    df["SESSION_DATE"] = et_index.date

    # RTH flag
    df["IS_RTH"] = (
        (
            et_index.hour > 9
        ) |
        (
            (et_index.hour == 9)
            &
            (et_index.minute >= 30)
        )
    ) & (
        et_index.hour < 16
    )

    # Only include RTH candles in VWAP
    df["TPV"] = (
        typical_price *
        df["Volume"] *
        df["IS_RTH"].astype(int)
    )

    df["VOL_RTH"] = (
        df["Volume"] *
        df["IS_RTH"].astype(int)
    )

    # Cumulative RTH only
    df["CUM_TPV"] = (
        df.groupby("SESSION_DATE")["TPV"]
        .cumsum()
    )

    df["CUM_VOL"] = (
        df.groupby("SESSION_DATE")["VOL_RTH"]
        .cumsum()
    )

    df["VWAP"] = (
        df["CUM_TPV"] /
        df["CUM_VOL"].replace(0, pd.NA)
    )

    # Forward fill VWAP into ETH candles
    df["VWAP"] = (
        df["VWAP"]
        .ffill()
    )

    # VWAP Distance %
    df["VWAP_DISTANCE"] = (
        (
            df["Close"] - df["VWAP"]
        ) / df["VWAP"]
    ) * 100

    # =====================================
    # Minimum candle protection for ATR
    # =====================================

    atr_window_map = {

        "5m": 14,

        "15m": 10,

        "1h": 5

    }

    atr_window = atr_window_map.get(
        interval,
        14
    )

    if len(df) < atr_window:

        print(
            f"[LOW DATA WARNING] "
            f"{symbol} "
            f"candles={len(df)} "
            f"for ATR14"
        )

        return pd.DataFrame()

    # ATR
    atr_window_map = {

        "5m": 14,

        "15m": 10,

        "1h": 5

    }

    atr_window = atr_window_map.get(
        interval,
        14
    )

    df["ATR"] = ta.volatility.average_true_range(
        df["High"],
        df["Low"],
        df["Close"],
        window=atr_window
    )

    df["ATR_PCT"] = (
        df["ATR"] / df["Close"]
    ) * 100

    # Candle Strength
    candle_body = abs(
        df["Close"] - df["Open"]
    )

    candle_range = (
        df["High"] - df["Low"]
    )

    df["BODY_STRENGTH"] = (
        candle_body / candle_range.replace(0, 0.0001)
    )

    # =========================
    # Support / Resistance
    # =========================

    # Previous candle structure
    df["PREV_HIGH"] = (
        df["High"]
        .shift(1)
    )

    df["PREV_LOW"] = (
        df["Low"]
        .shift(1)
    )

    # Rolling resistance
    df["ROLLING_RESISTANCE"] = (
        df["High"]
        .rolling(window=10)
        .max()
    )

    # Rolling support
    df["ROLLING_SUPPORT"] = (
        df["Low"]
        .rolling(window=10)
        .min()
    )

    # Distance to resistance %
    df["DISTANCE_TO_RESISTANCE"] = (
        (
            df["ROLLING_RESISTANCE"]
            - df["Close"]
        ) / df["Close"]
    ) * 100

    # Distance to support %
    df["DISTANCE_TO_SUPPORT"] = (
        (
            df["Close"]
            - df["ROLLING_SUPPORT"]
        ) / df["Close"]
    ) * 100



    # =========================
    # Trend Phase Detection
    # =========================

    df["TREND_PHASE"] = "RANGE"

    bullish_condition = (
        (df["EMA9"] > df["EMA20"]) &
        (df["MACD"] > df["MACD_SIGNAL"]) &
        (df["RSI"] > 55)
    )

    bearish_condition = (
        (df["EMA9"] < df["EMA20"]) &
        (df["MACD"] < df["MACD_SIGNAL"]) &
        (df["RSI"] < 45)
    )

    df.loc[
        bullish_condition,
        "TREND_PHASE"
    ] = "UPTREND"

    df.loc[
        bearish_condition,
        "TREND_PHASE"
    ] = "DOWNTREND"



    # =========================
    # Breakout / Breakdown Logic
    # =========================

    recent_high = (
        df["High"]
        .rolling(20)
        .max()
    )

    recent_low = (
        df["Low"]
        .rolling(20)
        .min()
    )

    df["BREAKOUT"] = (
        df["Close"] > recent_high.shift(1)
    )

    df["BREAKDOWN"] = (
        df["Close"] < recent_low.shift(1)
    )


    # =========================
    # Market Structure
    # =========================

    df["PREV_HIGH"] = df["High"].shift(1)

    df["PREV_LOW"] = df["Low"].shift(1)

    df["HIGHER_HIGH"] = (
        (df["High"] > df["PREV_HIGH"]) &
        (df["Low"] > df["PREV_LOW"])
    )

    df["LOWER_LOW"] = (
        (df["Low"] < df["PREV_LOW"])&
        (df["High"] < df["PREV_HIGH"])
    )

    df["HIGHER_LOW"] = (
        df["Low"] > df["PREV_LOW"]
    )

    df["LOWER_HIGH"] = (
        df["High"] < df["PREV_HIGH"]
    )    


    # =========================================
    # Consecutive Lower High Structure
    # =========================================

    df["LOWER_HIGH_SEQUENCE"] = (
        df["High"] < df["High"].shift(1)
    ) & (
        df["High"].shift(1)
        < df["High"].shift(2)
    )


    # =========================
    # Failed Breakout Detection
    # =========================

    df["FAILED_BREAKOUT"] = (
        df["BREAKOUT"].shift(1).fillna(False)
        &
        (df["Close"] < df["PREV_HIGH"])
    )

    df["FAILED_BREAKDOWN"] = (
        df["BREAKDOWN"].shift(1).fillna(False)
        &
        (df["Close"] > df["PREV_LOW"])
    )


    # =========================================
    # Opening Range Breakout (ORB)
    # =========================================

    try:

        market_open = pd.DataFrame()

        if isinstance(df.index, pd.DatetimeIndex):

            session_df = df.copy()

            if session_df.index.tz is None:

                session_df.index = session_df.index.tz_localize("UTC")

            session_df.index = session_df.index.tz_convert("America/New_York")
            market_open = session_df.between_time("09:30", "10:00")

        if len(market_open) >= 6:

            opening_range = market_open.iloc[:6]

        else:

            opening_range = df.iloc[:6]

        orb_high = opening_range["High"].max()

        orb_low = opening_range["Low"].min()

        df["ORB_HIGH"] = orb_high

        df["ORB_LOW"] = orb_low

        # Bullish ORB breakout
        df["ORB_BREAKOUT"] = (

            (df["Close"] > df["ORB_HIGH"])

            &

            (df["REL_VOLUME"] > 1.2)

        )

        # Bearish ORB breakdown
        df["ORB_BREAKDOWN"] = (

            (df["Close"] < df["ORB_LOW"])

            &

            (df["REL_VOLUME"] > 1.2)

        )

    except Exception:

        df["ORB_HIGH"] = 0

        df["ORB_LOW"] = 0

        df["ORB_BREAKOUT"] = False

        df["ORB_BREAKDOWN"] = False


    # =========================
    # Consolidation Detection
    # =========================

    rolling_high = (
        df["High"]
        .rolling(10)
        .max()
    )

    rolling_low = (
        df["Low"]
        .rolling(10)
        .min()
    )

    range_percent = (
        (rolling_high - rolling_low)
        / df["Close"]
    )

    df["CONSOLIDATING"] = (
        range_percent < 0.01
    )    

    # =========================
    # Final Cleanup
    # =========================

    required_cols_map = {

        "5m": [
            "EMA9",
            "EMA20",
            "RSI",
            "ATR"
        ],

        "15m": [
            "EMA9",
            "EMA20",
            "RSI",
            "ATR"
        ],

        "1h": [
            "EMA9",
            "RSI",
            "ATR"
        ]

    }

    required_cols = required_cols_map.get(
        interval,
        [
            "EMA9",
            "EMA20",
            "RSI",
            "ATR"
        ]
    )

    df = df.dropna(
        subset=["Close"] + required_cols
    )

    debug_print(
        f"[FINAL DF LEN] "
        f"{symbol}: {len(df)}"
    )    


    # =========================================
    # Relative Strength vs Market
    # =========================================

    try:

        # Intraday percentage move
        current_price = df["Close"].iloc[-1]

        session_open = df["Open"].iloc[0]

        symbol_move_pct = (
            (
                current_price - session_open
            )
            / session_open
        ) * 100

        # Store for downstream analysis
        df["SYMBOL_MOVE_PCT"] = symbol_move_pct

    except Exception:

        df["SYMBOL_MOVE_PCT"] = 0    

    return df



# def get_live_price(symbol):

#     url = (
#         f"https://api.polygon.io/"
#         f"v2/snapshot/locale/us/"
#         f"markets/stocks/tickers/{symbol}"
#     )

#     params = {
#         "apiKey": os.getenv("POLYGON_API_KEY")
#     }

#     try:

#         print(
#             f"[LIVE PRICE REQUEST] "
#             f"{symbol}"
#         )

#         response = requests.get(
#             url,
#             params=params,
#             timeout=10
#         )

#         print(
#             f"[SNAPSHOT STATUS] "
#             f"{symbol}: "
#             f"{response.status_code}"
#         )

#         # Raw response text
#         print(
#             f"[SNAPSHOT TEXT] "
#             f"{symbol}: "
#             f"{response.text[:300]}"
#         )

#         # Handle bad status
#         if response.status_code != 200:

#             return None

#         data = response.json()

#         print(
#             f"[SNAPSHOT RAW] "
#             f"{symbol}: "
#             f"{data}"
#         )

#         ticker_data = data.get("ticker")

#         if not ticker_data:

#             print(
#                 f"[NO TICKER DATA] "
#                 f"{symbol}"
#             )

#             return None

#         latest_trade = ticker_data.get(
#             "lastTrade",
#             {}
#         )

#         print(
#             f"[LAST TRADE] "
#             f"{symbol}: "
#             f"{latest_trade}"
#         )

#         # Polygon usually uses "p"
#         price = latest_trade.get("p")

#         # Fallback format
#         if price is None:

#             price = latest_trade.get(
#                 "price"
#             )

#         if price is None:

#             print(
#                 f"[NO PRICE FOUND] "
#                 f"{symbol}"
#             )

#             return None

#         print(
#             f"[LIVE PRICE] "
#             f"{symbol}: {price}"
#         )

#         return round(float(price), 2)

#     except Exception as e:

#         print(
#             f"[LIVE PRICE ERROR] "
#             f"{symbol}: {e}"
#         )

#         return None


def get_live_price(df):

    try:

        # df = compute_indicators(
        #     symbol,
        #     interval="5m"
        # )

        if df.empty:

            print(
                f"[LAST_AGG_PRICE FAILED] "
            )

            return None

        latest_price = (
            df["Close"]
            .iloc[-1]
        )

        debug_print(
            f"[LAST_AGG_PRICE] "
            f"{latest_price}"
        )

        return round(
            float(latest_price),
            2
        )

    except Exception as e:

        print(
            f"[LIVE PRICE ERROR] {e}"
        )

        return None