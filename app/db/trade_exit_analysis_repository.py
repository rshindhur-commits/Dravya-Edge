"""Per-trade exit analysis in Postgres.

The indicator state at exit and the analysis built on it lived only in
`trend_capture_analysis.csv`, on the container filesystem a redeploy wipes.
Nothing in Postgres held it at trade grain: `candidate_evidence.trend_capture`
is candidate-grain and `exit_quality_metrics` is a daily aggregate that was
itself all nulls on 2026-07-31. These are the numbers that answer "was this exit
right", so the post-market review depended entirely on a file that did not
survive the day.
"""

from __future__ import annotations

import json

from app.db.repository_base import BestEffortRepository

# CSV header -> column. The CSV is written with display-cased headers and has
# grown columns twice; anything not mapped here still reaches `payload`.
FIELD_MAP = {
    "Session ID": "session_id",
    "Symbol": "symbol",
    "Direction": "direction",
    "Setup": "setup",
    "Market Regime": "market_regime",
    "Entry Time": "entry_time",
    "Exit Time": "exit_time",
    "Entry Price": "entry_price",
    "Exit Price": "exit_price",
    "Bars Held": "bars_held",
    "Trend Capture %": "trend_capture_pct",
    "Available Move": "available_move",
    "Captured Move": "captured_move",
    "Left On Table": "left_on_table",
    "Maximum Favorable Excursion": "mfe",
    "Maximum Adverse Excursion": "mae",
    "Risk Reward": "risk_reward",
    "Peak Price": "peak_price",
    "Peak Time": "peak_time",
    "EMA9 At Exit": "ema9",
    "EMA20 At Exit": "ema20",
    "VWAP At Exit": "vwap",
    "MACD At Exit": "macd",
    "MACD Signal At Exit": "macd_signal",
    "MACD Histogram At Exit": "macd_histogram",
    "RSI At Exit": "rsi",
    "ATR At Exit": "atr",
    "Relative Volume At Exit": "relative_volume",
    "Trend Health Score": "trend_health_score",
    "Trend Health State": "trend_health_state",
    "Exit Reason": "exit_reason",
    "Primary Exit": "primary_exit",
    "Exit Quality": "exit_quality",
    "Exit Verdict": "exit_verdict",
    "Exit Comments": "exit_comments",
    "Trend Continued": "trend_continued",
    "Remaining Move": "remaining_move",
}

COLUMNS = (
    "trading_day", "trade_key", "session_id", "symbol", "direction", "setup",
    "market_regime", "entry_time", "exit_time", "entry_price", "exit_price",
    "bars_held", "trend_capture_pct", "available_move", "captured_move",
    "left_on_table", "mfe", "mae", "risk_reward", "peak_price", "peak_time",
    "ema9", "ema20", "vwap", "macd", "macd_signal", "macd_histogram", "rsi",
    "atr", "relative_volume", "trend_health_score", "trend_health_state",
    "exit_reason", "primary_exit", "exit_quality", "exit_verdict",
    "exit_comments", "trend_continued", "remaining_move",
)

_NUMERIC = {
    "entry_price", "exit_price", "trend_capture_pct", "available_move",
    "captured_move", "left_on_table", "mfe", "mae", "risk_reward", "peak_price",
    "ema9", "ema20", "vwap", "macd", "macd_signal", "macd_histogram", "rsi",
    "atr", "relative_volume", "trend_health_score", "remaining_move",
}


def _number(value):
    if value in (None, "", "None", "nan"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if parsed != parsed else parsed  # drop NaN


def _integer(value):
    parsed = _number(value)
    return None if parsed is None else int(parsed)


def _boolean(value):
    if isinstance(value, bool):
        return value
    if value in (None, "", "None"):
        return None
    return str(value).strip().lower() in {"true", "1", "yes"}


def _payload_safe(row):
    """Strip NaN/Inf before serialising.

    `json.dumps` happily emits bare `NaN`, which is valid Python-flavoured JSON
    and invalid to Postgres -- JSONB rejects the whole insert. The CSV is full of
    them: any column the analysis could not compute lands as NaN.
    """
    cleaned = {}

    for key, value in (row or {}).items():
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            cleaned[str(key)] = None
        else:
            cleaned[str(key)] = value

    return cleaned


def _moment(value):
    """Timestamps arrive in several shapes; normalise before Postgres sees them.

    The scanner wrote `%Z`-formatted values (`2026-07-31 00:38:19 EDT`) until
    2026-07-31, and archived rows still carry that form.
    """
    if value in (None, "", "None"):
        return None

    from app.ui.timestamps import to_utc

    parsed = to_utc(value)
    return None if parsed is None or parsed != parsed else parsed.to_pydatetime()


def to_record(trading_day, trade_key, row):
    """One CSV row as a typed database record, with the whole row in payload."""
    row = dict(row or {})
    record = {column: None for column in COLUMNS}
    record["trading_day"] = str(trading_day)
    record["trade_key"] = str(trade_key)

    for header, column in FIELD_MAP.items():
        if column not in record:
            continue
        value = row.get(header)
        if column in _NUMERIC:
            record[column] = _number(value)
        elif column == "bars_held":
            record[column] = _integer(value)
        elif column == "trend_continued":
            record[column] = _boolean(value)
        elif column in {"entry_time", "exit_time", "peak_time"}:
            record[column] = _moment(value)
        else:
            record[column] = None if value in (None, "", "None") else str(value)

    record["payload"] = json.dumps(_payload_safe(row), default=str, allow_nan=False)
    return record


class TradeExitAnalysisRepository(BestEffortRepository):

    def upsert(self, trading_day, trade_key, row):
        record = to_record(trading_day, trade_key, row)
        assignments = ", ".join(
            f"{column}=EXCLUDED.{column}"
            for column in COLUMNS
            if column not in {"trading_day", "trade_key"}
        )
        placeholders = ", ".join(f":{column}" for column in COLUMNS)
        return self._execute(
            f"""
            INSERT INTO trade_exit_analysis ({", ".join(COLUMNS)}, payload)
            VALUES ({placeholders}, CAST(:payload AS JSONB))
            ON CONFLICT (trading_day, trade_key) DO UPDATE
            SET {assignments}, payload=EXCLUDED.payload, recorded_at=now()
            """,
            record,
        )

    def load_day(self, trading_day):
        return self._fetch(
            """
            SELECT * FROM trade_exit_analysis
            WHERE trading_day = CAST(:trading_day AS DATE)
            ORDER BY exit_time NULLS LAST, trade_key
            """,
            {"trading_day": str(trading_day)},
        )
