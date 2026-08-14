# Project State

Last updated: 2026-07-29

## Project Purpose

This workspace is a Python-based options trading scanner and research sandbox. It scans a configured stock watchlist, fetches or falls back to market aggregate data, computes technical indicators across multiple timeframes, scores momentum setups, evaluates entries and risk, ranks options contracts, projects trade outcomes, manages session-aware open trade state, writes scan telemetry, stores candidate snapshots, and provides early replay calibration / expectancy / no-lookahead backtesting utilities for later analysis.

The current v2 intraday options watchlist is defined in `app/config/watchlist.py`. By default the scanner uses the static `WATCHLIST`; when `DYNAMIC_WATCHLIST_ENABLED=true`, `get_scanner_watchlist()` keeps the core symbols first, then merges Polygon snapshot movers and the static fallback list up to `DYNAMIC_WATCHLIST_SIZE`.

- QQQ
- SPY
- NVDA
- AAPL
- MSFT
- AMZN
- META
- GOOGL
- TSLA
- AMD
- AVGO
- MU
- PLTR
- NFLX
- CRWD
- SMCI
- SPCX
- SMH
- ARM
- TSM
- AMAT
- MRVL
- ORCL
- PANW
- JPM
- XOM

SOXL, LRCX and INTC were removed on 2026-08-01 and replaced with GOOGL, JPM and XOM. The list had 13 of 26 names in SEMIS, so half the universe was one bet expressed thirteen ways. SOXL is 3x SMH and both were held; LRCX shares the semicap-equipment cycle with AMAT; INTC is low-beta and detached from the AI-semis complex. The replacements add two genuinely uncorrelated drivers — JPM is rate-driven, XOM is oil-driven — and GOOGL closes the MEGA_TECH gap. JPM and XOM run cooler than the names they replaced, so if they rarely clear the ATR% and momentum thresholds they are the first candidates to revisit.

Non-trade market reference symbols are also defined in `app/config/watchlist.py`:

- SMH
- SOXX
- XLK
- XLF, the FINANCIALS sector reference for JPM
- XLE, the ENERGY sector reference for XOM
- VIX, fetched as VIXY because the current Polygon plan does not include index aggregate entitlement for `I:VIX`

## Current Entrypoint

Run path from the README:

```powershell
python -m app.main
```

The older direct command `python app/main.py` is not the recommended path because package imports such as `from app...` require running from the workspace as a module.

The main scanner function is `run_scanner()` in `app/main.py`.

At a high level, each scan does this per symbol:

1. Fetches 5-minute aggregate candles through Polygon logic.
2. Resamples 5-minute candles into 15-minute and 1-hour candles.
3. Computes indicators for each timeframe.
4. Scores each timeframe through the momentum strategy.
5. Detects entry setups when no active trade exists.
6. Calculates risk, stop loss, take profit, and risk/reward.
7. Applies market-session action gating: premarket candidates are watch-only, 9:30-9:45 ET waits for opening-range confirmation, and paper entry is only allowed after 9:45 ET when all gates pass.
8. Checks option quote freshness, bid/ask spread, volume, open interest, quality score, expiration bucket, affordability/capital profile, and event risk.
9. Opens, updates, or closes trade state.
10. Builds a multi-timeframe final signal.
11. Recommends/ranks an option contract and blocks stale/delayed or low-quality option quotes from execution-ready status.
12. Projects target, stop, probability, expected option gain, and grade.
13. Replays the projection over recent candles when possible.
14. Adds explicit market regime, sector-reference strength, watchlist breadth, relative strength rankings versus QQQ and SPY, relative volume, ATR %, premarket gap %, top 5 strongest/weakest leaderboards, and top 3 bullish/bearish candidate tags.
15. Stores normalized candidate snapshots and signal lifecycle observations for every scanner row so skipped, blocked, stale-quote, review-only, and opened setups can be reviewed later.
16. Optionally calls OpenAI for a trade summary on strong setups.
17. Saves actionable scan telemetry and paper-trade outcome/context telemetry to CSV.
18. Exports a scanner report to `scanner_output.xlsx`; stale/no-data/error symbols now still write dashboard-readable rows.

## Main Components

### Scanner Orchestration

- `app/main.py` coordinates the scanner loop, table output, Excel export, telemetry save, OpenAI summary calls, trade state updates, and projection replay.
- `app/exit/exit_engine.py` is the live source of truth for exit decisions and trade-action outputs. `app/trade_manager.py` is legacy historical reference only.
- `app/background/background_queue.py` owns the single daemon worker used for best-effort scanner persistence tasks. It queues callable work through `run_background()` and drains pending tasks at process exit.
- `app/background/background_queue.py::get_background_metrics()` exposes queued/completed/failed jobs, pending jobs, queue depth, average job time, longest job time, and longest job name for Engine Health.
- `app/ui/dashboard_state.py` builds the single `dashboard_state.json` object used by the Streamlit trading workstation.

### Historical Scanner Regression

- `app/regression/` is a read-only Historical Scanner Regression (HSR) subsystem, distinct from the existing replay and historical backtesting tools. Its purpose is trade-level strategy comparison: “would this strategy version have produced more or less $R$ on an archived day?”
- `REGRESSION_SNAPSHOT_ENABLED=false` is the default. When enabled, `_persist_scan_outputs()` queues a dedicated best-effort, normal-priority per-symbol `(scan_id, symbol)` hybrid snapshot task only after live scanner, alert, and state work completes. Neon `scanner_snapshot` stores separate computed `decision_payload` and bounded `market_payload` JSONB facts; the latter holds 200 5-minute, 80 15-minute, and 40 1-hour OHLCV bars so indicator changes can be recomputed without storing Polygon HTTP responses. `data/daily/YYYY-MM-DD/scanner_snapshots/` remains an immutable local cache/developer fallback.
- Regression persistence is scheduled as its own normal-priority fire-and-forget runtime task only when the scan has zero symbol failures. It never delays the next scan. The recorder hashes each per-symbol decision payload and drops unchanged state; `data/live/regression_snapshot_metrics.json` tracks enabled state, queued/completed/dropped/failure totals, and average persist time for Developer diagnostics.
- `RegressionContext` names the trading day, local fallback archive, durable baseline, results folder, strategy versions, and `readonly=True`. The regression runtime does not call market-data providers, Telegram, OpenAI, runtime/background jobs, dashboard code, or paper-trade automation; it reads Neon snapshots first and falls back to local cache only when needed. When `DB_WRITE_ENABLED=true`, it may append isolated `regression_run` and `regression_result` rows but never mutates production trades, daily reports, baselines, validation, or learning facts.
- `tools/regression_runner.py --date YYYY-MM-DD` reconstructs archived trades through a pure evaluator, compares them against the frozen baseline, and writes `regression_summary.json`, `regression_trades.csv`, and `regression_report.html` under `data/regression/YYYY-MM-DD/`.
- `finalize_daily_report()` freezes a baseline from reconstructed archive trades when available and otherwise from completed paper-trade events. The durable baseline is stored in Neon `scanner_regression_baseline` and cached under `data/regression/YYYY-MM-DD/baseline/`. HSR compares new strategy output to that fixed day baseline, never to yesterday’s code.
- Migration `013_scanner_regression.sql` creates the original durable snapshot/baseline tables. Migration `014_regression_symbol_snapshots.sql` upgrades snapshots to per-symbol market/decision payloads and adds versioned `regression_run` / `regression_result` history. Migration `015_regression_snapshot_deduplication.sql` adds payload-hash deduplication. All are applied through `DATABASE_DIRECT_URL`; scans never execute DDL.
- HSR reports only outcome-oriented metrics: trade count, wins/losses, win rate, total/average $R$, profit factor, added/removed/changed trades, net $R$ impact, and verdict. It does not surface scanner diagnostics as primary comparison metrics.
- Operator workflow: set `REGRESSION_SNAPSHOT_ENABLED=true` before the target session and restart the scanner/runtime; after close run `tools/daily_validation_report.py --date YYYY-MM-DD --finalize` to freeze the baseline; then run `tools/regression_runner.py --date YYYY-MM-DD --strategy-version <label>`. Keep the recorder disabled for sessions that do not require historical regression capture.
- The Streamlit **Regression** page mirrors the terminal workflow: choose a day and strategy label, verify archive and baseline status, then run HSR on demand. When an archive exists without a baseline, its **Freeze Baseline** action invokes the same immutable freeze path as terminal finalization and displays the version/frozen timestamp afterward. It shows baseline/current trade and win-rate deltas, net $R$, changed-trade count, verdict, and a durable versioned history table. It is available without a current scanner-output file and can only append isolated regression history.

### Holding Profiles And Session Lifecycle

- `app/state/holding_policy.py` defines the two supported profiles: `INTRADAY` and `MULTIDAY`. The `TradeHoldingPolicy` owns force-EOD-close, next-session restore, Telegram continuation, and candidate archival behavior so these decisions are not duplicated across the scanner, scheduler, dashboard, and alert code.
- `derive_holding_profile()` runs before the shared decision adapter returns a `TradeDecision`. It prefers an explicit candidate value and otherwise derives a multi-day profile from expected-hold intent or an eligible 14-30+ DTE, high-quality setup; it uses `Setup %` and falls back to `15m Score` when that normalized field is absent. All other candidates default to `INTRADAY`.
- `app/state/trade_session_lifecycle.py` restores open multi-day paper positions, refreshes `days_held` and `overnight_count`, marks an overnight transition, and archives stale prior-session suggestions. A promoted multi-day suggestion remains available; non-promoted or intraday candidates are set to `ARCHIVED`.
- Paper trades persist `trade_state`, `holding_profile`, `opened_at`, `closed_at`, `days_held`, `overnight_count`, `forced_eod_exit`, `session_id_open`, `session_id_current`, and `session_id_close`. `status` remains the backward-compatible open/closed field.
- `paper_trade_state.json` is the sole managed-trade state. Auto-paper is the only new-entry owner; scanner management reads, updates, and closes that same paper record. **The scanner is the sole owner of the paper trade lifecycle.** Scanner finalization invokes auto-paper entries, the paper position lifecycle sweep, and the suggestion lifecycle using persisted `auto_paper_settings.json` controls. The dashboard is read-only with respect to trade state: it no longer runs entries, exits, or suggestion sync. The deprecated direct scanner-state opening path no longer creates `SCANNER_TRACKED` trades. When an existing open legacy `trade_state.json` record is first encountered, it is promoted once into paper state with `entry_source=LEGACY_SCANNER_STATE_MIGRATION`, an `OPEN` paper event, and removal of the duplicate legacy record. It does not emit a retroactive entry alert.
- The `Auto Exit (During Market Hours)` toggle and the `Auto Profit Exit R` control have been **removed**. All market exit decisions (stop, target, EMA, VWAP, MACD, failed breakout, time, multiday profit protection) belong to `app/exit/exit_engine.py::evaluate_exit()` and cannot be disabled from the UI. `Force Close Intraday at Market Close` remains a separate, final holding-policy consumer: it closes only the trade's current `INTRADAY` profile and leaves `MULTIDAY` positions open. A manual profile change is therefore honored at EOD without mutating the frozen entry thesis.
- Disabling `Force Close Intraday at Market Close` never changes the frozen profile. An open intraday trade carried overnight is restored for management as `INTRADAY`, marked `overnight_intraday_carry`, and emits an operational warning. It is not promoted, does not send `POSITION CONTINUES`, and remains excluded from multi-day restoration semantics.
- `Restore Multi-day Positions Next Session` defaults to on. When disabled, it skips multi-day restoration and continuation-alert setup but still archives stale candidates at session start.
- A holding profile is frozen at entry. `override_paper_trade_holding_profile()` accepts only `MANUAL_OVERRIDE` and future `BROKER_SYNC` sources; scanner, exit engine, Telegram, and ranking paths cannot alter it. This preserves the entry thesis while allowing an explicit operator decision to be honored by the EOD policy.
- Paper trades also support `PAUSED` and `RESUMED` operational states for provider outages, market halts, and controlled restarts. Paused trades remain active for duplicate-entry protection but are excluded from automated management until explicitly resumed.
- The Trading page displays a live positions table with holding profile alongside decision feed, active-risk, Telegram delivery, market-pulse, and event-timeline panels. Multi-day positions send a single next-session `POSITION CONTINUES` Telegram alert rather than a second `NEW TRADE` alert.
- `app/db/migrations/012_holding_profiles.sql` adds reporting columns to `paper_trades`, `candidate_evidence`, and `event_stream`. Apply it manually through `DATABASE_DIRECT_URL`; scanner execution never applies DDL.

### Trading Workstation UI

- The Streamlit dashboard uses sidebar navigation: `Trading`, `Validation`, `Replay`, `Regression`, `Reports`, and `Developer`.
- The default `Trading` page is a live operations cockpit: canonical paper positions, ranked opportunities, active risk conditions, compact Telegram/market status, and an Activity Feed. The Activity Feed merges scanner decisions, auto-paper execution results, trade timeline events, and Telegram dispatches; it retains the trading day, supports category/symbol/search filters, pagination, and optional per-symbol grouping. It deliberately excludes performance analysis, replay rationale, research learning, and developer health diagnostics, which remain owned by Reports, Replay/Validation, Learning, and Developer.
- The sidebar keeps only trader-facing controls visible by default: auto refresh, paper automation, Operations (validation/replay generation), and navigation. Downloads are under `Tools: Downloads`, raw artifacts are under `Advanced files`, and runtime key status is not rendered outside Developer.
- `Post Market: Generate Everything` always finalizes the selected trading day, runs daily validation report generation and offline replay generation, attempts to freeze the regression baseline, then refreshes the dashboard. A missing archive/evidence leaves the baseline unfrozen without changing historical facts.
- `Trading`, `Replay`, and `Developer` pages share the same metadata-card pattern: scan id/data version, scan timing, refresh age, symbol count, and freshness status.
- Scanner rows persist `Scan ID` and `Data Version`; `dashboard_state.json` also carries `scan_id` and `data_version` for end-to-end traceability.
- `dashboard_state.json` is written under `data/live/dashboard_state.json` and `data/daily/YYYY-MM-DD/dashboard_state.json` whenever scanner outputs are written.
- The `Developer` page keeps legacy diagnostic panels available without putting them above the fold during live trading.

### Dashboard Page Responsibilities

| Page | Question answered | Contents | V1/V2 visibility |
| --- | --- | --- | --- |
| Trading | What is the engine trading right now? | Live positions, decisions, ranked opportunities, risk monitor, Telegram delivery, market pulse, event timeline | V1 only; V2 has no live execution control. |
| Validation | Did execution behave well today? | Trade Doctor, trade efficiency, Candidate Outcomes, Decision Analysis, V1/V2 comparison, Trend Outcome Attribution, execution failures, and V2 Learning Summary | Post-trade V2 evidence only. |
| Replay | What would saved scanner state have done? | Offline replay coverage, blockers, and summaries | V1-oriented today; V1/V2 replay comparison is pending Candidate Evidence merge. |
| Regression | Would current strategy code have improved an archived day? | Archive/baseline status, on-demand HSR result, and versioned run history | Read-only; never mutates daily historical truth. |
| Reports | Is performance improving across days? | Report status, historical Trade Efficiency, and Execution Learning Trends | Aggregated research only. |
| Developer | Is the system healthy? | Runtime, scheduler, cache, and engineering diagnostics | Operational diagnostics only. |

The Daily Validation Report is the post-market artifact for the Validation page. It includes V2 shadow counts, completed comparisons, Trend Outcome Attribution, strong-trend execution failures, and the V2 Learning Summary.

### Deprecated UI Migration

`_render_command_center`, `_render_current_opportunities`, `_render_why_no_trade`, `_render_missed_opportunities`, `_render_trading_page`, and `_render_trading_page_from_state` are marked `DEPRECATED` in `app/dashboard.py`. They are retained only until the state-driven Trading page has a live paper-session validation and the Candidate Evidence merge restores richer missed-winner analysis in Validation.

### Scanner Performance And Background Persistence

