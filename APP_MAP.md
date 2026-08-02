# App Map — Dravya Trade Works

Working reference built by reading the code, the live DB, and today's artifacts on 2026-07-29.
`Project_state.md` and `README.md` are the design-intent docs (~2000 lines combined). This file is
the shorter *"what actually exists and how it connects"* map, plus verified observations.

---

## 1. What the app is

A Python intraday **stock/options scanner + paper-trading validation harness** with a Streamlit
review front-end. It does **not** place real orders (`REAL_TRADING_ENABLED=false`,
`REAL_ALERTS_ONLY=true`). Everything is currently paper trading plus evidence collection.

Two processes, one shared data layer:

| Process | Command | Role |
| --- | --- | --- |
| Scanner | `python -m app.runtime.scan_loop` (loop) or `python -m app.main` (one scan) | Sole owner of the trade lifecycle: market data, decisions, paper open/manage/close, Telegram, all artifacts |
| Dashboard | `streamlit run app/dashboard.py` | Read-only w.r.t. trade state. Can *trigger* a scan; never opens, updates, or closes a trade |

There is no `streamlit_app.py` — `app/dashboard.py::main()` is the Streamlit entry point.

**Ownership:** the scanner is the sole owner of the trade lifecycle. The dashboard can
trigger a scan (`Run scanner now`, auto-refresh) but no longer runs entries, exits, or
suggestion sync. See the 2026-07-29 section of `Project_state.md` for what moved and why.

---

## 2. Scanner pipeline (`app/main.py`, ~6900 lines)

`run_scanner()` → `_run_scanner_impl()` → queues `_finalize_scan_outputs()` → `_persist_scan_outputs()`.

### 2.1 Identity assigned at scan start
```
trading_day = YYYY-MM-DD
session_id  = paper_validation_YYYY-MM-DD
scan_id     = YYYY-MM-DD_HHMMSS
generation  = ScanGeneration.new(scan_id)     # stamped into live JSON for staleness checks
```

### 2.2 Foreground, per symbol (sequential decisions, parallel data fetch)

Watchlist from `app/config/watchlist.py` (26 symbols; `DYNAMIC_WATCHLIST_ENABLED=false` by default).
Data prefetch is a bounded `ThreadPoolExecutor` (`SCANNER_MAX_WORKERS=5`).

| # | Stage (`stage_timer` label) | Module |
| --- | --- | --- |
| 1 | Market Data — 5m Polygon aggregates, retry/backoff, token-bucket rate limit, 30s TTL cache | `app/utils/polygon_client.py`, `app/indicators/technical_indicators.py` |
| 2 | Resample 5m → 15m → 1h | `app/utils/timeframe_resampler.py` |
| 3 | Indicators — EMA/VWAP/MACD/RSI/ATR/ORB/structure/breakout/S-R | `app/indicators/technical_indicators.py`, `enrich_indicators.py` |
| 4 | Strategy — momentum score per timeframe | `app/strategies/momentum_strategy.py::analyze_setup` |
| 5 | Entries — BREAKOUT, EMA_PULLBACK, EMA_REJECTION_SHORT, BREAKDOWN_SHORT, VWAP_REJECTION | `app/strategies/entry_engine.py::detect_entry` |
| 6 | Entry V2 Shadow (observational) | `app/strategies/entry_engine_v2.py` |
| 7 | Risk — stop/target/RR, ATR floor, direction geometry guard, `RR >= 1.5` | `app/risk/risk_manager.py::calculate_risk` |
| 8 | Exit V2 / Risk V2 independent shadow | `app/exit/exit_engine_v2.py` |
| 9 | Paper Trades — manage existing open trade, live exit evaluation | `app/state/paper_trade_manager.py`, `app/exit/exit_engine.py::evaluate_exit` |
| 10 | Options — chain fetch, filter, rank, affordability, quote refresh | `app/options/*` |
| — | Entry gate + diagnostics + rule evaluations | `app/gates/entry_gate.py`, `app/diagnostics/entry_diagnostics.py` |

