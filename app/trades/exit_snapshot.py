from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ExitSnapshot:
    trade_id: str
    final_r: object
    exit_time: object
    exit_price: object
    primary_exit: object
    secondary_exit: object
    ignored_exits: object
    trend_capture: object
    tes: object
    mfe: object
    mae: object
    left_on_table: object
    available_move: object
    captured_move: object
    trend_health: object
    bars_held: object
    ema: object
    vwap: object
    macd: object
    rsi: object
    reason: object
    exit_priority_order: object
    best_exit: object
    exit_penalty_pct: object

    def to_record(self): return asdict(self)


def create_exit_snapshot(trade, trend_row):
    row = trend_row or {}
    exit_price = row.get("Exit Price") or trade.get("close_price")
    best_exit = row.get("Peak Price")
    penalty = None
    try:
        penalty = round((float(best_exit) - float(exit_price)) / float(best_exit) * 100, 3) if best_exit and exit_price else None
    except Exception:
        pass
    return ExitSnapshot(
        trade_id=trade.get("trade_id") or trade.get("trade_key"), final_r=trade.get("r_multiple"), exit_time=trade.get("closed_at_et") or trade.get("closed_at"),
        exit_price=exit_price, primary_exit=row.get("Primary Exit") or trade.get("exit_reason"), secondary_exit=row.get("Secondary Exits"),
        ignored_exits=row.get("Ignored Exit Signals"), trend_capture=row.get("Trend Capture %"), tes=row.get("Trade Efficiency Score"),
        mfe=row.get("MFE") or row.get("Maximum Favorable Excursion"), mae=row.get("MAE") or row.get("Maximum Adverse Excursion"),
        left_on_table=row.get("Left On Table"), available_move=row.get("Available Move"), captured_move=row.get("Captured Move"),
        trend_health=row.get("Trend Health State"), bars_held=row.get("Bars Held"), ema=row.get("EMA9 At Exit"), vwap=row.get("VWAP At Exit"),
        macd=row.get("MACD At Exit"), rsi=row.get("RSI At Exit"), reason=trade.get("exit_reason"), exit_priority_order=row.get("Exit Priority Order"),
        best_exit=best_exit, exit_penalty_pct=penalty,
    )
