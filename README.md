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

## Historical Scanner Regression

Historical Scanner Regression (HSR) answers whether a strategy change would have produced better or worse **trade outcomes** for an archived trading day. It is separate from Replay and Backtesting and lives in `app/regression/`.

Set `REGRESSION_SNAPSHOT_ENABLED=true` to enable recording. When enabled, every completed scanner persistence cycle queues one immutable Neon `scanner_snapshot` row per `(scan_id, symbol)` after alerts and live state work complete. Each row keeps a computed `decision_payload` plus a bounded `market_payload` containing the last 200 5-minute, 80 15-minute, and 40 1-hour OHLCV bars. This is the durable HSR system of record. A matching immutable local developer/cache artifact is also written under:

```text
data/daily/YYYY-MM-DD/scanner_snapshots/HHMMSS_scan-id.parquet
```

CSV is used automatically if Parquet support is unavailable. The companion `manifest.json` records the scan IDs/timestamps, scanner version, non-secret entry/exit engine configuration, and the observed watchlist. Existing local snapshot files are never overwritten, but they are not required for regression when Neon is available.

Only a zero-failure completed scan is eligible for regression recording. The recorder is a separate normal-priority, best-effort runtime job; live alerts, paper state, dashboard state, and scanner completion never wait for Neon, hashing, or snapshot serialization. Per-symbol decision payloads are hashed, so an unchanged decision state is recorded as a dropped duplicate rather than a new durable snapshot. Developer > Regression Snapshot shows enabled state, queued/completed/dropped/failure totals, and average persist time.

At daily finalization, the system freezes the reconstructed baseline in Neon table `scanner_regression_baseline`, materializing a local cache at `data/regression/YYYY-MM-DD/baseline/baseline_trades.csv`. It falls back to completed paper-trade events when archive reconstruction is unavailable. A frozen baseline is never overwritten.

Run a read-only comparison with:

```powershell
python tools/regression_runner.py --date 2026-07-15
```

**When to enable it:** set `REGRESSION_SNAPSHOT_ENABLED=true` before the scanner session you want to study. Leave it `false` during ordinary sessions when you do not need a future regression archive. It takes effect on the next scanner process start, so restart the local scanner or Streamlit app after changing local `.env` or Cloud Secrets. Recording also requires `DB_WRITE_ENABLED=true` and a working `DATABASE_URL`.

**How to run it:** after the market session, finalize the day to freeze its baseline, then run HSR against that date:

```powershell
python tools/daily_validation_report.py --date 2026-07-15 --finalize
python tools/regression_runner.py --date 2026-07-15 --strategy-version v1.0.15
```

### Regression Page

The Streamlit sidebar includes **Regression** for the same workflow without a terminal. Choose a trading day and strategy-version label; the page shows durable archive availability, archived scan and snapshot-row counts, and frozen baseline version/time. When an archive exists but no baseline has been frozen, **Freeze Baseline** is enabled. It invokes the same immutable baseline workflow as terminal finalization and cannot overwrite an existing baseline. **Run Regression** is enabled only when both the archive and baseline exist.

After completion, the page shows baseline/current trade totals, win-rate change, net $R$, changed-trade count, and verdict. **Regression History** lists the latest versioned Neon runs with strategy, tested date, verdict, net $R$, and completion time. This page creates only new `regression_run` / `regression_result` records; it cannot modify paper trades, daily validation reports, or the frozen baseline.

**Post-market shortcut:** **Post Market: Generate Everything** always finalizes the selected trading day, generates Validation and Replay artifacts, and attempts to freeze its regression baseline. It reports whether a baseline was frozen or whether archive/evidence is not available yet.

HSR never calls Polygon, Telegram, OpenAI, runtime/background jobs, dashboard code, or paper-trade automation. It reads immutable snapshots and baselines from Neon first, falls back to local daily cache files when Neon is unavailable, reconstructs trades through a pure evaluator, and compares them at the trade level. When database writes are enabled it may append a new, isolated `regression_run` and `regression_result`; it never updates production trades, daily reports, baselines, validation, or learning records.

```text
data/regression/YYYY-MM-DD/
	regression_summary.json
	regression_trades.csv
	regression_report.html
```

Migrations `app/db/migrations/013_scanner_regression.sql`, `014_regression_symbol_snapshots.sql`, and `015_regression_snapshot_deduplication.sql` create the durable snapshot, baseline, versioned run, per-trade result, and payload-hash deduplication schema. Each HSR invocation records a new run when database writes are enabled; existing daily reports and baselines remain unchanged. The summary reports total/winning/losing trades, win rate, total and average $R$, profit factor, added/removed/changed trades, net $R$ impact, and a strategy-improvement verdict. Historical days that were scanned before this feature have no immutable archive, so HSR will report that the archive is unavailable rather than fabricate a result.

## Candidate Evidence Model

`app/analytics/candidate_evidence.py` materializes one daily row per stable candidate identity: `trading_day + symbol + direction + setup`. It consolidates repeated scan observations with suggestion status, paper-trade status, replay outcome, target/stop-first flags, winner/missed-winner labels, option quality, trend health, trend capture, Trade Efficiency Score (TES), quote freshness, and engineering root cause. Every finalized scan writes the local `candidate_evidence.csv` and `candidate_evidence_status.json`; the status records expected and written rows, repeated source observations collapsed into stable candidate identities, and database rows persisted. Postgres promotion is optional and its explicit status is recorded as `DISABLED`, `PERSISTED`, or `FAILED` rather than inferred from the local artifact.

Validation also builds a read-only rank-outcome calibration from trade timeline snapshots joined by immutable `trade_id`. It groups completed trades into rank buckets (`1`, `2-3`, `4-5`, `6+`) and reports win rate, average final $R$, Trend Capture, and MFE. Decision Waterfalls retain all failed rules and expose the first three trading blockers separately from `Telegram`, `Paper`, `Review`, lifecycle, and replay operational blockers. Rule-outcome attribution is a matched-cohort association report and is not presented as causal expected-$R$ attribution.

Each scan refreshes `data/daily/YYYY-MM-DD/candidate_evidence.parquet` when Parquet support is available and always writes `candidate_evidence.csv`. The same records upsert into Postgres table `candidate_evidence` through migrations `004_candidate_evidence.sql` and `006_candidate_intelligence_dimensions.sql`, with core analysis fields as columns and the full record in `payload` JSONB.

## Session-Aware Trade Lifecycle

Each candidate and paper trade has a `holding_profile`: `INTRADAY` or `MULTIDAY`. The profile is explicit when supplied by a candidate; otherwise the system derives it before the shared decision adapter runs from expected-hold intent or the eligible multi-day contract/setup profile. For the quality threshold, it uses `Setup %` and falls back to the scanner's `15m Score` when `Setup %` is absent.

`app/state/holding_policy.py` is the single source of session behavior:

| Profile | End of day | Next session | Telegram |
| --- | --- | --- | --- |
| `INTRADAY` | Force close when EOD automation is enabled | Does not restore | No continuation message |
| `MULTIDAY` | Remains open unless the exit engine triggers | Restores and refreshes holding duration | Sends one `POSITION CONTINUES` message |

Paper-trade state retains `trade_state`, `holding_profile`, `opened_at`, `closed_at`, `days_held`, `overnight_count`, `forced_eod_exit`, `session_id_open`, `session_id_current`, and `session_id_close`. At session startup, `initialize_session_lifecycle()` restores open multi-day positions and archives prior-session candidates unless they are promoted multi-day positions. Suggestions use `ARCHIVED` as their terminal session-cleanup state.

The Day 2 subscriber message is `POSITION CONTINUES`, not `NEW TRADE`; it identifies when the trade opened, current $R$, trend health, and the hold action. The Trading page shows separate Intraday and Multi-day counts for open trades.

The Paper Automation sidebar names its three lifecycle phases explicitly:

| Control | When it acts | Behavior |
| --- | --- | --- |
| `Auto Exit (During Market Hours)` | During the session | Evaluates normal stop, target, live-exit, invalidation, and profit-threshold rules. |
| `Force Close Intraday at Market Close` | At market close | Closes only trades whose current profile is `INTRADAY`, even when Auto Exit is disabled. |
| `Restore Multi-day Positions Next Session` | Next market session | Restores eligible multi-day positions without changing candidate archival. |

Multi-day positions remain open unless a normal exit rule fires.

Disabling `Auto Close Intraday Trades` does **not** promote an `INTRADAY` trade to `MULTIDAY`. If such a trade remains open overnight, the next session restores it as `INTRADAY`, refreshes its holding duration, and records the warning: `Intraday trade carried overnight because Auto Close Intraday Trades was disabled.` It does not receive a `POSITION CONTINUES` alert or become eligible for multi-day restoration rules.

`TRADE CLOSED` remains one subscriber message type, but its `Reason` line uses a standardized category when applicable: `🟥 Stop Loss`, `🟩 Target Hit`, `🟨 EMA Exit`, `🟦 VWAP Exit`, `🟪 Time Exit`, `⚠️ Failed Breakout`, or `📈 Manual Exit`. Near-close exits, end-of-day closures, and other time-based terminations are normalized to `🟪 Time Exit`. Other engine exits retain a concise generic trend, market, or profit-lock category.

Holding profile is frozen when a trade opens. It may change only through the paper-trade manager's `MANUAL_OVERRIDE` or future `BROKER_SYNC` source; scanner, exit engine, Telegram, and ranking code do not promote or demote it. `PAUSED` and `RESUMED` are operational trade states for data/provider outages, halts, or controlled runtime restarts. A paused trade blocks additional entries for that symbol and does not receive automated management until resumed.

Migration `app/db/migrations/012_holding_profiles.sql` exposes `holding_profile` and lifecycle dimensions as Postgres columns for reporting. Apply it manually through `DATABASE_DIRECT_URL` before querying those columns; runtime scans never execute schema DDL.

## Candidate Intelligence

`app/analytics/candidate_intelligence.py` is a read-only research layer built from the master evidence dataset. A **Good Candidate** requires setup score $\ge 70$, RR $\ge 1.8$, option quality $\ge 80$, and `HEALTHY` or `STRONG` trend health. It writes the enriched daily rows to `candidate_intelligence.csv` and a summary to `candidate_intelligence_summary.json`.

The Validation page and Daily Validation Report show:

- Good Candidate totals: opened, skipped, blocked, correct skips/blocks, missed winners, and investigation count.
- High Quality Blocked Candidates with reason, replay outcome, and verdict.
- Candidate Outcome Matrix: `OPENED_WON`, `OPENED_LOST`, `CORRECT_SKIP`, `MISSED_WINNER`, or `NEUTRAL`.
- Blocked Missed-Winner Attribution grouped by reason and type.
- Investigation Queue: high-RR, strong-setup non-entries and high-quality opened losses.