Session gating (`get_market_session`): premarket 4:00–9:30 → `PREMARKET_WATCH` (watch only);
9:30–9:45 → `OPENING_RANGE_CONFIRMATION`; after 9:45 → `ENTER`/`ENTER_PAPER` possible.
Auto-paper entry window is 9:45–15:30 ET.

Price geometry is a hard gate: CALL needs `stop < entry < target`, PUT needs `target < entry < stop`.

### 2.3 `_finalize_scan_outputs` (high-priority, non-cancelable runtime job)
1. Print operator table
2. Candidate persistence → relative strength → `rank_candidates` → init execution fields
3. Build `EngineHealth` + health score
4. **Auto-paper entries** (`run_auto_paper_entries`)
5. **Telegram dispatch** (`_dispatch_telegram_entry_alerts`)
6. Candidate funnel
7. `_persist_scan_outputs(...)`
8. Queue cache builders: validation (normal), replay / report / telemetry (low)

### 2.4 `_persist_scan_outputs` — the artifact writer
In order: Entry/Exit V2 shadow → Learning Engine → Quote attribution → Market opportunity audit
→ Option liquidity audit → Candidate funnel → **`dashboard_state.json`** → Engine health
→ Candidate snapshots + regression snapshot → Signal lifecycle → Recommendation outcomes
→ Candidate evidence → Candidate intelligence → Activity trace → Excel `scanner_output.xlsx`
→ stage profile → queue `persist_scan_artifacts_db` (normal) → queue `persist_regression_snapshot`.

Nearly every stage is individually `try/except`-wrapped and prints `[... WARNING]` on failure —
**partial persistence is silent by design**. This matters a lot when debugging "the page is empty".

### 2.5 Runtime scheduler
`app/runtime/runtime_scheduler.py` — priorities CRITICAL/HIGH/NORMAL/LOW, `cancel_old_jobs(scan_id)`
drops stale cancelable work, metrics to `data/runtime_metrics.csv`, queue state to
`data/live/runtime_state.json`. `app/background/background_queue.py` is a separate single daemon
worker for best-effort DB writes.

---

## 3. Streamlit pages

Sidebar: **Navigation radio** → **System** status → **Auto Refresh** → **Paper Automation** →
**Operations** → **Downloads**. The separate `Scan Engine` panel was removed on 2026-08-01 — it
rendered four captions the System block already carried, so it was two copies of one fact.
`_ensure_scan_engine_started` keeps the job that panel actually did (every render must confirm the
daemon thread is alive, because it only exists inside the Streamlit process); it no longer draws
anything. `Operations` leads with `Post Market: Generate Everything`, with the individual
generators behind a `Run one at a time` expander. `Downloads` is the review export plus the three
finished reports; the per-file buttons are gone. `paper_trade_state.json` is in `paper_trades` and
`telegram_dispatch_audit.jsonl` is in `telegram_dispatch`, so those were second copies of a durable
record. **`suggested_trade_state.json` is the one piece of live state with no table** — the review
export carries it under `state/`, and that is currently its only durable home.

**Developer → Database Tables** describes all 32 tables with grain and purpose, from
`app/db/catalog.py`, alongside live row counts. Grain is stated because it is what surprises:
`candidate_evidence` is one row per candidate per *day* while `candidate_snapshot` is one per
candidate per *scan*. A test fails when a migration creates a table the catalog does not describe,
and the page warns about any table present in the database but missing from it. Navigation and System
are `st.sidebar.container()` placeholders claimed first and filled last, because the controls
between them start the scan engine and return state the routing needs. Navigation previously sat
fourth, under three blocks of controls. `_render_system_status` answers "is the machine healthy"
— engine state, last/next scan, scan and failure counts, DB writes, Polygon key — and absorbs the
orphaned `_render_runtime_key_status`, which nothing had called.

Navigation is **Trading / Validation / Research / Developer**. Replay, Regression, Reports and
Learning became tabs on `app/ui/pages/research.py` on 2026-07-31; each tab keeps its own
cached-state-then-frame fallback, and the scanner frame is loaded lazily so opening Research for
the Regression tab costs no read of `scanner_output.xlsx`. `_migrate_dashboard_page` redirects a
session left on a folded page name — Streamlit raises when a radio's stored session value is not
one of its options, so an open tab would otherwise break on its next rerun after the redeploy.

