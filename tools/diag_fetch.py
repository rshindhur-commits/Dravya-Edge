from app.indicators.technical_indicators import (
    compute_indicators,
    get_polygon_data
)
from app.utils.timeframe_resampler import resample_timeframe


SYMBOLS = [
    "QQQ",
    "SPY",
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "TSLA",
    "AMD",
    "AVGO",
    "MU",
    "PLTR",
    "NFLX",
    "CRWD",
    "SMCI",
    "SPCX",
    "SMH",
    "ARM",
    "TSM",
    "INTC",
    "AMAT",
    "LRCX",
    "MRVL",
    "ORCL",
    "PANW",
    "SOXL"
]


def diagnose_symbol(symbol):

    raw_df = get_polygon_data(
        symbol,
        5,
        "minute",
        1
    )

    print(
        f"\n--- {symbol} raw_len={len(raw_df)}"
    )

    if raw_df.empty:

        print("EMPTY RAW DF")
        return

    df_15m = resample_timeframe(
        raw_df,
        "15m"
    )

    df = compute_indicators(
        df_15m,
        interval="15m",
        symbol=symbol
    )

    print(
        f"15m_len={len(df)}"
    )

    if df.empty:

        print("EMPTY INDICATOR DF")
        return

    print(
        "last index:",
        df.index[-1]
    )
    print(
        "last 3 closes:",
        df["Close"].dropna().tail(3).to_list()
    )
    print(
        "latest_price_from_df:",
        float(df["Close"].iloc[-1])
    )
    print(
        "symbol_move_pct:",
        df.get("SYMBOL_MOVE_PCT").iloc[-1]
        if "SYMBOL_MOVE_PCT" in df.columns
        else None
    )


if __name__ == "__main__":

    for symbol in SYMBOLS:

        try:

            diagnose_symbol(symbol)

        except Exception:

            import traceback

            print(
                f"ERROR diagnosing {symbol}"
            )
            traceback.print_exc()