Missed winners are classified as `OPERATIONAL_MISS`, `DATA_QUALITY_MISS`, `RISK_MISS`, or `INTENTIONAL_SKIP`. These classifications direct investigation; they never alter scanner thresholds, paper entries, or real trading automatically.

Existing dashboards remain derived views during the transition. They should migrate to this dataset rather than become new data sources. Example SQL:

```sql
SELECT setup, COUNT(*) AS candidates, COUNT(*) FILTER (WHERE winner) AS winners
FROM candidate_evidence
WHERE rr > 2
	AND quote_freshness = 'STALE_QUOTE'
GROUP BY setup;
```

```sql
SELECT top_candidate, AVG(setup_score) AS avg_setup_score,
			 AVG(CASE WHEN winner THEN 1.0 ELSE 0.0 END) AS winner_rate
FROM candidate_evidence
GROUP BY top_candidate;
```

## Calibration Phase Changes

The project is now in a calibration phase: avoid adding new indicators or broad threshold loosening until more paper sessions are reviewed. Recent code changes are intentionally targeted to behavior observed during the first six-day validation sample:

- ORB uses the regular-session 09:30-10:00 ET opening range instead of the first rows of a dataframe, so premarket candles do not corrupt opening-range breakout/breakdown levels.
- Candidate persistence tracks technically valid setups across repeated scans with first/last seen timestamps, scan count, best/current score, score delta, and strengthening status.
- Category score is recorded as a shadow diagnostic only. The legacy score remains the decision score until enough paper-trade evidence exists to compare old score versus category score.
- Breakout detection uses the prior 10-bar resistance level. EMA pullback detection is volatility-aware using `ATR * 0.40` for the latest low-to-EMA9 distance, and logs `[EMA_PULLBACK CHECK]` with signal, EMA alignment, low distance, and threshold for ticker-level debugging. Breakdown entries require breakdown structure, body strength, and relative volume confirmation.
- Early weak exits are guarded for the first few bars when the trade is near flat and trend remains intact. Hard stops, targets, and other risk exits remain active.
- Paper-mode option selection can keep the best-quality contract active for validation when paper affordability override is enabled. Real-trade readiness remains affordability-gated.

These changes are calibration refinements, not a new strategy family. Do not change EMA periods, RSI/MACD thresholds, ATR rules, DTE/delta rules, risk manager thresholds, or setup/RR thresholds without a larger validation sample.

Recent calibration bugfixes deliberately avoid strategy changes:

- Replay calibration now records `ignored_stop_hit` and `ignored_stop_bar` when a stop is touched during ignored opening bars, so calibration statistics can distinguish an intentionally ignored early stop from a clean path.
- Risk manager diagnostics always include an explicit `Risk/Reward below minimum threshold (1.5)` reason when RR rejects a trade.
- Risk manager error returns use `reasons` consistently instead of mixing `reason` and `reasons`.
- Risk manager entry metadata access is defensive for missing `entry_quality` / `avoid_chasing` fields.
- ATR-floor stop adjustment is logged with original stop, adjusted stop, RR before, and RR after. `EMA_PULLBACK` now uses a smaller pullback-specific minimum stop floor (`0.25 * ATR`) so structure-based pullback stops are not automatically widened to a full ATR; breakout-style entries still use the existing full ATR floor.

Exit decisions use `app/exit/exit_engine.py::evaluate_exit()` as the live single source of truth. `app/trade_manager.py` is legacy and retained only for historical reference. Exit precedence is explicit: hard stop, hard target, EMA, VWAP, MACD, failed breakout, time exit, then near-close exit. The exit engine now returns `exit_reasons`, `exit_diagnostics`, `primary_exit`, `secondary_exits`, and `ignored_exit_signals` so daily review can see every triggered exit condition even when a higher-priority exit wins.

## Daily Validation Report

### Daily Review Export

**Tools: Downloads** includes **Daily Review Export**, which downloads `review_YYYY-MM-DD.zip` for the selected validation date. The ZIP always contains the standard review CSV names plus `manifest.json`, which reports whether each source had rows:

- `analytics_summary.csv`
- `daily_engine_summary.csv`
- `candidate_snapshot.csv`
- `candidate_evidence.csv`
- `candidate_outcome.csv`
- `decision_waterfall.csv`
- `gate_decisions.csv`
- `rule_evaluation.csv`
- `paper_trades.csv`
- `trade.csv`
- `exit_quality_metrics.csv`

Use this one-day export for post-market review. Weekly review should query the prior five trading days from the database to identify emerging patterns, while monthly review should examine the prior 20 trading days before approving strategy or threshold changes. The export's rule-attribution fields remain observational; they are not causal expected-$R$ claims.

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

The report summarizes daily paper-trade R, opened trades, auto-paper open/block/skip counts, top block reasons, best skipped opportunities, replay outcome by setup, rolling expectancy tables, and rule-change suggestions. Its Data Quality section identifies the authoritative opened-trade count source (`paper_trade_events`, `auto_paper_decision_log`, or `paper_trade_state`) and displays the count from every source so mismatches remain visible. The State And Lifecycle Reconciliation section separately counts `paper_trade_state`, legacy `trade_state`, suggestion statuses, lifecycle observations, and lifecycle transitions, and emits explicit mismatch warnings.

Auto-paper decisions are stored in two places with different purposes:

- `data/daily/YYYY-MM-DD/auto_paper_decisions.csv`: full-day append-only audit trail and primary report source.
- `app/state/auto_paper_decision_log.json`: capped to the most recent 500 rows for fast dashboard display.

Every auto-paper decision includes market-session fields such as `trading_day`, `session_id`, `scan_id`, `scan_timestamp`, `market_session`, `decision_time_bucket`, `is_auto_entry_window`, `is_after_close`, `minutes_from_open`, and `minutes_to_close`. Starting from the latest fixes, each decision also logs the active gate thresholds at decision time: `gate_mode` (always `"auto_paper"`), `min_rr_used`, and `min_setup_used`. This makes it possible to tell whether a skip reason like `RR_BELOW_THRESHOLD` was caused by the actual threshold in use at that moment, or by a gate mismatch. Decision rows also include scanner eligibility diagnostics such as `setup_valid`, `execution_ready`, `realtime_ready`, `affordable`, `price_geometry_ok`, `price_geometry_error`, `scanner_output_age_minutes`, `allow_review_tv_chart_auto_paper`, and `review_validation_candidate`, so startup/restart skips like `NO_AUTO_PAPER_CANDIDATE` can be inspected directly. The report reads the full daily CSV first and falls back to the capped JSON with a warning if the daily file is missing.

Every completed scanner run also appends `data/daily/YYYY-MM-DD/market_opportunity_audit.csv`. This is a lightweight watchlist-level audit trail with symbol, score, setup, action, blocked reason, top-candidate tag, symbol move %, and replay outcome. Use it after the close to answer whether major movers were detected, accepted, blocked, or missed by a specific layer.

The opportunity audit also includes candidate persistence fields when a setup persists across scans: candidate scan count, best score, score delta, and whether the setup strengthened. This lets daily review separate one-scan false positives from setups that matured over time.

## Trading Research Widgets

The dashboard includes measurement-only research widgets built from daily files. They do not change scanner decisions:

- `Trading Scorecard`: summarizes market regime, market coverage, signal count, paper entries, expectancy, and recommendations for the current trading day.
- `Market Coverage`: estimates how many strong watchlist movers were detected, directionally correct, entered, profitable, missed, or correctly skipped.
- `Opportunity Funnel`: counts the watchlist through momentum, entry, risk, option, affordability, realtime, entry, and profitable stages.
- `Engine Health`: summarizes available scanner/runtime health proxies such as completed symbols, scanner errors, quote freshness, and per-symbol runtime when daily decision timing is available.
- `Market Leaderboard`: ranks watchlist movers by absolute move and shows detection, entry, result, and miss reason.
- `Entry Delay`: compares first seen time versus entry time for persisted candidates and reports average, median, longest, and fastest delay.
- `Candidate Strength`: shows current score, best score, score delta, and whether a persisted setup is strengthening, weakening, or flat.
- `Missed Opportunity Attribution`: classifies missed movers by likely layer: momentum, entry, risk, option, affordability, exit, or unknown.
- `Engineering Recommendation`: applies simple rules to coverage, entry rate, win rate, and option rejection rate to suggest the next engineering focus.
- `Strategy Journal`: upserts a daily strategy journal row with coverage, win rate, expectancy, largest miss, recommendation, and confidence.

These widgets are fed primarily by `market_opportunity_audit.csv`, `paper_trade_events.csv`, `scanner_output_close.csv`, `auto_paper_decisions.csv`, and candidate snapshots. Use them after each session to decide where engineering time should go without introducing new indicators, AI scoring, ML prediction, or exit-confidence models prematurely.

Dashboard KPI rows use the shared `kpi_card()` helper in `app/ui/components.py` and the global `.metric-card` CSS injected by `app/dashboard.py`. Use these compact KPI cards for scorecard, coverage, engine-health, entry-delay, and validation-summary metrics. Reserve larger headline typography for future primary performance metrics such as total P/L, total R, win rate, or profit factor.

## Dashboard Page Responsibilities

| Page | Primary question | Current contents | V1/V2 rule |
| --- | --- | --- | --- |
| **Trading** | What should I trade right now? | Today’s Decision Center, top-five ranked V1 decisions, open V1 trades, compact performance, and market summary | V1 only. V2 does not place trades, alter state, or create competing live controls. |
| **Validation** | Did execution behave well today? | Trade Doctor, Strategy Confidence, Trade Efficiency, Candidate Outcomes, Decision Analysis, V1/V2 completed-trade comparison, Trend Outcome Attribution, strong-trend execution failures, and V2 Learning Summary | Post-trade V2 evidence only. |
| **Replay** | What would the saved scanner state have done? | Offline replay coverage, blockers, saved replay outputs, and replay summary | Current replay is V1-oriented. V1/V2 replay comparison is pending the Candidate Evidence merge. |
| **Regression** | Would current strategy code have improved an archived day? | Neon archive/baseline status, on-demand HSR execution, result deltas, and versioned run history | Read-only against immutable snapshots and baselines; never changes daily truth. |
| **Reports** | Is performance improving across days? | Daily report status, historical Trade Efficiency, and multi-day Execution Learning Trends for trend age, entry efficiency, Trend Capture %, TES, and exit phase | Aggregated research only; no routing or execution controls. |
| **Developer** | Is the system healthy? | Runtime state, scheduler and performance diagnostics, cache/report status, and lazy-loaded engineering diagnostics | Operational diagnostics only. |

The Daily Validation Report is the portable post-market counterpart to Validation. It includes V2 shadow counts, completed V1/V2 comparisons, Trend Outcome Attribution, strong-trend execution failures, and the V2 Learning Summary.

### Deprecated Trading UI Paths