- `SCANNER_MAX_WORKERS` controls bounded `ThreadPoolExecutor` market-data prefetch in `app/main.py`. The default is `5`; set it to `1` to force serial fetches for debugging.
- The foreground scanner path still performs decision-critical work sequentially: strategy scoring, entry/risk evaluation, option selection, paper state mutation, Telegram send checks, candidate persistence, relative-strength rankings, market opportunity audit, engine-health calculation, and table rendering.
- `app/runtime/runtime_performance.py` records coarse runtime timings to `data/runtime_performance.csv` and daily `runtime_performance.csv`. Initial instrumentation covers scanner runtime, Telegram send calls, dashboard page render time, validation report generation, and replay generation.
- `RuntimeScheduler.set_scanner_running()` updates `scanner_running` in `data/live/runtime_state.json`; `run_scanner()` wraps the scanner implementation in a `try/finally` so the flag resets even if a scan raises.
- Production Runtime v2 Phase 1 is split into `app/runtime/runtime_priority.py`, `runtime_jobs.py`, `runtime_metrics.py`, and `runtime_scheduler.py`. `RuntimeScheduler` supports priority-backed job submission, `submit_critical` / `submit_high` / `submit_normal` / `submit_low`, stale cancelable job cancellation by scan id, queue state output to `data/live/runtime_state.json`, and queue runtime metrics in `data/runtime_metrics.csv`.
- Production Runtime v2 Phase 2 routes `record_scanner_run_start` and `_persist_scan_outputs` through high-priority `RuntimeJob`s. `run_scanner()` calls `cancel_old_jobs(scan_id)` after registering the new scan, allowing stale queued cancelable runtime work to be skipped without touching critical foreground decisions.
- `_persist_scan_outputs()` writes enriched live/daily `dashboard_state.json` before heavier persistence stages. `app/ui/dashboard_state.py` now accepts optional `scanner_health` and `telegram_summary` metadata so the Trading page can increasingly render from prebuilt JSON instead of recomputing from scanner CSVs.
- `app/ui/cache/validation_state_builder.py` builds cached Validation-page state. A normal-priority runtime job writes live/daily `validation_state.json` after scanner persistence, containing scanner KPIs, paper-trade KPIs, trend-capture metrics, exit verdict distribution, setup/regime/exit breakdowns, `trade_efficiency`, structured Trade Doctor diagnosis, and Strategy Confidence. On success it refreshes `today_performance` in the live and daily dashboard state.
- `app/ui/cache/replay_state_builder.py` builds cached Replay-page state. A low-priority runtime job writes live/daily `replay_state.json` after scanner persistence, summarizing existing offline replay outputs, coverage, biggest blockers, top misses, stale/missing status, and replay errors without regenerating replay from the dashboard refresh path.
- Manual dashboard replay generation refreshes `replay_state.json` immediately after writing replay CSVs, keeping the cached Replay page aligned with operator-triggered replay work.
- `app/ui/cache/report_state_builder.py` builds cached Reports-page state. A low-priority runtime job writes live/daily `report_state.json` after scanner persistence, summarizing existing daily validation report artifacts, file metadata, and stale/missing status. Its `historical_trade_efficiency` cache aggregates up to 20 existing daily validation-state JSON files into daily, weekly, monthly, setup, regime, exit, and weekday trends. Manual dashboard report generation refreshes this cache immediately after report creation.
- Dashboard Validation, Replay, and Reports pages now load `validation_state.json`, `replay_state.json`, and `report_state.json` first. The legacy CSV/HTML-heavy render paths remain as fallback when cache state is absent.
- Validation, Replay, and Reports pages now short-circuit before scanner CSV loading when their live cache state exists, reducing refresh I/O for non-trading pages. Cache-missing pages still fall back to the legacy full-data path.
- Dashboard Trading page now has a guarded `dashboard_state.json` fast path. When paper automation, auto exits, and EOD close automation are inactive, Trading renders directly from cached JSON and skips scanner CSV loading; when automation is active or cache is missing, the legacy full-data path remains active.
- The Performance Journey separates concerns without adding work to the critical path: Trading answers "How am I doing today?"; Validation explains trade-by-trade efficiency and exit quality; Reports shows whether performance is improving across days. These pages only render cached JSON and do not read analytics CSVs, replay trades, or query the database on refresh.
- Telegram dispatch occurs before dashboard persistence and analytics cache jobs. Builder failures are caught by `RuntimeScheduler`, logged as failed jobs, and do not block Telegram delivery or scanner completion.
- Developer diagnostics now include a `Runtime Performance` expander. It displays runtime queue counts from `runtime_state.json`, recent coarse timings from `runtime_performance.csv`, and recent priority job timings from `runtime_metrics.csv`.
- `runtime_performance_summary.json` is written under `data/live/` whenever runtime performance rows are appended. Developer diagnostics use it for slowest-stage and slowest-job summaries while still showing recent raw timing rows.
- Production hardening modules now exist under `app/runtime/`: `scan_generation.py`, `generation_validator.py`, `runtime_watchdog.py`, `shutdown_manager.py`, and `startup_manager.py`. Live JSON state files include top-level generation metadata and runtime-owned writes use atomic temp-file promotion. `runtime_health.json` reports queue overflow, scanner timeout, dashboard/cache staleness, worker health, and Telegram latency warnings for the Developer Runtime Performance panel.
- Developer diagnostics are explicitly lazy-loaded with per-section `Load ...` toggles. Collapsed Developer panels do not execute market coverage, telemetry, auto-paper logs, entry diagnostics, or validation health work until requested.
- Dashboard page-specific imports are lazy where practical. `market_coverage` imports only when its Developer panel is loaded, `trend_capture` imports only for the uncached Trade Efficiency fallback, and `dashboard_state` imports only when the live JSON fallback must rebuild state from a dataframe.
- Dashboard page entry points now live under `app/ui/pages/`: Trading, Validation, Replay, Regression, Reports, and Developer. The page modules own concrete render bodies and import shared helpers from `dashboard.py` while the helper migration continues.
- Live JSON state loading uses page-specific cache profiles: Trading `dashboard_state.json` uses a 5-second TTL, Validation uses 60 seconds, Developer runtime state uses 120 seconds, and Replay/Reports cached states invalidate from file modified time.
- `app/config/performance.py` defines the current Performance Mode defaults and env overrides. Trading lazy imports/background cache generation/dashboard-state-only behavior is enabled by default, with TTL overrides through `PERFORMANCE_TRADING_CACHE_TTL`, `PERFORMANCE_VALIDATION_CACHE_TTL`, and `PERFORMANCE_DEVELOPER_CACHE_TTL`.
- `app/runtime/telegram_dispatcher.py` is the Telegram dispatcher facade. Default `TELEGRAM_DISPATCH_MODE=DIRECT` preserves synchronous send behavior; optional `QUEUED` submits a non-cancelable critical `RuntimeJob`. In queued mode, alert sent-state persistence is attached as an after-success callback, so alerts are not marked sent before the Telegram send succeeds. The dispatcher writes replayable queued-message records to `data/live/telegram_dispatch_queue.jsonl` and ATTEMPT/SENT/FAILED audit rows to `data/live/telegram_dispatch_audit.jsonl`. Audit rows retain scan, symbol, direction, candidate key, decision, message type, parse mode, message length, attempt, latency, and the structured Telegram API response. A failed HTTP response therefore preserves Telegram's actionable `description`, including the reason for a `400` rejection. `recover_pending_telegram_dispatches()` can resubmit queued records with no successful audit event.
- `run_scanner()` now queues `summarize_telemetry()` as a low-priority runtime job after console table output, trimming noncritical foreground scanner tail work.
- Market opportunity audit, option liquidity audit, and candidate funnel file writes moved into `_persist_scan_outputs()` under `RuntimeScheduler`. Foreground scanner execution still computes rows/rankings/health/Telegram summary and prints the funnel, but noncritical audit file I/O is deferred.
- Scanner finalization is now a high-priority non-cancelable `RuntimeJob` named `finalize_scan_outputs`. Foreground scanner execution queues finalization after raw rows are collected and returns. Finalization performs operator table rendering, candidate persistence/ranking, health calculation, Telegram dispatch, funnel calculation, persistence, and cache job scheduling.
- Paper automation orchestration lives in `app/runtime/paper_automation.py`, with helper logic in `app/runtime/paper_automation_support.py` and non-per-symbol lifecycle work in `app/runtime/paper_position_lifecycle.py`. Only the scanner calls these; the dashboard does not.
- After `df_results` and the health payload are built, `run_scanner()` queues `_persist_scan_outputs()` through `RuntimeScheduler` as a high-priority `RuntimeJob`. That job writes dashboard state, engine health history, candidate snapshots, signal lifecycle rows, scanner output files, and scanner stage profiles. It then queues a separate normal-priority `persist_scan_artifacts_db` job, so DB work cannot delay file-backed persistence.
- Telegram alert audit DB writes and paper-trade DB upserts also use `run_background()`, so slow or failed Neon writes should not block Telegram sending, paper state JSON/CSV updates, or scanner output.
- `persist_scan_artifacts_db` promotes candidate snapshots, structured rule evaluations, and existing gate-decision summaries as best-effort database batches. The completed RuleEvaluation framework provides native emitters for Entry, Risk, Option Liquidity, Affordability, Telegram, Paper Automation, and Review. Every record has scan/symbol/setup identity, rule name/group, actual/required values, pass/fail, blocked state, priority, and `evaluation_phase`. `ENTRY` is the default phase; scanner rows emit `ACTIVE` for ongoing trade management, `EXIT` for live exit signals, and `REPLAY` for projection replay outcomes. Migration `003_rule_evaluation_phase.sql` adds the database column. `aggregate_rule_evaluations()` merges and deduplicates native outputs by scan, symbol, setup, phase, group, and name before persistence.
- `app/storage/signal_lifecycle_store.py::record_signal_lifecycle_events_for_scan()` batches lifecycle event and transition CSV appends per completed scan.
- `record_signal_lifecycle_events_for_scan()` writes its lifecycle CSV/state artifacts before attempting optional event-stream submission. Event-stream scheduling failures are caught and logged as `[LIFECYCLE EVENT STREAM WARNING]`; they cannot interrupt suggested-trade synchronization or scanner completion.
- `app/analytics/scanner_profiler.py::StageTimer.record()` lets foreground timings be carried into the background profile writer, so `scanner_stage_profile.csv` includes both foreground stages and deferred persistence stages.
- Deferred persistence stages are named with category/detail labels such as `Database / Engine Health`, `Database / Gate Decisions`, `Database / Candidate Snapshot`, `Database / Signal Lifecycle`, `Export / Scanner Output`, and `Database / Scanner Run` so the dashboard can show which persistence area is slow.
- Engine Health now persists Polygon requests, cache hits, cache misses, cache hit %, average API time, average cache read time, background pending/completed/failed jobs, queue depth, average job time, longest job time, and longest job name.
- `tests/test_background_queue.py` verifies that the background queue continues processing later tasks after a task raises.

### Market Data

- `app/indicators/technical_indicators.py` owns `get_polygon_data()` and `compute_indicators()`.
- `app/utils/polygon_client.py` owns low-level Polygon HTTP aggregate calls, retry/backoff handling, a token-bucket rate limiter, rate-limit header tracking, request/cache/timing metrics, and the short TTL aggregate cache used by `get_aggs_cached()`.
- `app/mock/load_mock_aggs.py` loads fallback aggregate data from `app/mock/*.json`.
- Runtime market data mode is controlled by `USE_MOCK_MARKET_DATA` in environment/settings. Current intended operating mode is live Polygon/Massive aggregate data with the user's full real-time subscription.
- Stock aggregate freshness is session-aware in `app/main.py`. Polygon aggregate timestamps represent candle bucket starts, so freshness subtracts the inferred aggregate interval before comparing against `MAX_STOCK_DATA_DELAY_MINUTES`. This prevents a current 5-minute candle from being mislabeled `STALE_STOCK_DATA` solely because its timestamp is the candle start.

### Market Session Entry Gating

- Premarket, 4:00-9:30 ET: strong call/put candidates can surface as `PREMARKET_WATCH`, but `Realtime Ready` remains false and auto paper entry is not allowed.
- Opening range, 9:30-9:45 ET: candidates use `OPENING_RANGE_CONFIRMATION`; the scanner waits for regular-market confirmation before entry.
- Regular session after 9:45 ET: `ENTER` or `ENTER_PAPER` is allowed only if stock freshness, risk, timing, option quality, option affordability, quote freshness, event, regime, and dashboard auto-entry gates pass.
- Price geometry is a hard gate: CALL candidates require `stop < entry < target`; PUT candidates require `target < entry < stop`. Invalid geometry is blocked as `INVALID_PRICE_GEOMETRY` before normal suggestions, auto-paper, Telegram/real entry gates, or paper setup display.
- The risk manager also performs a final intended-direction geometry guard, so bearish/PUT-intended setups cannot return bullish stop/target structures even if their entry type falls through a long-side branch.
- Regular session auto paper entries remain constrained to the dashboard's 9:45-15:30 ET entry window.

### Shared Decision Engine And Alert Consumers

- `app/decision/decision_engine.py` is the shared decision adapter for scanner-style candidate rows. `evaluate_candidate()` returns a `TradeDecision` with `action`, `setup_score`, `rr`, `option_quality`, `confidence`, `holding_profile`, `reasons`, and `block_reasons`.
- Telegram has a fixed six-message subscriber contract: `NEW TRADE`, `TRADE UPDATE`, `PARTIAL PROFIT`, `POSITION CONTINUES`, `TRADE CLOSED`, and `TRADE CANCELLED`. Every subscriber alert includes its ET date/time. `ACTIVE_TRADE` and `REVIEW_TV_CHART` rows are suppressed; a review candidate that expires without a confirmed entry may send the cancellation closure instead.
- Open V1 and paper trades publish `TRADE UPDATE` only for a $0.5R$ move, worsening trend health, material confidence change, stop movement, or partial profit. An open multi-day paper trade publishes one next-session `POSITION CONTINUES` alert. `TRADE CLOSED` uses standardized subscriber categories: `🟥 Stop Loss`, `🟩 Target Hit`, `🟨 EMA Exit`, `🟦 VWAP Exit`, `🟪 Time Exit`, `⚠️ Failed Breakout`, and `📈 Manual Exit`; near-close exits and end-of-day terminations normalize to `🟪 Time Exit`. Unclassified engine exits retain concise generic categories. Closed alerts report holding time and include execution / Trend Capture only after close. Loss alerts state that risk was managed according to plan. Scanner action codes and price-provenance fields remain dispatch-audit details rather than subscriber content.
- Paper-trade subscriber lifecycle identity is the immutable `trade_id`, with legacy state-key fallback only for older records. `TRADE UPDATE`, `PARTIAL PROFIT`, `POSITION CONTINUES`, and `TRADE CLOSED` require a delivered `NEW TRADE` / `TRADE_OPEN` record for that same identity; otherwise they are suppressed as `SUBSCRIBER_NEW_TRADE_NOT_SENT`. A `TRADE CANCELLED` message remains reserved for an unentered suggestion.
- A suggestion cancellation is subscriber-relative: `TRADE CANCELLED` is sent only when the same suggestion previously delivered a subscriber-facing review alert. A scanner-only or never-alerted suggestion expires silently, preventing orphan cancellation messages with no preceding alert context.
- All option-bearing subscriber messages preserve their existing trade/result layout and include the original selected `Contract`, `Expiry`, and `Contract Cost`; `NEW TRADE` also retains its option premium. Updates, continuations, partial exits, and closes recover those details from the persisted paper-trade scanner context, not from a new quote. A close message omits holding time when its event timestamp precedes the recorded open timestamp.
- Validation rank calibration joins `EntryOpened` and `ExitTriggered` timeline snapshots by immutable `trade_id`, then groups completed trades into rank buckets (`1`, `2-3`, `4-5`, `6+`) with win rate, final $R$, Trend Capture, and MFE. It is observational and does not alter ranking or trade selection.
- Rule analytics classify `Telegram`, `Paper`, `Review`, lifecycle, and replay evaluations as operational. Decision Waterfalls expose ordered trading `primary_blocker`, `secondary_blocker`, and `tertiary_blocker` values separately from `operational_blockers`. Rule-outcome attribution reports matched-cohort final-$R$ association only; it is explicitly not causal expected-$R$ attribution.
- The dashboard **Tools: Downloads** panel provides a Daily Review Export for the selected day. It packages standardized analytics, candidate, decision, rule, trade, and exit-quality CSVs into `review_YYYY-MM-DD.zip`, with a `manifest.json` that records empty or unavailable source artifacts. Daily review uses one export; weekly and monthly review remain database-backed five- and 20-trading-day analyses so single sessions do not drive strategy changes.
- Candidate Evidence is the daily local research fact for stable candidate identity, evidence evolution, decisions, and attached outcomes. Every finalized scan writes `candidate_evidence.csv` plus `candidate_evidence_status.json`; the latter records source/evidence row counts and whether optional Postgres promotion was `DISABLED`, `PERSISTED`, or `FAILED`. Candidate Evidence merges durable database snapshots, local daily snapshots, and the current scan by scan identity. It preserves the first actionable `ENTER` / `ENTER_PAPER` recommendation as `decision`, keeps the newest observed state as `latest_decision`, and records decision-state transitions with scan/time/reason/blocker metadata in `decision_history`; a later `WAIT` cannot erase an earlier scanner recommendation. The canonical scanner layer uses `ENTRY_RECOMMENDED`, while legacy `Action Status` stays available for compatibility. Execution, trade, and notification remain independent: `Execution Eligibility/Outcome/Reason` records auto-paper policy; `Trade Status` records `NOT_CREATED` or `OPEN`; `Telegram Status/Reason` records `NO_LIFECYCLE_EVENT`, `SENT`, or `NOT_SENT`. Migrations `018_candidate_evidence_decision_history.sql`, `019_execution_eligibility.sql`, and `020_trade_notification_layers.sql` expose these fields in Postgres. Downstream outcomes, waterfalls, rule evaluations, exit-quality metrics, and Learning summaries remain linked research artifacts rather than duplicate decision engines.
- Auto-paper decision logs distinguish scanner recommendation from execution outcome: `scanner_recommendation` records the scanner recommendation, `execution_eligibility` records `PENDING`, `ELIGIBLE`, `INELIGIBLE`, or `NOT_EXECUTED`, and `execution_outcome` records `OPENED`, `BLOCKED`, or `SKIPPED` with `execution_reason`. `action_status` remains the backward-compatible scanner field, `blocked_by` records the actual auto-paper gate that prevented an open, `scanner_blocked_by` preserves the raw scanner field, and `action_reason` preserves the scanner explanation. An action code such as `ENTER_PAPER` is never used as the execution blocker for a new blocked record.
- `app/analytics/recommendation_outcomes.py` records one immutable `recommendation_fact` for every `ENTRY_RECOMMENDED` scanner decision. Each later scan evaluates due facts at 5 and 10 business-session horizons using the current underlying price and records directional underlying return. Option return is measured only when the original option ticker still has a refreshed mid-price, preventing substitutions with a different contract. `recommendation_outcome_summary.csv` groups `1-3`, `4-10`, and `11+` ranks by execution rate, directional win rate, directional return, and option-return coverage. Migration `021_recommendation_outcomes.sql` creates the durable facts and horizon outcomes tables.
- V1 multiday exit management adds configurable profit protection without loosening entry gates: a `+2R` peak locks `+1R`; after a `+3R` peak, a `1R` giveback selects `PROFIT_PROTECTION` exit. The live paper state retains the lock, giveback, current price, R progress, trend health, and exit confidence and upserts each management update to Postgres. Delayed stock aggregates remain automated `STALE_STOCK_DATA` rejections when they exceed the configured freshness limit; no operator chart or quote confirmation path exists. `high_score_execution_audit.csv` captures each strong recommendation with rank and terminal execution blocker for daily review.
- `ENTER_PAPER` means the scanner recommends an entry; it is not an `OPENED` paper trade. Auto-paper then applies the entry window, top-candidate, entry-gate, realtime, bid/ask, event/regime, direction, duplicate, cooldown, capacity, and daily-limit checks. A candidate qualifies as top-ranked through its `BULLISH_TOP_*` / `BEARISH_TOP_*` tag or a `Candidate Rank` at or below `AUTO_PAPER_MAX_CANDIDATE_RANK` (default `3`), preventing a missing display tag from rejecting a top-three candidate. Every result is recorded as `OPENED`, `BLOCKED`, or `SKIPPED` in `auto_paper_decisions.csv`; the gate `reason` is the authoritative explanation for a recommendation that does not open. Outside the entry window, one system-level skip is recorded per automation invocation instead of one row per symbol.
- The V1 event sequence is captured for every evaluated ticker: scanner decision and gate context, one trace event per rule with stage/actual/required/pass-fail evidence, then (when eligible) auto-paper execution outcome, paper `OPEN` / lifecycle events, and Telegram delivery. Every scanner-decision event also stores the prior action and state-change flag, decision reason/stage/rule, setup score, RR, option quality, and exact completed 5-minute decision candle time/OHLCV/volume. `activity_trace.csv` is rebuilt during scan finalization from these sources, provides a chronological per-ticker record with scanner/paper/trade/Telegram origin, and is included in Daily Review Exports. The same normalized rows are queued to `activity_trace_event` in Postgres using `event_id` as the idempotency key, so historical trace queries can follow scan, trade, or ticker identity without relying on the local CSV. Migrations `016_activity_trace.sql` and `017_activity_trace_decision_context.sql` have been applied to the configured database; their best-effort runtime job cannot block scanner, paper, or Telegram work when the database is unavailable.
- Telegram does not recompute entry gates, setup, RR, option quality, quote freshness, affordability, event/regime, top-candidate rank, session, conviction, or an alert score. `TELEGRAM_ALERT_POLICY` and legacy Telegram score/threshold settings no longer block an alertable action.
- Duplicate-alert protection remains a delivery safeguard. Transport results must not be interpreted as an alternate trade decision.
- When a paper trade opens, its matching suggestion is immediately marked `PROMOTED_TO_PAPER`, including a suggestion that previously reached `EXPIRED_NOT_ENTERED`. A closed suggestion remains terminal.

