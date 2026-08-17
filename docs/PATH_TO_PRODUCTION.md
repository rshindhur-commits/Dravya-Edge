# The path to a signal worth subscribing to

Written 2026-08-16 at the operator's request: a plan with dates, and a date at
which this can honestly be called a production-quality signal product.

Extends `TRADE_QUALITY_PLAN.md` §5, which already fixes four gate dates
(Aug 21, Sep 18, Oct 9, Oct 30). **This does not replace those dates.** It maps
the gaps found in `SIGNAL_GAP_ANALYSIS.md` onto them and adds the acceptance
criteria that were never written down.

---

## 1. What "production ready" has to mean, in numbers

A launch claim needs a bar that can be failed. These are the operator's own
criteria — signal quality, direction, entry and exit on the right trend, and how
much of the move is left on the table — each turned into something measurable.

| # | dimension | metric | today | source | bar to launch |
|---|---|---|---|---|---|
| 1 | **Direction** | entries reaching +10% on the option, vs a random minute | **18.4% vs 20.7%** | §6 | beats random, and holds on holdout |
| 2 | **Signal quality** | Information Coefficient — rank correlation of score to forward return, per session | **unmeasured**; the bucketed version is **inverted** | §5, [[setup-score-is-not-predictive]] | IC > 0 with a day-resampled CI excluding zero, at a horizon the app trades |
| 3 | **Capture** | share of MFE kept at exit | **29.5%** (n=18) | `trade_exit_analysis` | ≥ 50% at n ≥ 200 |
| 4 | **Give-back** | winners finishing at or below zero | **32%** (was 53%) | §6 | < 20% |
| 5 | **Loss control** | hard stop behaviour | **working** | §6 | unchanged |
| 6 | **Money** | return on capital deployed, honest fills | **−3.0%** | §0 | > 0, day-resampled CI excluding zero |

**Criterion 3 is barely measured.** `trade_exit_analysis` holds 21 rows spanning
2026-07-30 to 08-14, and they are outlier-contaminated — one short records
−1105% capture. The 29.5% figure is `avg captured 0.331 / avg MFE 1.121` over
18 rows, and it is an indication, not a measurement. It is consistent with
[[breakeven-trigger-protects-only-half-the-book]], which is why it is quoted at
all. **Populating this table is a Phase 1 deliverable**, not an assumption.

### The order these must be cleared in

**1 and 2 gate everything else.** Capturing more of a move entered in the wrong
direction is worth nothing, and tightening exits on a signal that is worse than
random makes the product worse faster. Work on 3 and 4 is real, but its *value*
is conditional on 1 clearing.

---

## 2. The one thing no plan can schedule

Every date below is a date for a **measurement or a decision**. None is a date
for a *result*.

The enclosing question — does this strategy have directional edge at all — is
open, and §5.0 records three closed doors around it. A plan that schedules
"achieve edge by October" is the same error §5.0a cost a day for: working inside
a system whose enclosing question is already answered no.

So what follows commits to running the experiments, and to calling the answer on
a fixed date either way.

---

## 3. The arithmetic that constrains every date

Live book, measured 2026-08-16: **42 trades over 13 active sessions ≈ 3.4 per
session.**

| target n | trading days | calendar | reaches |
|---|---|---|---|
| 100 | ~30 | ~6 weeks | end of September |
| 200 | ~59 | ~12 weeks | early November |
| 400 | ~118 | ~24 weeks | February 2027 |

Set against §2.2g, where detecting an edge large enough to pay the option toll
needed **t = 4.2** and 792 trades produced **t = −0.33**.

**Three consequences, and they shape everything below:**

1. **Replay discovers, the live book only confirms.** At 3.4 trades a session,
   no live window inside this plan can detect a small effect. Replay over the
   archive can, and the archive holds 14 sessions with bars and 17,069 snapshots
   carrying the full decision payload.