The following renderers are retained temporarily for fallback/inspection and are marked `DEPRECATED` in [`app/dashboard.py`](app/dashboard.py). They are no longer used by the active Trading route.

| Deprecated renderer | Replacement | Removal condition |
| --- | --- | --- |
| `_render_command_center` | Decision Center in `app/ui/pages/trading.py` | One live paper session confirms the state-driven Decision Center covers live decisions. |
| `_render_current_opportunities` | Top-five Ranked Opportunities | One live session confirms top-five ranking and action ordering. |
| `_render_why_no_trade` | Validation > Decision Analysis | Validation cache contains a complete scan-day analysis. |
| `_render_missed_opportunities` | Validation > Candidate Outcomes / Decision Analysis | Candidate Evidence merge restores richer missed-winner attribution on this branch. |
| `_render_trading_page` / `_render_trading_page_from_state` | `app/ui/pages/trading.py` | New Trading route remains stable across cached and full-data paths. |

Sidebar grouping is now intentional: **Auto Refresh**, **Paper Automation**, **Operations** (validation/replay generation), and navigation remain visible. Downloads are under **Tools: Downloads**, with raw artifacts under **Advanced files**. Runtime keys and detailed diagnostics belong on Developer.

## Production Engineering Status

Current implementation status for the production-engineering roadmap:

| Item | Status | Notes |
| --- | --- | --- |
| Parallel Scanner | Implemented for safe foreground paths | Market-data prefetch is parallelized with bounded `ThreadPoolExecutor` via `SCANNER_MAX_WORKERS`. Scanner persistence is queued after results are built so DB writes, snapshots, lifecycle events, health history, stage profile, and Excel/CSV export do not block the operator table. Trading decisions and state mutation remain sequential. |
| Background Persistence Queue | Implemented | `app/background/background_queue.py` runs best-effort persistence tasks on one daemon worker, drains the queue at process exit, and exposes pending/completed/failed/depth/average/longest job metrics. Task failures are logged and do not stop later queued work. |
| Candidate Persistence State Machine | Implemented | Scanner output includes candidate persistence fields and writes `data/daily/YYYY-MM-DD/candidate_persistence_state.json`. The opportunity audit includes scan count, best score, score delta, and strengthening flags. |
| Dynamic Watchlist | Optional | Default scans still use the static watchlist. Set `DYNAMIC_WATCHLIST_ENABLED=true` to seed the scan from Polygon snapshot movers, anchored by the core symbols, with `DYNAMIC_WATCHLIST_SIZE` and `DYNAMIC_WATCHLIST_MOVERS_PER_BUCKET` controlling breadth. |
| Engine Health Score | Partial | `app/analytics/engine_health.py` records scan runtime, worker count, symbol completion/failure counts, quote freshness, Polygon request/cache/timing metrics, background queue metrics, and health score to `engine_health_history.csv`. |
| Validation Freeze | Documented, not enforced | The docs mark the project as being in calibration/freeze mode and recommend avoiding broad strategy changes. There is no code-level freeze gate. |
| Graduation Criteria | Not implemented | No formal confidence/position-size graduation rules are coded yet. Strategy journal includes a simple confidence value, but it does not enforce sizing rules. |

Before treating Strategy v1.0 as production-ready, the remaining production-engineering work is: measure foreground versus background scan runtime over live sessions, tune Polygon cache TTL/rate-limit settings from the new metrics, and define explicit graduation criteria for moving from paper validation to increased real position sizing.

Current scanner performance implementation:

- `SCANNER_MAX_WORKERS` controls a bounded `ThreadPoolExecutor` for parallel market-data prefetch. Default: `5`.
- `DYNAMIC_WATCHLIST_ENABLED=false` keeps the static watchlist. When enabled, Polygon snapshot movers are merged after core symbols and before the static fallback list.
- Foreground scan work still computes strategies, entries, risk, options, paper-trade decisions, Telegram checks, candidate persistence, relative-strength rankings, opportunity audit, and the operator table sequentially.
- Scanner finish persistence is queued through `RuntimeScheduler`: engine health, candidate snapshots, signal lifecycle rows, Excel/CSV output, and scanner stage profiles run after the foreground scan has queued the result. Database promotion is then submitted as a separate normal-priority scheduler job, so database latency cannot hold up file-backed artifacts.
- Runtime performance instrumentation writes `data/runtime_performance.csv` and `data/daily/YYYY-MM-DD/runtime_performance.csv` for measured scanner, Telegram, dashboard page render, validation report generation, and replay generation stages.
- `data/live/runtime_state.json` stores lightweight runtime queue state such as scanner/critical/high/normal/low job counts. This is instrumentation only; it does not change trading decisions.
- `scanner_running` in `runtime_state.json` is now actively set around `run_scanner()` with a safe wrapper, so dashboard diagnostics can distinguish active scans from queued background work.
- Production Runtime v2 Phase 1 lives under `app/runtime/`: `runtime_priority.py`, `runtime_jobs.py`, `runtime_metrics.py`, and `runtime_scheduler.py`. The scheduler supports `Priority.CRITICAL`, `HIGH`, `NORMAL`, and `LOW`, `RuntimeJob`, `RuntimeScheduler.submit_*()`, stale cancelable job cancellation by scan id, queue metrics, and compatibility wrappers `run_critical`, `run_high`, `run_normal`, and `run_low`.
- `data/runtime_metrics.csv` records priority scheduler queue wait, queue runtime, total runtime, status, job name, priority, job id, and scan id for submitted runtime jobs.
- Production Runtime v2 Phase 2 routes scanner-run start persistence and the post-scan persistence bundle through `RuntimeScheduler` as high-priority jobs. New scans call `cancel_old_jobs(scan_id)` before queueing work so stale cancelable queued jobs can be skipped while critical trade/Telegram paths remain protected.
- The high-priority scanner persistence job now writes enriched `data/live/dashboard_state.json` and daily `dashboard_state.json` before heavier DB, lifecycle, snapshot, and Excel persistence work. The payload includes command-center candidate state plus `scanner_health` and `telegram_summary` metadata for Trading-page rendering. Its `today_performance` object is refreshed asynchronously after validation analytics finish and contains completed/winning/losing trades, `last_completed_trade`, win rate, average R, average and best trend capture, average TES, exit-verdict counts, and average left on table.
- Validation cache generation is handled by `app/ui/cache/validation_state_builder.py`. After each scanner run, a normal-priority runtime job writes `data/live/validation_state.json` and `data/daily/YYYY-MM-DD/validation_state.json` with scanner KPIs, paper-trade KPIs, trend-capture metrics, exit verdict breakdowns, and engineering recommendations. The cache also contains `trade_efficiency`, a structured `diagnosis`, and `strategy_confidence` for the Validation page.
- Replay cache generation is handled by `app/ui/cache/replay_state_builder.py`. After each scanner run, a low-priority runtime job writes `data/live/replay_state.json` and `data/daily/YYYY-MM-DD/replay_state.json` summarizing existing offline replay artifacts, coverage, biggest blockers, top misses, stale/missing status, and replay errors without regenerating replay during dashboard refresh.
- Manual offline replay generation refreshes `replay_state.json` immediately after writing `offline_replay.csv` and `offline_replay_summary.csv`, so the Replay page cache reflects the newly generated replay without waiting for another scanner run.
- Report cache generation is handled by `app/ui/cache/report_state_builder.py`. After each scanner run, a low-priority runtime job writes `data/live/report_state.json` and `data/daily/YYYY-MM-DD/report_state.json` summarizing existing daily validation report artifacts, status, paths, sizes, modified times, and stale/missing state. Its `historical_trade_efficiency` object aggregates up to 20 existing daily `validation_state.json` caches into daily, weekly, monthly, setup, regime, exit, and weekday trends. Manual report generation refreshes this cache immediately after the report is built.
- The Validation, Replay, and Reports dashboard pages now prefer their live cached JSON state files when available and fall back to the older file-heavy rendering paths only when the cache is missing.
- Validation, Replay, and Reports now short-circuit before scanner CSV loading when their live cached JSON state exists. The older scanner-output path is used only when the relevant cache file is absent.
- The Trading page now has a guarded `dashboard_state.json` fast path: when paper automation/auto-exit/EOD close automation is off and live dashboard state exists, it renders directly from JSON without loading scanner CSVs. If automation is active or cache is missing, it falls back to the full scanner-output path.
- The performance journey is page-specific and cache-only: Trading answers "How am I doing today?" with six Today’s Performance KPI cards; Validation answers "Why did I perform that way?" with detailed trade efficiency, charts, and recommendations; Reports answers "Am I improving over time?" with multi-day summaries and trend tables. No dashboard page rereads `trend_capture_analysis.csv`, replays trades, or queries the database to render these analytics.
- Telegram remains ahead of dashboard and analytics work. Validation and report builders run as background runtime jobs after Telegram dispatch; runtime-scheduler failures are logged and marked failed without interrupting scanner completion or alert delivery.
- The Developer page includes a `Runtime Performance` expander that reads `runtime_state.json`, recent `runtime_performance.csv` rows, and recent `runtime_metrics.csv` scheduler jobs so runtime tuning can be measured from the workstation.
- Runtime performance summaries are also cached to `data/live/runtime_performance_summary.json`, including slowest stages and slowest scheduler jobs. The Developer page prefers this summary for aggregate diagnostics while retaining recent raw CSV rows for drill-down.
- Production hardening modules include `scan_generation.py`, `generation_validator.py`, `runtime_watchdog.py`, `shutdown_manager.py`, and `startup_manager.py`. Live JSON state files now include top-level generation metadata and are written atomically where runtime builders own the write path. `runtime_health.json` summarizes queue depth, scanner timeout, dashboard staleness, worker health, and Telegram latency warnings.
- Developer diagnostics are now explicitly lazy-loaded. Each heavy Developer panel has a `Load ...` toggle inside its expander, so collapsed sections do not execute CSV/report/coverage work on every refresh.
- Dashboard page-specific imports are now lazy where practical: market coverage loads only when that Developer section is requested, trend-capture analytics loads only when the uncached Trade Efficiency fallback renders, and dashboard-state building imports only when live cache fallback is needed.
- Dashboard navigation now routes through concrete page modules under `app/ui/pages/`: `trading.py`, `validation.py`, `replay.py`, `regression.py`, `reports.py`, and `developer.py`. Shared helpers remain in `dashboard.py` during the transition, but page entry-point bodies are now owned by the page modules.
- Live JSON state loading now uses page-specific cache profiles: Trading state TTL is 5 seconds, Validation state TTL is 60 seconds, Developer runtime state TTL is 120 seconds, and Replay/Reports state caches invalidate by file modified time.
- Performance mode configuration lives in `app/config/performance.py`. Defaults keep `lazy_imports`, background validation/replay/report cache generation, and Trading `dashboard_state_only` enabled. TTLs and state-only behavior can be overridden with `PERFORMANCE_TRADING_CACHE_TTL`, `PERFORMANCE_VALIDATION_CACHE_TTL`, `PERFORMANCE_DEVELOPER_CACHE_TTL`, and `PERFORMANCE_DASHBOARD_STATE_ONLY`.
- Telegram dispatch now has a runtime facade in `app/runtime/telegram_dispatcher.py`. `TELEGRAM_DISPATCH_MODE=DIRECT` is the default and preserves synchronous send behavior; `QUEUED` is available as an opt-in critical-priority scheduler path. In queued mode, alert sent-state persistence runs only from the dispatcher's after-success callback after the Telegram send succeeds. Queued sends append replayable message records to `data/live/telegram_dispatch_queue.jsonl`, while ATTEMPT, SENT, and FAILED rows append to `data/live/telegram_dispatch_audit.jsonl`. Audit rows correlate scan, symbol, direction, candidate key, decision, message type, parse mode, message length, attempt, and send latency. Failed Telegram responses retain the structured API response, including the actionable `description` returned for a `400` rejection. `recover_pending_telegram_dispatches()` can resubmit queued records that have no successful audit event.
- The scanner now queues the noncritical `summarize_telemetry()` step as a low-priority runtime job after table output, reducing foreground scanner tail work without changing trade decisions.
- Market opportunity audit, option liquidity audit, and candidate funnel file writes now run inside the scheduled scanner persistence job instead of the foreground scanner tail. The foreground path still computes candidate rows, rankings, health, Telegram summary, and prints the funnel, but noncritical audit file I/O is deferred.
- Scanner finalization now runs as a high-priority non-cancelable runtime job. The foreground scanner queues `finalize_scan_outputs` after raw rows are collected and returns; finalization handles operator table rendering, candidate persistence/ranking, health payload, Telegram dispatch, funnel, persistence, and cache job scheduling.
- Dashboard paper automation orchestration now lives in `app/runtime/paper_automation.py`, with helper logic in `app/runtime/paper_automation_support.py`. The runtime paper automation path no longer imports `app.dashboard`; dashboard only calls the runtime entry points.
- Telegram alert DB audit writes and paper-trade DB upserts are also queued through the background worker. Telegram sends, paper JSON state, and paper event CSV writes remain on the foreground path where needed for operator correctness.
- Gate-decision DB writes remain batched per scan. The same scheduler-only promotion job also batches structured `rule_evaluation` rows, retaining actual versus required values instead of relying on rejected-reason strings later.
- **RuleEvaluation framework:** native emitters now exist at the Entry, Risk, Option Liquidity, Affordability, Telegram, Paper Automation, and Review decision boundaries. Every emitted record has `scan_id`, `symbol`, `setup`, `rule_name`, `rule_group`, `actual_value`, `required_value`, `passed`, `blocked_trade`, `priority`, and `evaluation_phase`. `ENTRY` is the default; scanner artifacts also emit `ACTIVE` for ongoing trade management, `EXIT` for live exit signals, and `REPLAY` for projection replay outcomes. `aggregate_rule_evaluations()` deduplicates by scan, symbol, setup, phase, rule group, and rule name before persistence. Migration `003_rule_evaluation_phase.sql` adds the persisted column.
- Signal lifecycle events and transitions use batched CSV appends per scan through `record_signal_lifecycle_events_for_scan()` instead of one file append per candidate.
- Lifecycle CSV/state writes are authoritative. The optional event-stream submission is wrapped in a best-effort handler: if scheduling or DB event persistence fails, it logs `[LIFECYCLE EVENT STREAM WARNING]` and does not interrupt suggested-trade synchronization, lifecycle CSV output, or scanner completion.
- Polygon observability records total API requests, cache hits, cache misses, cache hit %, average API time, and average cache read time from `app/utils/polygon_client.py`.
- Background queue observability records pending jobs, completed jobs, failed jobs, queue depth, average job time, longest job time, and longest job name from `app/background/background_queue.py`.
- `engine_health_history.csv` is written under both `data/daily/YYYY-MM-DD/` and `data/` with scan runtime, workers, Polygon request/cache/timing metrics, background queue metrics, exceptions, completed/failed symbols, average symbol runtime, and health score.
- `scanner_stage_profile.csv` is written under both `data/daily/YYYY-MM-DD/` and `data/` with stage-level timings for market data, indicators, strategy, entries, risk, options, Telegram, export, and deferred persistence stages such as `Database / Engine Health`, `Database / Gate Decisions`, `Database / Candidate Snapshot`, `Database / Signal Lifecycle`, and `Database / Scanner Run`.
- The Engine Health dashboard widget reads the latest history row when available and shows scanner health, Polygon cache/API metrics, background queue metrics, and a category/detail stage breakdown.