### Observational Entry, Ranking, Exit, And Realtime Analytics

- V2 entry location is normalized through `app/analytics/entry_timing_engine.py` into `Entry Timing Score`, grade, and reason. It weights Entry Efficiency 35%, Trend Age 20%, Pullback Number 20%, Bars Since Breakout 10%, EMA extension 10%, and VWAP extension 5%; grades are `EXCELLENT` (>80), `GOOD` (70-80), `AVERAGE` (55-69), and `LATE_ENTRY` (<55).
- `app/analytics/trade_ranker.py` computes `Trade Quality Score` (TQS): Setup 25%, Entry Timing 20%, Trend Health 20%, Option Quality 15%, Relative Strength 10%, and Liquidity 10%.
- `app/decision/entry_optimizer.py` scores the location within an already-valid setup. Pullback number, trend age, and bars since breakout produce an `Entry Priority Adjustment`; extension from EMA/VWAP, relative volume, ADX, and candle strength estimate `Expected Remaining Trend` and a projected `A`/`B`/`C` grade. `trade_ranker.py` adds that adjustment to TQS as `Ranking Score` before assigning `Candidate Rank`. This does not reject a setup or change entry, risk, option, or alert eligibility.
- Candidate snapshots and Candidate Evidence persist Entry Timing score/grade/reason, TQS, Entry Priority Adjustment, Expected Remaining Trend, Projected Entry Grade, Ranking Score, and rank so outcome research can compare these values with winners, Trend Capture %, and TES. Trading exposes only the live ranked opportunity board; Validation exposes timing distributions, late entries, rankings, exit waterfalls, and a Decision Waterfall, and Reports aggregates timing/TQS/rank from daily Validation caches.
- `app/analytics/exit_waterfall.py` converts existing V1 `exit_diagnostics` into the persisted ordered `Exit Waterfall`, `Exit Rule`, and `Exit Stage` fields. The V1 exit engine still selects the live exit; the waterfall is an explanation artifact.
- `app/analytics/decision_waterfall.py` creates a read-only candidate Decision Waterfall from persisted Entry Diagnostics and native RuleEvaluation objects. Its fixed stage order is Momentum, Entry, Risk, Option, Affordability, Realtime, Telegram, Paper, and Decision. Every stage has pass/fail/not-evaluated status, a summary, passed/failed rule lists, and actual/required values. The payload exposes final action/reason plus first blocking stage/rule.
- `dashboard_state.json` and Validation cache V1 decision waterfalls, V1/V2 shadow path comparisons, and a current-session blocking-stage summary. Validation renders an inspectable candidate path, failed-rule values, V1/V2 stage differences, and today’s blocker percentages. Reports aggregates cache data into daily blocker rows and dominant blocking-stage trends.
- Migration `008_decision_waterfall.sql` has been applied to the configured database. It creates the optional `decision_waterfall` table; scheduled artifact persistence inserts one row per evaluated rule with scan/symbol/stage, pass/fail, selected blocker, actual/required values, priority, and summary. The table remains audit-only and cannot affect a scanner decision.
- V2 comparison is intentionally bounded to facts present in the shadow row: directional signal, V2 entry suggestion, V2 reason, and shadow action. It does not fabricate V2 risk, option, or execution gates.
- `entry_exit_v2_shadow_state.json` is the live V2 shadow-position state. A V2 position opens only after V2 independently suggests an entry and its independent risk calculation allows it; it is then updated and closed without changing V1. `v2_learning_dataset.csv` stores completed V2 shadow records only, so `v2_shadow_trades` is not inflated by V1 completions or per-scan observations. V1 completed events remain in `engine_trade_events.csv` for later pairing through `engine_trade_comparisons.csv`.
- V1/V2 completed comparisons now retain TES delta in addition to entry/exit timing, $R$, MFE, and Trend Capture deltas. The existing V2 shadow contract remains unchanged.
- `app/analytics/realtime_health.py` normalizes quote refresh telemetry. `refresh_contract_quote()` attempts one bounded retry by default (`OPTION_QUOTE_REFRESH_RETRIES=1`) when a quote is missing or remains non-live before downstream quote gating. Scanner, candidate snapshot, and lifecycle artifacts retain retry count, latency, refresh time, and final refresh outcome.
- Entry timing, TQS, projected grades, and ranking fields do not change V1 entry eligibility, risk/option gates, Telegram eligibility, or paper/real entry state. Continue to require at least 20 evidence days and 80 completed trades before broader controlled rule-change review.
- V2 exit shadow additionally records `exit_confidence_score`, a five-state health label (`VERY_HEALTHY`, `HEALTHY`, `WEAKENING`, `AT_RISK`, `FAILED`), soft confirmation set/count/streak, and Grace Zone status. Hard stop/target stay immediate; soft deterioration may enter `MONITOR` until confirmations persist. V1 promotes only the Grace Zone's first EMA-only-break case: when the trade is profitable, confidence finds healthy remaining trend evidence, and no other soft exit is present, V1 holds for one candle and persists `v1_ema_grace_pending`. A continuing EMA break exits on the next evaluation; a recovering bar clears the pending state. Hard stops, targets, VWAP loss, MACD reversal, and multi-factor deterioration bypass the grace zone.
- `app/analytics/learning_engine.py` writes `daily_engine_summary.json` during deferred persistence. It aggregates V2 learning, V1/V2 completed comparisons, exit-quality evidence, and V2 shadow blocker observations into a daily research summary without affecting decisions.
- The `Learning` dashboard route reads the materialized live summary and shows V2 comparison, Exit Confidence, blocker, and existing one-/two-bar post-exit continuation metrics without recalculating analytics on page refresh. Migration `009_learning_engine.sql` has been applied and creates the optional `daily_engine_summary`, `v2_learning_metrics`, `trade_comparison`, `rule_performance`, and `exit_quality_metrics` warehouse tables. File-backed daily summary JSON remains the resilient source when DB writes are inactive.
- Once the Learning Engine writes its daily/live JSON, `LearningEngineRepository` promotes summary metrics, blocker counts, exit-quality counts, and completed V1/V2 comparison rows to those tables through best-effort writes. A warehouse failure cannot interrupt scanner completion or the file-backed Learning page.
- Learning feedback now includes rolling last-50 quote refresh success, TQS outcome calibration, outcome-derived Rule ROI, and a feature promotion tracker. Feature status remains `CONTINUE_SHADOW` until the existing 20-day/80-completed-trade evidence threshold is met; no tracker field changes V1 behavior.
- The versioned `feature_registry` and `feature_statistics` tables from migration `010_feature_registry.sql` are populated best-effort from Learning summaries. Promotion requires 100 completed samples, 95% confidence, and positive measured lift; unknown lift remains `SHADOW`, and no status automatically changes V1.
- `app/analytics/market_regime.py` and `app/analytics/trade_lifecycle.py` provide formal observational adapters over existing regime/breadth and persisted trade/V2 shadow facts. Their macro and micro-state distributions feed Learning; they never change V1 entry, hold, or exit behavior.
- Learning reads the warehouse summary first only when optional DB writes are active and falls back to the live file-backed summary. Promotion review can mark a feature for controlled validation but cannot switch V1 automatically.
- `tools/reconcile_learning_memory.py` provides a DB-memory reconciliation/backfill workflow. With `--backfill`, it rebuilds daily Candidate Evidence and Learning summaries from persisted facts, promotes them through existing best-effort writers, and reports file/DB count mismatches without reading HTML reports.
- **Version 1.0 Evidence Freeze:** do not add or loosen V1 strategy behavior until 100-200 completed paper trades span at least 20 trading days and multiple market regimes. New logic must remain shadow-only until persisted evidence supports a human-controlled promotion review.
- Migration `011_analytics_completion.sql` adds `analytics_summary` for resolved setup, regime/market, and lifecycle/decision aggregates plus `promotion_review` for timestamped human review history. Learning writes repository/Neon state first, then exports the same daily/live JSON payload as a resilient cache; aggregate/review failures cannot alter V1 behavior.
- `LearningEngineRepository` exposes database-backed daily/lifetime summaries, feature statistics, aggregate statistics, and promotion candidates. Learning, Validation, and Reports prefer available warehouse memory for historical context while retaining file/cache fallback.
- Final validation baseline: `python -m unittest discover -t . -s tests` runs 174 tests. The `-t . -s tests` form is required so `tests/__init__.py` runs and redirects storage roots to a sandbox; bare `discover tests` skips it and pollutes real `data/` and `app/state/` files. The expected background-worker exception in `test_background_queue.py` verifies isolation and does not fail the suite.

### Production Entry Diagnostics

- `app/diagnostics/entry_diagnostics.py` is a permanent observational diagnostics engine for entry setups. It does not change trading decisions.
- Every normal scanner row records the closest entry setup candidate, readiness percentage, passed/failed entry conditions, an entry decision timeline, and full JSON diagnostics.
- Scanner output columns are `ENTRY_SETUP_CANDIDATE`, `ENTRY_READINESS`, `FAILED_ENTRY_CONDITIONS`, `PASSED_ENTRY_CONDITIONS`, `ENTRY_DECISION_TIMELINE`, and `ENTRY_DIAGNOSTICS_JSON`.
- Scanner output also stores `ENTRY_GATE_FAILURE_STAGE`, a broad failure layer such as `Momentum`, `Entry`, `Risk`, `Option Quality`, `Affordability`, `Realtime`, `Telegram`, `Paper Gate`, or `Generated`.
- The diagnostics module evaluates setup families such as `BREAKOUT`, `EMA_PULLBACK`, `EMA_REJECTION_SHORT`, `BREAKDOWN_SHORT`, and `VWAP_REJECTION`, including actual versus required condition values.
- `run_scanner()` prints an entry failure summary and market regime entry summary after building scanner rows.
- The Streamlit dashboard exposes an `Entry Diagnostics` expander so ticker-level readiness and raw diagnostics can be reviewed without inspecting code.

### Trade Efficiency Analytics

- `app/analytics/trend_capture.py` is a permanent observational analytics module. It does not change entries, exits, risk, sizing, Telegram alerts, or auto-paper decisions.
- Trade Efficiency subsystem modules live under `app/analytics/trade_efficiency/` for future consolidation: trend continuation, exit delay analysis, recommendations/TES, opportunity cost, and compatibility exports for trend health / trend capture / exit quality.
- `app/analytics/trade_snapshot.py` writes exit-time Trade Lifecycle Snapshots to `data/daily/YYYY-MM-DD/trade_exit_snapshots.csv`, including indicators, structure flags, trend health, exit reason, and bars held. The schema can later support entry snapshots without renaming the subsystem.
- `app/analytics/trend_health.py` scores trend health from configurable weights for EMA alignment, price above EMA9/VWAP, higher high/low structure, MACD, RSI, and relative volume. States are `STRONG`, `HEALTHY`, `WEAKENING`, and `BROKEN`.
- After a paper trade closes, the paper trade manager attempts to fetch recent 5m candles, computes the exit lifecycle snapshot, trend health, Trend Capture %, MFE, MAE, available move, captured move, left on table, post-exit continuation, exit delay analysis, trigger attribution, exit quality, exit verdict, and Trade Efficiency Score, then appends `trend_capture_analysis.csv` and `trade_exit_snapshots.csv`.
- Trend-capture rows also provide a trade-doctor view: `Entry Grade`, `Exit Grade`, `Exit Verdict`, `Exit Verdict Reason`, `Exit Trigger`, and `Engineering Recommendation`. Grades use Trend Capture % (`A` >= 70, `B` >= 50, otherwise `C`); trigger and recommendation mirror primary-exit and delay-analysis data. This remains observational and cannot alter exits.
- The Validation dashboard page renders Trade Efficiency Analytics with TES/capture/opportunity KPIs, delay analysis, opportunity-cost bubble chart, exit quality summary, exit trigger frequency, trend health versus trend capture scatter, setup/regime/exit breakdowns, and best/worst capture tables.
- Cached Validation Trade Efficiency Analytics now includes summary KPIs, a trade-efficiency table, capture and TES distributions, capture by setup/regime, exit verdicts, opportunity cost, trend-health versus capture, and recommendations. Cached Reports adds Today/Yesterday/5 Day/20 Day summaries, daily rolling capture, weekly/monthly TES, and capture trends by setup, regime, exit verdict, and weekday.
- Daily validation reports render Trade Efficiency Analytics and Trade Lifecycle Diagnostics, grouped by setup, regime, exit reason, exit triggers, and exit verdict. If average Trend Capture % is below 55, the report adds a recommendation to improve trend management.
- `validation_state.json` carries structured Trade Doctor findings for scanner, entry, exit, replay, missed winners, and tomorrow. Each finding has `status`, `reason`, `evidence`, and a conservative `action`, replacing generic recommendation text. Entry comparisons use setup-level completed-trade counts and average Trend Capture %; a single observed setup is not labelled as either an outperformer or underperformer. Exit evidence uses `EXIT_TOO_EARLY` verdicts, missed-winner evidence uses the leading attribution category, and replay is only described as ready when cached replay output reports `READY`.
- `validation_state.json.strategy_confidence` measures evidence strength—not predicted profitability—with `evidence_days`, `completed_trades`, `confidence_pct`, `level`, and `rule_change_allowed`. The calibration is about 18% at one evidence day/one trade and 92% at 20 days/80+ trades. `rule_change_allowed` stays false until both thresholds are met; even then it supports controlled validation review and never auto-changes a trading rule.
- The Trading Scorecard loss-attribution table documents missed winners with `root_cause`, `blocked_by`, `rule`, `threshold`, `would_have_passed_if`, and `confidence` in addition to setup, move, classification, and recommendation. Categories are momentum, entry, risk, option, affordability, exit, and unknown; this retrospective attribution cannot alter gates or orders.

