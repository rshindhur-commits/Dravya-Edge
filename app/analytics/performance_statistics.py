from __future__ import annotations

import pandas as pd


def build_performance_statistics(trades):
    """Daily performance, in R and in the premium actually paid.

    R alone measures the wrong instrument. R is computed on the underlying, but
    the position held is an option, and the option's round-trip spread is
    routinely larger than the underlying move the stop allows. On 2026-07-30 five
    closed trades summed to -0.76R and reported two winners at +1.35R and +0.88R;
    priced in premium, ask to bid, all five lost -- the two "winners" worst of all,
    at -7.69% and -4.95%, because they paid the widest spreads.

    So the premium columns written by close_paper_trade are aggregated alongside R:
    `option_pnl_pct_net` is the honest round trip, and `net_win_rate` is the win
    rate after costs. Where those columns are absent (older trades, or a frame
    that never carried them) the premium figures are None rather than zero, so a
    missing measurement never reads as a break-even one.
    """

    trades = trades if trades is not None else pd.DataFrame()
    r = pd.to_numeric(trades.get("r_multiple", trades.get("final_r", pd.Series(dtype=float))), errors="coerce").dropna()
    wins = r[r > 0]; losses = r[r < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0

    net = pd.to_numeric(trades.get("option_pnl_pct_net", pd.Series(dtype=float)), errors="coerce").dropna()
    spread = pd.to_numeric(trades.get("option_spread_cost_pct", pd.Series(dtype=float)), errors="coerce").dropna()
    net_wins = net[net > 0]

    return {
        "completed_trades": int(len(r)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(float(len(wins) / len(r) * 100), 1) if len(r) else None,
        "average_r": round(float(r.mean()), 2) if len(r) else None,
        "total_r": round(float(r.sum()), 2) if len(r) else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "max_drawdown_r": round(float((r.cumsum().cummax() - r.cumsum()).max()), 2) if len(r) else None,
        # Premium terms. These are the ones that decide whether the day made money.
        "priced_trades": int(len(net)),
        "net_win_rate": round(float(len(net_wins) / len(net) * 100), 1) if len(net) else None,
        "total_option_pnl_pct": round(float(net.sum()), 2) if len(net) else None,
        "average_option_pnl_pct": round(float(net.mean()), 2) if len(net) else None,
        "average_spread_cost_pct": round(float(spread.mean()), 2) if len(spread) else None,
    }