## Shared Trade Decision And Telegram Policy

Telegram is a trade-lifecycle notification transport, not a second decision engine or a scanner-event feed.

- `app/decision/decision_engine.py` exposes `evaluate_candidate()` and returns a `TradeDecision` with action, setup score, RR, option quality, confidence score, reasons, and block reasons.
- Scanner/dashboard/paper rows remain the decision source. Scanner `ENTER` / `ENTER_PAPER` rows are held until a confirmed V1 or paper trade opens, then publish one `NEW TRADE` event. `ACTIVE_TRADE` and `REVIEW_TV_CHART` rows never publish subscriber alerts.
- The subscriber protocol is fixed to six message types: `NEW TRADE`, `TRADE UPDATE`, `PARTIAL PROFIT`, `POSITION CONTINUES`, `TRADE CLOSED`, and `TRADE CANCELLED`. Every message includes its ET date/time. `TRADE UPDATE` requires a material $R$/trend/confidence/stop change; `POSITION CONTINUES` is sent once for an overnight transition, not every morning; and a review candidate that expires without entry may send `TRADE CANCELLED`.
- Closed-trade messages include a subscriber-facing exit reason icon, holding time, and available execution/Trend Capture information. Losses state that risk was managed according to plan; execution grading is not shown while a trade remains open.
- Telegram does not recompute setup, RR, option quality, quote freshness, affordability, event, regime, top-candidate, session, conviction, or alert-score eligibility. Legacy `TELEGRAM_ALERT_POLICY` and score/threshold settings do not block an alertable action.
- Duplicate-alert protection remains a transport safeguard. A Telegram delivery failure is not a trade-decision failure.

Paper-trade promotion reconciles the matching suggestion immediately. A matching suggestion that was previously `EXPIRED_NOT_ENTERED` transitions to `PROMOTED_TO_PAPER` when its paper trade opens; closed suggestions remain terminal.

## Observational Entry, Ranking, And Exit Analytics

The current calibration layer records additional execution-quality evidence without changing V1 scanner eligibility, paper execution, Telegram delivery, or live exit selection.

- `app/analytics/entry_timing_engine.py` converts existing V2 entry-location features into an `Entry Timing Score` from 0-100. Its weights are Entry Efficiency 35%, Trend Age 20%, Pullback Number 20%, Bars Since Breakout 10%, EMA extension 10%, and VWAP extension 5%. Grades are `EXCELLENT` above 80, `GOOD` from 70-80, `AVERAGE` from 55-69, and `LATE_ENTRY` below 55.
- `app/analytics/trade_ranker.py` computes the `Trade Quality Score` (TQS): Setup 25%, Entry Timing 20%, Trend Health 20%, Option Quality 15%, Relative Strength 10%, and Liquidity 10%.
- `app/decision/entry_optimizer.py` ranks already-valid candidates by entry location without changing setup eligibility, indicators, RR, risk, or option rules. It applies the pullback/trend-age/breakout-window priority adjustment to `Ranking Score` and exposes `Expected Remaining Trend` plus a projected `A`/`B`/`C` entry grade. `Candidate Rank` uses `Ranking Score`; execution and alert eligibility remain unchanged.
- Candidate snapshots and Candidate Evidence retain Entry Timing score/grade/reason, TQS, Entry Priority Adjustment, Expected Remaining Trend, Projected Entry Grade, Ranking Score, and candidate rank so later outcome research can compare score bands against winners, Trend Capture %, and TES.
- `app/analytics/exit_waterfall.py` formats existing V1 exit diagnostics as an ordered waterfall. Scanner rows retain `Exit Waterfall`, `Exit Rule`, and `Exit Stage`; the live exit engine still owns priority and final selection.
- `app/analytics/decision_waterfall.py` merges persisted Entry Diagnostics with native RuleEvaluation records into a candidate-level Decision Waterfall. Its fixed stage order is Momentum, Entry, Risk, Option, Affordability, Realtime, Telegram, Paper, and Decision. Each stage carries pass/fail/not-evaluated status, summary, passed rules, failed rules, and actual/required values. The payload exposes `final_action`, `final_reason`, `blocking_stage`, and `blocking_rule`.
- `dashboard_state.json` and `validation_state.json` retain V1 waterfalls, V1/V2 shadow path comparisons, and today’s blocking-stage percentage summary. Validation renders the selected candidate’s grouped stages, failed values, first blocker, V1/V2 comparison, and current blocker distribution.
- Migration `008_decision_waterfall.sql` creates the optional `decision_waterfall` audit table. The scheduled artifact writer inserts one immutable row per evaluated rule with stage, pass/fail, selected-blocker flag, actual/required values, priority, and summary. Reports aggregates cached Validation blocker distributions into daily and dominant-stage historical trends.
- Decision Waterfall remains a visualization and audit layer. V2’s path only represents V2 shadow facts currently available in scanner rows; it does not invent option/risk gates or alter V1 behavior.
- V1/V2 completed comparison rows include entry/exit delay, $R$, MFE, Trend Capture, and TES deltas. They remain shadow evidence, not a promotion switch.
- `refresh_contract_quote()` performs a bounded quote refresh before downstream quote gating. `OPTION_QUOTE_REFRESH_RETRIES` defaults to `1`; refresh results retain quote retry count, latency, refresh timestamp, and final freshness in scanner snapshots and lifecycle events.

These metrics remain observational until the existing evidence threshold is met: at least 20 evidence days and 80 completed trades. They must not automatically tighten or loosen V1 trading rules.

V2 shadow exits also record an `Exit Confidence Score` that measures deterioration rather than permission to hold. Hard stop and target remain immediate. Soft EMA/VWAP/MACD/volume deterioration can enter `MONITOR` when the Grace Zone applies, requiring confirmation persistence before a V2 shadow exit. V1 also uses this confidence evidence for one narrow live behavior: a profitable first EMA-only break with otherwise healthy trend evidence is held for one candle and recorded as a pending EMA grace state. A persistent EMA break exits on the next evaluation; hard stops, targets, VWAP loss, MACD reversal, and stacked deterioration remain immediate. `data/daily/YYYY-MM-DD/daily_engine_summary.json` is written during deferred scan persistence from V2 learning, completed comparisons, exit-quality facts, and shadow blocker observations.