### Offline Decision Replay

- `tools/replay_today.py` replays entry diagnostics from saved scanner CSV/XLSX snapshots without Polygon/API access.
- The replay tool prefers persisted `ENTRY_DIAGNOSTICS_JSON`; otherwise it rebuilds diagnostics from replay-ready scanner columns such as `ENTRY_EMA9`, `ENTRY_EMA20`, `ENTRY_VWAP`, `ENTRY_REL_VOLUME`, `ENTRY_BODY_STRENGTH`, `ENTRY_ATR`, `ENTRY_BREAKDOWN`, `ENTRY_LOWER_HIGH`, `ENTRY_RECENT_HIGH`, and `ENTRY_RECENT_LOW`.
- With `--output data/daily/YYYY-MM-DD/offline_replay.csv`, the replay tool also writes `data/daily/YYYY-MM-DD/offline_replay_summary.csv` containing `Symbol`, closest setup, readiness, failed/passed conditions, final decision, gate failure stage, first failed rule, recommendation, trade-block details, and replay source.
- Replay trade-block details include actual versus required values for RR, option spread, open interest, volume, quote age, option quality, and affordability when those fields are present.
- The Streamlit `Replay` page renders coverage, biggest blockers, and the replay summary directly in the app. CSV downloads remain available but are no longer the primary replay workflow.
- Replay prints coverage metrics: scanner rows, replay rows, missing indicators, partial replay count, and coverage percentage. For a fresh replay-ready scanner output, missing indicators and partial replay should be zero.
- Scanner output now stores these `ENTRY_*` indicator snapshot columns so future market states are reproducible after the market closes.
- Older scanner files without the replay indicator columns are reported as partial replay inputs rather than treated as exact reproductions.

### Timeframes And Indicators

- The scanner fetches fresh 5-minute candles, then creates higher timeframes through `app/utils/timeframe_resampler.py`.
- `compute_indicators()` adds trend, momentum, volume, volatility, VWAP, opening-range, structure, breakout, breakdown, consolidation, support/resistance, and related derived columns.
- Timeframe minimum candle checks exist for 5m, 15m, and 1h.

### Strategy And Entry Logic

- `app/strategies/momentum_strategy.py` scores bullish, bearish, and neutral evidence using EMA, MACD, RSI, VWAP, relative volume, ATR expansion, candle body strength, market structure, breakout/breakdown behavior, failed reclaim patterns, and market regime.
- `app/strategies/entry_engine.py` detects entries including breakout, VWAP reclaim, EMA pullback, higher-low continuation, coiled breakout, coiled breakdown, bearish breakdown short, and VWAP rejection. The active EMA pullback trigger requires bullish signal, close above EMA9, EMA9 above EMA20, and latest low within `0.40 * ATR` of EMA9.
- Multi-timeframe bias is combined in `app/main.py`, with heavier weighting on 15m and 1h signals.
- Entry/Exit V2 continues to provide the location and trend-health diagnostics used for research and V1 grace-zone confidence. `entry_engine_v1.py` / `exit_engine_v1.py` remain V1 adapters; `entry_engine_v2.py` records trend age, pullback number, bars since breakout, EMA/VWAP extension, relative volume, ADX, candle strength, and Entry Efficiency independent from V1 setup validity. `trend_health_engine.py` and `exit_engine_v2.py` record live Trend Health, MFE in $R$, confirmed trend failure, and exit phase. V1 remains the only engine allowed to open/close trades or send alerts.
- `entry_exit_v2_shadow_state.json` stores V2-only simulated trade state. V2 uses its own entry proposal and shared risk geometry, then manages and closes only this separate state. `engine_trade_events.csv` records completed V1/V2 facts; `engine_trade_comparisons.csv` sequence-matches completed pairs and calculates timing, entry/exit price, $R$, and MFE deltas; `engine_differences.csv` records scanner-level V1/V2 disagreements.
- The Daily Validation Report and cached Validation page compare V1 versus V2 scanner counts, disagreement count, V2 entry-efficiency/MFE averages, exit-phase distribution, and completed-trade $R$/timing/MFE metrics. Promote V2 only after 2-3 weeks of paper evidence improves Trend Capture, TES, left on table, `EXIT_TOO_EARLY` rate, win rate, or average $R$ without increasing false positives. Feature flags `ENTRY_ENGINE=v1|v2` and `EXIT_ENGINE=v1|v2` are a Phase 3 controlled-switch step and are not active in shadow mode.
- V2 operating boundary: V1 owns `trade_state.json`, paper execution, Telegram, and suggestion mutation. V2 owns only `entry_exit_v2_shadow_state.json` and never modifies V1 state. V2 uses its own indicator/trend/price entry proposal plus shared risk geometry; it does not consume a V1 entry decision. Hard stop and target remain absolute in the V2 simulation.
- Artifact map: `entry_exit_v2_shadow.csv` contains per-scan proposals and disagreements; `engine_differences.csv` contains only disagreements; `engine_trade_events.csv` contains completed V1/V2 facts; `engine_trade_comparisons.csv` contains sequence-matched V1/V2 completed pairs. Pairing is currently symbol + direction + per-engine sequence. Merge the master `candidate_evidence` foundation into this branch before candidate-key matching, Candidate Intelligence engine-version fields, Replay version comparison, or multi-week Reports aggregation are treated as complete.
- Every completed engine event includes stock direction, trade direction, stock finish, trade finish, trend outcome, capture flag, and `trend_capture_pct`. The Daily Validation Report writes `engine_trend_outcomes.csv`, displaying all outcomes and flagging `STRONG_TREND_EXECUTION_FAILED`: directional stock movement of at least $1R$ with a non-profitable engine result. `stock_finish` is the latest scanner price when the report runs and is provisional until market close.
- Trend Capture % is the primary Entry/Exit V2 engineering target. V2 promotion requires improved capture while win rate, average $R$, TES, left on table, and risk behavior remain similar or better. The event capture formula is `max(0, min(100, final_R / MFE_R * 100))` when MFE is positive.
- `app/analytics/v2_learning_dataset.py` defines a compact one-row-per-completed-engine-trade execution-learning record. It captures V2-only entry timing features, continuous trend-health/MFE/MAE state aggregates, exit state, Trend Capture %, and derived entry/exit/execution-quality labels. `v2_learning_writer.py` writes `v2_learning_dataset.csv` and optionally Parquet under the daily folder.
- Validation renders a daily V2 Learning Summary. Reports renders multi-day Execution Learning Trends for Trend Age, Entry Efficiency, Trend Capture %, TES, and exit phase. The Daily Validation Report includes the same daily summary. Candidate Evidence payload upsert is a future optional adapter; do not enable it on this branch until the missing master `candidate_evidence` foundation is merged.

### Risk And Position Sizing

- `app/risk/risk_manager.py` calculates entry price, stop loss, take profit, risk/reward, trade allow/deny state, and max risk percentage.
- Long and short setups use different ATR-based stop/target logic.
- `app/risk/position_sizing.py` estimates contracts, max risk, estimated loss, estimated profit, and aggressiveness for options sizing.
- Current position sizing defaults to `DAILY_START_CAPITAL` and `OPTION_MAX_RISK_PER_TRADE_PCT` when legacy `ACCOUNT_SIZE` / `RISK_PERCENT` are not set. It uses `OPTION_STOP_LOSS_PCT` for estimated option loss so sizing and affordability risk math stay aligned. Current example settings use `ACCOUNT_SIZE=2000`, `RISK_PERCENT=10`, and `MAX_CONTRACTS_PER_TRADE=1`.

### Options

- `app/options/live_options_chain.py` fetches Polygon options snapshots when mock mode is disabled.
- Runtime options mode is controlled by `USE_MOCK_OPTIONS` in environment/settings. Current intended operating mode is live Polygon option snapshots with delayed-quote safety gates.
- `app/options/option_metrics.py` computes option mid-price, spread %, DTE, expiration bucket, expiration risk, quote age/freshness, option quality score, liquidity grade, quality reasons, and option P/L.
- `app/config/capital_profiles.py` defines account-size presets for option affordability: `SMALL_ACCOUNT`, `GROWTH_ACCOUNT`, and `BEST_QUALITY`.
- `app/options/affordability_config.py` loads the active affordability mode/profile from env or Streamlit Secrets.
- `app/options/option_affordability.py` computes `contract_cost`, `risk_at_stop`, current capital, max allowed contract cost, preferred affordability, affordability status, and affordability booleans.
- `app/options/contract_ranker.py` scores contracts by direction, DTE/expiration bucket, volume, open interest, strike proximity, delta, IV, gamma, theta, spread readiness, and option quality score.
- `app/options/contract_ranker.py` preserves the technical `ranking_score` and adds affordability metadata plus an `affordability_adjusted_score` for affordable alternate selection.
- `app/options/options_filter.py` hard-rejects unavailable bid/ask, crossed markets, stale/delayed quotes, low open interest, low volume, wide spread, disabled 0DTE/1DTE contracts, low option quality score, and unaffordable contracts when `OPTION_AFFORDABILITY_MODE=HARD`.
- Quote freshness is unchanged: `LIVE_QUOTE` is under 10 minutes old, `DELAYED_QUOTE` is 10-30 minutes old, and `STALE_QUOTE` is older than 30 minutes under the active environment. The quote endpoint is requested in descending timestamp order and classifies `last_updated` with SIP/timestamp fallbacks. Candidate snapshots and lifecycle events now retain quote timestamp, classification time, provider timeframe, source, age seconds, allowed-age seconds, and a reason such as `AGE_EXCEEDS_ALLOWED_AGE`. The Validation Report's Quote Diagnostics table displays Symbol, Quote Timestamp, Timestamp Field, Current Time, Age (sec), Threshold (sec), Decision, and Reason for every delayed, stale, or unparseable quote. The current daily archive predates this provenance capture, so its historical stale blocks cannot yet be attributed conclusively; do not loosen freshness thresholds.
- `app/analytics/quote_attribution.py` writes one dedicated fact for every non-live quote classification to `data/daily/YYYY-MM-DD/quote_attribution.csv` and Postgres `quote_attribution` through migration `005_quote_attribution.sql`. The fact includes symbol, option ticker, scanner timestamp, normalized quote timestamp, quote age seconds, allowed-age seconds, selected provider timestamp field (`last_updated`, `sip_timestamp`, or `timestamp`), provider source, final classification, and reason.
- `app/options/options_recommender.py` returns a best-quality `primary` contract, a best affordable `affordable` contract when available, and an actionable `active` contract. `active` uses the affordable contract in `SOFT`/`HARD` modes when one exists; otherwise it falls back to the best-quality primary.
- Scanner option validation now starts from the bundle's `active` contract and falls back through `primary`, `affordable`, `short_dte`, `longer_dte`, and ranked contracts before setting an option-liquidity rejection. Duplicate tickers are skipped, so a fallback may surface as `ranked #2` when earlier ranked entries already matched `active` or `primary`.
- Fallback attempts are visible in runtime logs with `[LIQUIDITY FALLBACK] Try ...`, failure, and accepted messages. Scanner rows include `Option Liquidity Attempts` as JSON for the attempted source/ticker/code/reason/spread chain.
- `app/main.py` appends `option_liquidity_attempts.csv` daily rows for every liquidity attempt, including symbol, selected option ticker, attempt source/ticker/code/reason/spread, liquid flag, and accepted flag.
- `app/main.py` prints and appends a permanent `candidate_funnel.jsonl` summary with scanned, directional, entry-ready, risk-passed, option-selected, liquidity-passed, affordability-passed, `EMA_REJECTION_SHORT`, `ENTER_PAPER`, Telegram attempted/sent/blocked, and Telegram reasons. `EMA_REJECTION_SHORT_WARNING_THRESHOLD` defaults to `10` and prints a warning if the recent rejection window appears too permissive.
- Validation commands used for this slice: `d:/Dravya_Trade_Works/.venv/Scripts/python.exe -m unittest tests.test_option_liquidity_fallback` and `d:/Dravya_Trade_Works/.venv/Scripts/python.exe -m unittest discover -t . -s tests`.
- `app/main.py` marks high-quality unaffordable setups as `QUALITY_BUT_TOO_EXPENSIVE` instead of `ENTER_PAPER`. Cheap contracts that fail the minimum cost/delta affordability rules can surface as `NO_TRADE_LOW_OPTION_QUALITY`.
- Dashboard suggested-trade lifecycle and paper-validation candidate lists can ignore affordability for research visibility through `SUGGESTIONS_IGNORE_AFFORDABILITY=true` and `PAPER_IGNORE_AFFORDABILITY=true`, while preserving original affordability metadata and keeping real-trade readiness affordability-gated by default.

### Market References, Regime, And Breadth

