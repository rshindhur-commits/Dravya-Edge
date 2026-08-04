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

### 1.2 ~~Stop-viability enforcement has still never fired~~ — IT FIRES

**Answered on 2026-08-03: 11 blocks in one session**, all on SMCI, at multiples of
0.49×, 0.56×, 0.82×, 0.83×, 0.83× and 0.87×. The gate is reachable, it is
enforcing, and "RR always rejects first" was true only of 07-31's flow.

**It did not stop the loss it looks like it should have.** SMCI opened at 12:28 ET
with `stop_spread_multiple = 1.69` — clear of the 1.0 bar with room — and closed
at −1.0R. The entry spread was 2.65%; the **exit** spread was 4.55%, and realised
`option_spread_cost_pct` came in at 3.51% against a gross loss of 2.46%. **Friction
was 59% of that loss and none of it was visible at entry.** No entry-time multiple
could have caught it, and a threshold high enough to block 1.69× would be fitted to
a single trade.

**All six opened trades, by multiple:** 5.34 (−0.74R), 2.11 (−0.33R), 5.32
(+0.60R), 3.59 (+2.41R), 1.69 (−1.00R), 2.48 (+1.00R). The only entry below 2.0
lost. One observation, not a threshold. Note this supersedes the "CRWD cleared at
1.37×" figure this section previously carried — the ledger records 2.11.

**Watch.** `stop_round_trip_spread_pct` at entry against realised
`option_spread_cost_pct` at exit. Recordable for the first time as of the 08-03
change set; before it the ledger kept the multiple and discarded every input.

**Trigger.** Realised cost exceeding the entry spread by more than ~1.5× on a third
of trades means the gate is watching the wrong end of the trade, and the answer is
an exit-spread guard, not a higher entry multiple.

### 1.2a Position caps are not the constraint — stop reaching for them

**Measured across every session on record.** Max *concurrent* open positions by
day: 07-09 1, 07-10 1, 07-17 2, 07-20 1, 07-29 1, 07-30 **3**, 07-31 1, 08-03 1.
Trades run sequentially, not in parallel — on 08-03 ORCL closed at 16:28:33 and
SMCI opened at 16:28:33, the same second, so peak exposure was **one** position
against a cap of four.

`MAX_ACTIVE_PAPER_TRADES_REACHED`, `DIRECTION_ALREADY_ACTIVE` and
`DAILY_AUTO_PAPER_LIMIT_REACHED` have produced **5 blocks in total, ever**, and
none on 08-03.

**Why this is worth writing down.** Aggregate-exposure arithmetic (4 positions x
the cost cap) looks alarming and has never once described reality. Lowering the
concurrency caps as a risk control costs nothing on a normal day and costs a trade
on exactly the days worth capturing — 07-30 was the only session that ever reached
3. Per-*trade* cost is the exposure dial that actually moves; concurrency is not.

**Where the trades actually go, 08-03:** `NO_ENTRY_TRIGGER` 388,
`NO_DIRECTIONAL_EDGE` 132, `RISK_REJECTED` 103, `OPTION_REJECTED` 98, `LOW_RR` 72.

**Revisit when** max concurrent reaches the cap on three sessions in a fortnight.
Until then the caps are documentation, not constraints.

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

**Expect ~4.7/day.** Superseding the "~11/day" this section previously carried:
11 was 07-31 alone (against 65 raw review events, across NFLX, NVDA, QQQ, SPCX,
AAPL, ORCL, PLTR, TSLA, AMZN and CRWD). Measured over 9 sessions in production it
is 42 deduped alerts, 4.7/day. See 1.6 for the query.

**Trigger.** More than ~20 in a session means the dedup key is not holding.

### 1.6 Review alerts now carry a quality floor, and much more content

**New on 2026-08-01.** Two changes to the message subscribers see most.

**A volume valve, shipped OFF.** `TELEGRAM_MIN_REVIEW_SETUP_SCORE` (default **0**)
blocks candidates with reason `REVIEW_SETUP_BELOW_FLOOR`. Checked *before* dedup,
so a candidate rejected at 09:50 can still alert at 11:20 if it strengthens.

**It defaults off because calibration said the setup score is the wrong axis.**
Measured against production `candidate_snapshot`, 64 live `REVIEW_TV_CHART`
candidates over 9 sessions:

| | |
| --- | --- |
| Setup score | min 38, p25 79.8, **median 86**, p75 92, max 97 |
| Option quality | min 75, **median 100**, none below the 65 alert bar |
| Deduped alerts | **42 over 9 days = 4.7/day** |
| Floor 60 | removes **1 of 42** |
| Floor 70 | removes the same 1 |
| Floor 80 | removes 8, and starts cutting candidates the scanner rates highly |

Two corrections to what this file previously said. Review volume is **4.7/day,
not ~11/day** — 11 was a single-day peak on 07-31, quoted as if it were the norm.
And these candidates are **not low quality on either axis**; they reached
`REVIEW_TV_CHART` by scoring well, so filtering them on score is close to a no-op.

**Kept rather than deleted** because §1.5's own failure mode (">20 in a session
means the dedup key is not holding") needs a dial that turns mid-session without
a deploy. It is a circuit breaker, not a quality filter.

**Watch.** Daily review alert count. **Trigger.** Above ~20/day, set this to 80
as a stopgap and fix the dedup key; below ~2/day, check nothing else is
suppressing them.

**Content.** The message carried ticker, setup and next condition and nothing
else — no price, no direction, no evidence, no levels. It now carries price,
direction, the evidence that earned the listing (setup strength, alignment,
relative volume, relative strength), the confirmation still needed, and the
contract with cost and spread if one was selected. Every field was already
available at the call site and simply was not passed through. Sections are
omitted rather than dashed when data is missing.

### 1.7 Weekly results are posted to the channel