The `Learning` dashboard page reads the materialized live daily summary and displays V2 comparison, Exit Confidence, blocker, and existing one-/two-bar post-exit continuation metrics. Migration `009_learning_engine.sql` adds optional warehouse tables: `daily_engine_summary`, `v2_learning_metrics`, `trade_comparison`, `rule_performance`, and `exit_quality_metrics`. File-backed daily summaries remain available when DB writes are disabled.

After writing the daily and live JSON summaries, the Learning Engine promotes those aggregates and completed V1/V2 comparison rows through best-effort database writes. Database failures cannot block scanner completion or the Learning page's file-backed state.

The Learning summary also includes a rolling last-50 refresh success rate, TQS outcome buckets, evidence-based Rule ROI, and a feature promotion tracker. Rule ROI is observational: it uses resolved candidate outcomes and is never a direct trading input.

Feature promotion is versioned through `feature_registry` and `feature_statistics` (migration `010_feature_registry.sql`). A feature remains `SHADOW` while lift is not measurable; it becomes a promotion candidate only with 100 completed samples, 95% confidence, and positive measured lift. Promotion status never changes V1 automatically.

`app/analytics/market_regime.py` normalizes existing regime and breadth facts into an observational macro state and risk mode. `app/analytics/trade_lifecycle.py` classifies persisted trade facts as `SETUP`, `READY`, `EARLY`, `EXPANSION`, `PROTECTION`, `WEAKENING`, or `EXIT`. Both feed Learning distributions only and do not alter V1 decisions.

Learning also materializes completed paper-trade performance from persisted event facts: completed count, wins/losses, win rate, total and average $R$, profit factor, and maximum drawdown in $R$.

The Learning page prefers the latest warehouse summary when optional DB writes are active and always falls back to the live file-backed summary. Promotion review may mark a feature for controlled validation, but never switches V1 automatically.

Learning computes summaries from immutable persisted facts, attempts repository/Neon persistence first, and then exports the same summary to daily/live JSON regardless of database outcome. The JSON is a resilient cache and inspection artifact, not a learning input.

Learning also materializes resolved setup, regime/market, and lifecycle/decision aggregates into `analytics_summary`. Human promotion reviews are persisted in `promotion_review`; review may approve controlled validation but cannot switch V1 automatically.

Use `python tools/reconcile_learning_memory.py --date YYYY-MM-DD --backfill` to regenerate daily Candidate Evidence/Learning summaries from persisted facts, promote them to Neon when enabled, and compare file versus database counts. The tool never reads generated HTML reports.

Reconciliation reports `MATCH` for aligned file/database facts, `DB_AUTHORITATIVE` when historical database facts exist without a recoverable daily CSV, and `REVIEW` only for conflicting nonzero counts.

## Version 1.0 Evidence Freeze

Version 1.0 is feature-frozen for strategy behavior. Do not add or loosen V1 entry, exit, risk, or sizing rules until at least 100-200 completed paper trades span 20 or more trading days and multiple market regimes. New ideas must run in shadow mode, accumulate persisted evidence, and move only through the human-controlled promotion workflow.

Validation baseline: `python -m unittest discover tests` currently passes all 134 tests. The intentional background-worker failure printed by `test_background_queue.py` verifies failure isolation and does not fail the suite.

```mermaid
flowchart TD
	Scanner --> V1[V1 Engine]
	V1 --> TradeState[Trade State]
	Scanner --> V2[V2 Shadow]
	V2 --> Learning[Learning Engine]
	TradeState --> Learning
	Learning --> Files[Daily File Artifacts]
	Learning --> Warehouse[Optional Warehouse Tables]
	Files --> Validation
	Warehouse --> Validation
	Files --> Reports
	Warehouse --> Reports
	Files --> LearningPage[Learning Page]
	Warehouse --> LearningPage
```

## Quote Freshness Audit

Option quote freshness remains intentionally strict and unchanged. `LIVE_QUOTE` is less than `OPTION_DELAYED_QUOTE_MINUTES` old (currently 10 minutes), `DELAYED_QUOTE` is 10-30 minutes old, and `STALE_QUOTE` is older than `OPTION_MAX_QUOTE_AGE_MINUTES` (currently 30 minutes). Polygon's latest quote endpoint is queried in descending timestamp order; the classifier uses `last_updated`, with SIP/timestamp fallbacks.

The active environment requires real-time stock and options data, bid/ask, and fresh option quotes. Stock data uses `MAX_STOCK_DATA_DELAY_MINUTES=2`. No stale-quote threshold was loosened. Candidate snapshots and lifecycle events now persist the normalized quote timestamp, classification time, provider timeframe, source, age in seconds, allowed age in seconds, and freshness reason for each future occurrence. The Validation Report's **Quote Diagnostics** section lists every delayed, stale, or unparseable quote with Symbol, Quote Timestamp, Timestamp Field, Current Time, Age (sec), Threshold (sec), Decision, and Reason. The July 6 archive predates this capture, so it still cannot prove whether its historical stale blocks came from Polygon, timestamp provenance, or missing response data.

Every non-live quote also writes a dedicated `data/daily/YYYY-MM-DD/quote_attribution.csv` fact and upserts the `quote_attribution` SQL table through migration `005_quote_attribution.sql`. Each fact records the scanner timestamp, symbol, option ticker, quote timestamp, quote age in seconds, allowed age in seconds, the source timestamp field selected from `last_updated`, `sip_timestamp`, or `timestamp`, the provider source, final classification, and reason.

```sql
SELECT trading_day, symbol, option_ticker, scanner_timestamp,
	   source_timestamp_field, quote_age_seconds, allowed_age_seconds,
	   final_classification, reason
FROM quote_attribution
WHERE final_classification = 'STALE_QUOTE'
ORDER BY scanner_timestamp DESC;
```

## Production Entry Diagnostics

The scanner now writes permanent entry diagnostics for every ticker, independent of whether the ticker becomes `ENTER_PAPER`, `REVIEW_TV_CHART`, `WAIT`, `AVOID`, or `NO_ENTRY`.

- `app/diagnostics/entry_diagnostics.py` evaluates entry setup families observationally without changing trading decisions.
- Scanner output includes `ENTRY_SETUP_CANDIDATE`, `ENTRY_READINESS`, `FAILED_ENTRY_CONDITIONS`, `PASSED_ENTRY_CONDITIONS`, `ENTRY_DECISION_TIMELINE`, and `ENTRY_DIAGNOSTICS_JSON`.
- Scanner output also includes `ENTRY_GATE_FAILURE_STAGE` so the first broad failure layer is visible without opening JSON. Expected stages include `Momentum`, `Entry`, `Risk`, `Option Quality`, `Affordability`, `Realtime`, `Telegram`, `Paper Gate`, and `Generated`.
- `ENTRY_SETUP_CANDIDATE` records the closest setup, such as `BREAKOUT`, `EMA_PULLBACK`, `BREAKDOWN_SHORT`, `EMA_REJECTION_SHORT`, or `VWAP_REJECTION`, even when the entry engine ultimately returns `NO_ENTRY`.
- `ENTRY_DIAGNOSTICS_JSON` stores condition-level pass/fail values with actual and required values for review.
- The scanner prints `ENTRY FAILURE SUMMARY` and `MARKET REGIME ENTRY SUMMARY` after each run.
- The dashboard includes an `Entry Diagnostics` expander with ticker-level readiness, failed conditions, timeline, and raw JSON inspection.

## Trade Efficiency Analytics

Trade Efficiency Analytics is observational only. It does not influence entries, exits, risk, sizing, Telegram alerts, or auto-paper decisions.

- New post-trade analytics live under `app/analytics/trade_efficiency/`, with compatibility wrappers for earlier `app.analytics.trend_capture`, `app.analytics.trend_health`, and `app.analytics.trade_snapshot` imports.
- `app/analytics/trend_capture.py` computes Trend Capture % after a paper trade closes.
- Paper trade closes append `data/daily/YYYY-MM-DD/trend_capture_analysis.csv` when recent 5m candles are available.
- `app/analytics/trade_snapshot.py` writes `data/daily/YYYY-MM-DD/trade_exit_snapshots.csv` with an exit-time Trade Lifecycle Snapshot: indicators, structure flags, trend health, exit reason, and bars held.
- `app/analytics/trend_health.py` scores trend health from configurable weights such as EMA alignment, price above EMA9/VWAP, structure, MACD, RSI, and relative volume.
- Metrics include available move, captured move, Trend Capture %, MFE, MAE, left on table, post-exit continuation, delay analysis, trigger attribution, bars held, setup, regime, exit reason, trend health state, exit quality, exit verdict, and Trade Efficiency Score.
- Each post-exit trend-capture row also includes an operator-facing trade-doctor layer: `Entry Grade`, `Exit Grade`, `Exit Verdict`, `Exit Verdict Reason`, `Exit Trigger`, and `Engineering Recommendation`. Grades are derived from Trend Capture % (`A` at 70% or higher, `B` at 50–69%, otherwise `C`); the trigger mirrors the primary exit and the engineering recommendation mirrors the delay-analysis recommendation. These fields explain completed paper trades only and never change exit execution.
- The Validation page renders **Trade Doctor — Today's Diagnosis** from the cache. It gives scanner, entry, exit, replay, missed-winner, and tomorrow findings in engineering form: `Status`, `Reason`, `Evidence`, and `Action`; it does not generate generic performance prose. Entry findings cite each setup's completed-trade count and average Trend Capture %, exit findings cite the `EXIT_TOO_EARLY` verdict count, replay is only marked ready when cached replay output is ready, and missed winners cite the count plus the dominant attribution category.
- Trade Doctor actions are deliberately conservative. Findings default to `Observe; DO NOT CHANGE RULE.` A setup is called an outperformer or underperformer only when at least two setups have completed-trade evidence; a one-setup day is reported as an observation, not a relative conclusion.
- The validation cache also contains **Strategy Confidence**, an evidence-strength measure rather than a return forecast. It reports `Evidence` days, `Completed Trades`, `Confidence`, and decision status. No evidence returns 0%; approximately one evidence day / one completed trade returns 18%; 20 evidence days with 80+ completed trades returns approximately 92%. Until both 20 days and 80 completed trades are present, the state remains `OBSERVATIONAL_ONLY` and the UI says `DO NOT CHANGE RULE.` Reaching the threshold permits controlled-validation review only; it never changes strategy rules automatically.
- The Validation dashboard page shows Average Capture, Today's Capture, Best Capture, and Most Left On Table.
- Trading shows a cache-only `Today's Performance` row with Completed, Win Rate, Avg R, Trend Capture, Avg TES, and Left On Table. It reads `dashboard_state.today_performance` and does not calculate trade analytics during a page refresh. Before the first completed exit, it displays `No completed trades yet` instead of zero-valued post-trade KPI cards.
- Validation renders cached Trade Efficiency Analytics: Average/Today/Best/Worst Capture, Average TES, Average R, and Average Left On Table; a per-trade table; capture, TES, setup, regime, exit-verdict, opportunity-cost, and trend-health charts; and recommendations.
- Reports renders cached Trade Efficiency Summary windows for Today, Yesterday, 5 Day, and 20 Day, plus Daily Trend Capture with rolling average, Weekly TES, Monthly TES, and capture summaries by setup, regime, exit verdict, and weekday.
- Daily validation reports include Trade Efficiency Analytics and Trade Lifecycle Diagnostics with averages by setup, market regime, exit reason, exit trigger frequency, exit verdict distribution, delay gain, Trade Efficiency Score, and top EXIT_TOO_EARLY / EXCELLENT_EXIT trades.
- If average Trend Capture % is below 55, the report recommends improving trend management because entries are finding trends but exits are leaving significant profit.