- `app/config/watchlist.py` separates trade candidates from non-trade reference symbols.
- Reference symbols are SMH, SOXX, XLK, and VIX. VIX is fetched through VIXY as a volatility proxy under the current Polygon subscription.
- `app/main.py` classifies explicit regimes as `TRENDING_BULL`, `TRENDING_BEAR`, `RANGE_BOUND`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`, or `UNKNOWN`.
- Regime rules can block setup families before options are considered. For example, VWAP reclaim/breakout style longs are blocked outside bullish/high-volatility regimes, and breakdown/VWAP rejection shorts are blocked outside bearish/high-volatility regimes.
- Sector strength compares symbols against reference ETFs, especially semis versus SMH and mega-cap/software tech versus XLK.
- Breadth is currently a watchlist breadth proxy, not official NASDAQ/QQQ advancer-decliner data. It tracks watchlist advancers/decliners, above VWAP %, above EMA20 %, and top 5 strongest/weakest names.

### Entry Timing And Regime Aliases

- Dashboard refresh intervals are 1, 5, or 15 minutes, while full scanner cadence is selectable at 5 or 15 minutes. Live entry detection therefore treats bearish EMA rejection as a recent 3-bar EMA9 touch plus current close below EMA9 and EMA9 below EMA20, instead of requiring only the latest candle high to touch EMA9.
- Live EMA pullback detection logs `[EMA_PULLBACK CHECK]` with symbol, signal, `Close>EMA9`, `EMA9>EMA20`, low-to-EMA9 distance, and threshold so `NO_ENTRY` pullback failures can be diagnosed without parsing free text.
- `app/diagnostics/entry_diagnostics.py` mirrors the same recent 3-bar EMA9 touch rule for `EMA_REJECTION_SHORT`, keeping replay diagnostics aligned with live entry logic.
- Entry scoring and projection normalize regime aliases so both `TRENDING_BEAR` / `TRENDING_BEARISH` and `TRENDING_BULL` / `TRENDING_BULLISH` receive the intended trend-regime treatment.

### Risk Manager Calibration Notes

- `app/risk/risk_manager.py` keeps the universal ATR stop-distance floor for breakout-style entries.
- `EMA_PULLBACK` uses a smaller minimum stop floor of `0.25 * ATR`, preserving the structure-based pullback stop unless it is unrealistically tight. This avoids converting high-RR pullback candidates into risk rejections solely because the stop was widened to a full ATR.

### Exits

- `app/exit/exit_engine.py` evaluates hard stop, hard target, EMA, VWAP, MACD, failed-breakout, time, and near-close exits with explicit priority diagnostics.
- `app/trade_manager.py` is legacy and should not be used for live exit decisions.

### Projections And Replay

- `app/projections/trade_projection.py` estimates expected move percentage, projected option gain, target price, stop price, probability, best expiry bucket, and trade grade.
- `app/analytics/replay_engine.py` replays recent candles against projected target/stop.
- `app/analytics/trade_outcome_tracker.py` determines whether target, stop, no-hit, unknown, or error occurred during replay.
- `app/analytics/replay_calibration.py` calibrates replay paths across ATR stop/target multiples and time horizons. It reports MFE, MAE, bars to target/stop, best stop ATR multiple, best target ATR multiple, best time-exit bars, best R multiple, and win rate by horizon.

### Research, Expectancy, And Backtesting

- `app/analytics/candidate_evidence.py` is the daily candidate-grain evidence materializer. One row represents `trading_day + symbol + direction + setup`, aggregates repeated scans, and joins suggestion state, paper events, replay outcome, option quality, trend health, trend capture, TES, quote freshness, and engineering root cause. It writes `candidate_evidence.csv` and, where supported, `candidate_evidence.parquet` under the daily folder.
- Migrations `004_candidate_evidence.sql` and `006_candidate_intelligence_dimensions.sql` create and extend the queryable Postgres `candidate_evidence` table. It exposes common research dimensions as columns and preserves the complete evidence record in `payload` JSONB. Existing dashboard widgets remain derived views during migration; no dashboard should become a second data source.
- `app/analytics/candidate_intelligence.py` derives first-class research entities from master evidence without changing decisions. Good Candidates require setup >= 70, RR >= 1.8, option quality >= 80, and `HEALTHY`/`STRONG` trend health. It writes `candidate_intelligence.csv` and a JSON summary. Validation and the Daily Validation Report render Good Candidate counts, High Quality Blocked Candidates, Candidate Outcome Matrix, Blocked Missed-Winner Attribution, and a top-10 Investigation Queue. Missed winners are classified as `OPERATIONAL_MISS`, `DATA_QUALITY_MISS`, `RISK_MISS`, or `INTENTIONAL_SKIP`; these labels direct investigation only.
- `app/analytics/candidate_snapshot_writer.py` normalizes every scanner result row into a compact research schema and writes daily candidate snapshots under `data/daily/YYYY-MM-DD/`. It prefers parquet and falls back to CSV when no parquet engine is installed.
- `app/storage/auto_paper_decision_store.py` appends full-day auto-paper decisions to `data/daily/YYYY-MM-DD/auto_paper_decisions.csv` while keeping `app/state/auto_paper_decision_log.json` capped to recent dashboard rows.
- `app/storage/auto_paper_decision_store.py` also exposes batch helpers for appending multiple daily auto-paper decisions and updating the capped recent dashboard JSON in one write pass when callers have multiple rows.
- `app/storage/signal_lifecycle_store.py` appends one signal lifecycle event per observed candidate per scan and writes state transition rows whenever a candidate changes action/quote/readiness/block state. These files answer whether market-hours quotes are live often enough, whether candidates stay in `REVIEW_TV_CHART` too long, and whether suggestions expire before auto-paper can enter.
- `app/analytics/expectancy_engine.py` exposes reusable expectancy table builders for grouped analysis by setup, direction, regime, candidate rank, option-quality bucket, spread bucket, expiration bucket, and other available fields.
- `app/analytics/expectancy_report.py` builds grouped HTML-friendly expectancy reports and applies simple `KEEP`, `REVIEW`, `WATCH`, or `BLOCK/TIGHTEN` verdicts based on sample count and expectancy.
- `app/backtesting/historical_dataset_builder.py` loads CSV or Polygon-style JSON candle datasets and normalizes OHLCV columns.
- `app/backtesting/no_lookahead_scanner.py` evaluates a symbol using only candles available up to the requested timestamp, reusing the existing indicator, setup, risk, and projection logic where practical.
- `app/backtesting/backtest_runner.py` walks historical timestamps and symbols without lookahead, records all candidates, applies entry gates, and simulates underlying target/stop outcomes for opened trades.
- `app/backtesting/backtest_report.py` summarizes backtest candidate/trade counts and grouped expectancy tables into an HTML report.
- The first backtesting pass is stock-underlying validation only. Historical option quote replay and option P/L approximation remain future work.

### Daily Session Persistence

- `app/storage/daily_paths.py` owns `data/live/` and `data/daily/YYYY-MM-DD/` path helpers.
- `app/storage/session_manager.py` resolves `trading_day`, `session_id`, and `scan_id`, creates/updates `manifest.json`, and provides stable candidate/trade key helpers.
- One trading day maps to one validation folder and one session id: `paper_validation_YYYY-MM-DD`.
- Each scanner run gets a `scan_id` in the form `YYYY-MM-DD_HHMMSS` and updates the daily manifest instead of creating a new report universe.
- Candidate snapshots append to `data/daily/YYYY-MM-DD/candidate_snapshots.parquet` or `.csv`.
- Full auto-paper decisions append to `data/daily/YYYY-MM-DD/auto_paper_decisions.csv` from the Streamlit dashboard auto-paper path. The dashboard JSON at `app/state/auto_paper_decision_log.json` remains capped at 500 rows and is only a latest-state UI source.
- Auto-paper decisions include market-session context: `trading_day`, `session_id`, `scan_id`, `scan_timestamp`, `market_session`, `decision_time_bucket`, `is_regular_market`, `is_auto_entry_window`, `is_after_close`, `minutes_from_open`, and `minutes_to_close`.
- Signal lifecycle observations append to `data/daily/YYYY-MM-DD/signal_lifecycle_events.csv`; state changes append to `data/daily/YYYY-MM-DD/signal_state_transitions.csv`. Observation timestamps use current ET when scanner results are finalized while preserving the scan-start `scan_id`. The transient latest-state helper file is `app/state/signal_lifecycle_state.json` and is scoped by trading day internally.
- Lifecycle observations retain option quote freshness plus normalized quote timestamp, classification time, provider timeframe, source, age seconds, allowed-age seconds, and freshness reason. Daily validation reports reconcile `paper_trade_state`, legacy `trade_state`, suggestion lifecycle statuses, lifecycle observation count, and lifecycle transition count, with visible mismatch warnings and a Quote Diagnostics table for freshness rejections.
- Trade telemetry still writes to legacy `telemetry/trade_telemetry.csv` and also appends to `data/daily/YYYY-MM-DD/trade_telemetry.csv`.
- Paper trade opens/closes append immutable events to `data/daily/YYYY-MM-DD/paper_trade_events.csv`. Event types include `OPEN`, `MANUAL_CLOSE`, and `AUTO_EXIT`, with trade key, symbol, direction, option ticker, prices, status, R multiple, and exit reason when available.
- Scanner Excel output still writes to the configured legacy path for compatibility and mirrors to `data/live/scanner_output_latest.xlsx` plus `data/daily/YYYY-MM-DD/scanner_output_close.xlsx`.
- The scanner writes fast CSV mirrors to `data/live/scanner_output_latest.csv` and `data/daily/YYYY-MM-DD/scanner_output_close.csv` before Excel output. The dashboard reads live CSV first, then live Excel, then legacy `scanner_output.xlsx`. Dashboard-triggered scanner runs use `data/live/scanner_run.lock` with stale-lock cleanup plus `data/live/scanner_run_status.json` cooldown tracking to prevent overlapping or back-to-back Streamlit refreshes from running the scanner.
- `tools/daily_validation_report.py` prefers daily files, falls back to live/legacy files, writes `reports/daily_validation_YYYY-MM-DD.html`, copies the report to `data/daily/YYYY-MM-DD/daily_validation_report.html`, and can mark the manifest final with `--finalize`. It starts with a Validation Data Health section and uses paper trade events before telemetry/state for trade evidence. It reads full-day `auto_paper_decisions.csv` first and falls back to the capped auto-paper JSON with a warning.
- The daily report includes `F. Data Quality Checks` with invalid price geometry, direction/option mismatch, high setup plus setup-threshold block, missing realtime-block reason, actual opened trade, and suggested-not-entered counts.
- The daily report splits gate and quote diagnostics into full day, auto-entry window only, and after-close only, so after-close `DELAYED_QUOTE` or `STALE_QUOTE` noise does not distort regular-session strategy quality.
- The daily report includes `G. Signal Lifecycle Analysis` with auto-entry-window quote freshness percentages, `REVIEW_TV_CHART` duration metrics, and suggestion-expiry buckets such as expired under 5 minutes, expired while `LIVE_QUOTE`, expired while RR >= 1.8, and expired while setup >= 70.
- The Streamlit sidebar has a `Generate Daily Validation Report` control that runs the same daily report builder in-process, archives available inputs, optionally finalizes the manifest, and exposes a download button for the generated HTML report. This is the preferred Streamlit Cloud path because local terminals cannot see cloud-generated files.

### AI Summary

- `app/ai/trade_analyzer.py` uses `OPENAI_API_KEY` and the OpenAI Python client.
- The requested model is `gpt-4.1-mini`.
- `app/state/state_manager.py` throttles AI calls by symbol, signal changes, score improvements, and a 20-minute cooldown.
- Core scanner decisions are rules-first; OpenAI is explanation-only.
- Scanner refreshes do not call AI unless `SCANNER_AI_SUMMARY_ENABLED=true`; keep this false during market hours.
- Dashboard on-demand AI explanations use `OPENAI_API_KEY_APP` when provided, a small dashboard model, a max-token cap, and local cache file `app/state/ai_summary_cache.json`.
- Dashboard AI explanations are gated to top candidates with setup >= 70, RR >= 2.0, `Action Status=REVIEW_TV_CHART`, and no event/regime block.

### Persistence And Output

- `app/state/trade_state.json` stores active open trade state. It is currently empty.
- `app/state/signal_memory.json` stores the last AI-call signal state per symbol. It currently contains QQQ, SPY, AVGO, TSLA, and NVDA history.
- `telemetry/trade_telemetry.csv` stores scan/projection telemetry and paper-trade close telemetry with scanner context snapshots.
- `app/db/connection.py` owns the SQLAlchemy engine built from `DATABASE_URL`; `app/db/persistence.py` retains the original alert/gate/paper/scanner records, while dedicated repositories promote artifact facts. All repository calls are best-effort and must be made from `RuntimeScheduler` jobs.
- Promoted immutable tables are `candidate_snapshot`, `rule_evaluation`, canonical completed `trade`, append-only `event_stream`, `candidate_outcome`, and `telegram_dispatch`. Existing `alert_events`, `paper_trades`, `scanner_runs`, and `gate_decisions` remain for compatibility. Failed DB writes do not block Telegram, scanner output, paper-trade JSON/CSV state, validation reports, or Streamlit rendering.
- `telegram_dispatch` records dispatcher ATTEMPT, SENT, and FAILED transitions with scan/trade/symbol/direction/candidate metadata, message type, decision, parse mode, message length, retry attempt, latency, attempt/delivery status, failure reason, structured Telegram response, and Telegram message ID when available. JSONL audit remains at `data/live/telegram_dispatch_audit.jsonl`; each DB row is queued through `RuntimeScheduler` and cannot delay delivery. Migration `007_telegram_dispatch_audit_context.sql` has been applied to the configured database.
- Entry facts are captured once at paper entry and exit facts once after exit snapshot/trend-capture artifacts; both are inserted as one immutable completed `trade` aggregate after close. `trade_timeline.jsonl` mirrors `CandidateCreated`, state/promotion/realtime/rule events, `EntryOpened`, and `ExitTriggered`. Candidate outcomes are generated post-validation from audit/replay data, making Telegram misses and false alerts objective derived facts; cached Validation renders outcomes, Telegram miss/false-alert counts, and delay attribution. Engineering recommendations are derived at report/Trade Doctor time and are not persisted as immutable facts.
- `app/db/migrations/001_promote_scanner_artifacts.sql` was applied successfully through the configured `DATABASE_DIRECT_URL`. The scanner never runs DDL automatically; apply the migration manually for any additional environment.
- DB idempotency should stay narrow and intentional. `alert_events` may use deterministic Telegram alert dedupe keys so failed send attempts and later successful retries can update the same audit row, but broad constraints such as symbol/day/contract-only uniqueness are unsafe for scanner rows, paper trade lifecycles, re-entries, and refreshed option observations. Paper lifecycle promotion targets the deployed `paper_trades` table by unique `trade_key`; opens and closes queue best-effort upserts after their local state/event writes. Additional unique indexes should be added only with an explicit Neon migration and duplicate scanner/alert/paper-trade tests.
- `data/daily/YYYY-MM-DD/candidate_snapshots.parquet` or `.csv` stores every scanner candidate row in a normalized research schema, including skipped and blocked setups.
- `data/daily/YYYY-MM-DD/auto_paper_decisions.csv` stores every auto-paper decision for the full trading day. Use this as the report/audit source; `app/state/auto_paper_decision_log.json` is capped for dashboard speed.
- `data/daily/YYYY-MM-DD/signal_lifecycle_events.csv` stores one row per observed candidate per scan, including final signal, action status/reason, realtime readiness, quote freshness/age, option quality/spread, expiration bucket, affordability status, option contract cost, setup/RR, prices, market/reference regime, top-candidate tag, and composite `state_label`.
- `data/daily/YYYY-MM-DD/signal_state_transitions.csv` stores one row when a candidate's composite lifecycle state changes, including previous/new state, state start/end, duration minutes, from/to action status, from/to quote freshness, from/to realtime readiness, and transition reason.
- `data/daily/YYYY-MM-DD/paper_trade_events.csv` stores immutable paper open/close events so reports can detect true paper activity even if JSON state is later cleared.
- `data/daily/YYYY-MM-DD/manifest.json` tracks daily validation session status, first/last scan time, last scan id, and finalization state.
- `data/live/scanner_output_latest.xlsx` mirrors the latest scanner output for live/dashboard-style review.
- Dashboard sidebar export buttons provide quick downloads for `scanner_output.xlsx`, `telemetry/trade_telemetry.csv`, `app/state/paper_trade_state.json`, and `app/state/trade_state.json`.
- `scanner_output.xlsx` is produced by the scanner at runtime.
- `logs/` is available for diagnostics. `data/` now owns candidate snapshots and can also hold historical candle datasets for backtesting.

## Tools And Diagnostics

- `tools/dump_header_history.py` prints the in-memory Polygon rate-limit header history.
- `tools/get_suggested_rate.py` prints a suggested `POLYGON_RATE_LIMIT_PER_MINUTE` based on recent header observations.
- `tools/diag_fetch.py` fetches raw 5m Polygon data, resamples to 15m, computes indicators correctly, and prints latest close / move diagnostics.
- `tools/daily_validation_report.py` creates `reports/daily_validation_YYYY-MM-DD.html` from scanner output, trade telemetry, paper/live state JSON, full daily auto-paper decisions, signal lifecycle files, and suggested-trade state. Use `--archive` to copy available source files into `daily_reviews/YYYY-MM-DD/`.
- `tools/test_db_connection.py` verifies the configured SQLAlchemy/Postgres connection by running `SELECT now()`.
- `tools/test_db_insert.py` verifies inserts into `scanner_runs` using `manual_db_test_001`.
- The dashboard sidebar can generate and download the daily validation report directly, which should be used for Streamlit Cloud sessions.

## Dependencies

The project uses a UTF-8 encoded `requirements.txt`. Important packages include:

- `pandas`, `numpy`, `ta`
- `requests`, `urllib3`, `python-dotenv`, `pytz`, `tzdata`
- `SQLAlchemy`, `psycopg2-binary` for optional Neon/Postgres persistence
- `polygon-api-client` for Polygon REST client imports
- `openai` for optional AI trade summaries
- `rich` for console table output
- `openpyxl` for Excel report export
- `fastapi`, `uvicorn`, `plotly`, `yfinance`, and `websockets` are installed/listed but are not central to the current scanner path based on the inspected files.

## Environment Variables

Known environment variables used by the code:

- `POLYGON_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_API_KEY_APP`
- `OPENAI_DASHBOARD_MODEL`
- `OPENAI_SUMMARY_MAX_TOKENS`
- `AI_SUMMARY_CACHE_FILE`
- `SCANNER_AI_SUMMARY_ENABLED`
- `USE_EXTENDED_HOURS`
- `POLYGON_RATE_LIMIT_PER_MINUTE`
- `POLYGON_CACHE_TTL`
- `POLYGON_HEADER_HISTORY_MAX`
- `SCANNER_MAX_WORKERS`
- `TELEGRAM_ALERT_POLICY`
- `DYNAMIC_WATCHLIST_ENABLED`
- `DYNAMIC_WATCHLIST_SIZE`
- `DYNAMIC_WATCHLIST_MOVERS_PER_BUCKET`
- `USE_MOCK_MARKET_DATA`
- `USE_MOCK_OPTIONS`
- `ENABLE_AI_SUMMARY`
- `SCANNER_DEBUG`
- `SCANNER_OUTPUT_FILE`
- `ACCOUNT_SIZE`
- `RISK_PERCENT`
- `EVENT_BLOCKER_ENABLED`
- `EVENT_BLOCKER_DATES`
- `EVENT_BLOCKER_DAYS_BEFORE`
- `OPTION_MIN_VOLUME`
- `OPTION_MIN_OPEN_INTEREST`
- `OPTION_MAX_SPREAD_PCT`
- `OPTION_MIN_QUALITY_SCORE`
- `OPTION_DELAYED_QUOTE_MINUTES`
- `OPTION_MAX_QUOTE_AGE_MINUTES`
- `OPTION_ALLOW_0DTE`
- `OPTION_ALLOW_1DTE`
- `OPTION_MIN_DTE`
- `OPTION_PREFERRED_MIN_DTE`
- `OPTION_PREFERRED_MAX_DTE`
- `OPTION_MAX_DTE`
- `OPTION_AFFORDABILITY_MODE`
- `OPTION_CAPITAL_PROFILE`
- `DAILY_START_CAPITAL`
- `CAPITAL_GROWTH_MODE`
- `MAX_ACTIVE_PAPER_TRADES`
- `MAX_DAILY_ENTRIES`
- `OPTION_STOP_LOSS_PCT`
- `OPTION_MAX_RISK_PER_TRADE_PCT`
- `OPTION_MIN_CONTRACT_COST`
- `OPTION_PREFERRED_MAX_CONTRACT_COST`
- `OPTION_MAX_CONTRACT_COST`
- `OPTION_AGGRESSIVE_MAX_CONTRACT_COST`
- `OPTION_MIN_AFFORDABLE_DELTA`
- `OPTION_SHOW_BEST_QUALITY_CONTRACT`
- `OPTION_SHOW_AFFORDABLE_ALTERNATE`
- `TELEGRAM_ALERTS_ENABLED`
- `TELEGRAM_ENTRY_ALERTS_ENABLED`
- `TELEGRAM_EXIT_ALERTS_ENABLED`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_MAX_ENTRY_ALERTS_PER_DAY`
- `TELEGRAM_MAX_ACTIVE_ALERTED_TRADES`
- `TELEGRAM_ENTRY_COOLDOWN_MINUTES`
- `TELEGRAM_SYMBOL_COOLDOWN_MINUTES`
- `TELEGRAM_TOP_CANDIDATE_LIMIT`
- `TELEGRAM_MIN_ENTRY_ALERT_SCORE`
- `TELEGRAM_INSTANT_ENTRY_ALERT_SCORE`
- `TELEGRAM_AFTERNOON_MIN_ENTRY_ALERT_SCORE`
- `TELEGRAM_MIN_OPTION_QUALITY_SCORE`
- `TELEGRAM_MIN_RR`
- `TELEGRAM_MAX_SPREAD_PCT`
- `TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE`
- `TELEGRAM_MAX_MORNING_ENTRY_ALERTS`
- `TELEGRAM_MAX_MIDDAY_ENTRY_ALERTS`
- `TELEGRAM_MAX_AFTERNOON_ENTRY_ALERTS`
- `TELEGRAM_EXIT_PRICE_MISMATCH_PCT`
- `DATABASE_URL`
- `DATABASE_DIRECT_URL`
- `DB_WRITE_ENABLED`
- `DB_POOL_SIZE`
- `DB_MAX_OVERFLOW`
- `DB_CONNECT_TIMEOUT_SECONDS`
- `AUTO_PAPER_ENABLED`
- `ENABLE_MANUAL_PAPER_ENTRIES`
- `SHOW_MANUAL_PAPER_BUTTONS`
- `ALLOW_MANUAL_PAPER_CLOSE`
- `AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES`
- `MAX_TRADES_PER_SYMBOL_PER_DAY`

