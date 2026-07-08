# Dravya Trade Works

Python intraday stock/options scanner for watchlist ranking, setup detection, risk checks, option contract ranking, paper-trade tracking, and Streamlit dashboard review.

Full architecture and operating notes live in [Project_state.md](Project_state.md).

## Run Scanner

```powershell
python -m app.main
```

Run from the workspace root so package imports resolve correctly.

Each scanner run also writes normalized candidate snapshots and signal lifecycle observations for research review. Candidate snapshots prefer `data/daily/YYYY-MM-DD/candidate_snapshots.parquet`; if the local environment does not have a parquet engine such as `pyarrow` or `fastparquet`, the writer falls back to `data/daily/YYYY-MM-DD/candidate_snapshots.csv`. Lifecycle observations append to `data/daily/YYYY-MM-DD/signal_lifecycle_events.csv` and state changes append to `data/daily/YYYY-MM-DD/signal_state_transitions.csv`.

## Research And Backtesting

The app now has a first-pass research layer for validating edge quality before adding more strategy rules:

- `app/analytics/candidate_snapshot_writer.py` records every scanner candidate row, including skipped and blocked setups.
- `app/analytics/replay_calibration.py` measures MFE, MAE, bars to target/stop, best ATR stop/target multiples, best time-exit horizon, and win rate by horizon.
- `app/analytics/expectancy_engine.py` builds expectancy tables with trade count, win rate, average/median/total R, profit factor, average win/loss R, max drawdown R, and expectancy R.
- `app/analytics/expectancy_report.py` creates grouped expectancy reports by setup, direction, regime, candidate rank, time bucket, option-quality bucket, spread bucket, and expiration bucket when those fields are available.
- `app/backtesting/` contains the initial no-lookahead historical backtesting framework: dataset loading, scanner-at-time evaluation, backtest runner, and HTML report output.

The backtesting framework is intentionally stock-first. It validates whether the underlying setup hit target before stop using historical candles; historical option quote replay and option P/L approximation are later steps.

## Daily Validation Report

Daily validation uses a session model:

```text
One trading day = one validation folder
Many scanner refreshes = one daily dataset
App restart = same daily session
End of day = finalized daily report
```

Each scanner run resolves a trading day and scan id:

```text
trading_day = YYYY-MM-DD
session_id = paper_validation_YYYY-MM-DD
scan_id = YYYY-MM-DD_HHMMSS
```

Candidate snapshots and telemetry rows include `trading_day`, `session_id`, `scan_id`, `scan_timestamp`, and stable candidate/trade keys where available.

After each paper session, generate a daily scorecard from scanner output, telemetry, and state files:

```powershell
python tools/daily_validation_report.py --date 2026-07-02 --archive
```

This writes `reports/daily_validation_YYYY-MM-DD.html` and also copies the latest report to `data/daily/YYYY-MM-DD/daily_validation_report.html`.

Use `--update-daily` when you want to copy available live/legacy files into the daily folder before report generation. Use `--finalize` after market close to mark `data/daily/YYYY-MM-DD/manifest.json` as final:

```powershell
python tools/daily_validation_report.py --date 2026-07-02 --update-daily --finalize
```

With `--archive`, it also copies the available daily inputs into `daily_reviews/YYYY-MM-DD/`:

- `scanner_output.xlsx`
- `telemetry/trade_telemetry.csv`
- `app/state/paper_trade_state.json`
- `app/state/trade_state.json`
- `app/state/auto_paper_decision_log.json`
- `app/state/suggested_trade_state.json`

The report summarizes daily paper-trade R, opened trades, auto-paper open/block/skip counts, top block reasons, best skipped opportunities, replay outcome by setup, rolling expectancy tables, and rule-change suggestions.

Auto-paper decisions are stored in two places with different purposes:

- `data/daily/YYYY-MM-DD/auto_paper_decisions.csv`: full-day append-only audit trail and primary report source.
- `app/state/auto_paper_decision_log.json`: capped to the most recent 500 rows for fast dashboard display.