Downloads were consolidated on 2026-07-31 into a single `Downloads` expander. Before that they
were split across the Operations block and a `Tools: Downloads` expander, which served the
validation report, the replay summary and `scanner_output.xlsx` from two places each — 20 buttons
covering 17 files. It is now: **Daily review export** (built on click, not on every rerun — the
build reads every daily artifact and rebuilds the waterfall, so eager building charged that to
each rerun), the validation report, the replay summary, and an `Individual files` expander.
`build_daily_review_export` now also carries `raw/` verbatim copies of the artifacts that exist
**only** on the container filesystem and `state/` copies of the operator state, so one click
preserves what a redeploy would otherwise wipe. Buttons removed as redundant or dead:
`scanner_output.xlsx` (root legacy file, not what the page reads), `trade_state.json` (legacy),
`auto_paper_decision_log.json` (capped copy of a CSV Postgres also holds), `candidate_snapshots.csv`
(never written — the writer prefers parquet).

| Page | Question | Primary source | Fallback |
| --- | --- | --- | --- |
| **Trading** | What is the engine doing right now? | `data/live/dashboard_state.json` (5s TTL) + `paper_trade_state.json` + `activity_trace.csv` + telegram audit jsonl | `scanner_output.xlsx` full path |
| **Validation** | Did execution behave well today? | `validation_state.json` (60s TTL) | trade-efficiency CSV path in `dashboard.py` |
| **Replay** | What would saved scanner state have done? | `replay_state.json` (mtime) | `offline_replay*.csv` |
| **Regression** | Would current code have improved an archived day? | Neon `scanner_snapshot` / `scanner_regression_baseline` / `regression_run` | local `data/regression/` cache |

**HSR was unrunnable until 2026-07-31.** `app/db/connection.py` only read `os.getenv("DATABASE_URL")`
and relied on another module having called `load_dotenv()` as an import side effect. Entry points
that reach the database directly do not import those — `tools/regression_runner.py` imports only
`app.regression` — so the URL was unset, and `ScannerSnapshotRepository.load_day`'s bare
`except: return []` reported a configuration fault as "No scanner snapshots in Neon or local
fallback" for a day holding 702 archived rows. `connection.py` now loads `.env` itself with
`override=False` (real env and Streamlit Secrets still win, which is what Cloud needs), and the
repository announces read failures instead of swallowing them. `regression_runner.py` also
reconfigures stdout to UTF-8: the verdict strings carry emoji and the tool completed a whole
regression before dying printing its own result on a cp1252 console.
| **Reports** | Am I improving across days? | `report_state.json` (mtime) | daily validation HTML panel |
| **Learning** | What is the shadow/V2 evidence saying? | Neon `daily_engine_summary` → `data/live/daily_engine_summary.json` | — |
| **Developer** | Is the system healthy? | `runtime_state.json`, `runtime_health.json`, `runtime_*.csv` | lazy `Load ...` toggles per panel |

Page bodies live in `app/ui/pages/*.py` but **all shared helpers still live in `app/dashboard.py`
(~8,470 lines after the 2026-07-31 cleanup)**; the page modules import back into it. The orphaned
`DEPRECATED` renderers (`_render_command_center`, `_render_current_opportunities`,
`_render_today_performance`, `_render_why_no_trade`, `_render_missed_opportunities`,
`_render_trading_page*`) were deleted on 2026-07-31; `_metadata_status`, `_status_label`,
`_scan_metadata` and `_render_metadata_card` sat inside that block and are still live.

Trading page detail (`app/ui/pages/trading.py`), reworked 2026-07-31 into an operator console:
operator bar (trading day, LIVE / POST-MARKET, engine state) → health cards (engine, last-scan
age, scans/failures, DB writes, Telegram, book) + market pulse → **Book** as position cards with
an R gauge + Active Risk Monitor → **Current Opportunity Board** intraday, **Today's Result**
after 16:00 ET → Activity Feed, now collapsed because it re-reads and re-sorts the full activity
trace (17,742 rows on 2026-07-31) on every rerun.