2. **A frozen forward window proves absence of disaster, not presence of edge.**
   Four weeks of live trading is ~68 trades. That detects a catastrophe. It does
   not establish a small positive edge.
3. **Raising the trade rate is not available as a fix.** It contradicts
   [[trade-quality-over-trade-count]], and §7.3g already measured the per-symbol
   cap as earning its keep at 2.

---

## 4. The plan

### Phase 1 — instrument, and run the one cheap information item · **Aug 17 – Aug 28**

Gate A in §5 is Aug 21; this runs slightly past it because Aug 17–21 is a live
trading week and §5.6 forbids config changes mid-measurement.

| # | work | why now |
|---|---|---|
| 1.1 | Backfill `trade_exit_analysis` across the replay archive | criterion 3 has 21 rows; it cannot gate anything at that size |
| 1.2 | Expected move vs implied move (§2.3) as a **measurement**, not a gate | data already in hand; `iv_richness.py` is the seed |
| 1.3 | Capture report by exit reason, on the backfilled table | tells us which exit rule leaves the most behind |
| 1.4 | **Information Coefficient, per session, with a horizon curve** (§3a.1, §3a.2) | the app grades its signal on 42 trades while holding 17,069 scored snapshots |
| 1.5 | **Score the signal on every emitted candidate, not only taken trades** (§3a.5) | today's statistics describe what passed every gate, which is survivorship |

1.4 and 1.5 are one piece of work in practice: IC is only worth computing
*because* it runs on the emitted population rather than the surviving one. The
forward-return machinery already exists in `tools/relative_strength_ab.py`, so
the cost is mostly joining scores to forward returns across the archive.

**Gate A criteria, Aug 28:**
- 1.1 done means ≥ 200 rows in `trade_exit_analysis`. If the replay cannot
  produce MFE per trade, that is itself the finding and 3 drops off the launch
  criteria until it can.
- 1.2 passes only if the share of trades whose realised move exceeds the implied
  move differs between quintiles **on holdout**. §5.14 warns specifically that
  `iv` was the least stable of six conditions tested — best quintile Q2 in
  discovery, Q1 in holdout. Expect this to fail.
- **1.4 has no pass or fail.** It is instrumentation, and its first reading is a
  baseline rather than a verdict. Record IC and its CI at every horizon and
  leave it. The expectation from §0 and §2.2h is that IC is at or below zero at
  the horizons the app trades; **if that is what it shows, the instrument is
  working, not failing.**
- 1.5 done means a per-session IC computed over emitted candidates, with the
  count of candidates and the count of taken trades reported beside it, so the
  gap between the two populations is visible on every future report.

**One honest note on this phase's scope.** Adding 1.4 and 1.5 widens Phase 1
past what Aug 17–28 comfortably holds, and Aug 17–21 is a live trading week
where §5.6 forbids config changes. If something has to slip, **1.2 slips**: it
is the item §5.14 already predicts will fail, while 1.4 and 1.5 change what
every later phase can be measured against. Gate B does not move.

### Phase 2 — the direction question · **Aug 31 – Sep 25**

This is the critical path. Everything else is conditional on it.

| # | work | pass criterion |
|---|---|---|
| 2.1 | Cross-sectional ranking of the 23 (§2.2) | beats random on direction, both halves |
| 2.2 | A mean-reversion setup family (§2.4) | beats random on direction, both halves |

Both judged by `tools/null_model.py` against a matched random entry, never
against zero (§5.6). Both need their own stop geometry before they can be
replayed, which is most of the cost of 2.2.

**Gate B, Sep 25** — aligned to §5's Sep 18 gate, extended one week for 2.2's
geometry work:

- **Either clears random on both halves** → it becomes the signal, and Phase 3
  builds on it.
- **Neither clears** → this is the third independent failure to find directional
  edge in the technical-only feature space. Phase 3 becomes the catalyst layer
  by elimination, and the launch date moves out, not the scope.

### Phase 3 — catalyst, or the instrument decision · **Sep 28 – Oct 23**