### Missed-Winner Attribution

The Trading Scorecard's **Missed Winners / Loss Attribution** table identifies significant market moves that were not entered or ultimately resolved as winners. For each candidate it shows the setup, move percentage, classified reason, `root_cause`, `blocked_by`, relevant `rule`, observed `threshold`, optional `would_have_passed_if` value, a medium-confidence label, and a recommendation.

Reason categories are `MOMENTUM`, `ENTRY`, `RISK`, `OPTION`, `AFFORDABILITY`, `EXIT`, and `UNKNOWN`. The attribution is generated from persisted daily audit data and is intentionally retrospective: it helps prioritize investigation of gates, quote/liquidity handling, affordability, or exit timing; it does not relax thresholds, create entries, send Telegram alerts, or alter paper/real trade behavior.

## Entry/Exit V2 Shadow Mode

Entry/Exit V2 is an observational redesign, not a live strategy replacement. V1 remains the only engine permitted to open or close trades, mutate trade state, change suggestions, or send Telegram alerts.

- `app/strategies/entry_engine_v2.py` calculates trend age, pullback number, bars since breakout, EMA9/VWAP extension in ATR, volume confirmation, and a 0-100 Entry Efficiency Score. It proposes efficient first-pullback locations without changing V1 setup or RR rules.
- `app/exit/trend_health_engine.py` provides a lightweight live Trend Health score from EMA alignment, VWAP, structure, MACD, RSI, and volume.
- `app/exit/exit_engine_v2.py` keeps hard stop and hard target exits absolute, then classifies soft decisions as `TREND_FAILURE`, `PROFIT_PROTECTION`, `TIME_EXIT`, `END_OF_DAY`, or `HOLD`. Soft trend exits require multi-factor confirmation.
- `app/analytics/entry_exit_v2_shadow.py` records each scan to `data/daily/YYYY-MM-DD/entry_exit_v2_shadow.csv`, including V1/V2 entry and exit decisions, V2 exit phase, live MFE in $R$, trend-health state, and V1/V2 exit disagreement.
- `app/state/entry_exit_v2_shadow_state.py` owns V2-only shadow trade state. V2 can independently open, manage, and close a simulated trade using its own entry decision and the shared risk geometry; it never reads or mutates V1 trade state.
- `app/analytics/engine_version_comparison.py` appends completed engine facts to `engine_trade_events.csv`, writes sequence-matched V1/V2 pairs to `engine_trade_comparisons.csv`, and records entry/exit disagreements to `engine_differences.csv`. Completed pairs include entry/exit time and price, final $R$, MFE in $R$, and timing/$R$/MFE deltas.

The Daily Validation Report and Validation page include an **Entry/Exit V2 Shadow Comparison** section with scanner-level disagreements and completed-trade metrics. Evaluate V2 only after at least 2-3 weeks of paper evidence using Trend Capture %, TES, left on table, `EXIT_TOO_EARLY` rate, win rate, and average $R$. More entries alone are not a success criterion. A future controlled switch may route through `ENTRY_ENGINE=v1|v2` and `EXIT_ENGINE=v1|v2`, but those flags must remain inactive until V2 satisfies the promotion criteria.

### V2 Operating Contract

| Area | V1 | V2 shadow |
| --- | --- | --- |
| Entry decision | Production paper decision | Independent proposal from indicators, trend, price, and shared risk geometry |
| Trade state | `trade_state.json` | `entry_exit_v2_shadow_state.json` |
| Entry/exit execution | May open and close paper trades | Never places, closes, or modifies a V1 trade |
| Telegram and suggestions | V1-owned operational flow | No Telegram dispatch or suggestion mutation |
| Risk controls | Existing production controls | Uses the same risk geometry; hard stop and target remain absolute in V2 simulation |

Daily V2 artifacts have distinct purposes:

| Artifact | Purpose |
| --- | --- |
| `entry_exit_v2_shadow.csv` | Per-scan V1/V2 entry and exit proposals, trend health, MFE, and disagreements |
| `engine_differences.csv` | Only the entry/exit decisions where V1 and V2 differ |
| `engine_trade_events.csv` | Completed V1 and V2 engine events |
| `engine_trade_comparisons.csv` | Sequence-matched completed V1/V2 pairs with timing, price, $R$, and MFE deltas |

The completed-pair matching is currently by symbol, direction, and per-engine sequence. It is appropriate for paper comparison, but a later merge of the master `candidate_evidence` model is required before candidate-key matching, Candidate Intelligence version fields, Replay version fields, and multi-week Reports-page aggregation can be considered complete on this branch.

Do not enable the future `ENTRY_ENGINE` or `EXIT_ENGINE` routing flags until a documented evidence review confirms that V2 improves the stated metrics over a meaningful sample without degrading risk behavior.

### Trend Outcome And Primary Metric

Every completed V1 or V2 engine event records `stock_direction`, `trade_direction`, `stock_finish`, `trade_finish`, `trend_outcome`, `engine_captured_trend`, and `trend_capture_pct`. The Daily Validation Report writes the daily enrichment to `engine_trend_outcomes.csv` and highlights `STRONG_TREND_EXECUTION_FAILED` rows: cases where the stock moved at least $1R$ in the trade direction but the engine finished non-profitable.

`stock_finish` uses the latest scanner price available when the report runs. After market close, that is the end-of-day scanner outcome; before close, it is a provisional latest-price label. The `trend_capture_pct` event metric is $\max(0, \min(100, \frac{\text{final R}}{\text{MFE R}} \times 100))$ when MFE is positive.

Trend Capture % is the primary V2 engineering target. Win rate, average $R$, TES, MFE, and left-on-table remain guardrail metrics: V2 should not improve capture by materially degrading risk, win rate, or execution quality.

### V2 Learning Dataset

`app/analytics/v2_learning_dataset.py` creates one compact execution-learning record for every completed V1 and V2 shadow trade. It stores execution-specific features rather than duplicating raw scanner indicators:

- Entry timing: trend age, pullback number, bars since breakout, Entry Efficiency Score, EMA/VWAP/EMA20 distance, ATR extension, alignment score, and volume-confirmation score.
- Trade evolution: maximum/minimum/average trend health, trend-health standard deviation, MFE/MAE in $R$, bars held, and time held.
- Exit and outcome: exit phase/reason, final $R$, Trend Capture %, TES, left on table, grades/verdicts when available, and derived execution labels.

`app/analytics/v2_learning_writer.py` writes the daily dataset to `data/daily/YYYY-MM-DD/v2_learning_dataset.csv` and writes Parquet when available. V2 shadow state aggregates health/MFE/MAE continuously; V1 records use the same-entry observational metrics captured during its active lifecycle.

The **Validation** page shows a V2 Learning Summary for the day. The existing **Reports** page renders multi-day Execution Learning Trends for Trend Age, Entry Efficiency, Trend Capture %, TES, and V2 exit phase. The Daily Validation Report includes the same daily summary.

An optional future adapter can upsert these records into the master Candidate Evidence payload. It is intentionally not active on this branch because the `candidate_evidence` foundation is absent; merge that foundation before enabling candidate-key joins or payload writes.

## Offline Decision Replay

Saved scanner snapshots can be replayed without Polygon, market hours, or API access:

```powershell
python tools/replay_today.py --input data/daily/YYYY-MM-DD/scanner_output_close.csv
```

After market close, save both detailed and concise replay outputs:

```powershell
python tools/replay_today.py --input data/daily/YYYY-MM-DD/scanner_output_close.csv --output data/daily/YYYY-MM-DD/offline_replay.csv
```

This also writes `data/daily/YYYY-MM-DD/offline_replay_summary.csv` by default. The summary columns are `Symbol`, `Closest Setup`, `Readiness`, `Failed Conditions`, `Passed Conditions`, `Final Decision`, `Gate Failure Stage`, `First Failed Rule`, `Recommendation`, `Trade Block Details`, and `Replay Source`.

Replay now separates entry-rule failure from trade-block failure. Entry diagnostics explain setup readiness; trade-block details explain blockers such as RR, option spread, open interest, volume, quote age, option quality, and affordability with actual versus required values.

The Streamlit `Replay` page renders replay directly in the app: generate replay, review coverage, inspect biggest blockers, and read the replay summary table. Downloading CSVs is secondary and only needed for deeper offline review.

The replay tool reads persisted `ENTRY_DIAGNOSTICS_JSON` when available. If the JSON is not present, it rebuilds diagnostics from replay-ready scanner columns such as `ENTRY_EMA9`, `ENTRY_EMA20`, `ENTRY_VWAP`, `ENTRY_REL_VOLUME`, `ENTRY_BODY_STRENGTH`, `ENTRY_ATR`, `ENTRY_BREAKDOWN`, `ENTRY_LOWER_HIGH`, `ENTRY_RECENT_HIGH`, and `ENTRY_RECENT_LOW`.

Use `--output path/to/replay.csv` to save the replay result. Older scanner files that do not contain these `ENTRY_*` indicator columns cannot be replayed exactly; the tool reports which rows are missing replay indicators.

Daily validation checks after replay:

- Replay rows should equal scanner rows.
- Missing indicators should be `0`.
- Partial replay should be `0`.
- `ENTRY_SETUP_CANDIDATE`, `ENTRY_READINESS`, and `FAILED_ENTRY_CONDITIONS` should match between scanner output and replay output.

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

The main Streamlit page is now organized as a trading workstation with sidebar navigation: `Trading`, `Validation`, `Replay`, `Regression`, `Reports`, and `Developer`. The default `Trading` page answers the live operating questions: whether anything is tradable, why not, what is closest, and whether the engine is current. Developer diagnostics such as market coverage, action center, scanner watchlist, suggestion lifecycle, auto-paper logs, validation data health, telemetry, and last-seen candidates are kept under the `Developer` page.

The sidebar is intentionally trader-first: `Auto Refresh`, `Paper Automation`, compact `Downloads`, `Daily Validation`, and navigation. Raw engineering exports such as telemetry, paper state, candidate snapshots, lifecycle events, and audit files are hidden under `Downloads > Advanced`. Runtime key status is not shown in the trading UI.

