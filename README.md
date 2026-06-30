# Dravya Trade Works

Python intraday stock/options scanner for watchlist ranking, setup detection, risk checks, option contract ranking, paper-trade tracking, and Streamlit dashboard review.

Full architecture and operating notes live in [Project_state.md](Project_state.md).

## Run Scanner

```powershell
python -m app.main
```

Run from the workspace root so package imports resolve correctly.

## Market Session Flow

- Premarket, 4:00-9:30 ET: strong call/put candidates can surface as `PREMARKET_WATCH`, but they are not execution-ready and cannot auto paper-enter.
- Opening range, 9:30-9:45 ET: candidates wait as `OPENING_RANGE_CONFIRMATION` until regular-market confirmation develops.
- After 9:45 ET: `ENTER` or `ENTER_PAPER` is allowed only when stock freshness, risk, timing, option quality, quote freshness, event, regime, and dashboard auto-entry gates pass.

Polygon/Massive aggregate freshness accounts for candle bucket-start timestamps, so a current 5-minute aggregate is not marked stale solely because the timestamp is at the start of the candle.

## Validate

```powershell
python -m unittest tests.test_market_session_decisions
```
