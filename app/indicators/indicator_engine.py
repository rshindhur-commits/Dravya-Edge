# import ta


# def apply_indicators(df):

#     if df.empty:
#         return df

#     # EMA
#     df["EMA9"] = ta.trend.ema_indicator(
#         df["Close"],
#         window=9
#     )

#     df["EMA20"] = ta.trend.ema_indicator(
#         df["Close"],
#         window=20
#     )

#     # EMA Slope
#     df["EMA9_SLOPE"] = (
#         df["EMA9"] - df["EMA9"].shift(1)
#     )

#     # MACD
#     macd = ta.trend.MACD(df["Close"])

#     df["MACD"] = macd.macd()

#     df["MACD_SIGNAL"] = (
#         macd.macd_signal()
#     )

#     # RSI
#     df["RSI"] = ta.momentum.rsi(
#         df["Close"],
#         window=14
#     )

#     # RSI Slope
#     df["RSI_SLOPE"] = (
#         df["RSI"] - df["RSI"].shift(1)
#     )

#     # Volume
#     df["AVG_VOLUME"] = (
#         df["Volume"]
#         .rolling(window=20)
#         .mean()
#     )

#     df["REL_VOLUME"] = (
#         df["Volume"] / df["AVG_VOLUME"]
#     )

#     # Volume Trend
#     df["VOLUME_TREND"] = (
#         df["REL_VOLUME"] >
#         df["REL_VOLUME"].shift(1)
#     )

#     # Volume Spike
#     df["VOLUME_SPIKE"] = (
#         df["REL_VOLUME"] > 2
#     )

#     # VWAP
#     typical_price = (
#         df["High"] +
#         df["Low"] +
#         df["Close"]
#     ) / 3

#     cumulative_tp_vol = (
#         typical_price * df["Volume"]
#     ).cumsum()

#     cumulative_volume = (
#         df["Volume"].cumsum()
#     )

#     df["VWAP"] = (
#         cumulative_tp_vol /
#         cumulative_volume
#     )

#     # VWAP Distance
#     df["VWAP_DISTANCE"] = (
#         (
#             df["Close"] -
#             df["VWAP"]
#         ) / df["VWAP"]
#     ) * 100

#     if len(df) < 20:
#         print("[INDICATOR WARNING] Not enough candles")
#         return df    

#     # ATR
#     df["ATR"] = (
#         ta.volatility.average_true_range(
#             df["High"],
#             df["Low"],
#             df["Close"],
#             window=14
#         )
#     )

#     df["ATR_PCT"] = (
#         df["ATR"] / df["Close"]
#     ) * 100

#     # Candle Strength
#     candle_body = abs(
#         df["Close"] - df["Open"]
#     )

#     candle_range = (
#         df["High"] - df["Low"]
#     )

#     df["BODY_STRENGTH"] = (
#         candle_body / candle_range
#     )

#     # Resistance
#     resistance = (
#         df["High"]
#         .rolling(window=20)
#         .max()
#     )

#     df["DISTANCE_TO_RESISTANCE"] = (
#         resistance - df["Close"]
#     )

#     # Support
#     support = (
#         df["Low"]
#         .rolling(window=20)
#         .min()
#     )

#     df["DISTANCE_TO_SUPPORT"] = (
#         df["Close"] - support
#     )


#     # =========================
#     # Market Structure
#     # =========================

#     # Rolling highs/lows

#     df["HH_5"] = (
#         df["High"]
#         .rolling(window=5)
#         .max()
#     )

#     df["LL_5"] = (
#         df["Low"]
#         .rolling(window=5)
#         .min()
#     )

#     # Previous structure

#     df["PREV_HH_5"] = (
#         df["HH_5"]
#         .shift(5)
#     )

#     df["PREV_LL_5"] = (
#         df["LL_5"]
#         .shift(5)
#     )

#     # Higher highs

#     df["HIGHER_HIGH"] = (
#         df["HH_5"] > df["PREV_HH_5"]
#     )

#     # Higher lows

#     df["HIGHER_LOW"] = (
#         df["LL_5"] > df["PREV_LL_5"]
#     )

#     # Lower highs

#     df["LOWER_HIGH"] = (
#         df["HH_5"] < df["PREV_HH_5"]
#     )

#     # Lower lows

#     df["LOWER_LOW"] = (
#         df["LL_5"] < df["PREV_LL_5"]
#     )


#     # =========================
#     # Consolidation Detection
#     # =========================

#     recent_range = (

#         df["High"]
#         .rolling(window=10)
#         .max()

#         -

#         df["Low"]
#         .rolling(window=10)
#         .min()

#     )

#     range_pct = (
#         recent_range / df["Close"]
#     ) * 100

#     # Tight range compression

#     df["CONSOLIDATING"] = (
#         range_pct < 1.5
#     )


#     # =========================
#     # Breakout Structure
#     # =========================

#     rolling_high = (
#         df["High"]
#         .rolling(window=20)
#         .max()
#     )

#     rolling_low = (
#         df["Low"]
#         .rolling(window=20)
#         .min()
#     )

#     # Bullish breakout

#     df["BREAKOUT"] = (
#         df["Close"] > rolling_high.shift(1)
#     )

#     # Bearish breakdown

#     df["BREAKDOWN"] = (
#         df["Close"] < rolling_low.shift(1)
#     )


#     # =========================
#     # Failed Breakout
#     # =========================

#     df["FAILED_BREAKOUT"] = (

#         df["BREAKOUT"].shift(1)

#         &

#         (df["Close"] < df["EMA9"])

#     )


#     # =========================
#     # Trend Phase
#     # =========================

#     trend_phase = []

#     for i in range(len(df)):

#         row = df.iloc[i]

#         phase = "NEUTRAL"

#         if (

#             row["HIGHER_HIGH"]
#             and row["HIGHER_LOW"]
#             and row["EMA9"] > row["EMA20"]

#         ):

#             phase = "UPTREND"

#         elif (

#             row["LOWER_HIGH"]
#             and row["LOWER_LOW"]
#             and row["EMA9"] < row["EMA20"]

#         ):

#             phase = "DOWNTREND"

#         elif row["CONSOLIDATING"]:

#             phase = "CONSOLIDATION"

#         trend_phase.append(phase)

#     df["TREND_PHASE"] = trend_phase    

#     return df