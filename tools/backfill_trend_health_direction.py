"""Rescore the archived exit analyses the trade's own way.

`build_trade_snapshot` built every trend-health input unconditionally bullish, so
every PUT in `trade_exit_analysis` was scored backwards -- and `exit_verdict` is
derived from the state, so the self-review has been giving reversed advice on
shorts since 2026-07-30.

The raw readings are in each row's payload, so the correct score is recoverable
without replaying anything. Each corrected row is stamped with what it held
before.
"""

import sys

sys.path.insert(0, r"d:\Dravya_Trade_Works")

from dotenv import load_dotenv

load_dotenv(r"d:\Dravya_Trade_Works\.env")

from sqlalchemy import text

from app.analytics.trend_capture import classify_exit_verdict
from app.analytics.trend_health import evaluate_trend_health, trend_health_state
from app.db.connection import get_engine

APPLY = "--apply" in sys.argv


def oriented(payload, direction):

    short = str(direction).upper() in {"PUT", "SHORT"}
    rsi = payload.get("RSI At Exit")
    flag = lambda key: bool(payload.get(key))

    if not short:

        return {
            "ema_alignment": flag("EMA Alignment"),
            "price_above_ema9": flag("Price Above EMA9"),
            "price_above_vwap": flag("Price Above VWAP"),
            "higher_high": flag("Higher High At Exit"),
            "higher_low": flag("Higher Low At Exit"),
            "macd_bullish": flag("MACD Bullish"),
            "rsi": rsi,
            "relative_volume": payload.get("Relative Volume At Exit"),
        }

    return {
        "ema_alignment": not flag("EMA Alignment"),
        "price_above_ema9": not flag("Price Above EMA9"),
        "price_above_vwap": not flag("Price Above VWAP"),
        "higher_high": flag("Lower High At Exit"),
        "higher_low": flag("Lower Low At Exit"),
        "macd_bullish": not flag("MACD Bullish"),
        "rsi": (100 - rsi) if rsi is not None else None,
        "relative_volume": payload.get("Relative Volume At Exit"),
    }


engine = get_engine()

with engine.connect() as conn:
    rows = list(conn.execute(text("""
        select trade_key, symbol, direction, trading_day,
               trend_health_score::float s, trend_health_state st,
               exit_verdict, trend_capture_pct::float cap, payload
        from trade_exit_analysis
        order by trading_day, symbol
    """)))

plan = []

for row in rows:

    if not row.payload:
        continue

    scored = evaluate_trend_health(oriented(row.payload, row.direction))
    score = scored["score"]
    state = trend_health_state(score)
    verdict = classify_exit_verdict(row.cap, state)

    if score != row.s or state != row.st or verdict != row.exit_verdict:
        plan.append((row, score, state, verdict))

print(f"{len(rows)} archived rows, {len(plan)} need correcting\n")

for row, score, state, verdict in plan:
    print(f"  {row.trading_day} {row.symbol:6} {str(row.direction):5} "
          f"{row.s:5.0f} -> {score:3.0f}   {str(row.st):>10} -> {state:<10} "
          f"{str(row.exit_verdict):>16} -> {verdict}")

if not APPLY:

    print("\n  dry run. re-run with --apply to write.")

else:

    with engine.begin() as conn:

        for row, score, state, verdict in plan:

            note = (
                f"direction-aware 2026-08-21; was {row.s} {row.st} / {row.exit_verdict}"
            )
            conn.execute(text("""
                update trade_exit_analysis
                   set trend_health_score = :score,
                       trend_health_state = :state,
                       exit_verdict = :verdict,
                       payload = payload || jsonb_build_object(
                           'Trend Health Score', :score,
                           'Trend Health State', :state,
                           'Exit Verdict', :verdict,
                           'Trend Health Backfilled', :note)
                 where trade_key = :key
            """), {
                "score": score,
                "state": state,
                "verdict": verdict,
                "note": note,
                "key": row.trade_key,
            })

    print(f"\n  applied to {len(plan)} rows, each stamped with its previous value")
