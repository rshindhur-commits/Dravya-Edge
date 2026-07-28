# Operations

Runbook for running, validating, and troubleshooting the system. Companion docs:
[`STRATEGY.md`](STRATEGY.md), [`METRICS.md`](METRICS.md),
[`ARCHITECTURE.md`](ARCHITECTURE.md).

Verified against code on 2026-07-25.

---

## 1. Commands

```powershell
# Scanner (module form is required — `python app/main.py` breaks `from app...` imports)
python -m app.main

# Dashboard
streamlit run app/dashboard.py

# Tests — 140 tests, all passing
.venv/Scripts/python.exe -m unittest discover tests

# One focused test
.venv/Scripts/python.exe -m unittest tests.test_market_session_decisions
```

The expected `RuntimeError: expected test failure` printed by
`test_background_queue.py` is intentional — it verifies the background worker
isolates a failing task. It does not fail the suite.

Environment: Python 3.14.5, `.venv/`, deps pinned in `requirements.txt`
(note: the file is UTF-8 **with BOM**).

### Diagnostics

```powershell
python tools/diag_realtime_entitlements.py    # stock freshness, snapshot, option quote, bid/ask, spread
python tools/diag_fetch.py                    # raw 5m fetch → resample → indicators
python tools/dump_header_history.py           # Polygon rate-limit headers
python tools/get_suggested_rate.py            # suggested POLYGON_RATE_LIMIT_PER_MINUTE
python tools/test_db_connection.py            # SELECT now()
python tools/send_test_telegram_alert.py      # direct delivery check
```

## 2. Daily workflow

### Before open

1. Confirm `.env`: `USE_MOCK_MARKET_DATA=false`, `USE_MOCK_OPTIONS=false`,
   `ENABLE_AI_SUMMARY=false`, `SCANNER_AI_SUMMARY_ENABLED=false`.
2. Add known macro/earnings/Fed/OPEX dates to `EVENT_BLOCKER_DATES` as
   `SYMBOL:YYYY-MM-DD:Label` or `*:YYYY-MM-DD:Label`.
3. Start the scanner once enough 5m candles exist. With real-time entitlement,
   `Stock Data Freshness` should read `LIVE`.

Startup runs `validate_runtime_settings()`, which **hard-fails** on: missing
`POLYGON_API_KEY` while either mock flag is false; either mock flag true;
`OPTION_ALLOW_0DTE`/`1DTE` true; `REALTIME_OPTIONS_REQUIRED` without both
`OPTION_REQUIRE_BID_ASK` and `OPTION_REQUIRE_FRESH_QUOTE`;
`OPTION_MAX_SPREAD_PCT` outside `(0, 15]`; DTE ordering violated; unwritable
`SCANNER_OUTPUT_FILE` directory.

### During the session

Review only top candidates (`BULLISH_TOP_1..3`, `BEARISH_TOP_1..3`). For each,
check `RS vs QQQ`, `RS vs SPY`, `Relative Volume`, `ATR %`, `Risk Reward`,
`Option Quality Score`, `Option Liquidity Grade`, `Option Quote Freshness`,
`Expiration Bucket`, `Option Contract Cost`, `Affordability Status`,
`Event Blocked`.

Do **not** treat these `Action Status` values as execution-ready:
`REVIEW_TV_CHART`, `QUALITY_BUT_TOO_EXPENSIVE`, `DELAYED_QUOTE`, `STALE_QUOTE`,
or any option rejection code. Confirm the live chart and broker premium manually.