Every auto-paper decision includes market-session fields such as `trading_day`, `session_id`, `scan_id`, `scan_timestamp`, `market_session`, `decision_time_bucket`, `is_auto_entry_window`, `is_after_close`, `minutes_from_open`, and `minutes_to_close`. Starting from the latest fixes, each decision also logs the active gate thresholds at decision time: `gate_mode` (always `"auto_paper"`), `min_rr_used`, and `min_setup_used`. This makes it possible to tell whether a skip reason like `RR_BELOW_THRESHOLD` was caused by the actual threshold in use at that moment, or by a gate mismatch. Decision rows also include scanner eligibility diagnostics such as `setup_valid`, `execution_ready`, `realtime_ready`, `affordable`, `price_geometry_ok`, `price_geometry_error`, `scanner_output_age_minutes`, `allow_review_tv_chart_auto_paper`, and `review_validation_candidate`, so startup/restart skips like `NO_AUTO_PAPER_CANDIDATE` can be inspected directly. The report reads the full daily CSV first and falls back to the capped JSON with a warning if the daily file is missing.

Auto-paper decisions are emitted from the Streamlit dashboard auto-paper path, not from the standalone scanner loop. The dashboard writer appends the full-day CSV and then updates the capped dashboard JSON from the same decision row.

The first section is `Validation Data Health`. It shows counts for scanner rows, candidate snapshots, telemetry rows, paper-trade state records, paper-trade event rows, auto-paper decisions, opened decisions, suggested trades, and trade state records. If no paper trades are found, the report explicitly warns that it is based on blocked/skipped candidates only.

The report also includes `F. Data Quality Checks`, which counts invalid price geometry, option direction mismatches, high-setup rows blocked by setup threshold reasons, review rows missing realtime-block reasons, actual opened trades, and suggested-but-not-entered rows.

The report splits gate and quote diagnostics by session:

- `Gate Quality - Full Day`
- `Gate Quality - Auto Entry Window Only`
- `Gate Quality - After Close Only`
- `Quote Freshness - Auto Entry Window Only`
- `Quote Freshness - After Close Only`

Use the auto-entry-window sections to judge whether auto-paper is too strict during 9:45-15:30 ET. Treat after-close stale or delayed quotes as diagnostic noise unless they also appear during the auto-entry window.

The dashboard also adds research-only early-watch and shadow diagnostic columns to help review missed opportunities without changing execution behavior. These include `Early Watch Status`, `Early Watch Reason`, `Would Pass Gate If RR 1.7`, `Would Pass Gate If Setup 65`, `Would Pass Gate If Review Allowed`, `Late Entry Risk`, and `Missed Move Type`. They are informational only: they do not change `Action Status`, auto-paper gates, Telegram alerts, affordability checks, option-quality checks, or exit rules. When review-validation mode is enabled, `Late Entry Risk` and `Missed Move Type` become safety guards that block paper-only review entries that are already too late or have already moved.

The report also includes `G. Signal Lifecycle Analysis`, built from `signal_lifecycle_events.csv`, `signal_state_transitions.csv`, and `suggested_trade_state.json`. It answers:

- During 9:45-15:30 ET, what percent of option observations were `LIVE_QUOTE`, `DELAYED_QUOTE`, `STALE_QUOTE`, or missing?
- How long did candidates stay in `REVIEW_TV_CHART` before promotion, avoidance, wait, quote delay, or expiry?
- Did valid-looking suggestions expire quickly, including while `LIVE_QUOTE`, while RR >= 1.8, or while setup >= 70?

Lifecycle observation timestamps use the current ET time when scanner results are finalized, while `scan_id` remains tied to the scan start. Lifecycle event rows include debug context such as `final_signal`, `action_reason`, `option_quote_age_minutes`, `expiration_bucket`, `affordability_status`, `option_contract_cost`, `market_regime`, and `reference_regime`.

Paper trade opens/closes also append to `data/daily/YYYY-MM-DD/paper_trade_events.csv`. The report uses this event log before falling back to telemetry or current paper-trade state, so a cleared JSON state file does not hide the fact that a paper trade opened earlier in the session.