Health tones are stated by the caller, not inferred from the value text as `_render_compact_card_grid`
does — "990m ago" carries no keyword a matcher could read. Scan age is scored against the session:
stale is a fault before 16:00 ET and expected after it. `app/ui/components.py` holds the shared
`status_card_grid`, `operator_bar` and `position_card`; the R gauge is anchored on **-1R**, not
zero, because position risk is only readable against the stop — half a bar means breakeven.

The page was split on 2026-07-31: `app/ui/render_context.py` owns data access, `app/ui/pages/activity.py`
owns the timeline, and `trading.py` renders. `RenderContext` reads each source **at most once per
render** through `cached_property`, and a source no renderer asks for is never read at all. Before
this, one render read `paper_trade_state.json` four times, re-parsed the telegram audit two to
three times and read `paper_trade_events.csv` twice — on a page that auto-refreshes all session.

`app/ui/timestamps.py` is the only correct way to parse this app's timestamps. `format_timestamp`
([app/main.py](app/main.py)) **used** to render with `%Z`, so `Current ET` and everything derived
from it — including `activity_trace.csv` — carried `2026-07-31 00:38:19 EDT`. Pandas parses that
with a FutureWarning, would raise in a later version, and meanwhile silently dropped the zone,
turning an Eastern instant into a naive one four hours off. Since 2026-07-31 the writer emits a
numeric offset (`2026-07-31 00:38:19-04:00`), which pandas reads natively and which follows DST;
naive inputs are stamped Eastern rather than left ambiguous. **Archived files still hold the
abbreviation form**, so every read must accept both — `to_utc` / `to_utc_series` do, and treat
naive values as Eastern because that is what the column name promises.

`app/ui/trade_chart.py` draws 5m candles from `candles_5m.csv` with the engine's own entry, stop,
target and exit marked, and emits TradingView deep links (`REVIEW_TV_CHART` is the most common
action status the engine produces — 238 of 403 auto-paper decisions on 2026-07-31 — and had no
in-app destination before this).

**Exit analysis survives a container wipe.** Migration `025_trade_exit_analysis.sql` adds
`trade_exit_analysis`, one row per completed trade, written best-effort from
`append_trend_capture_row` **after** the CSV so a database problem never costs the file. It holds
the indicator state at exit (`ema9`, `ema20`, `vwap`, `macd`, `rsi`, `atr`, `relative_volume`,
`bars_held`) and the analysis on top of it (`trend_capture_pct`, `left_on_table`, `mfe`, `mae`,
`exit_quality`, `exit_verdict`), plus the whole CSV row in `payload` — that file has grown columns
twice, and a re-run should not need a migration. None of this was in Postgres before:
`candidate_evidence.trend_capture` is candidate-grain and `exit_quality_metrics` is a daily
aggregate that was all nulls on 2026-07-31.

`app/analytics/post_market_review.py` reads that table into a plain-English page — one paragraph
per trade covering what it made, what was available, why it closed, and whether the exit looks
right. It is **not** the daily validation report, which is a 23-section engineering diagnostic
(gate quality per window, quote freshness, replay calibration, backtest validation) aimed at
finding defects. `Post Market: Generate Everything` writes both; `tools/post_market_report.py
--date` writes the review alone. It falls back to the day's CSV when the database is unreachable,
and refuses to quote a capture percentage when the stored value is nonsense — a losing 2026-07-30
row records −2211%.

**Candles survive a container wipe.** `candles_5m.csv` sits on the ephemeral filesystem, which is
how 2026-07-31's bars were lost, but the same bars are already durable in Neon: every
`scanner_snapshot` row carries a `market_payload` with the last 200 5m, 80 15m and 40 1h bars.
`load_candles` falls back to the newest archived payload when the local file has nothing for the
symbol, so a redeployed container can still draw the day at no new storage cost. That fallback is
only as good as the archive, which is why the Trading page now carries an **Archive** health card —
it reads `NOT RECORDING` in red when the engine has run scans but none reached `scanner_snapshot`,
the exact silent state that lost 2026-07-31. The archive requires `REGRESSION_SNAPSHOT_ENABLED=true`
in Streamlit Secrets; `secrets.toml.example` ships it **false**.

