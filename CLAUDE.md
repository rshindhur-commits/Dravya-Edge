# CLAUDE.md

Python intraday **options scanner and research workstation**. It does not place
real orders. It scans a 26-symbol watchlist, scores momentum setups, computes
risk geometry, gates option contracts, manages simulated (paper) trades, sends
Telegram notifications, and persists a large evidence trail for analysis.

## Docs

| Doc | Read it for |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it's wired, the two trade-state stores, runtime scheduling, layer boundaries |
| [`docs/STRATEGY.md`](docs/STRATEGY.md) | Trading rules + **full parameter table with provenance** |
| [`docs/METRICS.md`](docs/METRICS.md) | Metric formulas, the three evidence gates, known measurement gaps |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Runbook, troubleshooting, DB/Streamlit setup, open questions |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why things are the way they are; what would reverse each choice |
| [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md) | The remediation program — phases, sessions, gates |
| [`docs/_inventory.md`](docs/_inventory.md) | Per-file map of all 265 modules, call graph, test map |

`README.md` and `Project_state.md` are the older hand-maintained docs. Where they
disagree with `docs/`, **`docs/` is correct** — verified corrections are listed
in [ARCHITECTURE.md §7](docs/ARCHITECTURE.md#7-corrections-vs-legacy-docs).

## Commands

```powershell
python -m app.main                                   # scanner (module form required)
streamlit run app/dashboard.py                       # dashboard
.venv/Scripts/python.exe -m unittest discover tests  # 140 tests, all passing
```

`test_background_queue.py` intentionally prints `RuntimeError: expected test
failure` — that is the isolation test working, not a failure.

## The one thing to internalize first

**There are two parallel trade-state stores, and only one produces the P&L
anyone reads.**

- **Path A** — `app/state/trade_state.json` via `state_trade_manager.py`. Runs
  inline in the scan loop, exits via `exit_engine.evaluate_exit()`. Fully wired,
  executes every scan, **currently empty**, and nothing downstream reads it.
- **Path B** — `app/state/paper_trade_state.json` via `paper_trade_manager.py`.
  **The authoritative store.** Triggered from the **dashboard's** refresh cycle
  (`dashboard.py:9343`), not the scanner. Reads back the
  `Candidate Entry/Stop/Target Price` columns the scanner already wrote.

Consequences that trip people up:

- `evaluate_exit()` **does not close paper trades.** It supplies the
  `Live Exit Signal` column, which `_auto_exit_reason` treats as one of four
  triggers (stop/target, live signal, profit threshold, EOD).
- **If the dashboard isn't running, no paper trades open or close.**
- Risk geometry is computed once at scan time and **frozen** onto the trade;
  realized R uses those frozen levels.
- A paper trade **can open without a `NEW TRADE` message** — the Telegram gate
  (RR ≥ 2.0) is stricter than the paper gate (RR ≥ 1.8).

## Program invariants (I1–I6)

From [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md) §1. These apply to every
session until the program completes.

| # | Invariant |
|---|---|
| I1 | **No V1 entry / exit / risk logic changes** in Phases 0–6. Everything before Phase 7 is measurement. Any diff touching `momentum_strategy`, `entry_engine`, `risk_manager`, or `exit_engine` decision logic is out of scope and must be refused. |
| I2 | **Dual-compute before replace.** Every new number is emitted *alongside* the old one, reconciled on archived data, and only then promoted. Nothing is swapped in place. |
| I3 | **Characterization tests first.** Pin current behaviour before refactoring it. |
| I4 | **`REAL_TRADING_ENABLED=false`** for the entire program. No exceptions, no temporary flips. |
| I5 | **Test count only rises.** 134 is the floor. Full suite green before any merge. |
| I6 | **Behaviour changes ship dark.** Anything that could alter trade selection lands behind a flag, defaulted off, logging the counterfactual. |

Two notes on the above: the **I5 floor is stale** — the suite is at **140**, so
treat 140 as the working floor. And `REAL_TRADING_ENABLED` is absent from the
live `.env`; I4 currently holds only because the code default is `False`.

## Architectural invariants

Structural rules that predate the program and outlive it. See
[`docs/DECISIONS.md`](docs/DECISIONS.md) for why each exists.

1. **V1 decides; everything else observes.** All 36 `app/analytics/` modules,
   `entry_engine_v2`/`exit_engine_v2`, RuleEvaluation, waterfalls, and the
   Learning engine are read-only w.r.t. trading behavior.
2. **Persistence is best-effort and downstream.** A DB, cache, or report failure
   must never block a decision, an alert, or file-backed state. All DB writes go
   through a `RuntimeJob`, never inline on a decision path.
3. **Telegram is a transport, not a second decision engine.** Six subscriber
   messages only. Scanner rows never send.
4. **Holding profile is frozen at entry** — only `MANUAL_OVERRIDE` may change it.
5. **Regression and replay are read-only** — no provider calls, no mutation of
   production trades, baselines, or daily facts.
6. **The scanner never runs DDL.** Migrations are applied manually via
   `DATABASE_DIRECT_URL`.
7. **Measurement freeze** — no new analytics modules, dashboard pages, scoring
   layers, or telemetry tables until Phase 5 completes.

## Conventions

- Settings load at **import** with `override=True` — config changes need a
  process restart, not a refresh.
- Runtime state (`app/state/*.json`, `data/live/`, `data/daily/`,
  `data/regression/`, `telemetry/`, `scanner_output.xlsx`) is `.gitignore`d and
  must never be committed. Some were tracked historically and have been
  untracked with `git rm --cached`.
- Real secrets live in `.env` (local) or Streamlit Secrets (cloud). Never commit
  tokens; rotate any exposed in a terminal error.
- Code style is idiosyncratic — generous blank lines inside functions, few
  comments, few docstrings. Match surrounding style rather than normalizing it.
- Tests are `unittest`, not pytest.

## Known debt and gaps

- `_run_scanner_impl` is ~3,300 lines in one function; `dashboard.py` is 9,431
  lines with 6 `DEPRECATED` functions retained.
- **Candidate funnel "Telegram" count is structurally always 0** — the scanner
  dispatcher cannot send. Use `data/live/telegram_dispatch_audit.jsonl` for real
  delivery counts.
- 93 of ~160 `app/` modules have no direct test coverage, including
  P&L-critical `contract_ranker`, `momentum_strategy`, `technical_indicators`,
  and `state_trade_manager`.
- 5 entry setup families (`VWAP_RECLAIM`, `HIGHER_LOW_CONTINUATION`,
  `BREAKOUT_CONTINUATION`, `COILED_BREAKOUT`, `COILED_BREAKDOWN`) are
  **commented out** in `entry_engine.py`. Only 5 families can fire.
- **No CI** — no `.github/workflows`. Anything specified as "fails CI" needs a
  pipeline stood up first.
- Local evidence archive is thin; real evidence lives on Streamlit Cloud / Neon.
  Dated observations are in [OPERATIONS.md §12](docs/OPERATIONS.md#12-open-questions)
  — re-check them rather than trusting this file.
- No broker integration, no risk kill-switch, no async streaming, and
  backtesting is stock-underlying only (no historical option quotes).
