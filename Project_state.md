# Project State

Last updated: 2026-07-12

## Project Purpose

This workspace is a Python-based intraday algo/options trading scanner and research sandbox. It scans a configured stock watchlist, fetches or falls back to market aggregate data, computes technical indicators across multiple timeframes, scores momentum setups, evaluates entries and risk, ranks options contracts, projects trade outcomes, manages open trade state, writes scan telemetry, stores candidate snapshots, and provides early replay calibration / expectancy / no-lookahead backtesting utilities for later analysis.

The current v2 intraday options watchlist is defined in `app/config/watchlist.py`. By default the scanner uses the static `WATCHLIST`; when `DYNAMIC_WATCHLIST_ENABLED=true`, `get_scanner_watchlist()` keeps the core symbols first, then merges Polygon snapshot movers and the static fallback list up to `DYNAMIC_WATCHLIST_SIZE`.

- QQQ
- SPY
- NVDA
- AAPL
- MSFT
- AMZN
- META
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
- INTC
- AMAT
- LRCX
- MRVL
- ORCL
- PANW
- SOXL

Non-trade market reference symbols are also defined in `app/config/watchlist.py`:

- SMH
- SOXX
- XLK
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

### Trading Workstation UI

- The Streamlit dashboard uses sidebar navigation: `Trading`, `Validation`, `Replay`, `Reports`, and `Developer`.
- The default `Trading` page renders a command center, current opportunities, cache-only Today's Performance KPIs, why-no-trade summary, and missed opportunities from `dashboard_state.json`. Today's Performance reads `today_performance`, including `last_completed_trade`, and never calculates trend capture, TES, or trade aggregates during a page refresh. Before the first completed trade it renders `No completed trades yet` rather than zero-valued post-trade KPIs.
- The sidebar keeps only trader-facing controls visible by default: auto refresh, paper automation, compact downloads, daily validation/replay actions, and navigation. Raw exports are hidden under `Downloads > Advanced`; runtime key status is no longer rendered.
- `Post Market: Generate Everything` runs daily validation report generation and offline replay generation, then refreshes the dashboard.
- `Trading`, `Replay`, and `Developer` pages share the same metadata-card pattern: scan id/data version, scan timing, refresh age, symbol count, and freshness status.
- Scanner rows persist `Scan ID` and `Data Version`; `dashboard_state.json` also carries `scan_id` and `data_version` for end-to-end traceability.
- `dashboard_state.json` is written under `data/live/dashboard_state.json` and `data/daily/YYYY-MM-DD/dashboard_state.json` whenever scanner outputs are written.
- The `Developer` page keeps legacy diagnostic panels available without putting them above the fold during live trading.

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
- Dashboard page entry points now live under `app/ui/pages/`: Trading, Validation, Replay, Reports, and Developer. The page modules own concrete render bodies and import shared helpers from `dashboard.py` while the helper migration continues.
- Live JSON state loading uses page-specific cache profiles: Trading `dashboard_state.json` uses a 5-second TTL, Validation uses 60 seconds, Developer runtime state uses 120 seconds, and Replay/Reports cached states invalidate from file modified time.
- `app/config/performance.py` defines the current Performance Mode defaults and env overrides. Trading lazy imports/background cache generation/dashboard-state-only behavior is enabled by default, with TTL overrides through `PERFORMANCE_TRADING_CACHE_TTL`, `PERFORMANCE_VALIDATION_CACHE_TTL`, and `PERFORMANCE_DEVELOPER_CACHE_TTL`.
- `app/runtime/telegram_dispatcher.py` is the Telegram dispatcher facade. Default `TELEGRAM_DISPATCH_MODE=DIRECT` preserves synchronous send behavior; optional `QUEUED` submits a non-cancelable critical `RuntimeJob`. In queued mode, alert sent-state persistence is attached as an after-success callback, so alerts are not marked sent before the Telegram send succeeds. The dispatcher writes replayable queued-message records to `data/live/telegram_dispatch_queue.jsonl` and attempt/sent/failed audit rows to `data/live/telegram_dispatch_audit.jsonl`; `recover_pending_telegram_dispatches()` can resubmit queued records with no successful audit event.
- `run_scanner()` now queues `summarize_telemetry()` as a low-priority runtime job after console table output, trimming noncritical foreground scanner tail work.
- Market opportunity audit, option liquidity audit, and candidate funnel file writes moved into `_persist_scan_outputs()` under `RuntimeScheduler`. Foreground scanner execution still computes rows/rankings/health/Telegram summary and prints the funnel, but noncritical audit file I/O is deferred.
- Scanner finalization is now a high-priority non-cancelable `RuntimeJob` named `finalize_scan_outputs`. Foreground scanner execution queues finalization after raw rows are collected and returns. Finalization performs operator table rendering, candidate persistence/ranking, health calculation, Telegram dispatch, funnel calculation, persistence, and cache job scheduling.
- Dashboard paper automation orchestration now lives in `app/runtime/paper_automation.py`, with helper logic in `app/runtime/paper_automation_support.py`. The runtime paper automation path no longer imports `app.dashboard`; dashboard only calls the runtime entry points.
- After `df_results` and the health payload are built, `run_scanner()` queues `_persist_scan_outputs()` through `RuntimeScheduler` as a high-priority `RuntimeJob`. That job writes dashboard state, engine health history, candidate snapshots, signal lifecycle rows, scanner output files, and scanner stage profiles. It then queues a separate normal-priority `persist_scan_artifacts_db` job, so DB work cannot delay file-backed persistence.
- Telegram alert audit DB writes and paper-trade DB upserts also use `run_background()`, so slow or failed Neon writes should not block Telegram sending, paper state JSON/CSV updates, or scanner output.
- `persist_scan_artifacts_db` promotes candidate snapshots, structured rule evaluations, and existing gate-decision summaries as best-effort database batches. `build_entry_gate_rule_evaluations()` emits native entry-gate RuleEvaluation objects; available Telegram, Paper, and Review scanner fields are normalized into that same batch.
- `app/storage/signal_lifecycle_store.py::record_signal_lifecycle_events_for_scan()` batches lifecycle event and transition CSV appends per completed scan.
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

