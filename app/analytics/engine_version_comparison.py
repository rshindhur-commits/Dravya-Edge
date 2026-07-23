from __future__ import annotations

import hashlib

import pandas as pd

from app.storage.daily_paths import daily_path


ENGINE_EVENT_COLUMNS = [
    "event_id", "trading_day", "engine_version", "symbol", "direction",
    "entry_time", "entry_price", "exit_time", "exit_price", "final_r",
    "mfe_r", "stop_loss", "exit_phase", "entry_efficiency_score",
    "trend_capture_pct", "stock_direction", "trade_direction", "stock_finish",
    "trade_finish", "trend_outcome", "engine_captured_trend",
]


def _direction(trade):
    value = str(trade.get("direction") or trade.get("entry_type") or "").upper()
    return "PUT" if any(token in value for token in ["PUT", "SHORT", "BEAR", "BREAKDOWN", "REJECTION"]) else "CALL"


def _event_from_trade(trading_day, engine_version, trade):
    entry_time = trade.get("opened_at") or trade.get("entry_time")
    exit_time = trade.get("closed_at") or trade.get("exit_time")
    source = "|".join([str(engine_version), str(trade.get("symbol")), str(entry_time), str(exit_time)])
    direction = _direction(trade)
    entry_price = trade.get("entry_price")
    exit_price = trade.get("close_price") or trade.get("exit_price")
    final_r = trade.get("final_r") if trade.get("final_r") is not None else trade.get("rr_progress")
    mfe_r = trade.get("mfe_r")
    outcome = _trend_outcome(
        entry_price,
        exit_price,
        trade.get("stop_loss"),
        direction,
        final_r,
    )
    return {
        "event_id": hashlib.sha256(source.encode()).hexdigest()[:24],
        "trading_day": trading_day,
        "engine_version": engine_version,
        "symbol": trade.get("symbol"),
        "direction": direction,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "final_r": final_r,
        "mfe_r": mfe_r,
        "stop_loss": trade.get("stop_loss"),
        "exit_phase": trade.get("exit_phase") or trade.get("exit_reason"),
        "entry_efficiency_score": trade.get("entry_efficiency_score"),
        "trend_capture_pct": _trend_capture_pct(final_r, mfe_r),
        "stock_direction": outcome["stock_direction"],
        "trade_direction": direction,
        "stock_finish": outcome["stock_finish"],
        "trade_finish": outcome["trade_finish"],
        "trend_outcome": outcome["trend_outcome"],
        "engine_captured_trend": outcome["engine_captured_trend"],
    }


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _trend_capture_pct(final_r, mfe_r):
    final_value = _number(final_r)
    mfe_value = _number(mfe_r)
    if final_value is None or mfe_value is None or mfe_value <= 0:
        return None
    return round(max(0.0, min(100.0, final_value / mfe_value * 100)), 2)


def _trend_outcome(entry_price, finish_price, stop_loss, direction, final_r):
    entry = _number(entry_price)
    finish = _number(finish_price)
    stop = _number(stop_loss)
    final_value = _number(final_r, 0.0)
    if entry is None or finish is None:
        return {
            "stock_direction": "UNKNOWN",
            "stock_finish": "UNKNOWN",
            "trade_finish": "WIN" if final_value > 0 else "LOSS" if final_value < 0 else "FLAT",
            "trend_outcome": "UNKNOWN",
            "engine_captured_trend": False,
        }
    move = finish - entry
    stock_direction = "BULLISH" if move > entry * 0.001 else "BEARISH" if move < -entry * 0.001 else "FLAT"
    stock_finish = "GREEN" if move > entry * 0.001 else "RED" if move < -entry * 0.001 else "FLAT"
    trade_finish = "WIN" if final_value > 0 else "LOSS" if final_value < 0 else "FLAT"
    risk = abs(entry - stop) if stop is not None else 0
    directional_move_r = (
        (entry - finish if direction == "PUT" else finish - entry) / risk
        if risk > 0
        else 0
    )
    strong_directional_trend = directional_move_r >= 1.0
    captured = strong_directional_trend and final_value > 0
    return {
        "stock_direction": stock_direction,
        "stock_finish": stock_finish,
        "trade_finish": trade_finish,
        "trend_outcome": (
            "STRONG_TREND_CAPTURED"
            if captured
            else "STRONG_TREND_EXECUTION_FAILED"
            if strong_directional_trend and final_value <= 0
            else "CHOPPY_OR_FLAT"
        ),
        "engine_captured_trend": captured,
    }


