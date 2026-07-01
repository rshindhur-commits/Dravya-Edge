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

## Option Affordability

The scanner keeps two option concepts separate:

- Best quality contract: the strongest technical/liquidity contract, even if it is too expensive for the active account profile.
- Active affordable contract: the best contract that still passes quality gates and fits the configured capital profile.

In `OPTION_AFFORDABILITY_MODE=HARD`, a high-quality but expensive option is marked `QUALITY_BUT_TOO_EXPENSIVE` instead of `ENTER_PAPER`. The dashboard still shows the best-quality contract for review, but Paper Trade Setup and suggested-trade sync require `Affordable=True`.

Small-account defaults are documented in [.env.example](.env.example) and [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example):

```env
OPTION_AFFORDABILITY_MODE=HARD
OPTION_CAPITAL_PROFILE=SMALL_ACCOUNT
DAILY_START_CAPITAL=1000
OPTION_STOP_LOSS_PCT=0.20
OPTION_MAX_RISK_PER_TRADE_PCT=0.12
OPTION_MIN_CONTRACT_COST=100
OPTION_PREFERRED_MAX_CONTRACT_COST=500
OPTION_MAX_CONTRACT_COST=650
OPTION_MIN_AFFORDABLE_DELTA=0.25
```

Use `OPTION_CAPITAL_PROFILE=GROWTH_ACCOUNT` as buying power grows, or `OPTION_AFFORDABILITY_MODE=OFF` with `OPTION_CAPITAL_PROFILE=BEST_QUALITY` to return to the original best-quality-only behavior.

## Validate

```powershell
python -m unittest tests.test_market_session_decisions
```