**New on 2026-08-01.** `dispatch_weekly_summary_if_due` runs on every scan and is
gated twice — a due window (Friday's close through the weekend) and a
once-per-ISO-week dedup key — so it is a no-op on all but one scan a week.

Reports in **R and in premium**, because R alone measures the wrong instrument.
The first real week bears this out: 2026-07-27 to 07-31 was 42.9% win rate and
−0.09R average, but **0% win rate after costs**, −4.45% average per trade against
a 7.95% average spread. A summary publishing only R would publish the flattering
half.

Samples under 30 trades carry an explicit "not statistically meaningful" line.

**Watch.** That exactly one summary goes out per week, and that `priced_trades`
rises toward `completed_trades` as trades opened after `eb56f75` close — only 3
of 7 were priced in the first week, and unpriced trades are invisible to the
premium figures.

**Trigger.** Two summaries in a week means the ISO-week key is not holding. Zero
means either nothing scanned Friday evening — recover with
`python -m tools.send_weekly_summary` — or `TELEGRAM_WEEKLY_SUMMARY_ENABLED` is off.

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

### 2.3 Regime block firing — TRIGGER MET, and it is the reason for the trade count

Evaluates 100% of candidates instead of 27.8%.

**Observed 07-31.** `HIGH_VOLATILITY` ran 44% pre-open → 58% at the bell → 67%
by mid-morning, against 73% on 07-30. The split is working but converges on the
old behaviour under real intraday volatility.

**Trigger.** Settling above ~70% means the trend read is not separating from the
volatility read in live conditions.

**2026-08-03: 75%.** `HIGH_VOLATILITY` was the reference regime for **2,258 of
2,990** evaluations. The trigger above is met, and this is now the single largest
constraint on trade count — larger than every gate this file otherwise tracks.

`apply_regime_entry_thresholds` raises `min_setup` to `MIN_SETUP_ELEVATED` (81) and
`min_rr` to 2.0 on `HIGH_VOLATILITY`. What that did to the Setup gate:

| Effective floor | Evaluations | Passed | Median setup |
| --- | --- | --- | --- |
| 62 (regime UNKNOWN, premarket) | 729 | 0 | 8 |
| **81 (HIGH_VOLATILITY)** | **1,902** | **6** | 49 |
| 83 (weak breadth) | 104 | 0 | 0 |
| 85 (RANGE_BOUND) | 255 | 1 | 24 |

**7 of 2,990 candidate-scans cleared Setup — 0.23%.** The full funnel that day:

```
2,990 evaluated -> 1,762 pass Momentum (58.9%) -> 7 pass Setup (0.23%)
               -> 4 reach an actionable Action Status -> 3 trades
```

Every gate downstream is measuring almost nothing. RR passed 34% but blocked only
3; Option Quality and Quote Freshness never blocked at all; the position caps never
fired. **Nothing changed on 2026-08-03 moved the trade count, and nothing in the
option or risk layer can, while Setup passes 7 rows in a session.**

**What to do about it is not obvious and should not be guessed at.** Lowering
`MIN_SETUP_ELEVATED` trades directly against the reason it exists, and 81 was
chosen on the same scale that makes 62 the floor for being a setup at all. The
prior question is whether `HIGH_VOLATILITY` at 75% is a true read of the tape or
the volatility-first regime bucket mislabelling a normal session — which is the
same doubt 3.1 records about the ATR buckets, still unresolved.

**Watch.** Daily `HIGH_VOLATILITY` share and the Setup pass rate against the
*effective* floor. **Trigger for action.** Two more sessions above 70% with a
Setup pass rate under 1% makes the regime read, not the setup bar, the thing to
fix.

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

### 2.6 `option_quality` vs the spread actually paid — open, and not yet measurable

**Claimed 2026-08-01, retracted the same day.** The claim was that
`option_quality_score` is blind to round-trip cost, on this evidence:

| Symbol | `option_quality_score` | `option_spread_cost_pct` | `option_pnl_pct_net` | live spread |
| --- | --- | --- | --- | --- |
| NVDA | 100 | 2.17% | −5.38% | 5.38% |
| NVDA | 95 | −2.06% | −2.20% | 2.20% |
| CRWD | 40 | 14.28% | −9.21% | 9.21% |
| NVDA | 95 | 11.62% | −1.94% | 1.94% |

**Every row is the pre-`eb56f75` artifact 2.5 describes.** `net` equals
*minus the live spread* exactly, in all four — the signature of reading the entry
ask and the close ask from the same live-refreshed key. **Zero** of the four
carry a frozen `option_entry_ask`. The 11.62% "spread cost" is just
`option_pnl_pct` (9.68%) minus that artifact (−1.94%); it never measured a spread.

So there is **no evidence either way** about whether the quality score tracks
round-trip cost, and no post-fix priced trade exists yet to provide any. Retuning
the score or the spread ceilings now would be tuning against corrupted data —
the exact failure this file was opened to prevent.

**The instrumentation is already in place.** `option_entry_bid`,
`option_entry_ask` and `option_entry_spread_pct` are all frozen at open as of
`eb56f75`. Nothing more is needed; the gap is elapsed time.

**Two changes landed instead of a retune:**

1. `build_performance_statistics` now counts a trade as *priced* only when it
   carries a frozen `option_entry_ask`. Pre-fix trades report as unpriced rather
   than contributing a confident-looking average of nothing. This is why the
   first weekly summary published "0% win rate after costs on a 7.95% spread" —
   all three of its priced trades were the artifact.
2. `build_spread_calibration` runs daily into `daily_engine_summary.json` under
   `spread_calibration`, recording `option_quality_score` against
   `option_entry_spread_pct` and realised `option_spread_cost_pct` per trade.

**Watch.** `spread_calibration.measurable_trades` climbing above zero, then
`high_score_wide_spread_count`.

**Trigger.** Two trades scoring above 80 with realised cost above 6% and the
score needs the entry spread folded in. A large positive `quality_vs_cost_gap`
means something different — the spread widened while the position was held, which
is a risk nothing currently models and no entry-time score could have caught.

### 2.7 Earnings blackout

Migration 023 applied, 4,777 dates cached, refreshed 01:19 ET on 07-31.

**No watchlist symbol reported on 07-31.** Upcoming: `PLTR` 08-03, `AMD` 08-04,
`SPCX` 08-04, `SMCI` ~08-11, `NVDA` ~08-26.

**Trigger.** Not blocked on those dates, or `max(fetched_at)` going stale.

### 2.8 The scan's own record now survives

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

### 2.9 `rule_performance` starts receiving rows

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

### 2026-08-03, after the session

Four changes, none of which alters an open position.

| Area | Change |
| --- | --- |
| Spread-to-stop gate | `spread_cost_exceeds_risk` delegates to `evaluate_stop_viability` instead of repeating its arithmetic. The rule existed **twice**, with reciprocal environment variables (`MIN_STOP_SPREAD_MULTIPLE` vs `AUTO_PAPER_MAX_SPREAD_TO_RISK`) and different premium fallbacks, so raising one left the other at 1.0. Survivable only because the auto-paper copy sits downstream of a row the scanner already downgraded — set `STOP_VIABILITY_ENFORCE=false` and the looser copy becomes the only one running. The legacy variable is still honoured as its reciprocal. **No threshold moved.** |
| Decision ledger | `min_setup_used` records the floor that actually applied (`ENTRY_GATE_MIN_SETUP`, regime-escalated) rather than the auto-paper control. On 08-03 it logged 62 beside `SETUP_BELOW_THRESHOLD` blocks at setup 62, 70 and 79 — three rows that read as contradictions. The control is kept as `auto_paper_min_setup`. Contract economics — delta, premium, bid/ask, ticker, the stop-viability inputs — now reach the payload; **not one of 869 rows on 08-03 carried any of them**, which is why the 11 spread blocks cannot be recalibrated. |
| Rule emitter | `Option Quality` and `Quote Freshness` are emitted only once a contract has been priced, matching what `Option Spread` always did. A candidate that dies at Momentum never gets one, so both scored 0.0 against their floors: **2,913 fabricated failures against 77 real evaluations on 08-03**, none of them blocking, which is how Option Quality came to head the rule tables while costing nothing. |
| Worker restarts | The heartbeat is keyed on `instance_id`, so a restarting worker overwrites its predecessor and resets `scans` to 0. A SIGTERM writes `STOPPED` and explains itself; an OOM kill writes nothing. Startup now reads the previous row first and, when it was still claiming to be alive, alerts the operator and carries `restarted_from` into its own payload. |
| Leverage in ranking | `Option Premium % of Notional` becomes a scoring component, funded out of the existing contract budget (option quality 0.15 + liquidity 0.10) so the weights still sum to 1.0 and `RANK_LEVERAGE_WEIGHT=0` restores the old scores exactly. |
| Profile budgets | `MAX_ACTIVE_INTRADAY_TRADES` / `MAX_ACTIVE_MULTIDAY_TRADES` and `MAX_DAILY_INTRADAY_ENTRIES` / `MAX_DAILY_MULTIDAY_ENTRIES`. **All four default to the shared caps, so this is inert until set.** |

Tests 426 → **841**.

#### Leverage weighting — what it is and what to watch

R is computed entirely on the underlying, so it cannot see what the contract costs
to control it. 2026-08-03: ORCL and SMCI both went the right way; ORCL booked
+2.41R and **+26.4% of premium**, SMCI booked +1.0R and **+5.1%**. ORCL's contract
cost 2.6% of notional, SMCI's 9.5%.

Measured over 77 archived candidates carrying both a premium and an entry price:

| | min | p10 | p25 | median | p75 | p90 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Premium as % of notional | 1.21 | 1.59 | 1.68 | 3.41 | 9.51 | 10.64 | 10.85 |

By symbol, with implied elasticity (delta × spot / premium):

```
AAPL 1.43 (23.4x)  TSLA 1.52 (23.7x)  AMZN 1.67 (20.8x)  NVDA 2.18 (24.3x)
ORCL 2.63 (13.1x)  XOM  2.68 (15.8x)  NFLX 3.41 (15.5x)  SMCI 9.51 (5.7x)
```

Scored on premium/notional rather than elasticity because the contract ranker bands
delta to 0.25–0.75 and penalises distance from 0.55 at 100 points per unit, so the
two orderings coincide. **Widen that band and this must become elasticity**, or it
starts rewarding far-OTM lottery tickets.

**Replayed over 4,654 archived rows across 179 scans: 419 rows (9%) change rank.**
SMCI is the only symbol materially affected — mean +1.20 ranks, and it loses rank 1
in three scans by an average of 5.7 places. Every other symbol moves by under a
third of a rank on average.

**Watch.** `Option Premium % of Notional` on opened trades against realised
`option_pnl_pct`. **Trigger.** Median premium ratio on opened trades not falling
below ~5% within two weeks means the component is not reaching the decision — see
the open item below.

**Open, and deliberately not done.** This changes `Candidate Rank`, which gates
entry only through `AUTO_PAPER_MAX_CANDIDATE_RANK`. It does **not** change
`Top Candidate` (`BULLISH_TOP_1..3`), which is ordered by `Expected Value Score`
= setup × RR in `_add_expected_value_rank` and is premium-blind. Both SMCI entries
on 08-03 came in as `BULLISH_TOP_1`, so neither would have been blocked by this
change. Folding leverage into the expected-value ordering would alter which trades
are taken on every scan and is its own decision.

### 2026-08-03, second pass

| Area | Change |
| --- | --- |
| MFE at close | `update_paper_trade` ratchets `mfe_r` but runs on **holding** scans only — the closing scan takes another path, so the final and usually highest excursion was never folded in. ORCL recorded `mfe_r 1.43` against a realised **+2.41R**: a peak below the outcome, which cannot happen. Trend capture reported 168%, and §1.1's profit-lock watch was comparing an exit against a peak lower than itself. Ratcheted at close against the realised R, so a genuine giveback still shows. |
| Spread widening | Recorded, **not acted on.** `option_spread_pct_peak`, `option_spread_widening_ratio` and `option_spread_peak_widening_ratio` on every scan. `EXIT_MAX_SPREAD_WIDENING_RATIO` ships at **0 (disabled)** and only latches `spread_widening_would_exit`. |
| Rejection evidence | Every non-liquid verdict now carries the ticker, strike, DTE, OI, volume, bid/ask, delta and the **threshold it failed** (`required_value`), and reaches the ledger as `option_rejection_evidence`. |
| Telemetry sandbox | `telemetry/` resolved as a bare relative path and so escaped the test sandbox — any test reaching `close_paper_trade` appended real rows to the tracked `telemetry/trade_telemetry.csv`. Now anchored to a storage root, with `DRAVYA_TELEMETRY_DIR` set in `tests/__init__.py`. |

Tests 844 → **858**.

#### Spread widening — the finding behind it

Every gate here judges the spread **once, at entry**. 2026-08-03 says that is the
wrong end of the trade:

| Trade | Entry spread | Exit spread | Realised round trip | Ratio |
| --- | --- | --- | --- | --- |
| ORCL | 2.70% | 3.16% | 3.68% | 1.36× |
| SMCI (loss) | 2.65% | **4.55%** | 3.51% | 1.32× |
| SMCI (win) | 1.12% | **2.81%** | 2.08% | **1.86×** |

**Three of three widened**, and realised cost exceeded the entry spread in all
three. On the loss, friction was 3.51% against a gross loss of 2.46% — **59% of
the loss** — while the entry gate had cleared it at 1.69× with room. No
entry-time multiple could have caught that, because at entry the spread genuinely
was 2.65%.

**Shipped disabled on purpose.** Three trades is not a threshold, and the correct
*response* is genuinely unclear: exiting into a widened spread pays that spread to
escape it. Closing early may cost less than closing later, or may only realise the
cost sooner. This records what it would have done, exactly as stop viability
shipped observe-only until the archive answered its rejection rate.

**Watch.** `option_spread_peak_widening_ratio` on closed trades, and
`spread_widening_would_exit` once a ratio is set. **Trigger.** A median peak ratio
above ~1.5× over ten trades makes this the dominant cost and worth acting on;
below ~1.2× the 08-03 sample was noise.

#### Rejection evidence — what it is for

`OPTION_REJECTED` was the fourth-largest blocker on 08-03 (98 decisions) and the
least legible. MSFT alone produced 29 rejections reading `Low open interest` and
`Wide bid/ask spread` with the ticker, the open interest and the threshold all
absent — so *is `OPTION_MIN_OPEN_INTEREST=500` too high, or is the selector
reaching for an illiquid strike?* was unanswerable from the record.

**Do not raise `OPTION_MIN_OPEN_INTEREST`.** It is a floor; raising it blocks
more. At 1200 it would have rejected **ORCL at OI 888 — the only good trade of
the day, +2.41R and +22.67% net** — and TSLA at 888, while both SMCI trades at
OI 5,452 sail through. Of the 77 contracts selected that day, 100% pass at 500 and
91% at 1200, and the 9% lost are the ones that earned.

**Watch.** `option_rejection_evidence` grouped by `code`, with the observed OI
distribution against `required_value`.

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