Both candle-file defects were fixed at the writer on 2026-07-31, but **files written before that
still carry them**, so the reader keeps its defences. Polygon anchors aggregate windows to `from_`,
and `from_` was "now minus N days" unrounded, so every scan requested a different grid: the file
held several interleaved 5m grids a minute or two apart (NVDA carried three) on top of ~25% exact
duplicates. `technical_indicators.floor_to_bar` now snaps the window to a bar boundary — which also
restores the `get_aggs_cached` hit rate, since the cache keys on `from_` — and
`_append_daily_candles` skips `(symbol, timestamp)` pairs already in the file, seeding its key set
from disk so a mid-session restart does not start duplicating again.

The Opportunity Board and the activity trace both render the **five independent layers**:
`Scanner Recommendation` (`ENTRY_RECOMMENDED`) → `Execution Eligibility` / `Execution Outcome` /
`Execution Reason` → `Trade Status` (`NOT_CREATED` / `OPEN`) → `Telegram Status` / `Telegram Reason`.
Legacy `Action Status=ENTER_PAPER` is kept for compatibility but does **not** mean a trade opened.

---

## 4. Decision → execution → notification chain

```
Momentum score
  → entry setup detected
    → risk allows (RR ≥ 1.5, geometry ok)
      → option selected (liquidity, spread, OI, volume, quality ≥ 65, DTE 10–45, quote fresh)
        → affordability (SMALL_ACCOUNT, contract ≤ $500, paper override allowed)
          → Action Status = ENTER_PAPER            ← scanner RECOMMENDATION only
            → auto-paper gates                     ← EXECUTION
                 entry window, top-3 candidate, entry gate, realtime, bid/ask,
                 event/regime, direction, duplicate, cooldown (45m), capacity
                 (MAX_ACTIVE_PAPER_TRADES=1), daily limit (MAX_DAILY_ENTRIES=5)
              → open_paper_trade()                 ← TRADE
                → Telegram NEW TRADE               ← NOTIFICATION
```

Auto-paper outcome for every candidate is written as `OPENED` / `BLOCKED` / `SKIPPED` to
`data/daily/<day>/auto_paper_decisions.csv` (full audit) and `app/state/auto_paper_decision_log.json`
(capped at 500 for the UI).

**Telegram is a transport, not a second decision engine.** Six subscriber message types only:
`NEW TRADE`, `TRADE UPDATE`, `PARTIAL PROFIT`, `POSITION CONTINUES`, `TRADE CLOSED`,
`TRADE CANCELLED`. Lifecycle identity is the immutable `trade_id`; downstream messages are
suppressed as `SUBSCRIBER_NEW_TRADE_NOT_SENT` if no `NEW TRADE` was delivered.
`TELEGRAM_DISPATCH_MODE=QUEUED` in `.env`, so sends go through a critical `RuntimeJob` and
sent-state is persisted only in the after-success callback.

Exit precedence (`app/exit/exit_engine.py::evaluate_exit`, the single source of truth):
hard stop → hard target → EMA → VWAP → MACD → failed breakout → time exit → near-close exit.
Multiday adds profit protection (+2R peak locks +1R; after +3R peak a 1R giveback exits).
`app/trade_manager.py` is legacy/reference only.

Holding profile (`app/state/holding_policy.py`): `INTRADAY` vs `MULTIDAY`, frozen at entry,
changeable only via `MANUAL_OVERRIDE` / future `BROKER_SYNC`.

---

## 5. State files (`app/state/`)

| File | Role | Current |
| --- | --- | --- |
| `paper_trade_state.json` | **The** managed-trade state | `{}` (empty) |
| `trade_state.json` | Legacy scanner state; promoted once on first lookup | `{}` (empty) |
| `suggested_trade_state.json` | Suggestion lifecycle / cancellation source | populated |
| `telegram_alert_state.json` | Dedupe + sent-state | 22 KB |
| ~~`auto_paper_settings.json`~~ | Removed. Auto-paper controls are env vars; the sidebar wrote this on a host the scanner does not run on | gone |
| `auto_paper_decision_log.json` | Capped UI copy of auto-paper decisions | 22 KB |
| `signal_memory.json` | Signal memory | small |