Session gating is in [STRATEGY.md §2](STRATEGY.md#2-session-gating) — nothing
auto-enters before 09:45 ET or after 15:30 ET.

### After close

Preferred (works on both local and Streamlit Cloud): the sidebar
**`Post Market: Generate Everything`** button. It finalizes the selected trading
day, generates the daily validation report, generates offline replay, attempts
to freeze the regression baseline, then refreshes the dashboard. A missing
archive leaves the baseline unfrozen without altering historical facts.

Terminal equivalent:

```powershell
python tools/daily_validation_report.py --date YYYY-MM-DD --archive --finalize
python tools/replay_today.py --output data/daily/YYYY-MM-DD/offline_replay.csv
```

Streamlit Cloud note: local terminals cannot see cloud-generated files — use the
sidebar buttons and download the HTML from there.

## 3. Paper trading control

Auto-paper runs from the **dashboard**, not the scanner
([ARCHITECTURE.md §3](ARCHITECTURE.md#3-the-two-trade-state-stores)). If the
dashboard is not running, no paper trades open or close.

| Control | Default | Effect |
|---|---|---|
| Auto paper entries | dashboard toggle | Opens trades in the 09:45–15:30 ET window |
| Auto exits | on | Stop / target / live-exit / profit-threshold closes |
| Force Close Intraday at Market Close | on | Closes `INTRADAY` profiles at 15:55 ET only |
| Restore Multi-day Positions Next Session | on | Restores `MULTIDAY` positions, sends `POSITION CONTINUES` |
| `ENABLE_MANUAL_PAPER_ENTRIES` | `false` | Hides manual entry buttons so telemetry stays system-generated |
| `ALLOW_MANUAL_PAPER_CLOSE` | `true` | Manual close/correction stays available |

Holding profile is **frozen at entry**. Only `MANUAL_OVERRIDE` can change it.
Disabling force-EOD-close does not reclassify an open intraday trade — it is
carried overnight, marked `overnight_intraday_carry`, emits a warning, is not
promoted, and does not send `POSITION CONTINUES`.

Paused trades (`PAUSED`/`RESUMED`) remain active for duplicate-entry protection
but are excluded from automated management until resumed.

## 4. Alert contract

Six subscriber messages only: `NEW TRADE`, `TRADE UPDATE`, `PARTIAL PROFIT`,
`POSITION CONTINUES`, `TRADE CLOSED`, `TRADE CANCELLED`. Scanner rows never
generate a message.

`TRADE UPDATE` fires on a 0.5R move, worsening trend health, material confidence
change, stop movement, or partial profit. `TRADE CLOSED` categories: 🟥 Stop
Loss, 🟩 Target Hit, 🟨 EMA Exit, 🟦 VWAP Exit, 🟪 Time Exit, ⚠️ Failed
Breakout, 📈 Manual Exit — near-close and EOD terminations normalize to Time Exit.

Setup: `TELEGRAM_ALERTS_ENABLED=true` plus `TELEGRAM_BOT_TOKEN`/
`TELEGRAM_CHAT_ID` (legacy lowercase `bot_token`/`chat_id` also accepted). Store
real tokens in `.streamlit/secrets.toml` or Streamlit Secrets — never commit
them. Rotate any token exposed in a terminal error.

`TELEGRAM_DISPATCH_MODE=DIRECT` (default) sends synchronously. `QUEUED` submits a
critical `RuntimeJob` and marks alerts sent only after the send succeeds;
`recover_pending_telegram_dispatches()` can resubmit queued records with no
successful audit event.

⚠️ **A paper trade can open without a `NEW TRADE` message.** The Telegram gate
(RR ≥ 2.0, quality ≥ 70, spread ≤ 8) is stricter than the paper gate (RR ≥ 1.8)
— see [STRATEGY.md §7](STRATEGY.md#7-telegram-gating). Check
`telegram_dispatch_audit.jsonl` before assuming a delivery failure.

## 5. Database

Optional and additive. Writes occur only when `DB_WRITE_ENABLED=true` **and**
`DATABASE_URL` is set; otherwise the JSON/CSV/Excel flow continues unchanged.

- `DATABASE_URL` → Neon **pooler** host, used for runtime writes.
- `DATABASE_DIRECT_URL` → direct host, **migrations only**.
- The scanner never runs DDL. Apply migrations manually:

```powershell
python tools/apply_db_migration.py app/db/migrations/0NN_name.sql
```

15 migrations exist, creating 22 tables, alongside the 4 original compat tables
(`alert_events`, `paper_trades`, `scanner_runs`, `gate_decisions`).

Startup prints a non-sensitive status line (`DB_WRITE_ENABLED`, whether
`DATABASE_URL` is present, whether writes are active) — never the URL or
password.

Idempotency: only `alert_events.dedupe_key` is a safe uniqueness target. Broad
constraints (symbol/day/contract) are unsafe for scanner rows, paper-trade
lifecycles, re-entries, and refreshed option observations.

## 6. Streamlit Cloud

Local `.env` is not available in deployed Streamlit — configure Streamlit
Secrets. The dashboard syncs secrets into env **before** scanner imports.

Put root-level DB keys **before** any `[telegram]` section — TOML keeps later
keys inside the active table. The loader also maps
`[connections.trading_db|neon|postgres].url`, accepts a `[database]` section
with `url`/`direct_url`/`write_enabled`, and recovers DB keys accidentally
nested under another table.

`APP_ENV` is read first, with `ENV` accepted as an alias.

## 7. Historical Scanner Regression

Answers "would current strategy code have produced more or less R on an archived
day?" Read-only; never mutates production trades or daily facts.

1. Set `REGRESSION_SNAPSHOT_ENABLED=true` **before** the target session and
   restart. Keep it disabled otherwise — it adds per-symbol snapshot work.
2. After close, freeze the baseline:
   `python tools/daily_validation_report.py --date YYYY-MM-DD --finalize`
3. Run: `python tools/regression_runner.py --date YYYY-MM-DD --strategy-version <label>`

Outputs land in `data/regression/YYYY-MM-DD/`. The **Regression** dashboard page
mirrors the same workflow, including a Freeze Baseline action.

The baseline is frozen per day and immutable — regressions compare against that
fixed day, never against yesterday's code.

## 8. Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| All symbols `STALE DATA` / `AVOID` | Weekend/holiday, or Polygon returning prior-day aggregates | `tools/diag_fetch.py`; freshness already accounts for bucket-start timestamps |
| No paper trades opening | Dashboard not running, outside 09:45–15:30 ET, or a gate blocked | `auto_paper_decisions.csv` — every decision logs `SKIPPED`/`BLOCKED`/`OPENED` with an exact reason |
| Trade opened but no Telegram | Telegram gate stricter than paper gate | `telegram_dispatch_audit.jsonl`, field `telegram_response.description` |
| Telegram 400 rejection | Malformed HTML entities in message | `telegram_response.description` in the audit row |
| `chat not found` | Invalid/unavailable chat ID | Rotate the token if it was exposed, then re-check `TELEGRAM_CHAT_ID` |
| Funnel shows `0 Telegram` despite alerts | Known gap — the counter is structurally always 0 | [METRICS.md §6](METRICS.md#6-known-measurement-gaps) |
| Everything `QUALITY_BUT_TOO_EXPENSIVE` | Affordability cap binding | Effective cap is `min(static, risk-based)` — [STRATEGY.md §5](STRATEGY.md#5-option-selection) |
| Dashboard slow | Cache miss forcing the legacy CSV path | Developer → Runtime Performance; check `validation_state.json` freshness |
| Config change had no effect | Settings load at import with `override=True` | Restart the process — module-level settings are not hot-reloaded |
| DB writes silently absent | Best-effort by design | Startup DB status line; `DB_WRITE_ENABLED` and `DATABASE_URL` |

## 9. Files, state, and hygiene

Runtime state is **not** source and must not be committed:

```
app/state/*.json          trade, paper trade, suggested trade, auto-paper, telegram, signal memory
data/live/                dashboard/validation/replay/report caches, runtime state, dispatch audit
data/daily/YYYY-MM-DD/    the daily audit record
data/regression/          regression baselines and results
telemetry/                legacy trade telemetry
scanner_output.xlsx       live scanner export
```

All of the above are `.gitignore`d. Several were tracked historically (ignore
rules were added after the files were committed) and were untracked with
`git rm --cached` — files stay on disk, git stops carrying scan state in every
commit.

Before restarts or EOD review, export scanner output, telemetry, paper trade
state, and candidate snapshots via the sidebar downloads.

## 10. Safety rules

1. **No broker integration exists.** Nothing here places a real order.
2. Paper first. For small real trades, use broker live bid/ask and limit orders
   only — never market orders, 0DTE/1DTE, wide spreads, stale quotes, or
   event-risk windows.
3. Confirm Polygon data against a live broker/TradingView chart before any real
   trade.
4. Respect the [V1.0 Evidence Freeze](METRICS.md#5-the-three-evidence-gates) —
   no V1 strategy loosening until the evidence exists. It is documentation
   discipline, not a code gate; nothing will stop you.
5. Do not loosen quote-freshness thresholds. The current daily archive predates
   full provenance capture, so historical stale blocks cannot yet be attributed
   conclusively.

## 11. Known operational gaps

- **Validation freeze is documented, not code-enforced.** No gate blocks a
  strategy change.
- **Graduation criteria are not implemented.** No coded rule moves the system
  from paper validation to larger real size.
- **No risk kill-switch.**
- **No async market-stream processing** — everything is REST poll + cache.
- **Backtesting is stock-underlying only**; no historical option-quote replay.
- **No CI.** There is no `.github/workflows`. Any plan step that says "fails CI"
  needs a pipeline stood up first.
- `_run_scanner_impl` is ~3,300 lines in one function; `dashboard.py` is 9,431
  lines. Both are known refactor targets.

## 12. Open questions

Dated runtime observations and unresolved questions. These are **not**
architectural properties — re-check them rather than trusting the date.

### Does the legacy live-trade path ever fire? (open — 2026-07-25)

`app/state/trade_state.json` was **empty** in this working tree on 2026-07-25.
Structurally it has no downstream evidence consumer, but Path A can still call
`open_trade()` and `maybe_send_trade_open_alert()` — so in principle it can emit
a `NEW TRADE` message for a trade no evidence surface tracks
([ARCHITECTURE.md §3](ARCHITECTURE.md#3-the-two-trade-state-stores)).

Unresolved: whether the branch never fires (its `entry_quality == "HIGH"` gate is
never satisfied), or fires and opens-and-closes within a session leaving the file
empty at rest. Cheap to settle — log at `main.py:4904` for one session, or grep
`telegram_dispatch_audit.jsonl` for `NEW TRADE` rows with no matching
`paper_trade_events.csv` OPEN.

Worth settling **before** building a cost model on top of it: if the path is
dead, it should be removed rather than maintained; if it is live, it is emitting
untracked alerts.

### Local evidence archive is effectively empty (observed 2026-07-25)

`paper_trade_state.json` is empty and `data/daily/` holds 5 folders, 4 of them
near-bare. Real evidence lives on Streamlit Cloud / Neon. Any measurement task
requiring "a real day's archive" must run there, or export a day down first.
