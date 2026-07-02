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

## Telegram Alerts

Telegram entry and exit alerts are opt-in and use duplicate protection so dashboard/scanner refreshes do not resend the same signal. Keep real bot tokens in local `.streamlit/secrets.toml` or Streamlit Cloud Secrets; do not commit them.

```toml
TELEGRAM_ALERTS_ENABLED = "true"
TELEGRAM_ENTRY_ALERTS_ENABLED = "true"
TELEGRAM_EXIT_ALERTS_ENABLED = "true"
TELEGRAM_MAX_ENTRY_ALERTS_PER_DAY = "3"
TELEGRAM_MAX_ACTIVE_ALERTED_TRADES = "2"
TELEGRAM_ENTRY_COOLDOWN_MINUTES = "60"
TELEGRAM_SYMBOL_COOLDOWN_MINUTES = "60"
TELEGRAM_TOP_CANDIDATE_LIMIT = "3"
TELEGRAM_MIN_ENTRY_ALERT_SCORE = "85"
TELEGRAM_INSTANT_ENTRY_ALERT_SCORE = "88"
TELEGRAM_AFTERNOON_MIN_ENTRY_ALERT_SCORE = "90"
TELEGRAM_MIN_OPTION_QUALITY_SCORE = "65"
TELEGRAM_MIN_RR = "2.0"
TELEGRAM_MAX_SPREAD_PCT = "8"
TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE = "70"
TELEGRAM_MAX_MORNING_ENTRY_ALERTS = "2"
TELEGRAM_MAX_MIDDAY_ENTRY_ALERTS = "1"
TELEGRAM_MAX_AFTERNOON_ENTRY_ALERTS = "1"
TELEGRAM_EXIT_PRICE_MISMATCH_PCT = "0.03"
AUTO_PAPER_ENABLED = "true"
ENABLE_MANUAL_PAPER_ENTRIES = "false"
SHOW_MANUAL_PAPER_BUTTONS = "false"
ALLOW_MANUAL_PAPER_CLOSE = "true"

[telegram]
bot_token = "YOUR_BOT_TOKEN_FROM_BOTFATHER"
chat_id = "YOUR_TELEGRAM_CHAT_ID"
```

The scanner sends entry alerts only for high-conviction `ENTER`, `ENTER_PAPER`, or `REVIEW_TV_CHART` option setups that are not marked unaffordable and are within the configured top-candidate limit. Entry alerts are scored after the full scan is ranked, then attempted strongest-first in the same scan; the system does not wait for a later time bucket to compare future candidates. Auto paper entries send entry alerts at the exact moment a system paper trade opens, as long as the opened row is realtime-ready, affordable, has sufficient setup/RR, fresh quote, acceptable spread, and option quality. Manual paper entry buttons are hidden by default (`ENABLE_MANUAL_PAPER_ENTRIES=false`, `SHOW_MANUAL_PAPER_BUTTONS=false`) to keep validation telemetry clean; manual close/correction remains available by default. The default entry buckets are max 2 regular alerts from 9:45-10:30 ET, max 1 regular alert from 10:30-13:30 ET, max 1 A+ style alert from 13:30-14:45 ET, and no new entries after 14:45 ET. A+ alerts at or above `TELEGRAM_INSTANT_ENTRY_ALERT_SCORE` bypass per-bucket caps but still respect the daily max, active alerted trade cap, duplicate cooldown, quote/quality/spread/affordability gates, and no-late-entry cutoff. Exit alerts send when scanner-managed trades close on stop, target, trailing/invalidation logic, or when paper trades are closed manually/automatically from the dashboard. Exit-alert price validation resolves the current underlying price from the freshest available same-symbol source in this order: `latest_quote`, `df_5m_latest_close`, then `df_15m_latest_close`. Alerts are blocked if that resolved price differs from the same-symbol expected close by more than `TELEGRAM_EXIT_PRICE_MISMATCH_PCT` (default 3%). Partial-profit scanner events send a one-time `PARTIAL EXIT ALERT`. Sent alert keys are stored in `app/state/telegram_alert_state.json`, which is ignored by Git.

## Validate

```powershell
python -m unittest tests.test_market_session_decisions
```