---

## 6. File artifacts

**`data/live/`** — latest-scan caches, atomically written with generation metadata:
`dashboard_state.json`, `validation_state.json`, `replay_state.json`, `report_state.json`,
`daily_engine_summary.json`, `runtime_state.json`, `runtime_health.json`,
`runtime_performance_summary.json`, `regression_snapshot_metrics.json`,
`telegram_dispatch_queue.jsonl`, `telegram_dispatch_audit.jsonl`, `quote_refresh_events.jsonl`.

**`data/daily/YYYY-MM-DD/`** — the durable daily fact set (one folder per trading day, many scans):
`candidate_snapshots.parquet|csv`, `candidate_evidence.csv` + `candidate_evidence_status.json`,
`candidate_intelligence.csv` + summary, `activity_trace.csv`, `auto_paper_decisions.csv`,
`market_opportunity_audit.csv`, `option_liquidity_audit.csv`, `candidate_funnel.json`,
`signal_lifecycle_events.csv` + `signal_state_transitions.csv`, `paper_trade_events.csv`,
`trend_capture_analysis.csv`, `trade_exit_snapshots.csv`, `engine_health_history.csv`,
`scanner_stage_profile.csv`, `runtime_performance.csv`, `offline_replay*.csv`,
`daily_engine_summary.json`, `high_score_execution_audit.csv`, per-page cached JSON,
`scanner_snapshots/` (regression), `manifest.json`.

Also: `data/recommendation_outcomes/` (append-only recommendation facts + 5/10-session horizon
outcomes), `data/regression/YYYY-MM-DD/` (baseline + HSR results), `telemetry/trade_telemetry.csv`,
`reports/daily_validation_YYYY-MM-DD.html`, root `scanner_output.xlsx`.

---

## 7. Database (Neon Postgres) — verified live

Connection OK. `DB_WRITE_ENABLED=true`. All DB writes are best-effort and never block the scanner.
**29 tables exist.** Row counts as of 2026-07-29:

| Table | Rows | Purpose |
| --- | --- | --- |
`rule_evaluation` | 40,159 | one row per evaluated rule (actual vs required, pass/fail, phase)
`event_stream` | 48,878 | trade lifecycle events
`decision_waterfall` | 27,288 | per-rule candidate decision path (audit only)
`gate_decisions` | 22,283 | gate decision summaries
`alert_events` | 20,729 | Telegram alert audit
`activity_trace_event` | 10,317 | chronological per-ticker operational trace
`candidate_snapshot` | 6,552 | normalized scanner row per scan/symbol
`scanner_snapshot` | 936 | immutable regression archive (decision + market payload)
`scanner_runs` | 864 | scan run records
`candidate_outcome` | 793 | resolved candidate outcomes
`candidate_evidence` | 606 | one row per day+symbol+direction+setup
`telegram_dispatch` | 481 | dispatch audit context
`v2_learning_metrics` | 42 | V2 shadow metrics
`analytics_summary` | 29 | resolved setup/regime/lifecycle aggregates
`trade` | 14 | completed trade facts
`paper_trades` | 13 | all `CLOSED`
`daily_engine_summary` / `exit_quality_metrics` | 6 / 6 | daily learning summaries
`recommendation_fact` | 4 | immutable `ENTRY_RECOMMENDED` facts
`feature_statistics` | 4 | feature lift stats
`scanner_regression_baseline` | 3 | frozen day baselines
`feature_registry`, `promotion_review`, `quote_attribution`, `recommendation_horizon_outcome`, `regression_run`, `regression_result`, `rule_performance`, `trade_comparison` | 0 | created, not yet populated

Evidence coverage: **7 distinct evidence days, 8 snapshot days, 13 completed paper trades.**
The documented freeze threshold is 20 days / 80–100+ completed trades, so V1 is still well inside
the evidence-freeze window.

