# Post-change watchlist

Two waves of changes landed on 2026-07-31. The first was a code review: nine
defects fixed across five commits plus a migration, calibrated against two to
three archived days and deployed before the open. The second came from watching
that deployment run live, which found five more defects the archive could not
have shown — three of them in the measurement layer, which meant the numbers
used to judge the first wave were themselves wrong.

This file is the list of things to look at once real sessions run, with the
number that would say a change went wrong. Delete an item once its question is
answered.

## Baselines

| | |
| --- | --- |
| Before the change set | **−0.44R per trade, 25% win rate, 8 trades** (flag-corrected) |
| Blended figure previously displayed | −0.37R, 38.9%, 18 trades |
| **2026-07-31, first live session** | **−0.157R per trade, 33% win, 3 trades, PF 0.56** |

The 07-31 book, reconciled:

```
NVDA  CALL  198.24 -> 197.5000   r=-0.74   VWAP invalidation (long)
CRWD  CALL  188.03 -> 187.6250   r=-0.33   EMA9 invalidation (long)
NVDA  CALL  197.96 -> 198.5501   r=+0.60   EMA9 invalidation (long)
```

Three trades is not a sample. Per-trade bleed fell to about a third of baseline,
which is the right direction and nothing more. Read the sections below for
*why* it came out that way rather than treating −0.157R as a result.

---

## 1. Watch first — the changes most likely to be wrong

### 1.1 Profit lock is the only change that alters a live position

**New on 2026-07-31 (`eb56f75`), and the highest-risk item now.** When a soft
exit (EMA/VWAP/MACD) fires on a trade with banked profit, a healthy trend and
low exit confidence, the position is held and the stop ratcheted to protect all
but `PROFIT_LOCK_MAX_GIVEBACK_R` of the peak instead of closing.

**Why.** NVDA ran to +1.66R, printed "Partial profit threshold reached" three
times over ten minutes, then closed at +0.60R on an EMA9 touch — trend health
95, exit confidence 11.5. Breakeven protection was active and did nothing,
because the exit came from a soft rule and not from the stop.

**Watch.** `profit_lock_active` in exit results, and the exit price of any trade
that engages it.

**Trigger.** A locked trade exiting materially *below* the price the soft rule
would have given. The correct comparison is against the soft-exit price, never
against the peak — the peak was never available to take.

**If it trips.** `PROFIT_LOCK_MAX_GIVEBACK_R` (default 1.0) is the dial. All
four `PROFIT_LOCK_*` settings are env-tunable, so this can be retuned or
disabled without a deploy.

### 1.2 Stop-viability enforcement has still never fired

**Why.** Ten historical blocks all fell on CALLs, which was the reason to watch
for direction tilt. In the first live session it produced **zero** blocks —
`execution_eligibility` never reached it, because RR rejects first. It remains
completely unvalidated in production.

**Watch.** Count of `STOP_INSIDE_OPTION_SPREAD` per session, and the direction
split of opened trades.

**Trigger.** Still zero after a week of sessions with entries means the gate is
unreachable in practice and the calibration exercise was moot.

**One data point exists.** CRWD cleared at **1.37×** — inside the 1.05–1.43 band
that a 1.5 multiple would reject — and lost. NVDA #1 cleared at 5.02× and also
lost. Keep counting that band; see 3.2.

### 1.3 Direction tilt

**Why.** All three trades on 07-31 were CALLs, all `EMA_PULLBACK`, all
`INTRADAY`, all `BULLISH_TOP_1`. That is one setup, one direction, one profile —
no diversification was available even in principle.

**Watch.** Direction split of opened trades.

**Trigger.** Three consecutive sessions with no PUT opening. Note the tape on
07-31 was range-bound with weak breadth, where buying pullbacks is the losing
side, and the regime raised thresholds without ever restricting direction. See
4.6.

### 1.4 Hold duration after the bars-in-trade retune