Which one runs depends on Gate B.

**If Gate B cleared:** build the news/catalyst layer (§2.5). Largest untouched
item on the operator's own requirement list, and the data is available on the
current Polygon plan.

**If Gate B did not clear:** §5.3's instrument decision runs instead. If four
independent attempts have failed to find directional edge in technicals, the
question stops being "which feature" and becomes "is a bought option the right
instrument for a signal this weak" — which §2.2e already bounds.

**Gate C, Oct 23:** does the composite signal beat random on direction, on
holdout, with a day-resampled CI excluding zero?

### 4a. Build timelines for the three untried information sources

Requested 2026-08-16. The dates differ by a lot, and the reason is not effort —
it is **whether the archive can already answer the question.** A feature testable
on 14 archived sessions is days of work. One that needs forward collection
cannot report until the collection window closes, however fast it is built.

Data status checked in the database on 2026-08-16, not assumed:

| | archive support | evidence |
|---|---|---|
| Cross-sectional ranking | **complete** | 17,069 snapshots, bars for all symbols, 14 sessions |
| Expected vs implied move | **1.7%** | `Option IV` on **408** rows of 23,689; `ATR %` on 21,463 |
| News | **none** | zero news keys in 322; no client method exists |

---

#### A. Cross-sectional ranking · build **Aug 17–26**, first read **Aug 28**

The cheapest of the three and the only one with no data dependency at all. The
forward-return machinery already exists in `tools/relative_strength_ab.py`.

| step | days |
|---|---|
| per-scan universe ranking (return vs the universe over a lookback) | 3 |
| join ranks to forward returns across the archive; IC by horizon | 2 |
| null-model comparison, both halves | 2 |

**Pass:** IC on the rank is positive with a day-resampled CI excluding zero, at a
horizon the app trades, **and** it beats a matched random entry on both halves.

This runs during a live trading week, which §5.6 permits: it is measurement
against the archive and changes no running config.

#### B. Expected move versus implied · biased read **Aug 26**, clean read **mid-October**

Split in two because the data is split in two. The *realised* side is well
populated; the *implied* side is not, and the reason matters.

`Option IV`, `Option Delta` and `Expected Option Profit %` each appear on
exactly **408** rows — they exist only where contract selection actually
succeeded. That is the 7.7% fill rate from
[[regime-escalation-is-not-the-trade-constraint]]. **So the 408 rows are
conditioned on the very funnel stage that is the app's known bottleneck**, and a
result from them is biased toward contracts that were cheap and tight enough to
be picked.

Option chains are **not** archived — `option_leg_replay` holds 29 rows and
`quote_attribution` is empty — so this cannot be backfilled from storage.

| route | work | first read | worth |
|---|---|---|---|
| **B1** measure on the 408 as they stand | 2 days | **Aug 26** | directional hint only; state the bias every time it is quoted |
| **B2** archive IV for every *examined* contract, not only the selected one | 3 days, ships **Sep 1** | n≈1,000 by **mid-Oct** | the honest test |

B2 ships after Gate A and after the live week. `CHAIN_EXAMINED` already counts
examined contracts, so the write path knows what it is skipping.

**Pass (B2 only):** the share of trades whose realised move exceeds the implied
move differs across quintiles **on holdout**. §5.14 rates `iv` the least stable
of six conditions tested — best quintile Q2 in discovery, Q1 in holdout — so the
prior here is failure, and B1 exists mainly to decide whether B2 is worth
three days.

#### C. News · feasibility **Aug 18**, build **Sep 28 – Oct 16**, first read **Oct 23 or mid-December**

Zero implementation and zero archived data. The date swings by six weeks on one
fact nobody has checked:

> **Does the current data plan serve *historical* news?**

That is a one-call check and it should happen on **Aug 18**, outside the market
window, before anything is designed. It decides the whole shape:

| answer | route | first read |
|---|---|---|
| historical available | backfill the same 14 sessions and measure like any other feature | **Oct 23** |
| live only | ship collection, then wait for the window to fill | **mid-December** |

| build step | days |
|---|---|
| provider client + storage table | 4 |
| **time-alignment: join on publication timestamp, never on trading day** | 3 |
| feature extraction (presence, recency, count, tone if available) | 5 |
| measurement harness | 3 |

**The correctness risk is the alignment, not the integration.** A headline joined
to the day it belongs to rather than the minute it was published is lookahead,
and it will manufacture an edge that does not exist. This is the same class of
error as the 0-DTE contamination in §7.3a, which produced a +758% trade the app
could never have bought. Build the alignment test before the feature.

**Pass:** candidates with a fresh catalyst beat those without, against a matched
random entry, on holdout.

---

#### What this ordering means

**A is first because it is free.** No new data, no new writes, no provider
dependency, and the measurement machinery exists. If cross-sectional ranking has
anything in it, that is known by **Aug 28** — the earliest any of the three can
report.

**B's honest version is gated on a write change**, so it cannot report properly
before mid-October whatever the effort.

**C is last and is the only one that can slip to December**, on a fact that costs
one API call to establish. Check it on Aug 18 so the December branch, if it is
real, is known in August rather than in October.

### Phase 4 — frozen forward validation · **Oct 26 – Nov 27**

Runs only if Gate C passed. **No config changes for the entire window** — §5.6,
and this is the phase whose only value is that nothing was touched.

Five trading weeks at ~3.4/session ≈ **85 trades**. State plainly what that
can and cannot show: it can detect a disaster or a large effect; it cannot
establish a small positive edge. That is a property of the trade rate, not of
the plan.

**Gate D, Nov 27:** criteria 1, 2, 4, 5, 6 from §1 all met, and criterion 3 met
if Phase 1 made it measurable.

---

## 5. The earliest honest launch date

**If every gate passes on schedule: early December 2026**, and that would be a
soft launch on five weeks of frozen live evidence, described as such.

A claim strong enough that "users look up to subscribing for the best signals"
needs criterion 6 — positive return on deployed capital with a CI excluding zero
— and at 3.4 trades a session that needs **n ≈ 200+**, which is **February
2027** on live data alone.

**Both dates assume every gate passes. On the evidence in §0, that is not the
most likely branch.** Three doors are already closed, the entry signal is worse
than random, and five experiments have now returned null. Any plan presenting
December as a likely outcome would be misrepresenting its own inputs.

### What would legitimately make this faster

- **A large effect.** All the arithmetic above is for detecting a *small* edge.
  Something that lifts the +10% rate from 18.4% to, say, 30% needs far fewer
  trades and would show in replay within days.
- **Accepting replay evidence at a higher weight.** Replay has 14 sessions and
  17k snapshots against the live book's 42 trades. It cannot see fills, but the
  fills are already honest. This is a judgement call for the operator, not a
  measurement.
- **Nothing else.** Trading more per session is ruled out; shortening the frozen
  window defeats its purpose.

---

## 6. The failure branch, stated in advance

§5.5 already names one. This adds the date and the wording.

If Gate C fails on **Oct 23**, the honest statement is that four independent
attempts failed to find directional edge in this feature space on this
instrument, and the product does not go to subscribers on that basis. The
options then are: change the instrument, change the horizon, or stop.

Recording this now is the point. It is much harder to call after another two
months of work has gone in.

---

## 7. What is not on this plan, and why

- **Calibrated probability output (§2.6).** Correctly last. Calibrating a signal
  that is worse than random reproduces it faithfully.
- **Contract tiering by delta (§7.4a).** Designed, deliberately not built. It
  serves subscriber segments, which matters only once there is a signal to serve.
- **Anything from `CHANGE_IMPACT_MAP.md` §8.** Every knob there has been
  measured. The app has run out of settings; what is left is information it does
  not yet receive.
