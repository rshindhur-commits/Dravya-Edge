# Metrics

Every number the system reports, where it is computed, and what it does and does
not mean. Companion docs: [`STRATEGY.md`](STRATEGY.md),
[`ARCHITECTURE.md`](ARCHITECTURE.md), [`OPERATIONS.md`](OPERATIONS.md).

Verified against code on 2026-07-25.

**All metrics here are observational.** None of them feed back into V1 entry,
exit, risk, sizing, or alert eligibility. That isolation is deliberate and is
what makes the evidence trustworthy — see
[ARCHITECTURE.md §6](ARCHITECTURE.md#6-layer-boundaries-the-rules-that-keep-this-safe).

---

## 1. Which numbers are the real ones

| Question | Read this | Not this |
|---|---|---|
| What did paper trading actually make? | `paper_trade_state.json` / `paper_trade_events.csv` | `trade_state.json` (empty, legacy — [ARCHITECTURE.md §3](ARCHITECTURE.md#3-the-two-trade-state-stores)) |
| How well did we hold winners? | Trend Capture % | Win rate |
| Is there enough evidence to change a rule? | `strategy_confidence.rule_change_allowed` | Confidence % alone |
| Did an alert actually go out? | `telegram_dispatch_audit.jsonl` | Candidate funnel "Telegram" count (always 0 — [§6](#6-known-measurement-gaps)) |

## 2. Trade outcome (the paper P&L formula)

`app/state/paper_trade_manager.py::_paper_trade_result` — the single formula for
every paper close, manual or automatic:

```python
is_short = direction == "PUT"

pnl  = (entry - close) if is_short else (close - entry)
risk = (stop  - entry) if is_short else (entry - stop)

pnl_pct    = round(pnl / entry * 100, 2)
r_multiple = round(pnl / risk, 2)          # only when risk > 0, else None
```

`outcome` resolves to `TARGET_HIT` (close reached take-profit), else `WIN` /
`LOSS` / `FLAT` by sign of `r_multiple`. Missing entry or close → `UNKNOWN`.

Two things to note:

- **`risk` uses the stop frozen at entry**, not a re-derived stop. Realized R is
  therefore comparable to the RR planned at scan time.
- The **underlying** move drives R. Option P&L is tracked separately by
  `option_metrics.py::calculate_option_pl(entry_mid, current_mid, contracts)`
  and is not what `r_multiple` measures.

Path A (legacy live-trade) reports R differently: it uses
`exit_setup["rr_progress"]` computed live by the exit engine. The two are not
guaranteed to agree; only Path B feeds reported performance.

## 3. Execution quality metrics

### Trend Capture % — the primary engineering target

```
capture = max(0, min(100, final_R / MFE_R * 100))     # when MFE_R > 0
```

`app/analytics/trend_capture.py`. Answers "of the move that was available while
we were in the trade, how much did we keep?" A 70% capture on a 2R move beats a
100% capture on a 0.5R move, which is why this — not win rate — is the stated
target for any V1→V2 promotion.

Grades: `A` ≥ 70, `B` ≥ 50, else `C`.

### Trade Efficiency Score (TES)

`app/analytics/trade_efficiency/recommendations.py`. Composite of capture, exit
timing, and opportunity cost. Reported alongside capture on the Validation page
and in daily reports.

### Supporting excursion metrics

| Metric | Meaning |
|---|---|
| MFE / MFE_R | Maximum favourable excursion, absolute and in R |
| MAE / MAE_R | Maximum adverse excursion |
| Available move / captured move | Total move offered vs. taken |
| **Left on table** | Available − captured; the headline regret number |
| Post-exit continuation | 1-bar and 2-bar move after exit — did we exit early? |

### Exit verdict and quality

`classify_exit_verdict` / `classify_exit_quality` produce `EXIT_TOO_EARLY`,
`GOOD_EXIT`, `NEEDS_REVIEW`, etc., plus an Exit Trigger and an Engineering
Recommendation. `EXIT_TOO_EARLY` rate is one of the promotion criteria.

If average Trend Capture % across a day is **below 55**, the daily validation
report automatically adds a trend-management recommendation.

## 4. Candidate quality metrics (pre-trade)

### Entry Timing Score

`app/analytics/entry_timing_engine.py` — weighted: Entry Efficiency 35%, Trend
Age 20%, Pullback Number 20%, Bars Since Breakout 10%, EMA extension 10%, VWAP
extension 5%.

Grades: `EXCELLENT` > 80, `GOOD` 70–80, `AVERAGE` 55–69, `LATE_ENTRY` < 55.

### Trade Quality Score (TQS)

`app/analytics/trade_ranker.py` — Setup 25%, Entry Timing 20%, Trend Health 20%,
Option Quality 15%, Relative Strength 10%, Liquidity 10%.

`entry_optimizer.py` adds an **Entry Priority Adjustment** (from pullback number,
trend age, bars since breakout) producing the final **Ranking Score** and
**Candidate Rank**. It also estimates Expected Remaining Trend and a projected
`A`/`B`/`C` grade. None of this rejects a setup or alters entry, risk, option, or
alert eligibility — it only orders candidates.

### Trend Health

`app/analytics/trend_health.py` — weighted EMA alignment, price vs EMA9/VWAP,
higher-high/higher-low structure, MACD, RSI, relative volume →
`STRONG` / `HEALTHY` / `WEAKENING` / `BROKEN`.

`app/exit/exit_confidence.py` adds a five-state live label
(`VERY_HEALTHY` → `HEALTHY` → `WEAKENING` → `AT_RISK` → `FAILED`) plus soft
confirmation counts, which drives V1's one-candle EMA Grace Zone hold.

## 5. The three evidence gates

Legacy docs state thresholds three different ways, which reads as a
contradiction. It is not — these are **three distinct gates at different
scopes**. Resolved:

| Gate | Threshold | Scope | Enforced? | Source |
|---|---|---|---|---|
| **`rule_change_allowed`** | `evidence_days ≥ 20 AND completed_trades ≥ 80` | Whole strategy — flips Strategy Confidence to `ACTIONABLE_EVIDENCE` | **Coded**, boolean flag only; blocks nothing mechanically | `validation_state_builder.py:188` |
| **V1.0 Evidence Freeze** | 100–200 completed trades across ≥ 20 days and multiple regimes | Human policy on changing V1 behaviour at all | **Not code-enforced** — documentation discipline | `Project_state.md`, README |
| **Feature promotion** | `sample_size ≥ 100 AND confidence ≥ 95 AND improvement_pct > 0` | One shadow feature at a time | **Coded** in `evaluate_promotion` | `promotion_rules.py:7-14` |

The freeze policy is deliberately stricter than the coded flag: reaching 20
days / 80 trades permits *controlled validation review*, not a rule change.

`evaluate_promotion` returns `SHADOW` on insufficient sample/confidence,
`SHADOW` when lift is not yet measurable, `RETIRED` on non-positive lift, and
`PROMOTION_CANDIDATE` otherwise. Nothing auto-promotes.

### Strategy Confidence formula

`validation_state_builder.py:183-195` — measures **evidence strength, not
expected profitability**:

```python
day_component   = min(35, 2 + max(0, evidence_days - 1) * 33 / 19)
trade_component = min(42, completed_trades)
confidence_pct  = min(95, round(15 + day_component + trade_component))
```

Calibration checkpoints: 0 evidence → 0%; 1 day / 1 trade → 18%;
20 days / 80 trades → 92%; ceiling 95%. Level is `OBSERVATIONAL_ONLY` until
`rule_change_allowed`, then `ACTIONABLE_EVIDENCE`.

## 6. Known measurement gaps

Real, verified, and currently unfixed. Do not read these numbers at face value.

1. **Candidate funnel "Telegram" count is structurally always 0.**
   `_build_candidate_funnel` sources it from the scanner dispatcher's
   `sent_count`, but that dispatcher only classifies rows and can never send
   (see [STRATEGY.md §7](STRATEGY.md#7-telegram-gating)). Actual sends happen in
   `maybe_send_paper_entry_alert` / `maybe_send_trade_exit_alert`. The operator
   funnel line therefore under-reports alerts to zero. **This predates the
   current session's cleanup** — it was already 0 because the old send path was
   unreachable. Use `telegram_dispatch_audit.jsonl` for true delivery counts.

2. **`stock_finish` in trend-outcome reports is provisional.** It is the latest
   scanner price at report time, not the settled close, until the session ends.

3. **Local evidence is effectively empty.** `paper_trade_state.json` is empty and
   `data/daily/` holds only a handful of near-empty folders in this working
   tree. Whatever evidence exists lives on the Streamlit Cloud / Neon side. No
   evidence gate in [§5](#5-the-three-evidence-gates) can be evaluated from this
   checkout.

4. **Breadth is a watchlist proxy**, not official NASDAQ/QQQ advance-decline
   data. Treat `Watchlist Breadth Score`, advancers/decliners, above-VWAP % and
   above-EMA20 % as relative signals over 26 names only — but note the
   weak-breadth rule *does* tighten real entry thresholds
   ([STRATEGY.md §9](STRATEGY.md#9-parameter-table)).

5. **Historical option P&L is not modelled.** Backtesting is stock-underlying
   only; there is no historical option-quote replay, so backtest R is not
   options R.

6. **Replay outcomes skew heavily to `STOP_HIT`** — likely tight stops, ambitious
   targets, short horizon, and immediate-entry assumptions. Calibration
   utilities exist (`replay_calibration.py`) but are not yet validated over a
   meaningful sample.

## 7. Where metrics land

| Artifact | Contents |
|---|---|
| `data/daily/<date>/paper_trade_events.csv` | Immutable OPEN / AUTO_EXIT / MANUAL_CLOSE events — the source of truth for "did we trade" |
| `data/daily/<date>/trend_capture_analysis.csv` | Capture, MFE/MAE, left-on-table, exit verdict, TES per closed trade |
| `data/daily/<date>/trade_exit_snapshots.csv` | Indicator/structure/trend-health state at exit |
| `data/daily/<date>/candidate_snapshots.parquet\|csv` | Every scanner row, including skipped and blocked |
| `data/daily/<date>/signal_lifecycle_events.csv` | One row per candidate per scan |
| `data/daily/<date>/signal_state_transitions.csv` | One row per composite state change |
| `data/daily/<date>/candidate_funnel.jsonl` | Per-scan funnel counts (⚠️ see gap 1) |
| `data/daily/<date>/quote_attribution.csv` | One fact per non-live quote classification |
| `data/daily/<date>/v2_learning_dataset.csv` | One row per completed engine trade |
| `data/daily/<date>/engine_trade_comparisons.csv` | Sequence-matched V1/V2 completed pairs |
| `data/live/validation_state.json` | Cached Validation KPIs, Trade Doctor, Strategy Confidence |
| `data/live/daily_engine_summary.json` | Learning-engine daily rollup |
| `data/live/telegram_dispatch_audit.jsonl` | ATTEMPT / SENT / FAILED with full Telegram API response |
| `reports/daily_validation_<date>.html` | Post-market operator report |
| Neon Postgres | 22 migration tables — queryable mirror, best-effort |