Post-market workflow is one click from the sidebar: `Post Market: Generate Everything` builds the daily validation report, offline replay, replay summary, and refreshes the dashboard.

`Trading`, `Replay`, and `Developer` pages use one standardized metadata card at the top of the page. It shows scan id/data version, scanner timing, refresh age, symbol count, and data freshness (`CURRENT`, `STALE`, or `OUTDATED`) using the configured refresh interval. Scanner output rows include `Scan ID` and `Data Version` so dashboard state, replay, validation, and CSV/JSON artifacts can be traced back to the same scan.

The scanner writes `dashboard_state.json` under both `data/live/` and `data/daily/YYYY-MM-DD/`. This object is the dashboard's primary render source for the command center, current opportunities, blockers, and missed opportunities. It is built by `app/ui/dashboard_state.py` from scanner rows so the Streamlit UI does not need to recalculate business rules during live use.

If the dashboard is running locally on the same machine, the terminal command can read the same files directly. If the dashboard is running on Streamlit Cloud, generate the report inside the dashboard instead: use the sidebar `Generate Daily Validation Report` button, then download `daily_validation_report.html` from the same sidebar. That keeps report generation in the same filesystem where Streamlit created scanner output, telemetry, and state files.


## Paper Validation Performance Dashboard

The Streamlit dashboard includes a `Paper Validation Performance` section near the top of the operator view. It is intended to answer the daily operating questions without opening the full HTML daily report:

- Did the paper system close trades today?
- What is today's paper win percentage and loss percentage?
- What is today's total R and average R per closed paper trade?
- What is the overall paper win percentage, loss percentage, total R, estimated dollar P/L, and average R across available closed paper trades?

The section shows two metric rows:

- `Today`: closed paper trades whose `trading_day` equals the current trading day.
- `Overall`: all closed paper trades found across available root and daily event/state files.

Each row displays:

- `Closed`: number of closed paper trades included in the calculation.
- `Win %`: closed trades with `r_multiple > 0` divided by closed trades.
- `Loss %`: closed trades with `r_multiple < 0` divided by closed trades.
- `Total R`: sum of closed-trade `r_multiple` values.
- `Est. $ P/L`: actual trade P/L if available; otherwise `Option Risk At Stop × R multiple × contracts`; otherwise a configured-risk fallback.
- `Avg R`: average `r_multiple` per closed paper trade.

Flat trades with `r_multiple == 0` are counted in `Closed` but not in `Win %` or `Loss %`. That means win rate plus loss rate can be less than 100% when flat exits exist.

The dashboard reads closed paper-trade history from these sources, in order to support both local and Streamlit Cloud validation sessions:

- `paper_trade_events.csv`
- `data/daily/*/paper_trade_events.csv`
- `app/state/paper_trade_state.json`
- `data/daily/*/paper_trade_state.json`

Event logs are preferred because they preserve closed-trade history even if the current JSON state is later cleared. Paper-state JSON is used as a fallback and to enrich event rows with scanner context such as `Option Risk At Stop`, contract count, and `Paper Affordability Override`.

The dashboard also includes a collapsed `Closed paper trades used for performance` table with fields such as trading day, close time, symbol, direction, R multiple, estimated dollar P/L, affordability-override flag, exit reason, and source.

Treat this dashboard section as paper-validation reporting, not real account P/L. Paper validation may include affordability-overridden trades when `PAPER_IGNORE_AFFORDABILITY=true`, while real-trade readiness can still block those trades with `PAPER_ONLY_UNAFFORDABLE` when `REAL_REQUIRE_AFFORDABILITY=true`.

Prefer `Total R` and `Avg R` when judging strategy behavior. `Est. $ P/L` is an estimate unless actual option fill/exit P/L is available from the paper trade record. This keeps the dashboard useful before real broker fills, slippage, and option execution quality are fully validated.

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

Current DB-backed tables are intentionally compact historical facts; CSV, JSON, Parquet, and Excel remain the live/debug artifacts and Streamlit reads them first:

- `alert_events`: Telegram entry/exit attempts, sent status, skip/error reason, and dedupe key.
- `telegram_dispatch`: first-class transport audit for attempted, delivered, and failed Telegram sends, including scan/trade/symbol/direction/candidate context, decision, message type, parse mode, message length, attempt, latency, failure reason, structured Telegram response, and Telegram message ID when supplied by the sender.
- `paper_trades`: auto/manual paper trade opens and closes, with compact payload context.
- `scanner_runs`: scanner start/end status, row count, output path, and small run summary.
- `gate_decisions`: per-symbol action/gate summary for scanner rows.
- `candidate_snapshot`: normalized scanner candidates promoted after the daily snapshot file has been written.
- `rule_evaluation`: one structured gate record per scanner row/rule, including actual value, required value, pass/fail, blocked state, and priority. The entry gate emits these objects directly through `build_entry_gate_rule_evaluations()`; Telegram, Paper, and Review observations are normalized into the same batch when their scanner-row fields are available.
- `trade`: the canonical immutable completed-trade aggregate. Its `entry_facts`, `exit_facts`, and `outcome` payloads retain entry geometry/rules and exit capture/penalty facts without separate mutable peer tables.
- `event_stream`: the append-only timestamped event backbone. It records `CandidateCreated`, `CandidateStateChanged`, `Promoted`, `Demoted`, `RealtimeReady`, `RuleEvaluated`, `EntryOpened`, and `ExitTriggered`; the file-backed mirror is `data/daily/YYYY-MM-DD/trade_timeline.jsonl`.
- `candidate_outcome`: post-validation outcome facts derived from the market opportunity audit and replay outcome.

Do not store full candle history, option chain snapshots, scanner Excel blobs, or large CSV payloads in Neon during the free-tier phase. The compact Telegram send response is the deliberate exception: it is retained with the dispatch audit so `400` errors can be diagnosed from Telegram's response description.

DB writes are optional audit persistence, not part of the live trading decision path. All promoted-artifact writes run only inside `RuntimeScheduler` jobs: scanner artifacts use a normal-priority job after CSV/JSON/Parquet persistence; immutable entry/exit snapshots are queued only after their corresponding paper-event and snapshot CSV artifacts; candidate outcomes are queued only after **Generate Validation Report** succeeds. Failed DB writes log warnings and must not block Telegram sends, scanner output, paper trade JSON/CSV state, report generation, or dashboard rendering.

Telegram dispatcher ATTEMPT, SENT, and FAILED events continue to append to `data/live/telegram_dispatch_audit.jsonl` and now also queue best-effort `telegram_dispatch` rows through `RuntimeScheduler`. Each row records scan/candidate correlation and dispatch timing; a Telegram HTTP rejection also records the returned JSON/text response. Dispatch database persistence cannot delay or alter message delivery.

Candidate evolution does not have a separate entity. `signal_lifecycle_events.csv` retains each scan observation and now includes score, rank, entry readiness, RR, option quality, trend health, prior rank, rank change, and promotion/demotion reasons. `signal_state_transitions.csv` is also the source for delay attribution (`Rule`, `Scans`, `Minutes`). Telegram quality is objective and derived from candidate outcomes: a Telegram miss is an unsent candidate that became a winner; a false alert is a sent candidate that became a loser.

The cached Validation page renders objective **Telegram Misses**, **False Alerts**, lifecycle-derived **Delay Attribution**, and the daily **Candidate Outcomes** table. These are generated after validation/report processing and do not trigger dashboard-time scanner recomputation.

`Engineering Recommendation` is not an immutable trade fact. Trend capture retains factual capture, TES, verdict, trigger, and penalty information; reports and Trade Doctor generate current recommendations from those facts each time they run.

Use narrow idempotency keys only. `alert_events.dedupe_key` is the safest early unique key because Telegram duplicate protection already uses deterministic alert keys; failed send attempts and later successful retries can update the same audit row. Avoid broad unique constraints such as `UNIQUE(symbol)`, `UNIQUE(symbol, trading_day)`, `UNIQUE(trading_day)`, or `UNIQUE(option_ticker)`, because valid intraday flows can produce multiple scans, blocked decisions, opens, closes, re-entries, and refreshed option observations for the same symbol or contract. Add broader table constraints only through an explicit Neon migration and duplicate-write tests.

`app/db/migrations/001_promote_scanner_artifacts.sql` has been applied to the configured database through `DATABASE_DIRECT_URL`. The application intentionally does not execute schema DDL during a scan. For another environment, apply the migration manually before enabling DB writes, then test connectivity from the workspace root:

`app/db/migrations/002_telegram_dispatch.sql` creates the Telegram dispatch table and has also been applied to the configured database. `app/db/migrations/007_telegram_dispatch_audit_context.sql` adds direction, candidate correlation, policy/parse-mode, message-length, response, attempt, and latency fields; it has also been applied to the configured database.

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

## Entry Timing Notes

- Dashboard refresh can run every 1, 5, or 15 minutes, but full scanner cadence is currently selectable at 5 or 15 minutes. Because a full scan may occur one candle after an EMA touch/rejection, bearish EMA rejection detection uses a recent 3-bar EMA9 touch window while still requiring the current candle to close below EMA9 and EMA9 to remain below EMA20.
- Entry diagnostics use the same recent 3-bar EMA9 touch window for `EMA_REJECTION_SHORT`, so `REJECTED_EMA9` should no longer fail solely because the latest candle itself did not touch EMA9.
- Regime comparisons accept both short and long aliases where scoring/projection bonuses are applied: `TRENDING_BEAR`/`TRENDING_BEARISH` and `TRENDING_BULL`/`TRENDING_BULLISH`.

## Option Affordability

The scanner keeps two option concepts separate:

- Best quality contract: the strongest technical/liquidity contract, even if it is too expensive for the active account profile.
- Active affordable contract: the best contract that still passes quality gates and fits the configured capital profile.
- `app/options/options_recommender.py` returns a best-quality `primary` contract, a best affordable `affordable` contract when available, and an actionable `active` contract. `active` uses the affordable contract in `SOFT`/`HARD` modes when one exists; otherwise it falls back to the best-quality primary.
- The scanner validates the bundle's `active` contract first, then falls back through `primary`, `affordable`, `short_dte`, `longer_dte`, and ranked contracts before rejecting a setup for option liquidity. Duplicate tickers are skipped, so a fallback may appear as `ranked #2` when `ranked #1` is the same ticker as `active` or `primary`.
- Fallback execution is visible in logs as `[LIQUIDITY FALLBACK] Try ...`, `[LIQUIDITY FALLBACK] ... liquidity failed`, and `[LIQUIDITY FALLBACK] Accepted ...`. Scanner output also includes `Option Liquidity Attempts`, a JSON list of attempted source/ticker/code/reason/spread values for review.
- Each scan appends long-form liquidity diagnostics to `option_liquidity_attempts.csv` under the trading day's daily data folder. Rows include symbol, selected option ticker, attempt index/source/ticker/code/reason/spread, whether the attempt was liquid, and whether it was accepted.
- Each scan also writes a candidate funnel line to `candidate_funnel.jsonl` with counts for scanned, directional, entry ready, risk passed, option selected, liquidity passed, affordability passed, `EMA_REJECTION_SHORT`, `ENTER_PAPER`, Telegram attempted, Telegram sent, Telegram blocked, and Telegram block reasons. This is the quick check for whether the bottleneck is setup, options, affordability, or alerts. If `EMA_REJECTION_SHORT` exceeds `EMA_REJECTION_SHORT_WARNING_THRESHOLD` (default `10`), the scanner prints a warning that the recent rejection window may be too wide.
- The focused fallback validation is `python -m unittest tests.test_option_liquidity_fallback`. Full local validation uses `python -m unittest discover tests`. Run both from the workspace root with the project virtualenv active.

