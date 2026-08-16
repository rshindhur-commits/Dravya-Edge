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
| 2 | **Signal quality** | forward return rising with the composite score | **inverted** | §5, [[setup-score-is-not-predictive]] | monotone, holdout-stable |
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

**Gate A criteria, Aug 28:**
- 1.1 done means ≥ 200 rows in `trade_exit_analysis`. If the replay cannot
  produce MFE per trade, that is itself the finding and 3 drops off the launch
  criteria until it can.
- 1.2 passes only if the share of trades whose realised move exceeds the implied
  move differs between quintiles **on holdout**. §5.14 warns specifically that
  `iv` was the least stable of six conditions tested — best quintile Q2 in
  discovery, Q1 in holdout. Expect this to fail.

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