Suggestion expiry observability is stored on `app/state/suggested_trade_state.json` records with fields such as `expired_at`, `lifetime_minutes`, `valid_minutes`, `review_minutes`, `scans_seen`, `last_state_before_expiry`, `expiry_market_session`, and `expiry_reason`. All suggested-trade timestamps (`first_seen_at`, `last_seen_at`, `last_valid_at`, `expired_at`) are stored as timezone-aware ET ISO-8601 strings (e.g., `2026-07-07T10:22:11-04:00`) to ensure consistent lifetime calculations. Expiry also writes a lifecycle transition to `EXPIRED_NOT_ENTERED|NO_OPTION|NOT_READY|NOT_PRESENT` when the prior state is known.

Current daily files live under `data/daily/YYYY-MM-DD/`; dashboard/latest mirrors live under `data/live/` while legacy root files remain for compatibility with the existing dashboard.

The dashboard reads `data/live/scanner_output_latest.csv` first, then `data/live/scanner_output_latest.xlsx`, then falls back to `scanner_output.xlsx`. Scanner execution is protected by a stale-aware lock at `data/live/scanner_run.lock` and a persistent cooldown/status file at `data/live/scanner_run_status.json`, so Streamlit refreshes or multiple browser sessions do not start overlapping or back-to-back scanner runs. Polygon aggregate requests use the short `POLYGON_CACHE_TTL` cache to avoid duplicate candle requests during rapid refreshes.

The main Streamlit page is organized as an operator dashboard: compact status cards, compact market health, Action Center, Scanner Watchlist, and Paper / Real Validation Summary. Diagnostic surfaces such as suggestion lifecycle, full auto-paper decision logs, validation data health, daily report downloads, telemetry, and last-seen candidates live under collapsed expanders so the live page focuses on whether there is something to do right now.

If the dashboard is running locally on the same machine, the terminal command can read the same files directly. If the dashboard is running on Streamlit Cloud, generate the report inside the dashboard instead: use the sidebar `Generate Daily Validation Report` button, then download `daily_validation_report.html` from the same sidebar. That keeps report generation in the same filesystem where Streamlit created scanner output, telemetry, and state files.

## Neon Persistence

Neon/Postgres persistence is optional and additive. The dashboard still reads the current CSV, Excel, and JSON files first, so Streamlit remains usable if the database is not configured or a write fails.

DB writes are enabled only when `DB_WRITE_ENABLED=true` and `DATABASE_URL` is present. For Streamlit Cloud, use the Neon pooler host for `DATABASE_URL`; keep `DATABASE_DIRECT_URL` for schema setup and one-time migrations. Do not commit real database passwords.

Streamlit Secrets placeholders:

```toml
# =========================
# DATABASE
# =========================
DATABASE_URL = "postgresql+psycopg2://neondb_owner:PASTE_ROTATED_PASSWORD_HERE@ep-round-pond-atro9w6h-pooler.c-9.us-east-1.aws.neon.tech/neondb?sslmode=require"
DATABASE_DIRECT_URL = "postgresql+psycopg2://neondb_owner:PASTE_ROTATED_PASSWORD_HERE@ep-round-pond-atro9w6h.c-9.us-east-1.aws.neon.tech/neondb?sslmode=require"
DB_WRITE_ENABLED = "true"
DB_POOL_SIZE = "5"
DB_MAX_OVERFLOW = "5"
DB_CONNECT_TIMEOUT_SECONDS = "10"
```

These keys should be at the root level of Streamlit Secrets. In TOML, keys placed after `[telegram]` belong to the `telegram` table until another `[section]` starts, so put the root-level database block before `[telegram]`. If you use Streamlit native connections, the app can also fall back to a connection `url` under common names such as `[connections.trading_db]`, `[connections.neon]`, or `[connections.postgres]`. It also supports a `[database]` section with `url`, `direct_url`, and `write_enabled` keys. Root-level keys remain the clearest deployment path.

Current DB-backed tables are intentionally small event/state tables:

