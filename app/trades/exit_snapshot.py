from __future__ import annotations

from dataclasses import asdict, dataclass

from app.versioning.strategy_version import UNVERSIONED


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
    option_ticker: object = None
    option_entry_mid: object = None
    option_exit_mid: object = None
    option_exit_quote_source: object = None
    r_multiple_net: object = None
    pnl_option_est: object = None
    cost_total: object = None
    pnl_source: object = None
    strategy_version: object = None

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
        option_ticker=trade.get("option_ticker"), option_entry_mid=trade.get("option_mid"),
        option_exit_mid=trade.get("option_current_mid"),
        option_exit_quote_source=trade.get("option_exit_quote_source"),
        r_multiple_net=trade.get("r_multiple_net"), pnl_option_est=trade.get("pnl_option_est"),
        cost_total=trade.get("cost_total"), pnl_source=trade.get("pnl_source"),
        # S2.5 backfill rule (EXECUTION_PLAN.md Phase 2): a trade opened
        # before this stamp existed has no strategy_version on it. Resolve to
        # the sentinel here, at the evidence boundary, rather than writing a
        # bare None that every downstream counter would have to special-case.
        strategy_version=trade.get("strategy_version") or UNVERSIONED,
    )