**Why.** `bars_in_trade` counted scans, not bars, so three thresholds fired ~3×
early. They were restated in real bars to hold the wall-clock behaviour that was
actually running, so hold times should **not** change much — that is the test.

**Observed 07-31.** 12.6 min, 34 min, 93 min. Inside the expected band, with the
short one being a trade that went 0.56R against almost immediately.

**Trigger.** Median moving materially outside 15–45 minutes in either direction.

### 1.5 Review alerts now reach subscribers

**New on 2026-07-31 (`cc1fe72`).** `REVIEW_TV_CHART` candidates alert again; the
suppression had made ~40 lines of already-written review-alert code unreachable
for a week.

**Watch.** Daily count of review alerts. Volume is bounded by `_review_alert_key`
(symbol + setup + date), so a candidate alerts **once a day** however many scans
it appears in.

**Expect ~11/day.** On 07-31 that was 11 alerts against 65 raw review events,
across NFLX, NVDA, QQQ, SPCX, AAPL, ORCL, PLTR, TSLA, AMZN and CRWD.

**Trigger.** More than ~20 in a session means the dedup key is not holding.

---

## 2. Watch second — should be quietly correct

### 2.1 Exit slippage misses the case that actually cost money

`exit_slippage` records the adverse gap between a **level-triggered** exit's
trigger and its fill. Soft exits fill at market and so record zero — correctly,
by that definition.

**But that is not where the slippage was.** NVDA #1's exit decision was taken at
197.68 (−0.56R) and filled at 197.50 (−0.74R): **0.18R lost between decision and
fill**, four minutes apart at the 5-minute cadence. Invisible to the metric.

**Watch.** Compare `rr_progress` at the exit decision against the realised
`r_multiple`. The gap is cadence cost and nothing currently records it.

**Trigger.** Median gap above ~0.2R means the cadence is the binding constraint,
not stop placement.

### 2.2 Realised R stops disappearing

R is measured against entry risk at close, not the moved stop.

**Watch.** `r_multiple IS NULL` on closed trades. **Trigger.** Any null on a
trade that reached +1R.

### 2.3 Regime block firing

Evaluates 100% of candidates instead of 27.8%.

**Observed 07-31.** `HIGH_VOLATILITY` ran 44% pre-open → 58% at the bell → 67%
by mid-morning, against 73% on 07-30. The split is working but converges on the
old behaviour under real intraday volatility.

**Trigger.** Settling above ~70% means the trend read is not separating from the
volatility read in live conditions.

### 2.4 Setup % on the new scale — the projection was never comparable

**Correction.** The projected ~9.5% base pass rate was computed against
`MIN_SETUP_BASE = 62`. The gate that actually runs demands **70** baseline
(hardcoded, see 4.3) and escalates to **83** on weak breadth and **85** on
`RANGE_BOUND`. Observed 3.2% on 07-31 is not evidence the archive was
unrepresentative — the two numbers measure different thresholds.

**Watch.** Pass rate against the *effective* threshold for the day's regime, not
against 62.

### 2.5 Premium P&L was measuring the spread, not the P&L

**Superseded.** This section previously said premium-terms results were merely
unsampled. They were worse than that: `option_pnl_pct_net` read the entry ask
and the close ask from the same live-refreshed `option_ask` key, so it evaluated
to **minus the current spread on every trade regardless of outcome** — NVDA
recorded −2.2% on a 2.2% spread, CRWD −9.21% on a 9.21% spread. A winner would
have booked ≈ −spread% too, so `net_win_rate` was structurally near zero.

Fixed in `eb56f75`: `option_entry_ask` is frozen at open like
`initial_stop_loss`. Trades opened before that field existed fall back to the
entry mid, which understates the spread rather than cancelling it out.

**Watch.** First closed trade after deploy — `option_pnl_pct` and
`option_pnl_pct_net` must now **differ by the spread**, and
`option_spread_cost_pct` must be **positive**. It was negative on every trade
before the fix, which is the tell.