- `alert_events`: Telegram entry/exit attempts, sent status, skip/error reason, and dedupe key.
- `paper_trades`: auto/manual paper trade opens and closes, with compact payload context.
- `scanner_runs`: scanner start/end status, row count, output path, and small run summary.
- `gate_decisions`: per-symbol action/gate summary for scanner rows.

Do not store full candle history, option chain snapshots, raw API responses, scanner Excel blobs, or large CSV payloads in Neon during the free-tier phase.

DB writes are optional audit persistence, not part of the live trading decision path. Failed DB writes should log warnings and must not block Telegram sends, scanner output, paper trade JSON/CSV state, or dashboard rendering.

Use narrow idempotency keys only. `alert_events.dedupe_key` is the safest early unique key because Telegram duplicate protection already uses deterministic alert keys; failed send attempts and later successful retries can update the same audit row. Avoid broad unique constraints such as `UNIQUE(symbol)`, `UNIQUE(symbol, trading_day)`, `UNIQUE(trading_day)`, or `UNIQUE(option_ticker)`, because valid intraday flows can produce multiple scans, blocked decisions, opens, closes, re-entries, and refreshed option observations for the same symbol or contract. Add broader table constraints only through an explicit Neon migration and duplicate-write tests.

After creating the tables in Neon SQL Editor and setting local `DATABASE_URL`, test connectivity from the workspace root:

```powershell
python tools\test_db_connection.py
```

To verify inserts into `scanner_runs`, run:

```powershell
python tools\test_db_insert.py
```

The scanner prints a non-sensitive startup line like this when it runs:

```text
[STREAMLIT SECRETS STATUS] ROOT_DATABASE_URL_PRESENT= True ROOT_DB_WRITE_ENABLED_PRESENT= True DATABASE_SECTION_FOUND= False NESTED_DATABASE_KEYS_FOUND= False CONNECTION_URL_FOUND= False ENV_DATABASE_URL_PRESENT= True ENV_DB_WRITE_ENABLED= true
[DB STATUS] DB_WRITE_ENABLED= true DATABASE_URL_PRESENT= True DB_WRITES_ACTIVE= True
```

If `DB_WRITES_ACTIVE` is false in Streamlit Cloud logs, check that Streamlit Secrets include `DB_WRITE_ENABLED=true` and `DATABASE_URL`. The runtime banner uses `APP_ENV`; `ENV` is also accepted as a fallback alias for Streamlit Secrets.

## Market Session Flow

- Premarket, 4:00-9:30 ET: strong call/put candidates can surface as `PREMARKET_WATCH`, but they are not execution-ready and cannot auto paper-enter.
- Opening range, 9:30-9:45 ET: candidates wait as `OPENING_RANGE_CONFIRMATION` until regular-market confirmation develops.
- After 9:45 ET: `ENTER` or `ENTER_PAPER` is allowed only when stock freshness, risk, timing, option quality, quote freshness, event, regime, and dashboard auto-entry gates pass.
- CALL/PUT price geometry is a hard safety rule. CALLs require `stop < entry < target`; PUTs require `target < entry < stop`. Invalid geometry is blocked before normal suggestions, alerts, or paper entry.

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

Exit alerts are only allowed for explicitly tracked `PAPER` or `REAL` trades. Legacy scanner-managed `trade_state.json` entries are treated as dashboard state and do not send Telegram exits unless promoted to a confirmed paper/real trade mode. Successful exit alerts mark `exit_alert_sent` on the trade, and deterministic alert keys include symbol, option ticker, open time, and exit reason.

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
ALLOW_REVIEW_TV_CHART_AUTO_PAPER = "false"
REAL_TRADING_ENABLED = "false"
REAL_ALERTS_ONLY = "true"
REAL_MAX_TRADES_PER_DAY = "1"
REAL_MIN_SETUP = "88"
REAL_MIN_RR = "2.0"
REAL_MIN_OPTION_QUALITY = "90"
REAL_MAX_SPREAD_PCT = "8"
REAL_MAX_QUOTE_AGE_MINUTES = "3"
REAL_ENTRY_CUTOFF_ET = "14:30"
ENABLE_MANUAL_PAPER_ENTRIES = "false"
SHOW_MANUAL_PAPER_BUTTONS = "false"
ALLOW_MANUAL_PAPER_CLOSE = "true"