## Current Operating Mode

- Intended Monday mode is `USE_MOCK_MARKET_DATA=false` and `USE_MOCK_OPTIONS=false`, using the user's current full Polygon/Massive real-time subscription.
- Real-time mode is available with `REALTIME_MARKET_DATA_REQUIRED=true`, `REALTIME_OPTIONS_REQUIRED=true`, `OPTION_REQUIRE_BID_ASK=true`, `OPTION_REQUIRE_FRESH_QUOTE=true`, and `MAX_STOCK_DATA_DELAY_MINUTES=2` after Polygon/Massive entitlement upgrade.
- Intended market-hours AI mode is `ENABLE_AI_SUMMARY=false` and `SCANNER_AI_SUMMARY_ENABLED=false`; use dashboard rules only.
- Runtime settings load `.env` with override enabled so local config changes, including `ENABLE_AI_SUMMARY=false`, win over stale process variables after restart.
- Streamlit Cloud must be configured through Streamlit Secrets; local `.env` is not automatically available in deployed Streamlit. The dashboard syncs Streamlit Secrets into env before scanner imports and shows non-sensitive sidebar key status.
- Runtime environment reads `APP_ENV` first and accepts `ENV` as a fallback alias, so Streamlit Secrets can set either `APP_ENV="production"` or `ENV="production"`.
- Neon/Postgres persistence is optional and additive. `DATABASE_URL` should use the Neon pooler host for Streamlit runtime writes, while `DATABASE_DIRECT_URL` is reserved for schema setup and one-time migrations. DB writes occur only when `DB_WRITE_ENABLED=true` and `DATABASE_URL` is configured; otherwise the current JSON/CSV/Excel dashboard flow continues unchanged.
- Streamlit Secrets DB keys should be root-level values when possible, and the root-level database block should appear before `[telegram]` because TOML keeps later keys inside the active table. The dashboard also maps common Streamlit connection URLs such as `[connections.trading_db].url`, `[connections.neon].url`, or `[connections.postgres].url` into `DATABASE_URL`, supports a `[database]` section with `url`, `direct_url`, and `write_enabled` keys, and recovers DB keys accidentally nested under another table.
- Scanner startup prints a non-sensitive DB status line showing `DB_WRITE_ENABLED`, whether `DATABASE_URL` is present, and whether DB writes are active. It never prints the URL or password.
- Current Neon tables are small event/state tables only: `alert_events`, `paper_trades`, `scanner_runs`, and `gate_decisions`. Full candle history, option chain snapshots, raw API responses, scanner Excel blobs, and large CSV payloads are intentionally kept out of Neon during the free-tier phase.
- Current safe idempotency target is `alert_events.dedupe_key`, matching Telegram's deterministic duplicate-protection keys. Scanner/gate/paper trade uniqueness should remain conservative until actual rows from live paper sessions have been reviewed and schema changes are tested with duplicate scanner runs, duplicate alerts, duplicate paper opens, and duplicate closes.
- Telegram alerts are opt-in with `TELEGRAM_ALERTS_ENABLED=true`. Bot credentials should be stored in Streamlit Secrets under `[telegram]` as `bot_token` and `chat_id`, or in local ignored env vars for development. Real bot tokens must not be committed.
- Local `.env` supports uppercase `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` and legacy lowercase `bot_token` / `chat_id`. `tools/send_test_telegram_alert.py` is the direct delivery check. A Telegram `chat not found` error means the destination chat ID is invalid or unavailable to the bot; rotate a token exposed by a terminal error before retrying.
- Scanner `ENTER` and `ENTER_PAPER` decisions are not subscriber messages. A successful V1/paper trade open publishes one `NEW TRADE` message, while scanner `ACTIVE_TRADE` rows are suppressed. `REVIEW_TV_CHART` uses one stable daily key per symbol/setup. A monitoring pass can publish `TRADE UPDATE` for a $0.5R$ move, trend-health/confidence deterioration, stop movement, or partial profit. Exit and partial-profit alerts retain dedicated messages and duplicate guards.
- Auto paper-entry alerts fire at the moment `open_paper_trade()` succeeds in the dashboard auto-paper path. Manual paper entry buttons are hidden by default through `ENABLE_MANUAL_PAPER_ENTRIES=false` and `SHOW_MANUAL_PAPER_BUTTONS=false` so telemetry represents system-generated scanner/alert trades. Manual close/correction remains enabled by default through `ALLOW_MANUAL_PAPER_CLOSE=true`. Legacy Telegram score, threshold, rank, cooldown, cap, and policy environment variables remain readable for compatibility but do not block an alertable scanner action.
- Telegram exit alerts resolve the current underlying price from the freshest available same-symbol source in priority order: `latest_quote`, `df_5m_latest_close`, then `df_15m_latest_close`. They validate that resolved price against the same-symbol expected close before sending. If the mismatch exceeds `TELEGRAM_EXIT_PRICE_MISMATCH_PCT` default 3%, the alert is blocked as `UNDERLYING_PRICE_MISMATCH`.
- Telegram exit alerts are allowed for explicitly tracked `PAPER`, `REAL`, and confirmed `SCANNER_TRACKED` V1 lifecycle trades. Successful exit alerts set `exit_alert_sent` and use deterministic duplicate keys built from event type, immutable trade lifecycle identity, and exit reason.
- Telegram entry alerts are dispatched in scanner row order after the scanner action is computed. Duplicate-alert protection is the only remaining entry-alert suppression safeguard; it prevents repeated delivery of the same alert key and does not change the trade decision. Entry alerts are no longer ranked, scored, bucketed, capped, cooled down, or restricted by `REAL_REVIEW` / `CUSTOM` gate behavior.
- Scanner entry-dispatch rows now record `Telegram Error Type`, `Telegram Error Reason`, and `Telegram Stage`. Successful/normal evaluations use `ENTRY_EVALUATION`; caught send failures use `ENTRY_DISPATCH` and an explicit `TELEGRAM_ERROR_<EXCEPTION_TYPE>` block reason. The dashboard exposes `data/live/telegram_dispatch_audit.jsonl` under Advanced downloads for audit review. Inspect `telegram_response.description` on a failed audit row to identify Telegram's specific rejection reason; these fields do not bypass policy or notification limits.
- Dashboard paper-entry decision records retain the Telegram result plus the trade `entry_source`, allowing manual and automated paper-entry provenance to be reviewed independently from notification delivery.
- Telegram duplicate protection stores sent alert keys in `app/state/telegram_alert_state.json`, which is ignored by Git.
- Runtime state files are ignored by Git, including scanner trade state, paper trade state, suggested trade state, auto-paper decision/settings JSON, and Telegram alert state. Do not commit stale active positions.
- Premarket real-time mode surfaces strong candidates as `PREMARKET_WATCH` but does not mark them execution-ready. The scanner waits for opening-range confirmation from 9:30-9:45 ET and only allows `ENTER`/`ENTER_PAPER` after 9:45 ET when all gates pass.
- Delayed-data mode remains acceptable for scanning and paper trading with manual confirmation. Real-time mode blocks truly stale stock aggregates and missing/stale/delayed option quotes.
- Current option gate defaults: minimum volume 100, minimum open interest 500, max spread 10%, minimum option quality score 65, delayed quote threshold 10 minutes, stale quote threshold 30 minutes, 0DTE disabled, 1DTE disabled.
- Current affordability defaults: `OPTION_AFFORDABILITY_MODE=HARD`, `OPTION_CAPITAL_PROFILE=SMALL_ACCOUNT`, `DAILY_START_CAPITAL=2000`, `OPTION_STOP_LOSS_PCT=0.20`, `OPTION_MAX_RISK_PER_TRADE_PCT=0.10`, `OPTION_MIN_CONTRACT_COST=100`, `OPTION_PREFERRED_MAX_CONTRACT_COST=400`, `OPTION_MAX_CONTRACT_COST=500`, `MAX_CONTRACTS_PER_TRADE=1`, and `OPTION_MIN_AFFORDABLE_DELTA=0.25`. The effective max contract cost is risk-capped to `DAILY_START_CAPITAL * OPTION_MAX_RISK_PER_TRADE_PCT / OPTION_STOP_LOSS_PCT`, so the current $2,000 small-account profile has a $1,000 risk-based cap and the static $500 contract max controls.
- Affordability modes: `OFF` preserves original best-quality-only behavior, `SOFT` keeps best-quality review visible while selecting affordable alternates, and `HARD` blocks unaffordable contracts from actionable scanner statuses. Dashboard research visibility can still include expensive technical setups when `SUGGESTIONS_IGNORE_AFFORDABILITY=true` / `PAPER_IGNORE_AFFORDABILITY=true`; real readiness remains strict with `REAL_REQUIRE_AFFORDABILITY=true`.
- Capital profiles: `SMALL_ACCOUNT` is the current $2,000/day profile, `GROWTH_ACCOUNT` widens contract-cost limits for larger buying power, and `BEST_QUALITY` effectively removes affordability limits while keeping metadata visible.
- Current DTE preference defaults: minimum 10 DTE, preferred 14-30 DTE, max fallback 45 DTE. The ranker heavily penalizes 2-6 DTE, allows 7-13 DTE as lower-priority short swing/fallback, favors 14-30 DTE, treats 31-45 DTE as acceptable fallback, and de-prioritizes 46+ DTE unless otherwise justified.
- Event blocker is configurable and enabled by default through environment settings.
- Polygon aggregate cache lookup and cache set are enabled in `app/utils/polygon_client.py` using the short `POLYGON_CACHE_TTL` setting to reduce duplicate aggregate requests during rapid refreshes.
- Polygon rate limiting is implemented in `app/utils/polygon_client.py` and active through `acquire_rate_limit()` in `get_aggs_cached()`.
- Polygon request URL logs redact the API key before printing.
- `POLYGON_API_KEY` loading now defaults to an empty string so missing env vars do not crash the module at import time.
- OpenAI summaries can run if `OPENAI_API_KEY` is present and `should_call_ai()` allows the call.
- `tools/diag_realtime_entitlements.py` verifies stock aggregate freshness, stock snapshot entitlement, ranked option contract selection, option snapshot quote, `/v3/quotes` quote fallback, bid/ask, spread, and quote freshness.

### Mock Data State

Mock aggregate JSON files exist for fallback/testing:

- QQQ
- SPY
- NVDA
- TSLA

## Telemetry Schema

Current telemetry fields include:

- symbol
- final_signal
- alignment_score
- probability
- trade_grade
- risk_reward
- market_regime
- replay_outcome
- bars_to_outcome
- mae
- mfe
- option_delta
- option_gamma
- option_iv
- option_theta
- option_mid_price
- option_spread_pct
- option_volume
- option_open_interest
- expiration_risk
- expiration_bucket
- option_quality_score
- option_liquidity_grade
- option_quote_freshness
- option_quote_age_minutes
- option_contract_cost
- option_risk_at_stop
- current_capital
- max_allowed_contract_cost
- affordability_status
- affordable
- preferred_affordable
- affordability_mode
- capital_profile
- best_quality_option_ticker
- best_quality_contract_cost
- best_quality_affordability_status
- affordable_option_ticker
- affordable_option_contract_cost
- event_blocked
- event_block_reason
- market_regime
- reference_regime
- regime_blocked
- regime_block_reason
- sector_group
- sector_reference
- sector_rs
- sector_strength
- strength_rank
- weakness_rank
- top_5_strongest
- top_5_weakest
- watchlist_advancers
- watchlist_decliners
- above_vwap_pct
- above_ema20_pct
- target_price
- stop_price
- setup_grade
- setup_percent
- scanner_final_signal
- scanner_context_source
- scanner_score_15m
- planned_rr
- market_data_delay_minutes
- realtime_confirmation_needed
- realtime_ready
- realtime_block_reason
- stock_data_freshness
- stock_data_age_minutes
- option_bid
- option_ask
- option_quote_timestamp
- option_quote_timeframe
- option_quote_source
- tradingview_check_status
- action_status
- blocked_by
- action_reason
- next_condition
- pnl_pct
- r_multiple
- exit_reason
- paper_trade
- live_confirmed
- opened_at
- closed_at

Paper trade telemetry captures scanner context at entry when opened from the dashboard. If a trade was opened before context capture existed, the dashboard can attach a close-time scanner snapshot when the trade is closed.

Candidate snapshots are separate from trade telemetry and intentionally include every scanner row, not only opened or replayed trades. Current normalized snapshot fields include timestamp, symbol, setup type, setup percent, direction, action status, blocked reason, candidate RR, market/reference regime, sector group, top-candidate tag, option ticker, option quality score, option spread %, option quote freshness, affordability, entry/stop/target prices, relative volume, ATR %, expiration bucket, option Greeks, and option mid price.

## Current System Maturity

Current status:

- Multi-timeframe orchestration: stable
- Projection engine: stable
- Replay engine: stable
- Telemetry analytics: stable
- Replay calibration utilities: initial implementation
- Expectancy reporting: initial implementation
- Historical backtesting framework: initial no-lookahead stock-underlying implementation
- Automated testing: minimal but now includes focused market-session scanner decision tests
- Live-trading readiness: experimental
- Delayed-data paper-trading readiness: improved for supervised use
- Paper-trade EOD review readiness: improved with context-rich close telemetry

## File Responsibility Matrix

| File | Responsibility |
|---|---|
| `main.py` | Orchestration |
| `replay_engine.py` | Replay simulation |
| `replay_calibration.py` | MFE/MAE, ATR multiple, and horizon calibration |
| `trade_outcome_tracker.py` | Target/stop evaluation |
| `candidate_snapshot_writer.py` | Daily normalized scanner candidate snapshots |
| `expectancy_engine.py` | Reusable grouped expectancy metrics |
| `expectancy_report.py` | Grouped expectancy report generation and verdicts |
| `backtesting/historical_dataset_builder.py` | Historical candle dataset loading/normalization |
| `backtesting/no_lookahead_scanner.py` | Scanner-at-time evaluation using only candles available at that timestamp |
| `backtesting/backtest_runner.py` | No-lookahead historical candidate/trade replay loop |
| `backtesting/backtest_report.py` | Backtest summary and expectancy HTML report |
| `trade_projection.py` | Projection generation |
| `contract_ranker.py` | Option scoring |
| `option_metrics.py` | Option quote, expiration, quality, and P/L metrics |
| `option_affordability.py` | Option contract cost, risk-at-stop, affordability, and profile metrics |
| `affordability_config.py` | Option affordability mode/profile env and Streamlit Secrets loading |
| `capital_profiles.py` | Preset capital profiles for small, growth, and best-quality option modes |
| `options_filter.py` | Option liquidity/quality hard gates |
| `event_blocker.py` | Manual event-risk entry blocker |

## Important Risks And Gaps

1. Automated test coverage is still sparse; current coverage includes focused market-session scanner decision tests in `tests/test_market_session_decisions.py`.
2. Some modules contain heavy debug printing, including request windows, system time, redacted Polygon URLs, and market data details.
3. Live/mocked behavior is now mostly settings-driven, but some module-level settings are still loaded at import time and should be restarted after `.env` edits.
4. Telemetry is still CSV-based and candidate snapshots fall back to CSV when parquet dependencies are absent; both may need schema/version handling as fields evolve.

## Known Replay Calibration Findings

Current replay outcomes are heavily skewed toward `STOP_HIT`.

Likely causes:

- Stops too tight
- Targets too ambitious
- Replay horizon too short
- Immediate entry assumptions
- Noisy intraday volatility

Implemented first-pass support:

- `app/analytics/replay_calibration.py` can evaluate ATR stop/target multiple grids by horizon.
- Calibration output includes MFE, MAE, bars to target/stop, best stop/target ATR multiple, best time exit, and win rate by horizon.
- `summarize_calibration()` can bucket calibration results by setup, regime, time bucket, top candidate, or any available grouping fields.

Future improvements:

- ATR-based adaptive stops
- Delayed entry confirmation
- Dynamic replay windows
- Regime-aware replay calibration

## Replay Engine Current Behavior

Replay currently:

- Simulates historical entries ~40 candles before the end of the dataset
- Evaluates target and stop hits candle-by-candle
- Ignores stop hits during the first 2 candles to reduce noise
- Currently produces predominantly `STOP_HIT` outcomes

