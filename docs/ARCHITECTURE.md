# Architecture

How the system is put together and where each responsibility lives. For the
per-file map see [`_inventory.md`](_inventory.md); for trading rules see
[`STRATEGY.md`](STRATEGY.md); for measurement definitions see
[`METRICS.md`](METRICS.md); for the runbook see [`OPERATIONS.md`](OPERATIONS.md).

Verified against the code on 2026-07-25 (branch `Claude_Changes`, `f99e690` +
uncommitted hygiene work). Where this document contradicts `README.md` or
`Project_state.md`, this document is correct — see
[Corrections vs. legacy docs](#7-corrections-vs-legacy-docs) at the end.

---

## 1. What the system is

A Python intraday options **scanner and research workstation**. It does not
place real orders. It scans a watchlist, scores momentum setups, computes risk
geometry, selects and gates option contracts, manages simulated (paper) trade
state, notifies over Telegram, and persists a large evidence trail for
post-session analysis.

Two processes, run independently:

| Process | Command | Owns |
|---|---|---|
| Scanner | `python -m app.main` | Market data → indicators → setups → risk → options → decision → V1 trade state → scanner output row + persistence |
| Dashboard | `streamlit run app/dashboard.py` | Rendering, **auto-paper entry/exit lifecycle**, operator actions, report/replay generation |

They communicate through files, not memory: the scanner writes
`data/live/scanner_output_latest.csv` and `dashboard_state.json`; the dashboard
reads them back. This is why paper trades can open from candidate rows the
scanner produced minutes earlier.

---

## 2. The scan pipeline

Per symbol, inside `app/main.py::_run_scanner_impl` (a single ~3,300-line
function — the dominant piece of structural debt):

```
watchlist (config/watchlist.py)
  → utils/polygon_client.py         bounded ThreadPoolExecutor prefetch (SCANNER_MAX_WORKERS)
  → indicators/technical_indicators.py::get_polygon_data + compute_indicators   (5m)
  → utils/timeframe_resampler.py    5m → 15m, 1h
  → strategies/momentum_strategy.py::analyze_setup                (per timeframe)
  → strategies/entry_engine.py::detect_entry                      (setup family)
  → risk/risk_manager.py::calculate_risk                          (entry/stop/target/RR)
  → options/options_recommender.py::recommend_live_option_bundle
       ├─ options/contract_ranker.py::rank_option_contracts
       ├─ options/option_affordability.py::add_affordability_metrics
       └─ options/options_filter.py::evaluate_option_liquidity
  → projections/trade_projection.py::project_trade
  → risk/position_sizing.py::calculate_position_size
  → decision/decision_engine.py + main.py::build_action_decision  (action_status)
  → main.py::build_candidate_trade_plan / build_status_result_row (the output row)
```

Foreground work is deliberately sequential for anything decision-critical.
Only market-data prefetch is parallel.

## 3. The two trade-state stores

**This is the single most important structural fact in the codebase.** There
are two parallel stores; only one produces the P&L anyone reads.

### Path A — `app/state/trade_state.json` (legacy V1 live-trade branch)

Runs **inline in the scan loop**, gated on `entry_quality == "HIGH"`
(`main.py:4904`). Opens via `state/state_trade_manager.py::open_trade`, exits
via `exit/exit_engine.py::evaluate_exit` → `close_trade`. Emits `NEW TRADE` /
`TRADE CLOSED` Telegram messages and V1 engine events.

Its R-multiple is `exit_setup["rr_progress"]`, computed live by the exit engine
— *not* recomputed from entry-vs-close price.

**Structurally, this store has no downstream consumer.** No evidence surface
(Validation page, Trade Doctor, trend-capture analytics, Reports) reads it for
reported performance — but it can still open trades and emit `NEW TRADE` /
`TRADE CLOSED` Telegram messages. That asymmetry is a live-correctness question,
not a settled design; see [OPERATIONS.md §12](OPERATIONS.md#12-open-questions).

For whether the store currently holds data, check the file — that is a runtime
observation, not an architectural property, and it is recorded with a date in
[OPERATIONS.md §12](OPERATIONS.md#12-open-questions).

### Path B — `app/state/paper_trade_state.json` (the authoritative P&L store)

Triggered from the **dashboard's** refresh cycle (`dashboard.py:9343-9356`),
not from the scanner process:

```
dashboard.py
  → runtime/paper_automation.py::run_auto_paper_entries
       → paper_automation_support.py::_auto_paper_entry_reason
            → gates/entry_gate.py::evaluate_entry_gate(mode="paper")
            → state/holding_policy.py::derive_holding_profile
       → state/paper_trade_manager.py::open_paper_trade
            entry/stop/target read back from the scanner row's
            "Candidate Entry Price" / "Candidate Stop Price" / "Candidate Target Price"
       → alerts/telegram_alerts.py::maybe_send_paper_entry_alert   → "NEW TRADE"

  → runtime/paper_automation.py::run_auto_paper_exits
       → paper_automation_support.py::_auto_exit_reason
            stop/target: direct compare against the trade's frozen levels
            live exit:   reads the scanner row's "Live Exit Signal"/"Live Exit Reason"
            profit:      _calculate_trade_r_progress >= controls["profit_r"]
            EOD:         15:55 ET if holding profile forces EOD exit
       → state/paper_trade_manager.py::close_paper_trade
            → _paper_trade_result()          ← the paper P&L formula (METRICS.md §2)
            → _append_paper_trade_event()    → paper_trade_events.csv (immutable)
            → _append_trend_capture_for_closed_trade()
                 → re-fetches 5m candles, analytics/trend_capture.py
            → alerts/telegram_alerts.py::maybe_send_trade_exit_alert → "TRADE CLOSED"
```

### Consequences worth internalising

- **`evaluate_exit()` does not close paper trades.** It supplies the
  `Live Exit Signal` / `Live Exit Reason` columns, which `_auto_exit_reason`
  treats as *one of four* triggers alongside its own independent stop/target,
  profit-threshold, and EOD checks.
- **Risk geometry is computed once, at scan time**, and frozen onto the paper
  trade at open. Realized R at close uses those frozen levels, not a
  re-evaluation.
- **Three call sites open paper trades**: `dashboard.py:4335`, `dashboard.py:4928`
  (both manual, disabled by default via `ENABLE_MANUAL_PAPER_ENTRIES=false` /
  `SHOW_MANUAL_PAPER_BUTTONS=false`) and `paper_automation.py:187` (auto).
  All funnel into one `open_paper_trade`, and all closes funnel into one
  `close_paper_trade` → one P&L formula.
- **V2 is a third, fully isolated store** (`entry_exit_v2_shadow_state.json`).
  It proposes its own entries, uses shared risk geometry, manages and closes
  only itself, and can never touch V1/paper state or send a message.

---

## 4. Runtime scheduling and persistence

Decision-critical work runs in the foreground; everything else is queued so a
slow database or file write cannot delay a trade decision or an alert.

`app/runtime/runtime_scheduler.py` provides a four-level priority queue
(`CRITICAL` / `HIGH` / `NORMAL` / `LOW`) with stale-job cancellation by
`scan_id`. Ordering after a scan:

| Stage | Priority | Work |
|---|---|---|
| `finalize_scan_outputs` | HIGH, non-cancelable | Operator table, candidate persistence/ranking, health, **Telegram dispatch**, funnel |
| `_persist_scan_outputs` | HIGH | `dashboard_state.json` first, then health history, candidate snapshots, lifecycle rows, Excel/CSV export, stage profiles |
| `persist_scan_artifacts_db` | NORMAL | Candidate snapshots, rule evaluations, gate decisions → Postgres |
| `validation_state.json` | NORMAL | Validation-page cache |
| `replay_state.json`, `report_state.json`, `summarize_telemetry` | LOW | Replay/Reports caches, telemetry summary |

**Telegram dispatch happens before dashboard/analytics cache jobs.** Cache
builder failures are caught, logged as failed jobs, and cannot block alert
delivery or scanner completion.

Hardening: `scan_generation.py` stamps every live JSON file with a generation
and writes atomically (temp + replace); `generation_validator.py` rejects stale
reads; `runtime_watchdog.py` writes `runtime_health.json`; `shutdown_manager.py`
drains on exit.

### Persistence layers

| Layer | Location | Durability |
|---|---|---|
| Live state | `data/live/*.json` | Overwritten each scan; generation-stamped |
| Daily artifacts | `data/daily/YYYY-MM-DD/` | Append-only per trading day; the audit record |
| Legacy compat | `scanner_output.xlsx`, `telemetry/trade_telemetry.csv` | Retained for older tooling |
| Postgres (Neon) | 22 migration tables + 4 compat tables | **Optional and additive.** Every write is best-effort; failure never blocks anything |

All DB writes must originate from a `RuntimeJob`, never inline on a decision
path. The scanner never executes DDL — migrations are applied manually through
`DATABASE_DIRECT_URL`.

---

## 5. Dashboard rendering model

Seven pages (`app/ui/pages/`): **Trading, Validation, Replay, Regression,
Reports, Learning, Developer**.

The page cache model exists because recomputing analytics on every Streamlit
refresh was the dominant latency cost. Each page prefers a prebuilt JSON cache
written by a background runtime job and falls back to the legacy CSV-heavy path
only when the cache is missing:

| Page | Cache file | TTL |
|---|---|---|
| Trading | `dashboard_state.json` | 5 s |
| Validation | `validation_state.json` | 60 s |
| Developer | `runtime_state.json` | 120 s |
| Replay / Reports | `replay_state.json`, `report_state.json` | file mtime |

The Trading page has a guarded fast path: when paper automation, auto-exits,
and EOD close are all inactive it renders straight from `dashboard_state.json`
without loading any scanner CSV. When automation is active the full path runs,
because automation needs live rows.

`app/dashboard.py` (9,431 lines) still owns shared helpers and the auto-paper
trigger; the page modules own render bodies. Six functions are marked
`DEPRECATED` and retained pending the Validation-page migration.

---

## 6. Layer boundaries (the rules that keep this safe)

1. **V1 decides. Everything else observes.** `app/analytics/` (36 modules),
   `entry_engine_v2` / `exit_engine_v2`, RuleEvaluation, decision/exit
   waterfalls, and the Learning engine are all read-only with respect to
   trading behavior. They may not change entry eligibility, risk, option gates,
   alert eligibility, or trade state.
2. **Persistence is best-effort and downstream.** A DB, cache, or report
   failure must never block a decision, an alert, or file-backed state.
3. **Telegram is a transport, not a second decision engine.** It re-derives no
   gates except the three thresholds noted in [STRATEGY.md](STRATEGY.md#7-telegram-gating);
   duplicate protection is a delivery safeguard only.
4. **Holding profile is frozen at entry.** Only `MANUAL_OVERRIDE` (and a future
   `BROKER_SYNC`) may change it — never the scanner, exit engine, or ranking.
5. **Regression and replay are read-only.** They never call market-data
   providers or mutate production trades, baselines, or daily facts.

---

## 7. Corrections vs. legacy docs

These were verified against code this session. `README.md` and
`Project_state.md` still contain the older claim in some places.

| # | Legacy claim | Verified truth | Evidence |
|---|---|---|---|
| 1 | Affordability defaults are 2000 / 0.10 / 400 / 500, "static $500 cap controls" | Those are the **`SMALL_ACCOUNT` profile + `.env.example`** values. The **live `.env`** overrides to 1000 / 0.12 / 500 / 650, making the **risk-based cap ($600) control** instead of the static cap | `capital_profiles.py:2-13`, `.env:92-100`, `option_affordability.py:89-92` |
| 2 | `ACCOUNT_SIZE=2000`, `RISK_PERCENT=10` | Live `.env` sets 1000 / 2 → position sizing risks **2%**, not 10% | `.env:123-124`, `position_sizing.py:33-45` |
| 3 | "Legacy Telegram score/threshold settings no longer block an alertable action" | **Partially false.** `TELEGRAM_MIN_RR`, `TELEGRAM_MIN_OPTION_QUALITY_SCORE`, `TELEGRAM_MAX_SPREAD_PCT` still gate `NEW TRADE`. Only 3 of the 14 policy keys are consumed; the 11 score/cap/cooldown keys are computed and discarded | `telegram_alerts.py:1854-1869` |
| 4 | Dashboard has 6 pages | **7** — `Learning` is in the sidebar nav | `dashboard.py:9223-9234` |
| 5 | "Current Neon tables are small event/state tables only" (4 tables) | **22 tables** across 15 migrations, plus the 4 compat tables | `app/db/migrations/*.sql` |
| 6 | Validation baseline "134 tests" | **284 tests**, all passing under pytest. The legacy `unittest discover` command collects only 227 — it cannot see the 20 files using bare `def test_*` functions | `python -m pytest tests -q` |
| 7 | Evidence thresholds stated three different ways | Three **different gates**, not one contradiction — see [METRICS.md §5](METRICS.md#5-the-three-evidence-gates) | `validation_state_builder.py:188`, `promotion_rules.py:7-14` |
| 8 | `exit_engine.py` is "the live source of truth for exit decisions" | True for **Path A only**. Paper trades — the reported P&L — close via `_auto_exit_reason`, which consumes the exit engine as one of four triggers | `paper_automation_support.py:451-484` |
| 9 | Scanner `REVIEW_TV_CHART` rows could send a review alert | The send path was **unreachable dead code** and was removed; the classifier now only labels rows. Docs and code now agree | `telegram_alerts.py::classify_scanner_entry_alert` |
| 10 | Candidate funnel reports a "Telegram" sent count | Structurally **always 0** — the scanner dispatcher cannot send. Real sends happen in the paper-entry/exit paths. Pre-existing gap, see [METRICS.md §6](METRICS.md#6-known-measurement-gaps) | `main.py::_build_candidate_funnel` |
| 11 | "Expanded active watchlist to **16** liquid option names" followed by a list of 26 | **26** — corrected in `Project_state.md` | `WATCHLIST`, verified `len() == 26` |
| 12 | Validation commands hardcode `d:/Dravya_Trade_Works/.venv/Scripts/python.exe` | Replaced with portable `python -m unittest ...` in both legacy docs | `Project_state.md`, `README.md` |
| 13 | `trade_state.json` "is currently empty" stated as an architectural property | Volatile runtime observation; removed from this document. The **structural** fact (no downstream consumer, yet still able to alert) stays here; the dated observation moved to [OPERATIONS.md §12](OPERATIONS.md#12-open-questions) | — |
| 14 | Three freshness regimes make the 2-minute stock gate "unachievable by construction" | **The premise is incorrect.** Freshness is evaluated at fetch time and is interval-adjusted, so scan cadence never enters it. Three thresholds act on two objects at three scopes — see [STRATEGY.md §5.1](STRATEGY.md#51-the-three-freshness-thresholds-are-not-one-concept) | `main.py:1675-1698`, `dashboard.py:2472-2477` |
| 15 | "Test coverage is still sparse" vs "134 tests pass" | **Both are true, not a contradiction.** 284 tests pass, and 93 of ~160 `app/` modules have no direct test import — high count, uneven coverage. Substantiated in [`_inventory.md` §4.1](_inventory.md#41-modules-with-no-direct-test-import) | `pytest`, AST import map |
