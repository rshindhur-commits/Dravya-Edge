from __future__ import annotations

import pandas as pd


def build_performance_statistics(trades):
    trades = trades if trades is not None else pd.DataFrame()
    r = pd.to_numeric(trades.get("r_multiple", trades.get("final_r", pd.Series(dtype=float))), errors="coerce").dropna()
    wins = r[r > 0]; losses = r[r < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
    return {
        "completed_trades": int(len(r)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(float(len(wins) / len(r) * 100), 1) if len(r) else None,
        "average_r": round(float(r.mean()), 2) if len(r) else None,
        "total_r": round(float(r.sum()), 2) if len(r) else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "max_drawdown_r": round(float((r.cumsum().cummax() - r.cumsum()).max()), 2) if len(r) else None,
    }