def build_daily_trend_outcomes(events, scanner):
    if events is None or events.empty:
        return pd.DataFrame()
    latest_prices = {}
    if scanner is not None and not scanner.empty:
        symbol_column = "Symbol" if "Symbol" in scanner.columns else "symbol" if "symbol" in scanner.columns else None
        price_column = "Price" if "Price" in scanner.columns else "Close" if "Close" in scanner.columns else None
        if symbol_column and price_column:
            for _, row in scanner.iterrows():
                latest_prices[str(row.get(symbol_column)).upper()] = row.get(price_column)
    records = []
    for _, event in events.iterrows():
        stock_finish_price = latest_prices.get(str(event.get("symbol")).upper(), event.get("exit_price"))
        outcome = _trend_outcome(
            event.get("entry_price"),
            stock_finish_price,
            event.get("stop_loss"),
            event.get("trade_direction") or event.get("direction"),
            event.get("final_r"),
        )
        records.append({
            **event.to_dict(),
            "stock_finish_price": stock_finish_price,
            **outcome,
        })
    return pd.DataFrame(records)


def build_trade_comparisons(events):
    if events is None or events.empty:
        return pd.DataFrame()
    frame = events.copy()
    frame = frame[frame["engine_version"].isin(["v1", "v2"])].copy()
    if frame.empty:
        return pd.DataFrame()
    frame["sequence"] = frame.groupby(["symbol", "direction", "engine_version"]).cumcount()
    v1 = frame[frame["engine_version"].eq("v1")].copy().set_index(["symbol", "direction", "sequence"])
    v2 = frame[frame["engine_version"].eq("v2")].copy().set_index(["symbol", "direction", "sequence"])
    joined = v1.join(v2, lsuffix="_v1", rsuffix="_v2", how="inner").reset_index()
    if joined.empty:
        return joined
    for metric in ["entry_price", "exit_price", "final_r", "mfe_r", "trend_capture_pct"]:
        v1_metric = f"{metric}_v1"
        v2_metric = f"{metric}_v2"
        joined[f"{metric}_delta"] = (
            pd.to_numeric(joined[v2_metric], errors="coerce")
            - pd.to_numeric(joined[v1_metric], errors="coerce")
            if v1_metric in joined and v2_metric in joined
            else None
        )
    entry_time_v1 = pd.to_datetime(joined["entry_time_v1"], errors="coerce")
    entry_time_v2 = pd.to_datetime(joined["entry_time_v2"], errors="coerce")
    exit_time_v1 = pd.to_datetime(joined["exit_time_v1"], errors="coerce")
    exit_time_v2 = pd.to_datetime(joined["exit_time_v2"], errors="coerce")
    joined["entry_delta_minutes"] = (entry_time_v2 - entry_time_v1).dt.total_seconds() / 60
    joined["exit_delta_minutes"] = (exit_time_v2 - exit_time_v1).dt.total_seconds() / 60
    return joined


def append_engine_trade_event(trading_day, engine_version, trade):
    event = _event_from_trade(trading_day, engine_version, trade)
    events_path = daily_path(trading_day, "engine_trade_events.csv")
    comparisons_path = daily_path(trading_day, "engine_trade_comparisons.csv")
    existing = pd.read_csv(events_path) if events_path.exists() and events_path.stat().st_size else pd.DataFrame()
    events = pd.concat([existing, pd.DataFrame([event])], ignore_index=True, sort=False)
    events = events.drop_duplicates(subset=["event_id"], keep="last")
    events.to_csv(events_path, index=False)
    comparisons = build_trade_comparisons(events)
    comparisons.to_csv(comparisons_path, index=False)
    return {"event_path": str(events_path), "comparison_path": str(comparisons_path)}


def summarize_completed_comparisons(comparisons):
    if comparisons is None or comparisons.empty:
        return {
            "Trades compared": 0,
            "V2 higher R": 0,
            "Avg R improvement": None,
            "Avg entry delay minutes": None,
            "Avg exit delay minutes": None,
            "Avg MFE improvement": None,
            "V2 higher Trend Capture": 0,
            "Avg Trend Capture improvement": None,
        }
    r_delta = pd.to_numeric(comparisons.get("final_r_delta"), errors="coerce")
    entry_delta = pd.to_numeric(comparisons.get("entry_delta_minutes"), errors="coerce")
    exit_delta = pd.to_numeric(comparisons.get("exit_delta_minutes"), errors="coerce")
    mfe_delta = pd.to_numeric(comparisons.get("mfe_r_delta"), errors="coerce")
    trend_capture_delta = pd.to_numeric(comparisons.get("trend_capture_pct_delta"), errors="coerce")
    return {
        "Trades compared": int(len(comparisons)),
        "V2 higher R": int((r_delta > 0).sum()),
        "Avg R improvement": round(float(r_delta.mean()), 2) if r_delta.notna().any() else None,
        "Avg entry delay minutes": round(float(entry_delta.mean()), 2) if entry_delta.notna().any() else None,
        "Avg exit delay minutes": round(float(exit_delta.mean()), 2) if exit_delta.notna().any() else None,
        "Avg MFE improvement": round(float(mfe_delta.mean()), 2) if mfe_delta.notna().any() else None,
        "V2 higher Trend Capture": int((trend_capture_delta > 0).sum()),
        "Avg Trend Capture improvement": round(float(trend_capture_delta.mean()), 2) if trend_capture_delta.notna().any() else None,
    }