Migrations `001`–`021` live in `app/db/migrations/` and are applied **manually** through
`DATABASE_DIRECT_URL` — scans never execute DDL. Spot checks confirm `012`, `019`, `020` are applied
(`paper_trades.holding_profile`, `candidate_evidence.execution_eligibility`,
`candidate_snapshot.telegram_status` all present).

---

## 8. Config (`.env`, `app/config/settings.py`, `app/config/performance.py`)

Notable current values:

```
APP_ENV=production                REAL_TRADING_ENABLED=false / REAL_ALERTS_ONLY=true
USE_MOCK_MARKET_DATA=false        USE_MOCK_OPTIONS=false
REALTIME_MARKET_DATA_REQUIRED=true / REALTIME_OPTIONS_REQUIRED=true
MAX_STOCK_DATA_DELAY_MINUTES=2    ← tight; drives STALE_STOCK_DATA rejections
ACCOUNT_SIZE=2000  RISK_PERCENT=10  MAX_CONTRACTS_PER_TRADE=1
OPTION_MIN_DTE=10  PREFERRED 14–30  MAX 45   OPTION_MIN_QUALITY_SCORE=65
OPTION_MAX_SPREAD_PCT=10  MIN_VOLUME=100  MIN_OI=500  0DTE/1DTE=false
OPTION_AFFORDABILITY_MODE=HARD    OPTION_MAX_CONTRACT_COST=500
PAPER_IGNORE_AFFORDABILITY=true   SUGGESTIONS_IGNORE_AFFORDABILITY=true
MAX_ACTIVE_PAPER_TRADES=1         MAX_DAILY_ENTRIES=5
AUTO_PAPER_ENABLED=true           AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES=45
ALLOW_REVIEW_TV_CHART_AUTO_PAPER=true
TELEGRAM_DISPATCH_MODE=QUEUED     DB_WRITE_ENABLED=true
REGRESSION_SNAPSHOT_ENABLED=true  ← currently ON, so every clean scan archives snapshots
PERFORMANCE_DASHBOARD_STATE_ONLY=true  (Trading renders from JSON when automation is off)
```

`validate_runtime_settings()` hard-fails startup if mock data is on, 0/1DTE is allowed, DTE
ordering is wrong, spread limit is outside 0–15, or the output dir isn't writable.

---

## 9. Verified observations (first audit, 2026-07-29)

> **Status: mostly resolved the same day.** Items 1-8 and 11 were fixed; see the
> `2026-07-29` section of `Project_state.md`. Kept here as the original findings
> and because the reasoning explains why several subsystems look the way they do.
> Still open: item 6 (eight tables have no migration file) and item 10 (doc drift,
> now corrected).

These are things I confirmed by reading files/DB, worth checking against the issues you saw today.

1. **Today's daily folder is nearly empty.** `data/daily/2026-07-29/` contains only
   `auto_paper_decisions.csv` and an empty-header `recommendation_outcomes.csv`. No candidate
   snapshots, no `activity_trace.csv`, no `dashboard_state.json`, no candidate evidence. A full
   `_persist_scan_outputs()` never completed today.

2. **No page caches exist at all.** `data/live/` has no `dashboard_state.json`,
   `validation_state.json`, `replay_state.json`, `report_state.json`, or `runtime_health.json`.
   Every page therefore takes its legacy fallback path, and Trading falls back to reading
   `scanner_output.xlsx` — which was last written **2026-07-27 15:22**.

3. **`runtime_state.json` is stale** (`updated_at_utc = 2026-07-27T21:47Z`), i.e. the runtime
   scheduler has not written queue state since the 27th.

4. **`MISSING_SCANNER_FIELDS` in today's auto-paper log.** Rows show NVDA with
   `action_status=ENTER_PAPER`, `scanner_recommendation=ENTRY_RECOMMENDED`, skipped for
   `MISSING_SCANNER_FIELDS:Setup Valid,Candidate Entry Price,Candidate Stop Price,Candidate Target
   Price,Candidate RR,Next Condition,Live Chart Checklist`
   (`app/runtime/paper_automation_support.py:321`, against `AUTO_PAPER_REQUIRED_COLUMNS`).
   The on-disk `scanner_output.xlsx` **does** have all 7 of those columns (148 columns, 16 rows), so
   the dataframe passed to auto-paper was not that file. A companion `SYSTEM` row in the same scan
   says *"no eligible entry candidates and no symbol rows found"* — which contradicts the NVDA
   `ENTER_PAPER` row in the same batch. Worth tracing which df reached `run_auto_paper_entries`.