In `OPTION_AFFORDABILITY_MODE=HARD`, a high-quality but expensive option is marked `QUALITY_BUT_TOO_EXPENSIVE` instead of `ENTER_PAPER` at the scanner action layer. The dashboard still shows the best-quality contract for review, and research visibility can ignore affordability through `SUGGESTIONS_IGNORE_AFFORDABILITY=true` and `PAPER_IGNORE_AFFORDABILITY=true`. Paper entries opened under that override are tagged with `Paper Affordability Override` and original affordability/cost fields. Real-trade readiness remains affordability-gated by default through `REAL_REQUIRE_AFFORDABILITY=true`.

Small-account defaults are documented in [.env.example](.env.example) and [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example):

```env
OPTION_AFFORDABILITY_MODE=HARD
OPTION_CAPITAL_PROFILE=SMALL_ACCOUNT
DAILY_START_CAPITAL=2000
OPTION_STOP_LOSS_PCT=0.20
OPTION_MAX_RISK_PER_TRADE_PCT=0.10
OPTION_MIN_CONTRACT_COST=100
OPTION_PREFERRED_MAX_CONTRACT_COST=400
OPTION_MAX_CONTRACT_COST=500
OPTION_MIN_AFFORDABLE_DELTA=0.25
MAX_CONTRACTS_PER_TRADE=1
MAX_DAILY_REAL_LOSS=1000
```

The effective contract cap is the lower of `OPTION_MAX_CONTRACT_COST` and the risk-based cap from `DAILY_START_CAPITAL * OPTION_MAX_RISK_PER_TRADE_PCT / OPTION_STOP_LOSS_PCT`. With the defaults above, the static cap is `$500`, while the risk cap is `$2000 * 0.10 / 0.20 = $1000`, so the hard contract max controls. A `$500` contract with a 20% option stop implies `$100` risk against a `$200` max risk budget. The affordability metrics expose `risk_at_stop`, `max_allowed_risk`, `risk_based_max_contract_cost`, and `max_allowed_contract_cost` for review. `MAX_CONTRACTS_PER_TRADE=1` keeps scanner and paper sizing to one contract while the strategy is still being validated.

Position sizing uses the same `OPTION_STOP_LOSS_PCT` for estimated option loss, so projected sizing loss stays aligned with affordability risk-at-stop math.

Use `OPTION_CAPITAL_PROFILE=GROWTH_ACCOUNT` as buying power grows, or `OPTION_AFFORDABILITY_MODE=OFF` with `OPTION_CAPITAL_PROFILE=BEST_QUALITY` to return to the original best-quality-only behavior.

## Telegram Alerts

Telegram entry and exit alerts are opt-in and use duplicate protection so dashboard/scanner refreshes do not resend the same signal. Keep real bot tokens in local `.streamlit/secrets.toml` or Streamlit Cloud Secrets; do not commit them.

Subscriber messages are decision-first rather than scanner-first. `NEW TRADE` leads with `BUY NOW` or `SELL NOW`, then lists underlying entry/stop/target, $R$, Trade Quality or projected grade, confidence, recommended option strike/expiration/premium/cost/spread/liquidity, and the setup rationale. Every option-bearing lifecycle message (`NEW TRADE`, update, partial profit, continuation, and close) includes the original selected `Contract`, `Expiry`, and `Contract Cost`; `NEW TRADE` also retains the option premium. `TRADE UPDATE`, `STOP MOVED`, and `PARTIAL PROFIT` messages are compact state transitions. `TRADE CLOSED` leads with outcome and $R$, then explains the exit and includes Trend Capture % and left-on-table $R$ when MFE data is available. Internal action codes, quote provenance, and other scanner diagnostics remain in the dispatch audit rather than subscriber messages.

Subscriber sequencing is enforced per immutable `trade_id`: a delivered `NEW TRADE` / `TRADE_OPEN` is required before an update, partial-profit, continuation, or close can be sent for that trade. Terminal messages without that prior subscriber entry are suppressed and audited as `SUBSCRIBER_NEW_TRADE_NOT_SENT`; a cancellation is reserved for a suggestion that was never entered. Close messages suppress the holding-time line when reconstructed timestamps would produce a negative duration.

For local `.env` configuration, credential lookup accepts both `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` and the legacy lowercase `bot_token` / `chat_id` keys. The direct test utility is `python tools/send_test_telegram_alert.py`. If Telegram returns `Bad Request: chat not found`, refresh the chat ID after the bot has been started or added to the destination chat; rotate any token exposed in a terminal URL/error trace.

Exit alerts are allowed for explicitly tracked `PAPER`, `REAL`, and confirmed `SCANNER_TRACKED` V1 lifecycle trades. Successful exit alerts mark `exit_alert_sent` on the trade, and deterministic alert keys include the immutable lifecycle identity and exit reason.

Scanner entry-dispatch results include `Telegram Error Type`, `Telegram Error Reason`, and `Telegram Stage` in addition to eligibility, block reason, sent status, and alert score. Normal evaluations use `ENTRY_EVALUATION`; caught dispatch failures use `ENTRY_DISPATCH` with a normalized `TELEGRAM_ERROR_<EXCEPTION_TYPE>` reason. The **Downloads > Advanced** area exposes `data/live/telegram_dispatch_audit.jsonl` for operational inspection. For a Telegram `400`, inspect `telegram_response.description` to distinguish problems such as an invalid chat, malformed HTML entities, or an overlong message. These fields are audit data only and do not bypass alert-policy, duplicate, cooldown, or rate-limit controls.

Dashboard paper-entry audit rows record the resulting Telegram-entry outcome and `entry_source`, so manual paper entries are traceable alongside automated decisions. The paper trade itself is recorded independently of whether an optional Telegram notification succeeds.

```toml
TELEGRAM_ALERTS_ENABLED = "true"
TELEGRAM_ENTRY_ALERTS_ENABLED = "true"
TELEGRAM_EXIT_ALERTS_ENABLED = "true"
TELEGRAM_ALERT_POLICY = "PAPER"
TELEGRAM_MAX_ENTRY_ALERTS_PER_DAY = "3"
TELEGRAM_MAX_ACTIVE_ALERTED_TRADES = "2"
TELEGRAM_ENTRY_COOLDOWN_MINUTES = "60"
TELEGRAM_SYMBOL_COOLDOWN_MINUTES = "60"
TELEGRAM_TOP_CANDIDATE_LIMIT = "3"
TELEGRAM_MIN_ENTRY_ALERT_SCORE = "85"
TELEGRAM_INSTANT_ENTRY_ALERT_SCORE = "92"
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
SUGGESTIONS_IGNORE_AFFORDABILITY = "true"
PAPER_IGNORE_AFFORDABILITY = "true"
REAL_REQUIRE_AFFORDABILITY = "true"
ALLOW_REVIEW_TV_CHART_AUTO_PAPER = "false"
INDEX_REVIEW_MIN_SETUP = "82"
INDEX_REVIEW_MIN_RR = "1.8"
INDEX_REVIEW_MIN_OPTION_QUALITY = "90"
INDEX_REVIEW_MAX_SPREAD_PCT = "3"
INDEX_REVIEW_MAX_QUOTE_AGE_MINUTES = "3"
INDEX_REVIEW_MIN_SCANS = "2"
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

For the current validation phase, use `TELEGRAM_ALERT_POLICY=PAPER`. Scanner eligibility remains shared, but subscriber delivery is lifecycle-based: confirmed V1/paper opens publish `NEW TRADE`, a multi-day position publishes one next-session `POSITION CONTINUES` message, updates publish only for material $R$/health/confidence/stop/partial transitions, and exits publish once with available Trend Capture %. Scanner `ENTER` rows and `ACTIVE_TRADE` refreshes do not publish.

Real-trade readiness is dashboard guidance only. `REAL_TRADING_ENABLED=false` and `REAL_ALERTS_ONLY=true` keep the app in manual-review mode; no real orders are placed. Rows marked `A_PLUS_REAL_REVIEW` must already have an active paper trade open for the same symbol, be `ENTER`, `ENTER_PAPER`, or `REVIEW_TV_CHART`, be `BULLISH_TOP_1` or `BEARISH_TOP_1`, meet the real thresholds, have a live quote age within `REAL_MAX_QUOTE_AGE_MINUTES`, avoid late/chase and missed-move flags, avoid event/regime blocks, appear in at least two consecutive suggested-trade scans, remain under `MAX_DAILY_REAL_LOSS`, and occur before `REAL_ENTRY_CUTOFF_ET`. The dashboard shows `Paper Trade Opened`, `Real Trade Readiness`, `Real Review Scan Count`, and `Real Entry Checklist` for manual tiny-trade review only.

Affordability is intentionally split by workflow: suggested-trade lifecycle and paper validation can include technically valid expensive setups for research when the ignore flags are enabled, but real-trade readiness and real/Telegram safety checks remain affordability-gated. Auto paper entries normally respect setup, RR, quote freshness, bid/ask, spread, option quality, event/regime, cooldown, duplicate, and daily-cap checks. When `PAPER_IGNORE_AFFORDABILITY=true`, affordability can be bypassed for paper validation only and the trade is tagged with `Paper Affordability Override`. Telegram and real-trade readiness may remain stricter.

Paper auto-exits still honor stop, target, live exit signal, momentum/VWAP/EMA invalidation, failed-breakout/breakdown invalidation, and profit-threshold exits before any end-of-day rule. For paper validation, keep `Auto Exit` enabled so those protections apply to every profile. When `End-of-day Auto Close` is enabled, the holding policy closes `INTRADAY` positions at `AUTO_PAPER_EOD_CLOSE`; `MULTIDAY` positions remain open unless an ordinary exit rule fires.

## Validate

```powershell
python -m unittest tests.test_market_session_decisions
```

The current environment may not have `pytest` installed. The pytest-style Telegram exit-price tests can still be exercised by importing and calling their test functions, or by installing pytest in the project venv.