## Current Technical Debt

- `run_scanner()` in `main.py` is becoming orchestration-heavy.
- Replay/debug logging is verbose.
- Mock/live config is scattered across modules.
- Telemetry storage still uses CSV, though writes now handle empty existing files correctly.
- Candidate snapshots currently prefer parquet but fall back to CSV unless `pyarrow` or `fastparquet` is installed.
- Backtesting is stock-underlying only and does not yet replay actual historical option quotes.
- No centralized config management exists yet.

## Monday Operating Workflow

Use this workflow while keeping the current delayed Polygon subscription:

1. Before market open, confirm `.env` has `USE_MOCK_MARKET_DATA=false`, `USE_MOCK_OPTIONS=false`, `ENABLE_AI_SUMMARY=false` unless summaries are explicitly wanted, and conservative option gates.
2. Add known macro/earnings/Fed/OPEX dates to `EVENT_BLOCKER_DATES` using `SYMBOL:YYYY-MM-DD:Label` or `*:YYYY-MM-DD:Label`.
3. Run `python -m app.main` after the market has enough 5m candles. With real-time entitlements, current 5-minute aggregate buckets should report `Stock Data Freshness=LIVE`; stale labels now account for Polygon bucket-start timestamps.
4. Open the dashboard and review only the top candidates: `BULLISH_TOP_1` through `BULLISH_TOP_3` and `BEARISH_TOP_1` through `BEARISH_TOP_3`.
5. For each candidate, check `RS vs QQQ`, `RS vs SPY`, `Relative Volume`, `ATR %`, `Risk Reward`, `Option Quality Score`, `Option Liquidity Grade`, `Option Quote Freshness`, `Expiration Bucket`, `Option Contract Cost`, `Affordability Status`, `Affordable`, and `Event Blocked`.
6. Before 9:30 ET, use `PREMARKET_WATCH` rows as a watchlist only. From 9:30-9:45 ET, use `OPENING_RANGE_CONFIRMATION` rows as candidates waiting for confirmation. Do not paper-enter until after 9:45 ET.
7. If `Action Status` is `REVIEW_TV_CHART`, `QUALITY_BUT_TOO_EXPENSIVE`, `DELAYED_QUOTE`, `STALE_QUOTE`, or any option rejection code, do not treat the scanner as execution-ready. Confirm the live chart and broker option premium manually.
8. Paper trade first. For small real trades, use broker live bid/ask and limit orders only; avoid market orders, 0DTE, 1DTE, wide spreads, stale quotes, and event-risk windows.
9. After the session, review paper/real outcomes, candidate snapshots, replay calibration, and expectancy tables before changing thresholds. Paper closes now preserve setup grade, RS, regime, sector, option-quality, blocker, and realized R context for model tuning.
10. If running locally, run `python tools/daily_validation_report.py --date YYYY-MM-DD --archive` to write `reports/daily_validation_YYYY-MM-DD.html` and archive scanner output, telemetry, paper trade state, trade state, auto-paper decision log, and suggested-trade state under `daily_reviews/YYYY-MM-DD/`. If running on Streamlit Cloud, use the dashboard sidebar `Generate Daily Validation Report` button and download the generated HTML from there.
11. Download/export scanner output, telemetry, paper trade state, trade state, and candidate snapshots before restarts or end-of-day review.

## Suggested Next Priorities

1. Add focused tests for option metrics, option hard gates, RS ranking, risk calculation, state transitions, replay outcomes, replay calibration, candidate snapshot writing, and no-lookahead backtest loops.
2. Run a 5-day then 20-day stock-underlying backtest from historical candles to catch obvious strategy/gate logic issues.
3. Add option P/L approximation using entry mid, delta, gamma, theta, underlying move, and elapsed time.
4. Re-enable and validate Polygon TTL caching if duplicate aggregate calls are still a problem.
5. Add official market breadth data if a reliable source/entitlement becomes available.
6. Consider moving telemetry and candidate snapshots from CSV/parquet files to SQLite or another schema-aware store once sample size grows.

## Next Evolution Roadmap

1. Replay calibration refinement
2. Expectancy report refinement
3. Win-rate analytics
4. ATR adaptive stops
5. Regime-aware position sizing
6. Historical backtesting expansion
7. Live alerting
8. Web dashboard/API

## Current Strategic Direction

The project focus has shifted away from adding indicators and toward:

- Replay validation
- Expectancy analytics
- Calibration
- Measurable edge discovery
- Historical outcome analysis

## Quick Mental Model

The project currently behaves like a command-line trading copilot:

```text
WATCHLIST
  -> Polygon/mock aggregate candles
  -> 5m indicators
  -> 15m and 1h resampled indicators
  -> momentum scoring
  -> entry detection
  -> risk and trade management
  -> option chain ranking and quality/freshness gates
  -> projection and replay
  -> relative strength ranking and top candidate tagging
  -> candidate snapshots, Rich table, Excel report, telemetry CSV, optional OpenAI summary
```

## Runtime Data Flow

Polygon/mock candles
  -> indicator generation
  -> multi-timeframe scoring
  -> entry detection
  -> risk evaluation
  -> option ranking and option safety gates
  -> projection generation
  -> replay simulation
  -> RS ranking/top candidate tagging
  -> candidate snapshot persistence
  -> telemetry persistence
  -> analytics summary
  -> optional AI narration

## Current Major Limitations

- No real broker integration
- Historical backtesting framework exists but still needs real multi-day datasets and wider validation
- Replay still often uses limited recent candle windows in live scanner mode
- Historical option quote replay is not implemented yet
- Real-time diagnostics must pass before using scanner output for manual real-trade decisions
- No asynchronous market stream processing

## Fresh Chat Bootstrap

When starting a fresh GPT session:

1. Upload this PROJECT_STATE.md file
2. Upload only the files relevant to the current task
3. Clearly state:
   - current objective
   - current blockers
   - whether mock mode is enabled
4. Avoid uploading the full workspace unless necessary

## Recent Major Changes

2026-07-11
- Entered calibration phase after the initial six-day review. Avoid broad strategy/indicator changes until more paper-session evidence is collected.
- Fixed opening range breakout/breakdown calculation to use the regular-session 09:30-10:00 ET range instead of premarket-contaminated first dataframe rows.
- Added daily candidate persistence so repeated symbol/direction/setup candidates carry first/last seen timestamps, scan count, current/best score, score delta, and strengthening status.
- Category-based scoring is retained as a shadow diagnostic for future comparison, while the legacy score remains the active decision score.
- Added measurement-only analytics modules for market coverage, missed opportunity attribution, entry delay, and daily trading scorecards. The dashboard renders these through `app/dashboard_components/market_coverage.py` without changing scanner decisions.
- Added `opportunity_funnel.py`, `engine_health.py`, `market_leaderboard.py`, `engineering_recommendation.py`, and `strategy_journal.py` so daily review can show where symbols drop out, whether runtime/quote health affected the day, which movers mattered most, what engineering focus is suggested, and how strategy metrics evolve over time.
- Added shared dashboard KPI card styling through `app/ui/components.py::kpi_card()` and global `.metric-card` CSS so scorecard, coverage, engine-health, entry-delay, and validation-summary metrics use consistent compact cards.
- Production-engineering status: candidate persistence is implemented; scanner market-data prefetch now uses bounded `ThreadPoolExecutor` through `SCANNER_MAX_WORKERS`; engine health writes `engine_health_history.csv` with runtime/workers/symbol/failure/health-score fields plus Polygon cache/API timing and background queue metrics; scanner profiling writes `scanner_stage_profile.csv` with categorized foreground and deferred persistence stage timings; validation freeze is documented but not code-enforced; graduation criteria are not implemented.
- Refined entry detection: breakout uses the prior 10-bar resistance level, EMA pullback uses an ATR-aware near-touch, and breakdown requires body strength plus relative-volume confirmation.
- Added an early weak-exit guard: during the first few bars, near-flat EMA/VWAP/MACD/failed-breakout exits can hold when the trend remains intact. Hard stops, targets, and risk exits still take priority.
- Added paper-mode option selection so validation can keep the best-quality primary contract active while real readiness remains affordability-gated.
- Added `data/daily/YYYY-MM-DD/market_opportunity_audit.csv` after each scan for watchlist-level opportunity attribution: symbol, score, shadow category score, setup, action, blocked reason, top candidate, persistence metrics, move %, and replay outcome.
- Fixed replay calibration ignored-stop observability, standardized risk-manager error diagnostics on `reasons`, added explicit RR rejection reasons, made entry metadata access defensive, and added ATR-floor adjustment diagnostics without changing ATR/risk thresholds.
- Marked `app/trade_manager.py` as legacy. `app/exit/exit_engine.py::evaluate_exit()` is the live exit source of truth and now returns explicit exit priority diagnostics: primary exit, secondary exits, all exit reasons, and ignored exit signals.

2026-07-09
- Added invalid fresh-entry filtering in the dashboard so `ACTIVE_TRADE`, `PAPER_TRADE`, `OPEN_TRADE`, `NO_ENTRY`, `NO_SETUP`, and blank/null entry states cannot appear as new suggested-trade or paper-entry candidates.
- Separated affordability behavior by workflow: suggested-trade lifecycle and paper validation can ignore affordability by default for research visibility, while real-trade readiness requires affordability by default through `REAL_REQUIRE_AFFORDABILITY=true`.
- Paper validation entries that bypass affordability are tagged with `Paper Affordability Override` plus original affordability status/cost fields in scanner context.
- Auto-paper decision logs now include affordability override fields, and shadow gate diagnostics use the paper-validation affordability row so expensive validation candidates do not show false gate failures.
- Dashboard now includes a Paper Validation Performance section with today/overall closed trade count, win/loss rate, total R, average R, estimated dollar P/L, and an expandable closed-trade detail table sourced from daily/root paper trade events and paper trade state files.
- Added a narrow paper-only high-quality index/ETF review-validation exception for SPY/QQQ `REVIEW_TV_CHART` rows. The exception is limited to safe setup types, live option quotes, strong setup/RR/option-quality thresholds, tight spread/quote-age/scans requirements, no late-chase/missed-move flags, no event/regime blocks, and valid price geometry. It does not loosen non-top-candidate gates for single names such as SMCI.
- Dashboard manual and auto paper-entry promotion now passes full suggestion identity: symbol, direction, setup type, option ticker, opened timestamp, and paper trade key.
- Suggested trade promotion now marks matched records as `PROMOTED_TO_PAPER`, stores `promoted_at` and `paper_trade_key`, clears stale expiry/realtime block reasons, and protects both legacy `ENTERED_PAPER` and new `PROMOTED_TO_PAPER` statuses from later expiry/cleanup.
- Paper trade opens now use America/New_York timestamps for the backward-compatible `opened_at` trade key text and also store explicit `opened_at_et` and `opened_at_utc` ISO timestamps to avoid ET/UTC ambiguity.
- Daily validation reports now include a Missed Opportunity Replay section. Expired suggestions are classified with available daily candle files as `MISSED_WINNER_TARGET_FIRST`, `CORRECT_SKIP_STOP_FIRST`, `AMBIGUOUS_SAME_CANDLE`, `INCONCLUSIVE_NO_TARGET_OR_STOP`, or data/level/schema fallback statuses. Promoted/opened suggestions are shown as `PROMOTED_TO_PAPER` instead of fake missed opportunities.
- Scanner runs now append normalized 5-minute candle rows to `data/daily/YYYY-MM-DD/candles_5m.csv` so daily missed-opportunity replay has local high/low data to classify expired suggestions.
- Added optional `INDEX_REVIEW_MIN_SETUP`, `INDEX_REVIEW_MIN_RR`, `INDEX_REVIEW_MIN_OPTION_QUALITY`, `INDEX_REVIEW_MAX_SPREAD_PCT`, `INDEX_REVIEW_MAX_QUOTE_AGE_MINUTES`, and `INDEX_REVIEW_MIN_SCANS` settings to `.env.example` and README Streamlit secrets examples.
- Hardened promoted suggestion lifecycle so `upsert_suggestion_from_scan()` preserves existing `PROMOTED_TO_PAPER` / legacy `ENTERED_PAPER` records when the same scanner row appears again.
- Paper trade close timestamps and paper event log timestamps now use America/New_York time and store explicit ET/UTC ISO fields, matching open-trade timestamp behavior.
- Daily validation data-quality checks now count actual opened trades from unique `OPEN` paper events instead of all event rows, so an `OPEN` plus `AUTO_EXIT` pair counts as one opened trade.
- Quote freshness summaries now filter to option-bearing decision rows before counting missing/live/stale quote states, avoiding false missing-quote noise from rows with no option contract.

2026-07-01
- Added optional Neon/Postgres persistence with SQLAlchemy for Telegram alert attempts, paper trade opens/closes, scanner run summaries, and gate decision summaries. DB writes are best-effort and gated by `DB_WRITE_ENABLED=true` plus `DATABASE_URL`, preserving JSON/CSV/Excel fallback behavior for Streamlit.
- Added hard CALL/PUT price-geometry validation and regression tests so reversed PUT stop/target structures are blocked before suggestion display or paper/alert entry.
- Added a final risk-manager geometry invariant and daily report data-quality counters for invalid geometry, option mismatch, stale setup-threshold reasons, and missing realtime-ready explanations.
- Added normalized candidate snapshot persistence for every scanner row under `data/candidate_snapshots/`, with parquet preferred and CSV fallback.
- Added replay calibration utilities for MFE/MAE, bars to target/stop, ATR stop/target multiple grids, time-exit horizons, and win rate by horizon.
- Extended expectancy analytics with reusable grouped tables covering trade count, win rate, average/median/total R, profit factor, average win/loss R, max drawdown R, and expectancy R.
- Added grouped expectancy report generation with simple `KEEP`, `REVIEW`, `WATCH`, and `BLOCK/TIGHTEN` verdicts.
- Added initial stock-underlying no-lookahead backtesting package with historical dataset loading, scanner-at-time evaluation, backtest runner, and report output.
- Added `tools/daily_validation_report.py` for daily paper-session review, including trade result scorecard, gate quality summary, skipped opportunities, replay scorecard, rolling expectancy tables, rule-change suggestions, and optional source-file archiving.
- Added Validation Data Health counts to the daily report and append-only `paper_trade_events.csv` logging for paper opens/closes.
- Added a Streamlit sidebar daily-validation report button and HTML download path for cloud/local dashboard workflows.
- Added profile-driven option affordability controls with `OPTION_AFFORDABILITY_MODE` and `OPTION_CAPITAL_PROFILE`.
- Added `SMALL_ACCOUNT`, `GROWTH_ACCOUNT`, and `BEST_QUALITY` capital presets.
- Scanner now keeps the best-quality option contract visible while selecting an affordable `active` contract when available.
- Added contract cost, risk-at-stop, current capital, max allowed contract cost, affordability status, and best-quality/affordable alternate fields to scanner output and dashboard context.
- `HARD` affordability mode blocks expensive contracts from actionable statuses and marks high-quality expensive setups as `QUALITY_BUT_TOO_EXPENSIVE`.
- Dashboard suggested-trade sync, Paper Trade Setup, and paper-validation gates originally required `Affordable=True`; this was later split so research/paper validation can include expensive technical setups with explicit override tagging while real readiness remains affordability-gated.
- Added opt-in Telegram entry, exit, and partial-profit alerts for high-conviction actionable/reviewable option setups and managed trade exits, with Streamlit Secrets/env credential loading and JSON duplicate protection.

2026-06-30
- Added session-aware scanner action gating: premarket candidates surface as `PREMARKET_WATCH`, 9:30-9:45 ET candidates wait as `OPENING_RANGE_CONFIRMATION`, and `ENTER`/`ENTER_PAPER` is only allowed after 9:45 ET when all gates pass.
- Fixed stock aggregate freshness so Polygon/Massive aggregate bucket-start timestamps do not incorrectly trigger `STALE_STOCK_DATA` for current 5-minute candles.
- Added `market_session`, `raw_delay_minutes`, and `aggregate_interval_minutes` to stock data status calculations for clearer diagnostics.
- Added focused regression tests in `tests/test_market_session_decisions.py` covering premarket freshness, watch-only premarket labels, opening-range hold, and post-9:45 entry eligibility.

