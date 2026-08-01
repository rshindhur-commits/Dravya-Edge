"""A plain-English account of the trading day, one story per trade.

Deliberately not the daily validation report. That is a 23-section engineering
diagnostic -- gate quality by window, quote freshness, replay calibration,
backtest validation -- written to find defects. This answers a different
question, in sentences: what did we do today, what did it make, and was getting
out when we did the right call.

Everything comes from `trade_exit_analysis` in Postgres, so it can be rebuilt
for any past day from any machine. It falls back to the day's
`trend_capture_analysis.csv` when the database is unreachable.
"""

from __future__ import annotations

from html import escape

import pandas as pd

from app.storage.daily_paths import daily_path


def _number(value):
    if value in (None, "", "None"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if parsed != parsed else parsed


def _clock(value):
    if not value:
        return None
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(stamp):
        return None
    return stamp.tz_convert("America/New_York").strftime("%H:%M")


def _points(value, digits=2):
    parsed = _number(value)
    return None if parsed is None else f"{parsed:,.{digits}f}"


def load_exit_rows(trading_day):
    """Trade-grain exit analysis, from Postgres first and the CSV second."""
    try:
        from app.db.trade_exit_analysis_repository import TradeExitAnalysisRepository

        rows = TradeExitAnalysisRepository().load_day(trading_day)
        if rows:
            return rows
    except Exception as exc:
        print(f"[POST MARKET REVIEW WARNING] database read failed: {exc}")

    path = daily_path(trading_day, "trend_capture_analysis.csv")
    if not path.exists() or not path.stat().st_size:
        return []

    from app.db.trade_exit_analysis_repository import to_record

    try:
        frame = pd.read_csv(path)
    except Exception:
        return []

    return [
        to_record(trading_day, row.get("Trade Key"), row)
        for row in frame.to_dict("records")
    ]


def _what_happened(row):
    direction = "a bet it would go up" if str(row.get("direction") or "").upper() in {
        "CALL", "LONG"
    } else "a bet it would go down"
    opened, closed = _clock(row.get("entry_time")), _clock(row.get("exit_time"))
    entry, exit_price = _points(row.get("entry_price")), _points(row.get("exit_price"))
    bars = row.get("bars_held")

    held = ""
    if bars:
        minutes = int(bars) * 5
        held = f" It was held for about {minutes} minutes."

    when = f" from {opened} to {closed} ET" if opened and closed else ""
    return (
        f"{row.get('symbol')} — {direction}{when}. "
        f"In at {entry}, out at {exit_price}.{held}"
    )


def _what_it_made(row):
    captured = _number(row.get("captured_move"))
    if captured is None:
        return "The result was not recorded."

    if captured >= 0:
        return f"It made {_points(captured)} points per share."
    return f"It lost {_points(abs(captured))} points per share."


def _what_was_available(row):
    """How much of the move that existed did we actually take."""
    available = _number(row.get("available_move"))
    captured = _number(row.get("captured_move"))
    left = _number(row.get("left_on_table"))
    capture_pct = _number(row.get("trend_capture_pct"))

    if available is None or available <= 0:
        return "The price never moved our way after entry, so there was nothing to capture."

    if captured is not None and captured < 0:
        return (
            f"The price did move our way by {_points(available)} points at best, "
            f"but we came out on the wrong side of it."
        )

    # The stored percentage goes wild on losing trades (one 2026-07-30 row reads
    # -2211%), so it is only quoted when it describes something sensible.
    share = ""
    if capture_pct is not None and 0 <= capture_pct <= 200:
        share = f" That is {capture_pct:.0f}% of what was there."

    tail = ""
    if left is not None and left > 0:
        tail = f" We left {_points(left)} points on the table."

    return (
        f"There were {_points(available)} points available after we got in, "
        f"and we took {_points(captured)}.{share}{tail}"
    )


EXIT_IN_PLAIN_WORDS = {
    "EMA9": "the price slipped back under its short-term average",
    "VWAP": "the price dropped through the day's average price",
    "MACD": "momentum turned against us",
    "HARD STOP": "it hit the stop we had set",
    "HARD TARGET": "it reached the profit target we had set",
    "TIME": "it ran out of time",
    "NEAR CLOSE": "the market was about to close",
    "TREND FAILURE": "the trend broke down",
}


def _why_we_got_out(row):
    reason = str(row.get("exit_reason") or row.get("primary_exit") or "").strip()
    if not reason:
        return "There is no record of why this trade was closed."

    plain = next(
        (words for key, words in EXIT_IN_PLAIN_WORDS.items() if key in reason.upper()),
        None,
    )
    if plain:
        return f"We got out because {plain}."
    return f"We got out on: {reason}."


VERDICT_IN_PLAIN_WORDS = {
    "EXIT_TOO_EARLY": "We got out too early — the move kept going without us.",
    "EXIT_TOO_LATE": "We held on too long and gave back profit.",
    "GOOD_EXIT": "The timing of the exit looks right.",
    "EXCELLENT": "The exit was close to the best available.",
    "OPTIMAL": "The exit was close to the best available.",
}


def _the_verdict(row):
    verdict = str(row.get("exit_verdict") or "").strip().upper()
    quality = str(row.get("exit_quality") or "").strip()
    comments = str(row.get("exit_comments") or "").strip()

    plain = VERDICT_IN_PLAIN_WORDS.get(verdict)
    if not plain and quality:
        plain = f"The exit was graded {quality.lower()}."
    if not plain:
        return None

    return f"{plain} {comments}".strip() if comments else plain


def _the_chart_at_exit(row):
    """The indicator state, described rather than tabulated."""
    parts = []
    exit_price = _number(row.get("exit_price"))
    ema9, vwap = _number(row.get("ema9")), _number(row.get("vwap"))
    rsi, atr = _number(row.get("rsi")), _number(row.get("atr"))
    health = _number(row.get("trend_health_score"))
    state = str(row.get("trend_health_state") or "").strip()

    if exit_price is not None and ema9 is not None:
        side = "above" if exit_price > ema9 else "below"
        parts.append(f"price was {side} its short-term average ({_points(ema9)})")
    if exit_price is not None and vwap is not None:
        side = "above" if exit_price > vwap else "below"
        parts.append(f"{side} the day's average price ({_points(vwap)})")
    if rsi is not None:
        mood = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral"
        parts.append(f"RSI {rsi:.0f} ({mood})")
    if atr is not None:
        parts.append(f"typical swing {_points(atr)} points")
    if state:
        score = f" ({health:.0f}/100)" if health is not None else ""
        parts.append(f"trend read as {state.lower()}{score}")

    return ("At the moment we exited, " + ", ".join(parts) + ".") if parts else None


def describe_trade(row):
    """One trade as a short paragraph an operator can read at a glance."""
    return {
        "symbol": row.get("symbol"),
        "headline": _what_happened(row),
        "result": _what_it_made(row),
        "available": _what_was_available(row),
        "why": _why_we_got_out(row),
        "verdict": _the_verdict(row),
        "chart": _the_chart_at_exit(row),
        "flagged": str(row.get("exit_verdict") or "").upper() in {
            "EXIT_TOO_EARLY", "EXIT_TOO_LATE"
        },
    }


def summarise(rows):
    captured = [_number(row.get("captured_move")) for row in rows]
    captured = [value for value in captured if value is not None]
    left = [_number(row.get("left_on_table")) for row in rows]
    left = [value for value in left if value is not None and value > 0]
    early = sum(
        1 for row in rows
        if str(row.get("exit_verdict") or "").upper() == "EXIT_TOO_EARLY"
    )

    return {
        "trades": len(rows),
        "winners": sum(1 for value in captured if value > 0),
        "losers": sum(1 for value in captured if value <= 0),
        "net_points": round(sum(captured), 2) if captured else None,
        "left_on_table": round(sum(left), 2) if left else None,
        "exits_too_early": early,
    }


def _headline(summary):
    trades = summary["trades"]
    if not trades:
        return "No trades were completed on this day."

    net = summary["net_points"]
    outcome = (
        "made" if net is not None and net > 0
        else "lost" if net is not None and net < 0
        else "finished flat on"
    )
    amount = f" {abs(net):,.2f} points per share" if net not in (None, 0) else ""
    plural = "trade" if trades == 1 else "trades"

    sentence = (
        f"{trades} {plural} completed — {summary['winners']} made money, "
        f"{summary['losers']} did not. Overall the day {outcome}{amount}."
    )

    if summary["left_on_table"]:
        sentence += (
            f" Across the day {summary['left_on_table']:,.2f} points were left on "
            f"the table after we exited."
        )
    if summary["exits_too_early"]:
        count = summary["exits_too_early"]
        sentence += (
            f" {count} exit{'s' if count > 1 else ''} looked too early."
        )
    return sentence


def build_review(trading_day):
    """(html, summary) for one trading day."""
    rows = load_exit_rows(trading_day)
    summary = summarise(rows)
    stories = [describe_trade(row) for row in rows]

    blocks = []
    for story in stories:
        lines = [story["result"], story["available"], story["why"]]
        if story["verdict"]:
            lines.append(story["verdict"])
        if story["chart"]:
            lines.append(story["chart"])
        # quote=False: this is body text, not an attribute, and escaping the
        # apostrophe turns "the day's average price" into "day&#x27;s".
        body = "".join(f"<p>{escape(line, quote=False)}</p>" for line in lines if line)
        flag = " flagged" if story["flagged"] else ""
        headline = escape(str(story["headline"]), quote=False)
        blocks.append(
            f'<section class="trade{flag}"><h2>{headline}</h2>{body}</section>'
        )

    if not blocks:
        blocks.append(
            "<section class='trade'><p>Nothing was completed on this day, so "
            "there is nothing to review.</p></section>"
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Post-Market Review {escape(str(trading_day))}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 46rem;
        line-height: 1.6; color: #1f2937; }}
 h1 {{ font-size: 1.6rem; margin-bottom: .2rem; }}
 .day {{ font-size: 1.05rem; background: #f3f4f6; padding: .9rem 1.1rem;
         border-radius: 10px; margin-bottom: 1.6rem; }}
 .trade {{ border-left: 4px solid #cbd5e1; padding: .1rem 0 .1rem 1.1rem;
           margin-bottom: 1.7rem; }}
 .trade.flagged {{ border-left-color: #f59e0b; }}
 .trade h2 {{ font-size: 1.05rem; margin: .2rem 0 .5rem 0; }}
 p {{ margin: .35rem 0; }}
 footer {{ margin-top: 2.5rem; font-size: .85rem; color: #6b7280; }}
</style></head><body>
<h1>Post-Market Review — {escape(str(trading_day))}</h1>
<div class="day">{escape(_headline(summary), quote=False)}</div>
{''.join(blocks)}
<footer>Read from trade_exit_analysis. Points are per share of the underlying,
not option premium. For gate quality, quote freshness and replay calibration,
see the daily validation report.</footer>
</body></html>""", summary


def write_review(trading_day):
    html, summary = build_review(trading_day)
    path = daily_path(trading_day, "post_market_review.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path, summary