**Also note.** `option_spread_pct` is still live-refreshed, so it holds the exit
spread, not the entry spread. Entry economics cannot be reconstructed after the
fact.

### 2.6 Earnings blackout

Migration 023 applied, 4,777 dates cached, refreshed 01:19 ET on 07-31.

**No watchlist symbol reported on 07-31.** Upcoming: `PLTR` 08-03, `AMD` 08-04,
`SPCX` 08-04, `SMCI` ~08-11, `NVDA` ~08-26.

**Trigger.** Not blocked on those dates, or `max(fetched_at)` going stale.

### 2.7 The scan's own record now survives

`persist_scan_artifacts` and `persist_regression_snapshot` are no longer
cancelable (`f13002b`).

**Why.** `cancel_old_jobs` kills queued cancelable jobs from an earlier scan the
moment the next one starts. Premarket's 600s cadence left seven idle minutes and
hid it; the opening range's 120s against a ~150s scan left about five seconds.
**13 of 50 runs archived nothing on 07-31 and 9 never left `STARTED`**, because
`record_scanner_run_finish` sits inside the cancelled job.

**Watch.** `select count(distinct scan_id) from scanner_snapshot where
trading_day = current_date` should equal the run count, and no run should remain
`STARTED` after the next one begins.

**Trigger.** Any gap, particularly between 09:30 and 09:45.

### 2.8 `rule_performance` starts receiving rows

It had **never** received a row: `write_daily_learning_summary` passed
`entry_exit_v2_shadow.csv` where the waterfall was expected, a frame with no
`stage` column and no `blocking` flag.

It now reads the `decision_waterfall` table and counts **blocks**, not failures.
That distinction matters: on 07-31 Option Quality "failed" 1,039 times and
genuinely blocked six — once a candidate dies at Momentum no contract is priced,
so downstream rules record 0.0 and cascade.

**Expect, from 07-31's shape:**

```
Setup 599 | Directional Signal 358 | PULLBACK_TO_EMA9 33 | BREAKOUT_LEVEL 32
EMA_ALIGNMENT 15 | REL_VOLUME 14 | Option Quality 6 | RR 3
```

**Trigger.** Still empty after a session means the daily learning summary is not
running at all.

---

## 3. Not yet fixed, deliberately

### 3.1 ATR regime buckets on the risk side — deferred

`ATR_PCT > 0.45` is the 25th percentile of the observed distribution (median
0.70). The regime *block* was fixed by splitting out a trend read; the risk
manager's multipliers still use the volatility-first regime.

Left alone because the dominant setups use structure stops that bypass
`stop_atr_multiplier`, and where it does bite the mislabelling is permissive
rather than restrictive.

**Revisit when** stop geometry changes. Note the contradiction to resolve then:
at the `HIGH_VOLATILITY` multiplier the implied stop exceeds that regime's own
1.15% cap for 100% of names with ATR% > 0.8.

### 3.2 Stop-viability multiple 1.0 → 1.5

1.0 rejects only what cannot cover its own round trip once. 1.5 also rejects the
1.05–1.43 band, which is thin but winnable.

**First data point recorded.** CRWD cleared at 1.37× and lost −0.33R. One trade
is not a decision; keep counting that band.

### 3.3 Setup % predictive validation

The metric provably measures setup conviction rather than RR. Whether it
*predicts outcomes* is untested. **Revisit at ~30 completed strategy trades.**

### 3.4 `PARTIAL_PROFIT` banks nothing

`rr_progress >= 1.5` sets `partial_profit_taken`, a `trade_action` and a label.
No size is reduced and no profit is realised — NVDA printed it three times at
1.55R, 1.61R and 1.66R and closed the full position at +0.60R.

Deliberately not fixed with the rest: doing it properly touches position sizing,
R accounting and alert copy, and the profit lock (1.1) already protects the
gains partial-taking was meant to protect. **Should be its own change**, so its
effect is attributable.

---

## 4. Known-wrong, worth fixing

