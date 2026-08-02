from __future__ import annotations

import pandas as pd


def strategy_trades_only(trades):
    """Drop trades that were never meant to count toward strategy performance.

    `include_in_strategy_stats` is set at open -- False for review-validation
    entries (the SPY/QQQ index experiments taken under REVIEW_TV_CHART) and for
    manual dashboard trades, True for real auto-paper entries. It was written on
    every trade and read by nothing, so the flag existed, expressed the right
    intent, and had no effect.

    That is not a rounding error. Of 19 closed trades, 10 carry the flag as
    False, and they are not a random sample: the review-validation set won 50%
    against the strategy's 25%, so the blended record read -0.37R per trade at
    38.9% wins when the strategy itself was -0.44R at 25%. Index validation
    trades were flattering the number used to judge the strategy.

    Absent flag means include. Trades opened before the field existed are real
    strategy trades, and defaulting them out would silently shrink the record --
    the opposite failure, and a harder one to notice.
    """

    if trades is None or not len(trades):
        return trades if trades is not None else pd.DataFrame()

    if "include_in_strategy_stats" not in getattr(trades, "columns", []):
        return trades

    flag = trades["include_in_strategy_stats"]
    excluded = flag.map(
        lambda value: str(value).strip().lower() in {"false", "0", "no", "n"}
    )

    return trades[~excluded]


def _premium_measurable(trades):
    """Trades whose premium P&L can actually be reconstructed.

    Requires `option_entry_ask`, frozen at open since `eb56f75`. A trade without
    it has no knowable entry price, so its `option_pnl_pct_net` is the pre-fix
    artifact rather than a measurement. Absent column means nothing qualifies.
    """

    if trades is None or not len(trades):
        return trades if trades is not None else pd.DataFrame()

    if "option_entry_ask" not in getattr(trades, "columns", []):
        return trades.iloc[0:0]

    return trades[pd.to_numeric(trades["option_entry_ask"], errors="coerce").notna()]


def spread_calibration_from_db(days=30, reference=None, repository=None):
    """Calibration over a trailing window, straight from Postgres.

    `build_spread_calibration` needs trades handed to it, and the only caller was
    `learning_engine`, which writes the result into `validation_state.json`. The
    dashboard read it back out of that file -- written by whichever process ran
    the scan, so once scanning moved to Render the panel built to settle this
    question would never have rendered at all.

    A trailing window rather than one day because a measurable trade needs a
    frozen `option_entry_ask`, which only exists for positions opened after
    `eb56f75`. At one day at a time the panel would read zero for weeks.

    Returns None when the read fails, which the caller must not draw as "no
    measurable trades" -- that conflation is what published a false weekly
    summary on 2026-08-01.
    """

    from datetime import timedelta

    if repository is None:
        from app.db.paper_trade_repository import PaperTradeRepository

        repository = PaperTradeRepository()

    end_day = reference or _today_et()
    start_day = end_day - timedelta(days=int(days))

    try:
        trades = repository.fetch_closed_between(start_day, end_day)

    except Exception as exc:
        print(f"[SPREAD CALIBRATION] read failed: {exc}")

        return None

    if trades is None:
        return None

    return build_spread_calibration(pd.DataFrame(trades))


def _today_et():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/New_York")).date()


def build_spread_calibration(trades):
    """Does `option_quality_score` predict what the round trip actually costs?

    The open question from 2026-08-01. A contract scoring 95 that costs 11% to
    round-trip would mean the score is blind to the cost that decides the trade.
    That claim was first made against pre-`eb56f75` data and did not survive --
    every figure behind it was the measurement artifact. This block accumulates
    the clean version, one day at a time, so the question gets settled by data
    instead of re-argued.

    `entry_spread_pct` is what was quoted when the position was opened;
    `realised_cost_pct` is both legs actually paid. They should be close. A wide
    gap means the spread moved while the trade was held, which is a separate
    risk nothing currently models.
    """

    priced = _premium_measurable(
        strategy_trades_only(trades if trades is not None else pd.DataFrame())
    )

    if priced is None or not len(priced):
        return {"measurable_trades": 0, "rows": [], "quality_vs_cost_gap": None}

    quality = pd.to_numeric(priced.get("option_quality_score", pd.Series(dtype=float)), errors="coerce")
    entry_spread = pd.to_numeric(priced.get("option_entry_spread_pct", pd.Series(dtype=float)), errors="coerce")
    realised = pd.to_numeric(priced.get("option_spread_cost_pct", pd.Series(dtype=float)), errors="coerce")

    rows = []

    for position in range(len(priced)):
        rows.append({
            "symbol": priced.iloc[position].get("symbol"),
            "option_quality_score": _none_or_round(quality.iloc[position]),
            "entry_spread_pct": _none_or_round(entry_spread.iloc[position]),
            "realised_cost_pct": _none_or_round(realised.iloc[position]),
            # The failure this is watching for: a high score on an expensive
            # round trip. Threshold matches watchlist 2.6.
            "high_score_wide_spread": bool(
                pd.notna(quality.iloc[position])
                and pd.notna(realised.iloc[position])
                and quality.iloc[position] >= 80
                and realised.iloc[position] > 6
            ),
        })

    both = pd.notna(entry_spread) & pd.notna(realised)

    return {
        "measurable_trades": int(len(priced)),
        "rows": rows,
        "high_score_wide_spread_count": int(sum(row["high_score_wide_spread"] for row in rows)),
        # Positive means the spread widened while the position was held.
        "quality_vs_cost_gap": (
            round(float((realised[both] - entry_spread[both]).mean()), 2)
            if both.any() else None
        ),
    }


def _none_or_round(value, digits=2):
    return None if pd.isna(value) else round(float(value), digits)


def build_performance_statistics(trades):
    """Daily performance, in R and in the premium actually paid.

    Scoped to strategy trades; see `strategy_trades_only`.

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

    trades = strategy_trades_only(trades if trades is not None else pd.DataFrame())
    r = pd.to_numeric(trades.get("r_multiple", trades.get("final_r", pd.Series(dtype=float))), errors="coerce").dropna()
    wins = r[r > 0]; losses = r[r < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0

    # Premium figures are only meaningful for trades whose entry ask was frozen
    # at open (`eb56f75`). Before that, `option_pnl_pct_net` read the entry ask
    # and the close ask from the same live-refreshed key and evaluated to minus
    # the current spread on every trade regardless of outcome -- the tell is
    # `net == -option_spread_pct` exactly, which holds for all four pre-fix
    # trades on record. Averaging those produces a confident-looking number that
    # measures nothing, so they count as unpriced rather than as data points.
    priced = _premium_measurable(trades)
    net = pd.to_numeric(priced.get("option_pnl_pct_net", pd.Series(dtype=float)), errors="coerce").dropna()
    spread = pd.to_numeric(priced.get("option_spread_cost_pct", pd.Series(dtype=float)), errors="coerce").dropna()
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