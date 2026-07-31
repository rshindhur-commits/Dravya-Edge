# Post-change watchlist — 2026-07-31

Nine defects were fixed on 2026-07-31 across five commits plus one migration. Most
of them change trading behaviour deliberately, and several were calibrated against
an archive of 1,633–1,742 rows spanning **two to three trading days**. That is
enough to establish direction and not enough to establish magnitude.

This file is the list of things to look at once real sessions run, with the number
that would say a change went wrong. Delete an item once its question is answered.

Baseline to compare against, from before the change set:

| | |
| --- | --- |
| Strategy record | **−0.44R per trade, 25% win rate, 8 trades** (flag-corrected) |
| Blended record as previously displayed | −0.37R, 38.9%, 18 trades |
| Average hold | 6.8 minutes on 07-30, 14–48 minutes on 07-27 |
| Exits by soft invalidation | 9 of 13 |
| Candidates reaching ENTER_PAPER | ~0.5% |

---

## 1. Watch first — the changes most likely to be wrong

### 1.1 Direction tilt from stop-viability enforcement

**Why.** All ten historical blocks fell on CALLs, taking the book from 27:10 to
17:10 long:short. `EMA_PULLBACK` is the only long setup firing in volume and takes
the tightest stop (0.25×ATR structure), so a neutral-looking risk filter lands
entirely on one direction. This compounds with the scoring-asymmetry fix, which
also removed a long-side disadvantage.

**Watch.** Direction split of opened trades, and of `Blocked By =
STOP_INSIDE_OPTION_SPREAD` rows.

**Trigger.** Three consecutive sessions with no CALL opening, or PUTs above ~70% of
opened trades. Note `max_active` is 4 with only a per-direction limit of 1, so a
short-heavy book can run several positions at once.

**If it trips.** The fix is stop geometry, not the gate — the 0.25×ATR floor on
`EMA_PULLBACK` is what makes those stops unclearable. Widening it lifts the spread
multiple and the rejections stop on their own.

### 1.2 Stop-viability rejection rate

**Why.** 1.0× was chosen from a clean break in the distribution of 25 ENTER_PAPER
rows. Twenty-five rows.

**Watch.** Count of `STOP_INSIDE_OPTION_SPREAD` per session. Expect **1–4/day**.

**Trigger.** Consistently 0 (the gate is not reaching, check for missing delta or
spread on candidate rows) or consistently above ~6 (threshold too tight for live
flow).

**Note.** This should self-correct as `MIN_STOP_DISTANCE_PCT=0.50` widens stops.
Rejections falling over time is expected and healthy, not a regression.

### 1.3 Hold duration after the bars-in-trade retune

**Why.** `bars_in_trade` counted scans, not bars, so three thresholds fired ~3×
early. They were restated in real bars to hold the wall-clock behaviour that was
actually running, so **hold times should not change much** — that is the test.

**Watch.** Median hold, and `bars_in_trade` at exit.

**Trigger.** Median hold moving materially away from the 15–45 minute range in
either direction. Much longer means the retune overshot; still ~7 minutes means
something else is closing trades early (see 1.4).

### 1.4 Soft-exit scratches

**Why.** The grace zone now covers VWAP and MACD, not just EMA. Two trades
previously exited at +0.04R and +0.10R.

**Watch.** Exits with `|R| < 0.25`, and the frequency of
`adjustment_reason` containing "grace zone active".

**Trigger.** Scratch exits continuing at the old rate means the grace zone's
eligibility conditions (trend health ≥ 60, in profit or MFE ≥ 1R, exactly one soft
confirmation) are too strict to ever fire. Confirm by counting how often the grace
zone actually engages — if it is near zero, that is the problem, not the rules.

---

## 2. Watch second — should be quietly correct

### 2.1 Exit slippage is now visible

`exit_slippage` records the adverse gap between the trigger level and the fill,
in price terms. Divide by risk-per-share for the R cost.

**Watch.** Median slippage as a fraction of R on `HARD_STOP` exits. Historically
SPCX lost 0.88R to overshoot on a 1R trade.

**Trigger.** Median above ~0.2R means the 300s scan cadence is the binding
constraint, not the stop placement, and the answer is a faster cadence for open
positions rather than any change to the exit rules.

### 2.2 Realised R stops disappearing

R is now measured against the entry risk at close, not the moved stop.

**Watch.** `r_multiple IS NULL` on closed trades.

**Trigger.** Any null on a trade that reached +1R. That was the original bug and
it should now be impossible.

### 2.3 Regime block actually firing

Evaluates 100% of candidates instead of 27.8%.

**Watch.** `Regime Blocked` share. Expect **~5.5%**, was 3.2%. Also compare the new
`Trend Regime` column against `Market Regime` — they should disagree often, since
that divergence is the entire point.

**Trigger.** `Regime Blocked` still near 3% means the trend regime is not being
passed through; near 0% means something upstream is short-circuiting.

### 2.4 Setup % on the new scale

Thresholds were re-derived at matched pass rates, so **volume should be unchanged**
and only the composition should shift.