### 4.1 ~~`load_dotenv(override=True)` makes local runs unsafe~~ — FIXED

Both call sites now load with `override=False`, so a variable already set in the
environment wins over `.env` and `DB_WRITE_ENABLED=false python -m app.main` is
finally honoured. Streamlit Cloud is unaffected: there is no `.env` there and
Secrets arrive as real environment variables.

Kept for the history. It was how the orphaned NVDA position came to be closed at
−4.12R during a verification run believed to be write-disabled. Both DB writers
were always correctly guarded — the guard simply never received the value it was
given. The commit message on `31d825e` blames `upsert_paper_trade`; that is
wrong.

### 4.2 ~~Four tables the code writes to do not exist~~ — RETRACTED

**This item was false and is kept only so it is not rediscovered.** It claimed
`scanner_run`, `daily_session_summary`, `missed_winner_analysis` and
`trade_efficiency` were written to and missing, with every write failing
silently.

Checked properly on 2026-07-31: **zero SQL references to any of them.**

- `scanner_run` — only ever appears as `scanner_run.lock`, a lock *filename*
- `trade_efficiency` — a Python *module directory*, `app/analytics/trade_efficiency/`
- `daily_session_summary`, `missed_winner_analysis` — no references at all

Nothing writes to them, nothing fails, and no migration is needed. Creating four
unused tables would have been the actively worse outcome.

The claim was inherited from an earlier session's assertion and repeated twice
without verification — including into this file's own rewrite, a few hours after
section 6 was written warning about exactly this. Worth keeping as the concrete
example: **a confident claim in a handover document is a lead, not a fact.**

### 4.3 ~~Config in Secrets that never reaches the gate~~ — FIXED

`app/main.py` hardcoded the scanner's entry gate:

```python
SCANNER_ENTRY_GATE_CONFIG = EntryGateConfig(
    min_rr=2.0, min_setup_percent=70.0,
    min_option_quality=65.0, max_spread_pct=10.0)
```

So `OPTION_MAX_SPREAD_PCT = 6` did **not** reach it — every `Option Spread` rule
row evaluated against 10 (or 5 in `RANGE_BOUND`), which is why the 2026-07-31
waterfall showed `required_value 10.0` for a setting that had been 6 since the
previous night.

Now read from configuration: `OPTION_MAX_SPREAD_PCT`, `OPTION_MIN_QUALITY_SCORE`
and two new named knobs, `SCANNER_GATE_MIN_RR` and `SCANNER_GATE_MIN_SETUP`. The
scanner's own bar stays deliberately above `MIN_SETUP_BASE` — 62 is the floor
below which a row is not a setup at all, 70 is the bar for putting a candidate
forward — but it is now nameable and tunable rather than a literal in module
scope.

### 4.4 Entry score is computed and discarded

`entry_engine._entry_score` builds a score and uses it only to pick between
setups within one bar. `best_score` is never returned.

**`exit_confidence_score` had the same problem** and is now partially consumed
by the profit lock (1.1) — but only as a threshold, and only for soft exits on
profitable trades. All three exits on 07-31 scored under 20.

### 4.5 Timestamps are four hours off

`paper_trades.opened_at` and `closed_at` store **ET wall-clock in a `timestamptz`
column** — NVDA opened at `10:58:46+00:00` when the real UTC was `14:58:46`.
`created_at` is correct. Durations across the pair are safe since both ends are
shifted equally; **any join against `created_at` or `now()` is wrong by four
hours.** The 2026-07-31 manual reconciliation deliberately followed the broken
convention to stay consistent with its peers.

### 4.6 Regime raises thresholds but never restricts direction

`RANGE_BOUND` escalates setup to 85 and RR to 2.0 for both directions.
`TRENDING_BEAR` + `CALL` already has a precedent for asymmetry. On 07-31 all
three trades were longs in a range-bound, weak-breadth tape — the losing side of
a range. A rule change rather than a bug fix; needs more than one session.