- `app/decision/decision_engine.py` is the shared decision adapter for scanner-style candidate rows. `evaluate_candidate()` returns a `TradeDecision` with `action`, `setup_score`, `rr`, `option_quality`, `confidence`, `reasons`, and `block_reasons`.
- Telegram entry alerts consume the shared decision instead of duplicating all dashboard/paper/real-review gates inline. The default policy is operational notification, not real-money approval.
- `TELEGRAM_ALERT_POLICY=PAPER` sends eligible scanner/paper decisions when the action is alertable, the entry gate passes, the alert score meets `TELEGRAM_MIN_ENTRY_ALERT_SCORE`, and notification limits/cooldowns allow it. This is the recommended validation mode so Telegram follows paper/reviewable decisions instead of reapplying the ultra-strict high-conviction real-review gate.
- `TELEGRAM_ALERT_POLICY=REAL_REVIEW` keeps strict A+ gating for real-review style alerts: real setup/RR/option-quality thresholds, top-1 candidate, minimum consecutive scans, and a required `HIGH CONVICTION BULLISH` / `HIGH CONVICTION BEARISH` signal label.
- `TELEGRAM_ALERT_POLICY=CUSTOM` keeps explicit Telegram threshold behavior using settings such as `TELEGRAM_MIN_RR`, `TELEGRAM_MIN_OPTION_QUALITY_SCORE`, and `TELEGRAM_MAX_SPREAD_PCT`.
- Cooldowns, duplicate alert protection, daily caps, active-alert caps, and time-of-day alert windows remain Telegram notification policy rather than decision logic.

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
- `app/options/options_recommender.py` returns a best-quality `primary` contract, a best affordable `affordable` contract when available, and an actionable `active` contract. `active` uses the affordable contract in `SOFT`/`HARD` modes when one exists; otherwise it falls back to the best-quality primary.
- Scanner option validation now starts from the bundle's `active` contract and falls back through `primary`, `affordable`, `short_dte`, `longer_dte`, and ranked contracts before setting an option-liquidity rejection. Duplicate tickers are skipped, so a fallback may surface as `ranked #2` when earlier ranked entries already matched `active` or `primary`.
- Fallback attempts are visible in runtime logs with `[LIQUIDITY FALLBACK] Try ...`, failure, and accepted messages. Scanner rows include `Option Liquidity Attempts` as JSON for the attempted source/ticker/code/reason/spread chain.
- `app/main.py` appends `option_liquidity_attempts.csv` daily rows for every liquidity attempt, including symbol, selected option ticker, attempt source/ticker/code/reason/spread, liquid flag, and accepted flag.
- `app/main.py` prints and appends a permanent `candidate_funnel.jsonl` summary with scanned, directional, entry-ready, risk-passed, option-selected, liquidity-passed, affordability-passed, `EMA_REJECTION_SHORT`, `ENTER_PAPER`, Telegram attempted/sent/blocked, and Telegram reasons. `EMA_REJECTION_SHORT_WARNING_THRESHOLD` defaults to `10` and prints a warning if the recent rejection window appears too permissive.
- Validation commands used for this slice: `d:/Dravya_Trade_Works/.venv/Scripts/python.exe -m unittest tests.test_option_liquidity_fallback` and `d:/Dravya_Trade_Works/.venv/Scripts/python.exe -m unittest discover tests`.
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
- Promoted immutable tables are `candidate_snapshot`, `rule_evaluation`, canonical completed `trade`, append-only `event_stream`, and `candidate_outcome`. Existing `alert_events`, `paper_trades`, `scanner_runs`, and `gate_decisions` remain for compatibility. Failed DB writes do not block Telegram, scanner output, paper-trade JSON/CSV state, validation reports, or Streamlit rendering.
- Entry facts are captured once at paper entry and exit facts once after exit snapshot/trend-capture artifacts; both are inserted as one immutable completed `trade` aggregate after close. `trade_timeline.jsonl` mirrors `CandidateCreated`, state/promotion/realtime/rule events, `EntryOpened`, and `ExitTriggered`. Candidate outcomes are generated post-validation from audit/replay data, making Telegram misses and false alerts objective derived facts; cached Validation renders outcomes, Telegram miss/false-alert counts, and delay attribution. Engineering recommendations are derived at report/Trade Doctor time and are not persisted as immutable facts.
- `app/db/migrations/001_promote_scanner_artifacts.sql` was applied successfully through the configured `DATABASE_DIRECT_URL`. The scanner never runs DDL automatically; apply the migration manually for any additional environment.
- DB idempotency should stay narrow and intentional. `alert_events` may use deterministic Telegram alert dedupe keys so failed send attempts and later successful retries can update the same audit row, but broad constraints such as symbol/day/contract-only uniqueness are unsafe for scanner rows, paper trade lifecycles, re-entries, and refreshed option observations. Additional unique indexes should be added only with an explicit Neon migration and duplicate scanner/alert/paper-trade tests.
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
- Telegram sends entry alerts for eligible actionable/reviewable scanner setups according to `TELEGRAM_ALERT_POLICY`; only `REAL_REVIEW` requires the `HIGH CONVICTION` signal label. It also sends dashboard paper-entry opens, full exit alerts for scanner-managed and paper-trade closes, and one-time partial-profit alerts when scanner trade management reaches the partial threshold.
- Auto paper-entry alerts fire at the moment `open_paper_trade()` succeeds in the dashboard auto-paper path. Manual paper entry buttons are hidden by default through `ENABLE_MANUAL_PAPER_ENTRIES=false` and `SHOW_MANUAL_PAPER_BUTTONS=false` so telemetry represents system-generated scanner/alert trades. Manual close/correction remains enabled by default through `ALLOW_MANUAL_PAPER_CLOSE=true`. With `TELEGRAM_ALERT_POLICY=PAPER`, auto paper alerts use the shared `ENTER_PAPER` decision plus `TELEGRAM_MIN_ENTRY_ALERT_SCORE` and notification controls such as cooldowns/caps. `TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE`, `TELEGRAM_MIN_RR`, `TELEGRAM_MIN_OPTION_QUALITY_SCORE`, and `TELEGRAM_MAX_SPREAD_PCT` remain for `CUSTOM` / `REAL_REVIEW` compatibility.
- Telegram exit alerts resolve the current underlying price from the freshest available same-symbol source in priority order: `latest_quote`, `df_5m_latest_close`, then `df_15m_latest_close`. They validate that resolved price against the same-symbol expected close before sending. If the mismatch exceeds `TELEGRAM_EXIT_PRICE_MISMATCH_PCT` default 3%, the alert is blocked as `UNDERLYING_PRICE_MISMATCH`.
- Telegram exit alerts are restricted to explicitly tracked `PAPER` or `REAL` trades. Scanner-managed `trade_state.json` entries use `trade_mode=SCANNER_TRACKED` and are dashboard/state only; they cannot send Telegram exits. Successful paper/real exit alerts set `exit_alert_sent` and use deterministic duplicate keys built from event type, symbol, option ticker, opened time, and exit reason.
- Telegram entry alerts are intentionally rate-limited, not strategy-redecided, in validation mode: defaults are `TELEGRAM_ALERT_POLICY=PAPER`, max 3 entry alerts per day, max 2 active alerted trades, 60-minute same-symbol/setup cooldown, 60-minute post-exit symbol cooldown, minimum alert score 85, and instant alert score 92. Entry alerts are dispatched after the full scanner dataframe is ranked, sorted by alert score, and attempted immediately in that scan. Time buckets are caps, not delays: max 2 regular alerts from 9:45-10:30 ET, max 1 regular alert from 10:30-13:30 ET, max 1 from 13:30-14:45 ET with a higher score threshold, and no new entries after 14:45 ET. A+ alerts at or above `TELEGRAM_INSTANT_ENTRY_ALERT_SCORE` bypass per-bucket caps but still respect daily max, active alerted trade cap, duplicate cooldown, symbol cooldown, and the no-late-entry cutoff. `REAL_REVIEW` and `CUSTOM` modes can still apply stricter RR/quality/spread/top-candidate gates when explicitly selected.
- Scanner entry-dispatch rows now record `Telegram Error Type`, `Telegram Error Reason`, and `Telegram Stage`. Successful/normal evaluations use `ENTRY_EVALUATION`; caught send failures use `ENTRY_DISPATCH` and an explicit `TELEGRAM_ERROR_<EXCEPTION_TYPE>` block reason. The dashboard exposes `data/live/telegram_dispatch_audit.jsonl` under Advanced downloads for audit review; these fields do not bypass policy or notification limits.
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