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


def closed_trades_from_db(days=30, reference=None, repository=None):
    """Closed trades over a trailing window, or None when unreadable.

    The shared read behind every Postgres-backed Validation panel. They were each
    reading their own CSV under `data/daily/`, written by the process that ran the
    scan -- so on a dashboard that never ran one, the whole page reported an empty
    record as a quiet week.
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
        print(f"[VALIDATION] closed trade read failed: {exc}")

        return None

    return None if trades is None else pd.DataFrame(trades)


def trade_efficiency_summary(trades):
    """How much of the available move the exits actually kept.

    Computed from the columns `_flatten_closed` already lifts, rather than from
    `trend_capture_analysis.csv`. The CSV is written per trading day by the
    scanning process, so the dashboard's copy was routinely absent and the panel
    reported nothing on days that had trades.
    """

    if trades is None:
        return None

    frame = strategy_trades_only(trades if trades is not None else pd.DataFrame())

    if frame is None or not len(frame):
        return {"trades": 0}

    def _mean(column):
        """Mean and the count it was taken over.

        The count is not decoration. `trend_capture` and `left_on_table` come
        from the post-market review, which has not run for every closed trade, so
        their averages are taken over a smaller set than `trades`. Reporting the
        mean alone puts a figure measured on 21 trades under a panel that says 29.
        """

        if column not in frame.columns:
            return None, 0

        values = pd.to_numeric(frame[column], errors="coerce").dropna()

        return (
            round(float(values.mean()), 3) if len(values) else None,
            int(len(values)),
        )

    trend_capture, trend_capture_trades = _mean("trend_capture")
    left_on_table, left_on_table_trades = _mean("left_on_table")
    mfe_r, mfe_r_trades = _mean("mfe_r")

    # Capture is bounded above at 100 and unbounded below, so its mean is at the
    # mercy of a couple of trades: on the first 17 measured, two exits at -880%
    # and -558% pulled the average to -90.9% while the median sat at 0.0. The
    # mean alone would have read as a strategy that gives back nine times the
    # move it catches. Both are reported for the same reason bootstrap CIs sit
    # beside any mean return here.
    def _median(column):
        if column not in frame.columns:
            return None

        values = pd.to_numeric(frame[column], errors="coerce").dropna()

        return round(float(values.median()), 3) if len(values) else None

    return {
        "trades": int(len(frame)),
        # Percent of the available move the exit kept. Unbounded below: an exit
        # that gave back more than the move was ever worth reads under -100.
        "trend_capture": trend_capture,
        "trend_capture_median": _median("trend_capture"),
        "trend_capture_trades": trend_capture_trades,
        # Absolute price points per share, not a percent and not a fraction --
        # `max(highest - exit_price, 0)` on the underlying. Averaging it across
        # symbols mixes a $40 stock with a $400 one, so it is reported with its
        # unit and read per trade rather than as a headline.
        "left_on_table": left_on_table,
        "left_on_table_trades": left_on_table_trades,
        "mfe_r": mfe_r,
        "mfe_r_trades": mfe_r_trades,
    }


def exit_reason_summary(trades):
    """Which exit rule is producing the losses.

    `exit_reason` has been on every closed trade the whole time and nothing on the
    Validation page read it, so the page could say the book lost money without
    saying what closed the trades. The first window it was run on answered
    immediately: profit targets returned +19.9% and the invalidation rules -3 to
    -5% each, on comparable trade counts.

    Grouped over all strategy trades, with cash and premium scoped to the priced
    subset -- a reason can have trades that no premium figure covers, and folding
    those in as zero would understate it. `trades` and `priced` are both reported
    so the denominators are visible rather than inferred.

    Ordered worst cash first. The question this answers is which rule to look at,
    and that is the one bleeding the most money, not the one appearing most often.
    """

    frame = strategy_trades_only(trades if trades is not None else pd.DataFrame())

    if frame is None or not len(frame) or "exit_reason" not in frame.columns:
        return []

    frame = frame.copy()
    frame["exit_reason"] = frame["exit_reason"].fillna("(not recorded)").astype(str)
    priced = _premium_measurable(frame)

    # Grouped once rather than re-filtered inside the loop, which was a full pass
    # over the priced frame per exit reason. Invisible at 17 trades; the window
    # selector now reaches 90 days, and this is the slowest step on the page.
    priced_groups = (
        {reason: group for reason, group in priced.groupby("exit_reason", dropna=False)}
        if len(priced) else {}
    )
    empty = priced.iloc[0:0]
    rows = []

    for reason, group in frame.groupby("exit_reason", dropna=False):
        r = pd.to_numeric(group.get("r_multiple", pd.Series(dtype=float)), errors="coerce").dropna()
        priced_group = priced_groups.get(reason, empty)
        net = pd.to_numeric(
            priced_group.get("option_pnl_pct_net", pd.Series(dtype=float)), errors="coerce"
        ).dropna()
        dollars = pd.to_numeric(
            priced_group.get("option_pl_dollars", pd.Series(dtype=float)), errors="coerce"
        ).dropna()

        rows.append({
            "exit_reason": reason,
            "trades": int(len(group)),
            "priced": int(len(net)),
            "avg_r": round(float(r.mean()), 2) if len(r) else None,
            "avg_premium_pct": round(float(net.mean()), 1) if len(net) else None,
            "total_dollars": round(float(dollars.sum()), 2) if len(dollars) else None,
        })

    # None sorts last: a reason with no priced trade is not evidence of a cheap
    # rule, it is an absence of measurement, and it does not belong at the top of
    # a table read worst-first.
    return sorted(
        rows,
        key=lambda row: (row["total_dollars"] is None, row["total_dollars"] or 0),
    )


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

    trades = closed_trades_from_db(days, reference, repository)

    return None if trades is None else build_spread_calibration(trades)


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

    # R concentrates the same way premium does, and on the window that prompted
    # this it concentrated harder: one orphaned SMCI position booked -23.67R
    # against a -21.78R total, so the other 27 trades summed to +1.89R. "Total R
    # -21.78" and "+1.89R across everything except one accident" support opposite
    # decisions, and only the first was ever on screen.
    worst_r = float(r.min()) if len(r) else None
    worst_r_is_a_loss = worst_r is not None and worst_r < 0
    worst_r_symbol = (
        str(trades.loc[r.idxmin()].get("symbol") or "")
        if worst_r_is_a_loss else None
    )

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

    # The book in cash, and how much of it is one position.
    #
    # A percentage average is silent about size. On the first clean window the
    # page reported -6.7% average premium P&L across 17 trades, which reads as a
    # strategy that loses steadily. In cash it was -$143 total, of which a single
    # SMCI position was -$207: every other trade combined made +$64. Those are
    # opposite conclusions from the same 17 trades, and only one of them was on
    # screen.
    dollars = pd.to_numeric(
        priced.get("option_pl_dollars", pd.Series(dtype=float)), errors="coerce"
    ).dropna()
    worst_position = dollars.idxmin() if len(dollars) else None
    worst_dollars = float(dollars.min()) if len(dollars) else None

    # Only meaningful when the extreme is a loss. On an all-winning window the
    # minimum is the smallest gain and "total excluding it" says nothing.
    worst_is_a_loss = worst_dollars is not None and worst_dollars < 0
    worst_symbol = (
        str(priced.loc[worst_position].get("symbol") or "")
        if worst_is_a_loss and worst_position is not None
        else None
    )

    return {
        "completed_trades": int(len(r)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(float(len(wins) / len(r) * 100), 1) if len(r) else None,
        "average_r": round(float(r.mean()), 2) if len(r) else None,
        "total_r": round(float(r.sum()), 2) if len(r) else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "max_drawdown_r": round(float((r.cumsum().cummax() - r.cumsum()).max()), 2) if len(r) else None,
        "worst_r": round(worst_r, 2) if worst_r_is_a_loss else None,
        "worst_r_symbol": worst_r_symbol,
        "total_r_ex_worst": (
            round(float(r.sum()) - worst_r, 2) if worst_r_is_a_loss else None
        ),
        # Premium terms. These are the ones that decide whether the day made money.
        "priced_trades": int(len(net)),
        "net_win_rate": round(float(len(net_wins) / len(net) * 100), 1) if len(net) else None,
        "total_option_pnl_pct": round(float(net.sum()), 2) if len(net) else None,
        "average_option_pnl_pct": round(float(net.mean()), 2) if len(net) else None,
        # Beside the mean, because the mean of a premium series with a -99.5%
        # in it describes no trade that was actually taken.
        "median_option_pnl_pct": round(float(net.median()), 2) if len(net) else None,
        "average_spread_cost_pct": round(float(spread.mean()), 2) if len(spread) else None,
        # Cash.
        "priced_dollar_trades": int(len(dollars)),
        "total_option_pl_dollars": round(float(dollars.sum()), 2) if len(dollars) else None,
        "average_option_pl_dollars": round(float(dollars.mean()), 2) if len(dollars) else None,
        "worst_trade_dollars": round(worst_dollars, 2) if worst_is_a_loss else None,
        "worst_trade_symbol": worst_symbol,
        "total_ex_worst_dollars": (
            round(float(dollars.sum()) - worst_dollars, 2) if worst_is_a_loss else None
        ),
    }