### 4.7 Delivery outcome is never written back

`telegram_dispatch` records intent, never outcome: rows stamp `ATTEMPTED` at
hand-off to the queued CRITICAL job and nothing updates them. All-time there are
2 `DELIVERED` rows and 238 `ATTEMPTED`.

**Alerts are being delivered** — this is a telemetry gap, not a delivery failure,
confirmed against the user's own receipt of the 07-30 PLTR and ORCL alerts. It
is listed because it cost an hour of misdiagnosis on 07-31 and there is no audit
trail of what subscribers actually received.

### 4.8 Data staleness

`STALE_STOCK_DATA` was ~37% of candidate rows before the 2026-07-29 freshness
fix, ~13% after, and **4.1% on 07-31**. Improving. Not random — it correlates
with which symbols the feed lags.

---

## 5. Change log

### 2026-07-31, pre-open

| Commit | Change |
| --- | --- |
| `d20ff06` | R measured against entry risk at close; `bars_in_trade` counts bars not scans; three long/short asymmetries; grace zone extended to VWAP and MACD; level-triggered exit fills with recorded slippage; `Setup %` rebuilt and deduplicated |
| `c1acfcf` | Regime block no longer waived on volatility; single setup registry replacing five drifted lists |
| `31d825e` | `include_in_strategy_stats` honoured — see the correction in 4.1 |
| `64f2b6a` | Stop viability enforced at 1.0× |
| migration | `023_earnings_calendar` applied to Neon; 4,777 dates cached |

### 2026-07-31, from watching the live session

| Commit | Change |
| --- | --- |
| `f13002b` | Scan artifacts and regression snapshot no longer cancelable; `rule_performance` fed from the real waterfall and counting blocks rather than failures |
| `cc1fe72` | `REVIEW_TV_CHART` candidates alert again; four tests re-pointed at the restored contract plus one for the dedup that bounds volume |
| `eb56f75` | MFE ratchets instead of overwriting; profit lock on low-confidence soft exits; `upsert_paper_trade` refuses to regress a `CLOSED` row; `option_entry_ask` frozen at open |

Tests 305 → **426**.

### Manual data corrections

**2026-07-31 16:17 ET.** The third NVDA trade was left `OPEN` by the
queued-upsert race after exiting at 14:30:37. Reconciled directly in production
from the `TRADE_EXIT` alert payload — the only record of the close that existed
— to `close_price=198.5501, r_multiple=0.60, pnl_pct=0.30`. `r_multiple`
recomputed independently from entry and risk-per-share agreed with the alert.
`option_close_mid` left NULL rather than filled with the 14:10 quote of 5.45,
which was the mid at +1.66R and not at the exit. The row carries a
`manual_reconciliation` block in its payload.

---

## 6. What 2026-07-31 actually showed

Worth keeping, because it is the first evidence from a live session rather than
from an archive.

**The entry side is working.** 26 symbols scanned in **41 of 41 scans** with no
dropouts. 599 Setup blocks and 358 momentum blocks produced three trades, each
clearing Setup 83–94 at RR ≥ 2.26 as `BULLISH_TOP_1`. Nothing was stopped by
anything spurious — every block traced to a real rule with real values.

**The exit side is where the money went.** All three exits fired on soft
invalidation at an exit confidence under 20 (19.0, 17.5, 11.5) with trend health
still reading 70, 75 and 95. The most expensive gave back 1.06R.

**The measurement layer was the weakest part of the system**, and three of the
five defects found were in it. Two of them — MFE resetting and net premium P&L
returning the spread — were corrupting the very numbers used to evaluate
everything else. That is the lesson worth carrying: on 07-31 more was learned by
querying what the system recorded about itself than by reading its code, and
three separate times an un-maintained status column was misread as a failure
(`scanner_runs.status`, `telegram_dispatch.delivered`, cascade `Option Quality
0.0`). **Check whether a column is maintained before drawing a conclusion from
it.**
