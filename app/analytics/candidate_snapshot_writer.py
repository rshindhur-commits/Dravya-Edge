from datetime import datetime
from pathlib import Path

import pandas as pd

from app.storage.daily_paths import daily_path
from app.storage.session_manager import (
    candidate_key,
    get_or_create_session_manifest,
    get_scan_id,
    get_session_id,
    get_trading_day,
    now_et
)


SNAPSHOT_COLUMNS = [
    "trading_day",
    "session_id",
    "scan_id",
    "scan_timestamp",
    "candidate_key",
    "timestamp",
    "symbol",
    "setup_type",
    "setup_percent",
    "direction",
    "action_status",
    "blocked_by",
    "candidate_rr",
    "market_regime",
    "reference_regime",
    "sector_group",
    "top_candidate",
    "option_ticker",
    "option_quality_score",
    "option_spread_pct",
    "option_quote_freshness",
    "option_quote_timestamp",
    "option_quote_checked_at",
    "option_quote_timeframe",
    "option_quote_source",
    "option_quote_timestamp_field",
    "option_quote_age_minutes",
    "option_quote_age_seconds",
    "option_quote_allowed_age_seconds",
    "option_quote_freshness_reason",
    "option_quote_retry_count",
    "option_quote_latency_ms",
    "option_quote_refresh_time",
    "affordable",
    "entry_price",
    "stop_price",
    "target_price",
    "relative_volume",
    "atr_pct",
    "expiration_bucket",
    "option_delta",
    "option_gamma",
    "option_theta",
    "option_mid_price",
    "trend_health",
    "entry_timing_score",
    "entry_timing_grade",
    "entry_timing_reason",
    "trade_quality_score",
    "entry_priority_adjustment",
    "expected_remaining_trend",
    "projected_entry_grade",
    "ranking_score",
    "candidate_rank",
    "rank_reason",
    "exit_waterfall",
    "exit_rule",
    "exit_stage",
    "replay_outcome",
]


def _row_get(row, *names, default=None):

    for name in names:

        try:

            value = row.get(name)

        except Exception:

            value = None

        if value is None:

            continue

        try:

            if pd.isna(value):

                continue

        except Exception:

            pass

        if str(value).strip().lower() in {"", "nan", "none"}:

            continue

        return value

    return default