**Watch.** Pass rate at the base threshold (expect ~9.5%) and the strict tier
(~1.5%). Median `|15m Score|` of candidates that clear should rise toward 13.5 from
10.7; median RR should fall toward 1.94 from 2.27.

**Trigger.** Volume moving more than ~30% either way means the archive was not
representative of live flow.

### 2.5 Earnings blackout

Migration 023 applied, 4,777 dates cached, verified firing on the right days.

**Watch.** `PLTR` blocked on 2026-08-02 and 2026-08-03; `AMD` on 08-03 and 08-04;
`SMCI` around 08-11; `NVDA` around 08-26.

**Trigger.** Not blocked on those dates, or the cache going stale — check
`max(fetched_at)` in `earnings_calendar` is refreshing.

---

## 3. Not yet fixed, deliberately

### 3.1 ATR regime buckets on the risk side — deferred

`ATR_PCT > 0.45` puts **71% of candidates** in `HIGH_VOLATILITY`; 0.45 is the 25th
percentile of the observed distribution (median 0.70). The regime *block* was fixed
by splitting out a trend read; the risk manager's multipliers still use the
volatility-first regime.

Deliberately left alone: the dominant setups use structure stops that bypass
`stop_atr_multiplier` entirely, so the practical effect is second-order, and where
it does bite (`max_stop_distance_pct`) the mislabelling is permissive rather than
restrictive. Recalibrating now would reject more on already-thin flow.

**Revisit when** stop geometry changes — if the 0.25×ATR floor is widened in
response to 1.1, ATR stops stop being bypassed and these buckets start to matter.
Note the internal contradiction to resolve then: at the `HIGH_VOLATILITY` stop
multiplier the implied stop exceeds that regime's own 1.15% cap for **100% of names
with ATR% > 0.8**.

### 3.2 Stop-viability multiple 1.0 → 1.5

1.0 rejects only what cannot cover its own round trip once. 1.5 also rejects the
1.05–1.43 band, which is thin but winnable — a preference, not arithmetic.

**Revisit when** enough resolved trades exist to show that band losing money.

### 3.3 Setup % predictive validation

The new metric provably measures setup conviction rather than RR. Whether it
*predicts outcomes* better is untested and needs resolved trades.

**Revisit at** ~30 completed strategy trades.

---

## 4. Known-wrong, worth fixing

### 4.1 `load_dotenv(override=True)` makes local runs unsafe

`app/config/settings.py:8` and `app/utils/polygon_client.py:20` call
`load_dotenv(override=True)`, which **overwrites shell environment variables with
`.env` values**. `DB_WRITE_ENABLED=false python -m app.main` silently becomes
`true`, so there is currently **no way to run the scanner without writing to the
production database**.

This is how the orphaned NVDA position came to be closed at −4.12R during a
verification run on 2026-07-31 that was believed to be write-disabled.

Both DB writers (`persistence.upsert_paper_trade` via `_safe_execute`, and
`BestEffortRepository._batch_execute`) are correctly guarded — the guard simply
never sees `false`. **The commit message on `31d825e` states that
`upsert_paper_trade` is unguarded. That is wrong; the cause is the dotenv
override.**

Fix: drop `override=True`, or add an explicit dry-run switch that `.env` cannot
overwrite.

### 4.2 Four tables the code writes to do not exist

`scanner_run` (singular), `daily_session_summary`, `missed_winner_analysis`,
`trade_efficiency`. Every write to them has been failing silently since they were
introduced, and no migration creates them. `scanner_runs` (plural) exists and
works, so there are two competing scan-run schemas.

### 4.3 Entry score is computed and discarded

`entry_engine._entry_score` builds a score from base setup weight, analysis score,
regime bonus and volume, then uses it only to pick between setups within one bar.
`best_score` is never returned, so nothing downstream can rank on it, and
`ENTRY_BASE_SCORES` silently decides ties — `BREAKDOWN_SHORT` at 90 outranks
`BREAKOUT` at 80 whatever the market is doing.

### 4.4 Two accountings of the same trades

`paper_trades` and `trade` disagree — the latter shows 3.41R and 3.56R
`TARGET_HIT` rows that do not appear in the former. Decide which is authoritative
before either is used for evaluation.

### 4.5 Data staleness

`STALE_STOCK_DATA` was ~37% of candidate rows before the 2026-07-29 freshness fix
and ~13% after. Still the largest single source of discarded observations, and it
is not random — it correlates with which symbols the feed lags, so watchlist
coverage is uneven.

---

## 5. Change log — 2026-07-31

| Commit | Change |
| --- | --- |
| `d20ff06` | R measured against entry risk at close; `bars_in_trade` counts bars not scans; three long/short asymmetries; grace zone extended to VWAP and MACD; level-triggered exit fills with recorded slippage; `Setup %` rebuilt and deduplicated |
| `c1acfcf` | Regime block no longer waived on volatility; single setup registry replacing five drifted lists |
| `31d825e` | `include_in_strategy_stats` honoured — see the correction in 4.1 |
| `64f2b6a` | Stop viability enforced at 1.0× |
| migration | `023_earnings_calendar` applied to Neon; 4,777 dates cached |

Tests 305 → 409.
