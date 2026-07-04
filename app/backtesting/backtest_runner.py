import pandas as pd

from app.analytics.trade_outcome_tracker import evaluate_trade_outcome
from app.backtesting.no_lookahead_scanner import evaluate_symbol_at_time
from app.gates import EntryGateConfig, evaluate_entry_gate


def _candidate_key(candidate):

    return f"{candidate.get('symbol')}|{candidate.get('timestamp')}|{candidate.get('setup_type')}"


def _direction_for_outcome(candidate):

    direction = str(candidate.get("direction") or "").upper()

    if direction == "PUT":

        return "BEARISH"
    if direction == "CALL":

        return "BULLISH"
    return direction


def _future_candles(candles, timestamp, horizon_bars):

    future_df = candles[candles.index > timestamp].head(horizon_bars).copy()

    return future_df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
        }
    )


def run_backtest(
    candles_by_symbol,
    timestamps=None,
    gate_config=None,
    horizon_bars=20,
    min_history_bars=45
):

    gate_config = gate_config or EntryGateConfig(
        min_option_quality=0,
        max_spread_pct=100
    )
    candidates = []
    trades = []
    opened_keys = set()

    if timestamps is None:

        all_timestamps = set()

        for candles in candles_by_symbol.values():

            all_timestamps.update(candles.index[min_history_bars:])

        timestamps = sorted(all_timestamps)

    for timestamp in timestamps:

        for symbol, candles in candles_by_symbol.items():

            candles_until_now = candles[candles.index <= timestamp]

            if len(candles_until_now) < min_history_bars:

                continue

            candidate = evaluate_symbol_at_time(
                symbol=symbol,
                candles_5m=candles_until_now,
                timestamp=timestamp
            )

            if not candidate:

                continue

            candidates.append(candidate)
            allowed, gate_reason = evaluate_entry_gate(
                candidate,
                gate_config,
                mode="backtest"
            )

            if not allowed:

                candidate["blocked_by"] = candidate.get("blocked_by") or gate_reason
                continue

            trade_key = _candidate_key(candidate)

            if trade_key in opened_keys:

                continue

            opened_keys.add(trade_key)
            future_df = _future_candles(candles, timestamp, horizon_bars)
            outcome = evaluate_trade_outcome(
                symbol=symbol,
                trade_direction=_direction_for_outcome(candidate),
                entry_price=float(candidate["entry_price"]),
                target_price=float(candidate["target_price"]),
                stop_price=float(candidate["stop_price"]),
                future_df=future_df
            )
            trades.append({
                **candidate,
                "trade_id": trade_key,
                "replay_outcome": outcome.get("outcome"),
                "bars_to_outcome": outcome.get("bars_processed"),
                "r_multiple": outcome.get("r_multiple"),
                "mae": outcome.get("mae"),
                "mfe": outcome.get("mfe"),
                "run_type": "historical_backtest",
            })

    return {
        "candidates": pd.DataFrame(candidates),
        "trades": pd.DataFrame(trades),
    }