2026-06-15
- Added real-time data validation mode: stock aggregate freshness gates, option bid/ask and quote freshness gates, quote source/timeframe/timestamp fields, `ENTER_PAPER` status for real-time-ready paper candidates, and `tools/diag_realtime_entitlements.py`.
- Updated option contract selection preference to favor 14-30 DTE, keep 10-13 and 31-45 DTE as fallbacks, heavily penalize 2-6 DTE, de-prioritize 46+ DTE, and keep 0DTE/1DTE blocked.
- Fixed Streamlit key loading so secrets override stale/blank env values before scanner import; Polygon aggregate calls now read the API key dynamically instead of relying only on an import-time cached value.
- Added dashboard sidebar runtime key status for Polygon and app AI key presence without exposing key values.
- Disabled automatic scanner AI summaries unless `SCANNER_AI_SUMMARY_ENABLED=true`, preserving rules-only refreshes during market hours.
- Added gated on-demand dashboard AI explanations for top candidates only, with app-key support via `OPENAI_API_KEY_APP`, small-model configuration, max-token cap, and local cache.
- Set local `.env` to `ENABLE_AI_SUMMARY=false` and changed settings loading so `.env` overrides stale process variables.
- Added context-rich paper trade telemetry at close, including setup grade/score, planned RR, relative strength, market/sector regime, breadth, option quality/freshness, blockers, and realized outcome/R multiple.
- Added dashboard export/download buttons for scanner output, telemetry CSV, paper trade state JSON, and live trade state JSON.
- Added paper-close telemetry validation warnings for missing end-of-day review fields.
- Dashboard paper trades opened after this change store scanner context at entry. Older open paper trades can attach close-time scanner context when closed.
- Added paper-only automation controls in Streamlit: auto paper entries OFF by default, auto exits ON by default, max daily entries, setup/RR thresholds, direction filter, end-of-day close, and configurable profit R threshold.
- Auto paper minimum RR default lowered to 1.8 for sample collection, while keeping top-candidate, setup %, blocker, quote, quality, expiration, duplicate, active-trade, direction, and daily-limit gates intact.
- Auto paper entry gates enforce market hours, 9:45-15:30 ET entry window, top candidate status, setup/RR thresholds, allowed action status, event/regime/stale-data blocks, fresh option quote, option quality, spread, expiration bucket, duplicate prevention, active trade caps, per-direction cap, and daily limits.
- Auto paper entries and Telegram alerts now use the shared `app/gates/entry_gate.py` gate for actionable status, RR, setup score, option quality, quote freshness, affordability, spread, and stricter range-bound/weak-breadth thresholds. Auto paper can allow missing option spread only when option quality is strong (`Option Quality Score >= 80`), because Polygon may omit bid/ask. Telegram/real alert modes block unknown spread. Known auto-paper spreads above 10% still block, range-bound days tighten to setup >= 90, RR >= 2.0, and spread <= 5%, and real-money execution remains manual with broker quote confirmation.
- Paper trade state is keyed by unique trade keys in the form `symbol|option_ticker|opened_at` instead of symbol alone. Helper lookup still prevents duplicate open symbols, but closed same-symbol history is preserved for telemetry and review.
- Auto paper applies post-exit symbol cooldown through `AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES` and per-symbol daily caps through `MAX_TRADES_PER_SYMBOL_PER_DAY` to reduce churn in names that repeatedly enter/exit intraday. Current validation examples use `AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES=45` and `MAX_TRADES_PER_SYMBOL_PER_DAY=2`; stricter customer-style alerting should use 60 and 1.
- Added `app/state/auto_paper_decision_log.json` and dashboard display to show each auto-entry decision as `SKIPPED`, `BLOCKED`, or `OPENED` with the exact gate reason.
- Paper Trade Setup now only shows current valid scanner candidates with entry buttons. Stale/blocked/history rows are shown in a read-only Last Seen Candidates section with no entry controls.
- Added `app/state/suggested_trade_state.json` and `app/state/suggested_trade_manager.py` to persist suggested call/put lifecycle across dashboard refreshes.
- Dashboard wording separates review ideas, eligible paper candidates, and actual opened trades. Main sections are ordered as System Status, Market Health, Top Scanner Opportunities, New Suggested Calls / Puts - Review Only, Still Valid Suggested Trades, Paper Trade Setup - Eligible Candidates, Active Paper Trades - Actually Opened, Exit Now Alerts, Auto Paper Decision Summary, Validation Data Health, Daily Validation Report, and Telemetry & Debug.
- Dashboard refresh cadence is separated from full scanner cadence: dashboard can refresh every 1 minute while full scanner defaults to every 5 minutes.
- Scanner output now includes lifecycle placeholder columns: Suggestion Status, Suggestion First Seen, Suggestion Last Seen, Suggestion Age Minutes, Still Valid, Invalidation Reason, Exit Status, and Exit Reason Live.
- Auto paper exits can close on stop, target, live exit signal/reason, profit R threshold, or end-of-day close. Real trading remains manual only.
- Hardened JSON state writes for Streamlit Cloud/local filesystems by writing temp files in the target directory, creating parents, using atomic replace, and falling back to direct write if replace fails.
- Telemetry summaries now tolerate empty CSVs and schemas without projection-only columns such as `trade_grade`.
- Active Trades now calculates live R progress from entry/current/stop for paper trades and tracked trades, instead of showing stale stored zero progress.
- Fixed dashboard telemetry loading so an existing telemetry file cannot return `None` and crash the Telemetry section.
- Made telemetry CSV writing robust when `telemetry/trade_telemetry.csv` exists but is empty.
- Made Market Health and Active Trades dataframe display safer for Streamlit/Arrow mixed-type serialization and replaced deprecated `use_container_width` usage with `width="stretch"`.
- Dashboard Trade Opportunities and Paper Trade Setup now show recommended option contract details directly: option ticker, strike, expiration date, expiration bucket/risk, moneyness, mid price, spread %, quality grade, and quote freshness.
- Scanner output and dashboard now include alternate option contracts when available: Short DTE alternate from 2-13 DTE and Longer DTE alternate from 31-45 DTE, while preserving 14-30 DTE as the primary recommendation.

2026-06-13
- Expanded active watchlist to 16 liquid option names: QQQ, SPY, NVDA, AAPL, MSFT, AMZN, META, TSLA, AMD, AVGO, MU, PLTR, NFLX, CRWD, SMCI, SPCX, SMH, ARM, TSM, INTC, AMAT, LRCX, MRVL, ORCL, PANW, SOXL.
- Added relative strength ranking fields: `Symbol Move %`, `RS vs QQQ`, `RS vs SPY`, `RS Rank Score`, `Premarket Gap %`, `Relative Volume`, `ATR %`, `Bullish Rank`, `Bearish Rank`, and `Top Candidate`.
- Added non-trade references SMH, SOXX, XLK, and VIX/VIXY proxy for regime, sector, breadth, and volatility context.
- Added explicit regime labels and setup-family regime blockers.
- Added watchlist breadth proxy, above VWAP %, above EMA20 %, and top 5 strongest/weakest leaderboards.
- Dashboard Trade Opportunities now sorts top candidates first and displays RS/ranking context.
- Scanner now writes stale/no-data/error rows to Excel so the dashboard remains readable even when Polygon returns stale weekend data.
- Fixed replay direction normalization so `HIGH CONVICTION BULLISH` and `HIGH CONVICTION BEARISH` replay as bullish/bearish directions.
- Added startup readiness checks for mock-mode safety, Polygon key presence, scanner output writability, 0DTE/1DTE disabled state, and max spread sanity.
- Repaired `tools/diag_fetch.py` so it fetches data before calling `compute_indicators()`.
- Dashboard Trade Opportunities now directly shows `Market Data Delay Minutes`, `Option Quote Freshness`, `Option Quote Age Minutes`, `Action Status`, `Blocked By`, `Event Blocked`, and `Regime Blocked`.
- Dashboard sidebar now supports auto refresh with 1, 5, and 15 minute intervals. Market-hours default is ON at 5 minutes; after-hours default is OFF. The scanner auto-runs only when the Excel output is missing or older than the selected interval.
- Added option quote freshness, quality score, liquidity grade, expiration bucket/risk, delayed/stale quote blocking, 0DTE/1DTE blocking, and configurable option gate thresholds.
- Added manual event blocker settings and event-risk report fields.
- Verified scanner run on 2026-06-13: all 15 symbols correctly reported `STALE DATA` / `AVOID` because Polygon returned 2026-06-12 data on Saturday.

2026-05-24
- Added replay engine
- Added telemetry analytics
- Added replay outcome tracking
- Added expectancy analytics foundation
- Added PROJECT_STATE governance

## Next Session Validation Focus

Do not add more indicators or feature gates until the new state/gating behavior is observed over another full paper session. Review:

1. Whether `paper_trade_state.json` preserves multiple closed trades for the same symbol instead of overwriting by symbol.
2. Whether `auto_paper_decision_log.json` clearly separates `SKIPPED`, `BLOCKED`, and `OPENED` decisions with exact gate/state reasons.
3. Whether `RANGE_BOUND` and weak-breadth threshold tightening meaningfully reduces low-quality/choppy-day trade count.
4. Whether Telegram entry alerts are fewer and higher quality after the 85 score, 2.0 RR, 8% spread, active-alert, and symbol-cooldown changes.
5. Whether any valid A+ setups are over-blocked by RR, spread, unknown-spread, duplicate-symbol, per-symbol daily cap, or cooldown rules.

## Current Live Risks

The system should not yet be trusted for fully automated live trading because:

- replay calibration is newly implemented and not yet validated over enough samples
- expectancy reporting is newly implemented and needs larger live/backtest sample sizes
- no broker integration safeguards exist
- production backtesting validation is not complete; the current framework is stock-underlying first and needs multi-day datasets plus option P/L modeling
- Polygon data is delayed and must be confirmed against live broker/TradingView data before any real trade

## Production Readiness Checklist

Before live deployment:

- Disable mock market data
- Confirm delayed-data warnings and option quote gates are visible in dashboard/report
- Keep 0DTE/1DTE disabled unless deliberately testing them
- Add event dates before market open
- Validate Polygon cache behavior
- Add automated replay tests
- Add automated replay calibration and backtest tests
- Add risk kill-switch
- Add config validation
- Validate expectancy over large sample size
- Run 5-day, 20-day, 60-day, then 6-12 month no-lookahead backtests
- Add option P/L approximation or historical option quote replay
- Add persistent DB telemetry storage

## 2026-07-29 Ownership, Measurement, And Correctness Changes

### Single ownership of the trade lifecycle

The dashboard is now read-only with respect to trade state. It previously ran
`run_auto_paper_entries` / `run_auto_paper_exits` from a page render, using a possibly
hours-old `scanner_output.xlsx`, with a second exit rule set (`_auto_exit_reason`)
that bypassed the exit engine and a `profit_r` control that closed at +1R whatever
the engine intended. That path only executed on the Trading and Developer pages, so
lifecycle work depended on which page happened to be open.

- Removed the automation block from `app/dashboard.py::main()`, plus ~465 lines of
  dead duplicate automation (`_run_auto_paper_entries`, `_run_auto_paper_exits`,
  `_legacy_auto_exit_reason`, `_render_paper_trade_controls`,
  `_is_swing_hold_eligible`, `_paper_automation_active`).
- `run_auto_paper_exits` and `_auto_exit_reason` are deleted. `eod_force_close_reason()`
  owns the single end-of-day holding-policy decision, and the unused third EOD path
  `apply_end_of_day_policy` is gone.
- New `app/runtime/paper_position_lifecycle.py` holds the scanner-owned lifecycle work
  that has no home in the per-symbol loop: session restore/archival, one-per-session
  `POSITION CONTINUES` alerts, holding-policy EOD force close, visibility for open
  positions the scan could not manage, and `sync_scan_suggestions()`. It contains no
  stop/target/profit-R rules by design.
- The `Auto Exit (During Market Hours)` toggle and `Auto Profit Exit R` input are
  removed from the sidebar and from `load_auto_paper_controls()`.
- `_exit_now_alerts` reads the exit engine's persisted `Live Exit Signal` /
  `Live Exit Reason` instead of re-deriving exits.
- The dashboard still *triggers* scans; that is delegation to the owner, not a
  competing decision.

### R is measured against the risk frozen at entry

`_calculate_rr_progress` derived risk from the *current* stop. When the engine moved a
stop to breakeven at +1R, `abs(entry - stop)` became 0, so `rr_progress` and `mfe_r`
collapsed to 0 permanently and never recovered. That silently disabled partial profit,
the ATR trailing stop, and multiday profit protection, all gated on R thresholds. The
2026-07-29 NVDA trade reached roughly +1.4R, lost all upside management, and closed
flat with `r_multiple` NULL and trend capture -36.21%.

- `resolve_risk_per_share()` is the single R denominator. `initial_stop_loss` and
  `initial_risk_per_share` are persisted at open and backfilled before any stop move.
- `evaluate_exit()` returns `mfe_r` and `risk_per_share`, so V1's own R is observable
  instead of borrowed from the V2 shadow.
- The active-trade direction fallback uses the entry stop; the current stop would
  infer LONG for a short whose stop had moved to breakeven.

### Data freshness is interval-aware

`delay_minutes` counts from the last candle's close, so it cycles from 0 to one full
interval as the next candle forms. A fixed 2-minute allowance against 5-minute candles
is unsatisfiable for 3 of every 5 minutes. On 2026-07-29 that produced 1028
`REALTIME_STOCK_DATA_REQUIRED` blocks (the single largest reason) and 277
`STALE_STOCK_DATA` actions, mostly clock-phase artifacts rather than a stalled feed.

`stock_data_delay_allowance()` returns the larger of `MAX_STOCK_DATA_DELAY_MINUTES` and
the candle interval, and **all three** gates use it, so the freshness label and the
blocking gates can no longer disagree. A genuinely missing bar is still `STALE`.

### Audit and observability invariants

- `audit_unrecorded_entry_recommendations()` guarantees every `ENTRY_RECOMMENDED`
  candidate carries an execution verdict; an unreached gate records
  `NO_GATE_VERDICT_RECORDED` instead of leaving the miss undiagnosable.
- Operational rule groups (`Telegram`, `Paper`, `Review`, `Trade Lifecycle`, `Replay`)
  can never report `blocked_trade=True`. The Telegram rule previously claimed to have
  blocked all 884 rows, including the one trade that opened. `resolve_blocked_trade()`
  derives it from `rule_domain()` so it cannot regress.
- `_add_holding_profiles()` stamps `INTRADAY`/`MULTIDAY` on every scanner row and
  `candidate_evidence.holding_profile` is populated. It was NULL on all 295 rows for
  2026-07-29, making intraday-versus-multiday funnel analysis impossible.
- `upsert_paper_trade` writes the migration `012` lifecycle columns. They were never
  written, so `holding_profile` kept its `DEFAULT 'INTRADAY'` while the payload held
  the real value.
- `tools/reconcile_learning_memory.py --ledgers` compares `paper_trades` (live state
  mirror) against `trade` (completed-trade facts). They must agree; on 2026-07-29
  eleven completed facts had no live-state row. The tool now also loads `.env`, which
  its database checks always required and never did.

### The scanner schedules itself

`python -m app.main` is single-shot and nothing else looped, so repeated scanning
depended on Streamlit auto-refresh. On 2026-07-29 only 32 scans were archived between
08:01 and 17:09 ET, including an 86-minute blind hole from 11:09 to 12:35, roughly 38%
of a 5-minute cadence.

`app/runtime/scan_loop.py` owns cadence only and makes no trading decision:

```powershell
python -m app.runtime.scan_loop                 # session-aware cadence
python -m app.runtime.scan_loop --interval 300  # fixed 5-minute cadence
python -m app.runtime.scan_loop --skip-closed   # idle while the session is CLOSED
```

Cadence: `OPENING_RANGE` 120s, `REGULAR` 300s, `PREMARKET` 1800s, `AFTERHOURS` 1800s,
`CLOSED` 3600s. A failing scan is logged and the loop continues; SIGINT/SIGTERM finish
the current scan and exit.

The three sparse values are set by the Neon bill rather than by the market. None of
them scans -- `idle_reason` stops scanning 20 minutes after the bell -- so each pass
only writes a heartbeat, and wakes the compute for the full 300s suspend timer to do
it. Measured over 2026-08-08..13 the worker burned 9.65 compute-hours a day, 2.7 of
them in AFTERHOURS and CLOSED.

### Stop anchoring is measurable but NOT adopted

`calculate_risk(..., stop_anchor="SWING")` defaults to current behaviour. `"STRUCTURE"`
anchors breakout/breakdown stops to local structure with the 0.25x ATR floor, the
treatment `EMA_PULLBACK` already receives. Motivation: on 2026-07-29 `BREAKDOWN_SHORT`
averaged RR 1.13 with 6 of 64 clearing the 1.5 floor and `BREAKOUT` averaged 1.33,
because both anchor to `recent_high`/`recent_low` at the far end of the swing just
travelled; the three local-structure setups averaged 1.52 to 3.05. Raw logs show the
ATR floor cutting RR from 4.89 to 1.8 in 35 rows.

`tools/regression_ab.py --all` runs both arms over archived days, recomputing
indicators, entry, and risk from `scanner_snapshot.market_payload` so both arms
exercise the real `calculate_risk`. It is read-only and compares the arms to each
other, not to the frozen baseline, which was built with the replay evaluator and would
measure the evaluator swap instead of the code change.

**Not adopted.** The 2026-07-29 result was +6.03R but average R was identical (1.26 vs
1.26), win rate -0.5pt, and profit factor 5.21 to 5.13: more trades of the same
quality, not better trades. Adopt only if average R or profit factor improves across
multiple days and regimes. The reconstruction's exit model is crude (scan-time price
against target/stop between snapshots 5-15 minutes apart, full planned RR on a win and
exactly -1.0 on a loss, target checked before stop), so treat its absolute numbers as a
relative instrument only, never as P/L.

### Test isolation

`python -m unittest discover -t . -s tests` is now required. Only the
package-importing form runs `tests/__init__.py`, which redirects `DRAVYA_DATA_DIR` and
`DRAVYA_STATE_DIR` to a sandbox. Bare `discover tests` imports test modules top-level,
skips the bootstrap, and writes into the real `data/` and `app/state/` artifacts.
`pytest tests` is isolated either way via `tests/conftest.py`.