[telegram]
bot_token = "YOUR_BOT_TOKEN_FROM_BOTFATHER"
chat_id = "YOUR_TELEGRAM_CHAT_ID"
```

The scanner sends entry alerts only for high-conviction `ENTER`, `ENTER_PAPER`, or `REVIEW_TV_CHART` option setups that are not marked unaffordable and are within the configured top-candidate limit. Entry alerts are scored after the full scan is ranked, then attempted strongest-first in the same scan; the system does not wait for a later time bucket to compare future candidates. Auto paper entries send entry alerts at the exact moment a system paper trade opens, as long as the opened row is realtime-ready, affordable, has sufficient setup/RR, fresh quote, acceptable spread, and option quality. `ALLOW_REVIEW_TV_CHART_AUTO_PAPER=false` by default; when enabled, high-quality `REVIEW_TV_CHART` rows can enter paper-only validation as `entry_source=AUTO_PAPER_REVIEW_VALIDATION`, `trade_mode=PAPER`, and `include_in_strategy_stats=false` after the same strict paper gates, top-candidate filter, bid/ask, quote freshness, affordability, event/regime, cooldown, duplicate, and daily-cap checks pass. Review-validation entries are additionally blocked at or after 14:45 ET, when `Late Entry Risk` is `LATE_CHASE_RISK`, when `Missed Move Type` is populated, or when the row is not a configured top candidate. Manual paper entry buttons are hidden by default (`ENABLE_MANUAL_PAPER_ENTRIES=false`, `SHOW_MANUAL_PAPER_BUTTONS=false`) to keep validation telemetry clean; manual close/correction remains available by default. The default entry buckets are max 2 regular alerts from 9:45-10:30 ET, max 1 regular alert from 10:30-13:30 ET, max 1 A+ style alert from 13:30-14:45 ET, and no new entries after 14:45 ET. A+ alerts at or above `TELEGRAM_INSTANT_ENTRY_ALERT_SCORE` bypass per-bucket caps but still respect the daily max, active alerted trade cap, duplicate cooldown, quote/quality/spread/affordability gates, and no-late-entry cutoff. Exit alerts send only when confirmed paper/real trades close manually or automatically; scanner-managed `trade_state.json` exits remain dashboard-only. Exit-alert price validation resolves the current underlying price from the freshest available same-symbol source in this order: `latest_quote`, `df_5m_latest_close`, then `df_15m_latest_close`. Alerts are blocked if that resolved price differs from the same-symbol expected close by more than `TELEGRAM_EXIT_PRICE_MISMATCH_PCT` (default 3%). Sent alert keys are stored in `app/state/telegram_alert_state.json`, which is ignored by Git.

Real-trade readiness is dashboard guidance only. `REAL_TRADING_ENABLED=false` and `REAL_ALERTS_ONLY=true` keep the app in manual-review mode; no real orders are placed. Rows marked `A_PLUS_REAL_REVIEW` must be `ENTER`, `ENTER_PAPER`, or `REVIEW_TV_CHART`, be `BULLISH_TOP_1` or `BEARISH_TOP_1`, meet the real thresholds, have a live quote age within `REAL_MAX_QUOTE_AGE_MINUTES`, avoid late/chase and missed-move flags, avoid event/regime blocks, appear in at least two consecutive suggested-trade scans, and occur before `REAL_ENTRY_CUTOFF_ET`. The dashboard shows `Real Trade Readiness`, `Real Review Scan Count`, and `Real Entry Checklist` for manual tiny-trade review only.

## Validate

```powershell
python -m unittest tests.test_market_session_decisions
```

The current environment may not have `pytest` installed. The pytest-style Telegram exit-price tests can still be exercised by importing and calling their test functions, or by installing pytest in the project venv.