5. **Four tables the code writes to do not exist in the database**, so those best-effort writes are
   silently failing forever:
   - `scanner_run` (singular) — `app/db/scanner_run_repository.py:7`
   - `daily_session_summary` — `app/db/daily_summary_repository.py:6`
   - `missed_winner_analysis` — `app/db/loss_attribution_repository.py:5`
   - `trade_efficiency` — `app/db/trade_efficiency_repository.py:6`
   None of the 21 migrations creates them. Note `scanner_runs` (plural, written by
   `app/db/persistence.py:250`) **does** exist with 864 rows — so there are two competing
   scan-run schemas and only the plural one works.

6. **Eight tables have no migration file** even where they exist in the DB (`paper_trades`,
   `gate_decisions`, `alert_events`, `scanner_runs`, plus the four above). Whatever created them was
   applied out-of-band, so a fresh environment cannot be rebuilt from `app/db/migrations/`.

7. **Duplicate `paper_trades` writers**: `app/db/persistence.py:172` and
   `app/db/paper_trade_repository.py:10`.

8. **2 of 174 unit tests fail** (`python -m unittest discover -t . -s tests`):
   - `test_scanner_diagnostics.test_telegram_dispatch_returns_enter_paper_and_sent_counts` —
     `summary["attempted_count"]` is `0`, expected `2`.
   - `test_trend_capture.test_paper_close_hook_appends_trend_capture_row` — helper now returns the
     row dict instead of the filename `"trend_capture_analysis.csv"`.
   Both look like assertions left behind by a recent signature/behaviour change rather than new
   logic bugs, but #1 touches Telegram attempt accounting, which is worth confirming.
   (Docs still claim "134 tests pass".)

9. **`paper_trade_state.json` and `trade_state.json` are both empty**, and all 13 rows in
   `paper_trades` are `CLOSED` — there is no open position anywhere right now.

10. **Doc drift:** `Project_state.md` lists the sidebar as Trading / Validation / Replay /
    Regression / Reports / Developer, but `dashboard.py:9255` also includes **Learning**.

11. **Silent-failure design is the main debuggability cost.** Almost every persistence stage catches
    broadly and prints `[X WARNING]`. Combined with page caches being optional, a failed stage shows
    up in the UI as an empty or stale panel rather than an error. There is no single "did this scan
    persist everything?" indicator surfaced in the UI.

---

## 10. Quick orientation cheatsheet

| I want to… | Look at |
| --- | --- |
| Change how a setup is detected | `app/strategies/entry_engine.py`, `momentum_strategy.py` |
| Change stop/target/RR | `app/risk/risk_manager.py` |
| Change why a trade did/didn't exit | `app/exit/exit_engine.py` (live), `exit_waterfall.py` (explanation) |
| Change why a recommendation didn't become a trade | `app/runtime/paper_automation.py` + `paper_automation_support.py` |
| Change why an alert didn't send | `app/alerts/telegram_alerts.py`, `app/runtime/telegram_dispatcher.py` |
| Change option selection | `app/options/options_recommender.py`, `contract_ranker.py`, `options_filter.py`, `option_affordability.py` |
| Change what a page shows | `app/ui/pages/<page>.py` + the `_render_*` helper in `app/dashboard.py` |
| Change what a page *reads* | `app/ui/cache/*_state_builder.py`, `app/ui/dashboard_state.py` |
| Trace one ticker's whole day | `data/daily/<day>/activity_trace.csv` or DB `activity_trace_event` |
| Find why a candidate was blocked | `decision_waterfall`, `rule_evaluation`, `auto_paper_decisions.csv` |
| Post-market review bundle | Tools: Downloads → Daily Review Export (`review_<day>.zip`) |