def normalize_candidate_row(
    row,
    timestamp=None,
    trading_day=None,
    session_id=None,
    scan_id=None,
    scan_timestamp=None
):

    scan_timestamp = scan_timestamp or timestamp or _row_get(
        row,
        "Data Timestamp ET",
        "Current ET",
        "timestamp",
        default=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    trading_day = trading_day or _row_get(row, "trading_day", default=get_trading_day())
    session_id = session_id or _row_get(row, "session_id", default=get_session_id(trading_day))
    scan_id = scan_id or _row_get(row, "scan_id", default=get_scan_id(trading_day))

    normalized_row = {
        "trading_day": trading_day,
        "session_id": session_id,
        "scan_id": scan_id,
        "scan_timestamp": scan_timestamp,
        "timestamp": timestamp or _row_get(
            row,
            "Data Timestamp ET",
            "Current ET",
            "timestamp",
            default=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ),
        "symbol": _row_get(row, "Symbol", "symbol"),
        "setup_type": _row_get(row, "Entry", "entry", "setup_type"),
        "setup_percent": _row_get(row, "Setup %", "setup_percent", "15m Score"),
        "direction": _row_get(row, "Candidate Direction", "direction"),
        "action_status": _row_get(row, "Action Status", "action_status"),
        "blocked_by": _row_get(row, "Blocked By", "blocked_by"),
        "candidate_rr": _row_get(row, "Candidate RR", "Risk Reward", "candidate_rr"),
        "market_regime": _row_get(row, "Market Regime", "market_regime"),
        "reference_regime": _row_get(row, "Reference Regime", "reference_regime"),
        "sector_group": _row_get(row, "Sector Group", "sector_group"),
        "top_candidate": _row_get(row, "Top Candidate", "top_candidate"),
        "option_ticker": _row_get(row, "Option Ticker", "option_ticker"),
        "option_quality_score": _row_get(
            row,
            "Option Quality Score",
            "option_quality_score"
        ),
        "option_spread_pct": _row_get(row, "Option Spread %", "option_spread_pct"),
        "option_quote_freshness": _row_get(
            row,
            "Option Quote Freshness",
            "option_quote_freshness"
        ),
        "option_quote_timestamp": _row_get(row, "Option Quote Timestamp", "option_quote_timestamp"),
        "option_quote_checked_at": _row_get(row, "Option Quote Checked At", "option_quote_checked_at"),
        "option_quote_timeframe": _row_get(row, "Option Quote Timeframe", "option_quote_timeframe"),
        "option_quote_source": _row_get(row, "Option Quote Source", "option_quote_source"),
        "option_quote_timestamp_field": _row_get(row, "Option Quote Timestamp Field", "option_quote_timestamp_field"),
        "option_quote_age_minutes": _row_get(row, "Option Quote Age Minutes", "option_quote_age_minutes"),
        "option_quote_age_seconds": _row_get(row, "Option Quote Age Seconds", "option_quote_age_seconds"),
        "option_quote_allowed_age_seconds": _row_get(row, "Option Quote Allowed Age Seconds", "option_quote_allowed_age_seconds"),
        "option_quote_freshness_reason": _row_get(row, "Option Quote Freshness Reason", "option_quote_freshness_reason"),
        "option_quote_retry_count": _row_get(row, "Option Quote Retry Count", "option_quote_retry_count"),
        "option_quote_latency_ms": _row_get(row, "Option Quote Latency Ms", "option_quote_latency_ms"),
        "option_quote_refresh_time": _row_get(row, "Option Quote Refresh Time", "option_quote_refresh_time"),
        "affordable": _row_get(row, "Affordable", "affordable"),
        "entry_price": _row_get(row, "Candidate Entry Price", "entry_price", "Price"),
        "stop_price": _row_get(row, "Candidate Stop Price", "stop_price", "Stop Loss"),
        "target_price": _row_get(row, "Candidate Target Price", "target_price", "Take Profit"),
        "relative_volume": _row_get(row, "Relative Volume", "relative_volume"),
        "atr_pct": _row_get(row, "ATR %", "atr_pct"),
        "expiration_bucket": _row_get(row, "Expiration Bucket", "expiration_bucket"),
        "option_delta": _row_get(row, "Option Delta", "option_delta"),
        "option_gamma": _row_get(row, "Option Gamma", "option_gamma"),
        "option_theta": _row_get(row, "Option Theta", "option_theta"),
        "option_mid_price": _row_get(row, "Option Mid Price", "option_mid_price"),
        "trend_health": _row_get(row, "Trend Health State", "trend_health"),
        "entry_timing_score": _row_get(
            row,
            "Entry Timing Score",
            "entry_timing_score"
        ),
        "entry_timing_grade": _row_get(
            row,
            "Entry Timing Grade",
            "entry_timing_grade"
        ),
        "entry_timing_reason": _row_get(
            row,
            "Entry Timing Reason",
            "entry_timing_reason"
        ),
        "trade_quality_score": _row_get(
            row,
            "Trade Quality Score",
            "trade_quality_score"
        ),
        "entry_priority_adjustment": _row_get(
            row,
            "Entry Priority Adjustment",
            "entry_priority_adjustment"
        ),
        "expected_remaining_trend": _row_get(
            row,
            "Expected Remaining Trend",
            "expected_remaining_trend"
        ),
        "projected_entry_grade": _row_get(
            row,
            "Projected Entry Grade",
            "projected_entry_grade"
        ),
        "ranking_score": _row_get(
            row,
            "Ranking Score",
            "ranking_score"
        ),
        "candidate_rank": _row_get(
            row,
            "Candidate Rank",
            "candidate_rank"
        ),
        "rank_reason": _row_get(row, "Rank Reason", "rank_reason"),
        "exit_waterfall": _row_get(row, "Exit Waterfall", "exit_waterfall"),
        "exit_rule": _row_get(row, "Exit Rule", "exit_rule"),
        "exit_stage": _row_get(row, "Exit Stage", "exit_stage"),
        "replay_outcome": _row_get(row, "Replay Outcome", "replay_outcome"),
    }

    normalized_row["candidate_key"] = _row_get(
        row,
        "candidate_key",
        default=candidate_key(normalized_row)
    )

    return normalized_row


def _snapshot_date(df):

    try:

        timestamps = pd.to_datetime(df["timestamp"], errors="coerce")
        latest_timestamp = timestamps.dropna().max()

        if pd.notna(latest_timestamp):

            return latest_timestamp.strftime("%Y-%m-%d")

    except Exception:

        pass

    return datetime.now().strftime("%Y-%m-%d")


def _write_parquet(df, path):

    if path.exists():

        existing_df = pd.read_parquet(path)
        df = pd.concat([existing_df, df], ignore_index=True)

    df.to_parquet(path, index=False)


def save_candidate_snapshots(
    rows,
    output_dir="data/candidate_snapshots",
    preferred_format="parquet",
    trading_day=None,
    scan_id=None,
    scan_timestamp=None,
    filename_stem=None
):

    if rows is None:

        return None

    if isinstance(rows, pd.DataFrame):

        raw_rows = rows.to_dict("records")

    else:

        raw_rows = list(rows)

    if not raw_rows:

        return None

    scan_timestamp = scan_timestamp or now_et().strftime("%Y-%m-%d %H:%M:%S")
    trading_day = trading_day or get_trading_day()
    scan_id = scan_id or get_scan_id(trading_day)
    session_id = get_session_id(trading_day)
    get_or_create_session_manifest(trading_day)

    snapshot_rows = [
        normalize_candidate_row(
            row,
            trading_day=trading_day,
            session_id=session_id,
            scan_id=scan_id,
            scan_timestamp=scan_timestamp
        )
        for row in raw_rows
    ]
    snapshot_df = pd.DataFrame(snapshot_rows, columns=SNAPSHOT_COLUMNS)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    snapshot_date = filename_stem or _snapshot_date(snapshot_df)

    if preferred_format == "parquet":

        parquet_path = output_path / f"{snapshot_date}.parquet"

        try:

            _write_parquet(snapshot_df, parquet_path)

            return {
                "path": str(parquet_path),
                "rows": len(snapshot_df),
                "format": "parquet"
            }

        except Exception as exc:

            print(
                f"[CANDIDATE SNAPSHOT WARNING] parquet unavailable: {exc}"
            )

    csv_path = output_path / f"{snapshot_date}.csv"
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    snapshot_df.to_csv(
        csv_path,
        mode="a",
        header=write_header,
        index=False
    )

    return {
        "path": str(csv_path),
        "rows": len(snapshot_df),
        "format": "csv"
    }


def append_candidate_snapshots(
    rows,
    trading_day=None,
    scan_id=None,
    preferred_format="parquet"
):

    trading_day = trading_day or get_trading_day()
    output_dir = daily_path(trading_day, "candidate_snapshots.parquet").parent

    return save_candidate_snapshots(
        rows,
        output_dir=str(output_dir),
        preferred_format=preferred_format,
        trading_day=trading_day,
        scan_id=scan_id,
        filename_stem="candidate_snapshots"
    )