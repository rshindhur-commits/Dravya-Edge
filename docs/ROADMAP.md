# Roadmap to the target state

**Target state:** an algo trading signals app whose subscribers receive options
signals that are profitable.

Written 2026-08-09, after the 21-session measurement closed §2.2. Dates are
working assumptions; the gates are not. One phase in this plan cannot be
scheduled, and it is named as such rather than given an optimistic date.

---

## 1. The bar, in one number

Everything converted to basis points of underlying notional per trade, because
that is the only unit in which signal and cost are comparable.

| | bp/trade |
|---|---|
| option spread crossed twice, at `OPTION_MAX_SPREAD_PCT=2` | 3.4 |
| theta over a ~2 hour hold | 2.5 |
| **total cost** | **~6** |
| break-even captured move | **+0.155%** |
| product-grade (roughly 2x cost) | **+0.25%** |
| **measured today** | **−0.012% ± 0.037%** |

So the gap is **+0.17 to +0.27 percentage points of captured move per trade**.
Not a tuning gap. A different-signal gap.

### The finding that makes a schedule possible

792 trades over 21 sessions give a standard error of **0.037%** per trade. A
break-even edge of +0.155% would appear at **t = 4.2**.

**21 sessions is already enough statistical power to detect an edge worth
having.** The corollary matters more than the result: research does not have to
wait for 90 sessions. A hypothesis can be proposed on Monday and answered by
Wednesday against data already on disk, at zero API cost. That is what turns
this from an open-ended wait into a weekly loop.

What 21 sessions *cannot* do is confirm a small edge (SE 0.037% against a
hypothesised 0.05%). That distinction sets the gates below.

---

## 2. What is already strong — do not rebuild

Roughly 60k lines, and the plumbing is not the problem.

| area | state |
|---|---|
| capture and archival | 27 db modules, migrations applied, bloat fixed 489 -> 354 MB |
| replay fidelity | parity harness, no-lookahead scanner, forward replay with own contract selection |
| gates and audit trail | structured `RuleEvaluation`, activity trace, per-rule pass/fail persisted |
| exits | four momentum rules plus protective, measured as a class, switchable |
| risk geometry | now measured to its boundary, and the boundary is documented |
| tests | 988 passing, 134 files, and they have caught three of my own errors this month |
| offline research | `swing_anchor_geometry.py` — 21 sessions, 5,590 candidates, no API cost |

The last row did not exist a day ago and is the foundation of everything below.

---

## 3. What is missing

Four gaps. Only the first two block edge research.

### 3.1 No out-of-sample framework in code — **the most expensive gap**
`grep` for holdout, walk-forward, train/test across `app/` and `tools/` returns
**nothing**. Every holdout in this project has been applied by hand, in
conversation, by me. It killed three findings — which is the system working, but
it is not a system, it is a habit, and habits do not survive a context window.

Roughly ten arms have now been run against the same 21 sessions. Without an
enforced split and a recorded count of comparisons, the eleventh finding is
uninterpretable no matter how good it looks.

**Needed:** a sessions split fixed once and stored, arms declared before they
run, a recorded comparison count, and a summary that refuses to report a result
that has not been confirmed out of sample.

### 3.2 No feature research dataset — **the highest-leverage build**
The geometry rows store six fields: symbol, day, moment, entry type, price,
direction. No indicator values at decision time. So testing "does relative
strength predict the move" means a 45-minute re-walk instead of a 5-second
regression on a dataframe.

**Needed:** one row per candidate carrying the full indicator snapshot at
decision time (the ~45 columns already computed) plus the forward outcome
already computed by the walk. Build it once, and a hypothesis costs seconds.

This is what makes the weekly loop in §4 possible. Without it the loop is
monthly and the roadmap roughly triples.

### 3.3 No circuit breaker — **blocks subscriber return, not research**
Nothing stops alerts going out if live performance degrades. During 5–7 Aug
alerts kept flowing while the database was down, because Telegram needs no
database. The entry-persistence guard fixed that specific path; the general one
is open.

**Needed:** a rule that halts alerting on a measured condition — consecutive
losses, capture failure, or performance outside the replayed envelope — plus a
manual kill switch that does not require a deploy.

### 3.4 No signal versioning
Live results cannot be attributed to a signal version. Once rules start changing
again this makes every performance question ambiguous.

**Needed:** a version stamp on every emitted signal, persisted with the trade.

---

## 4. The plan

### Phase 0 — Capture and harness · Mon 11 Aug → Fri 15 Aug
No trading changes. Nothing deployed to production behaviour.

| | work | done when |
|---|---|---|
| 1 | Item 7 verification: `rss_mb` series, capture completeness, spread config reached the worker | Mon 11 Aug, first full session after the fixes |
| 2 | Build the feature research dataset (§3.2) | Wed 13 Aug |
| 3 | Build the out-of-sample framework (§3.1) | Fri 15 Aug |
| 4 | Fix duplicate close alerts (display-only, long deferred) | Fri 15 Aug |

### Phase 1 — Edge research · Mon 18 Aug → Fri 4 Sep
Three weekly cycles. Each hypothesis: state it, test on the train split, confirm
on holdout, record the comparison count. Cost per cycle is compute, not quota.

The current feature set is ~45 classical price/volume indicators on 5m/15m/1h
across 26 megacaps — the most heavily arbitraged information set that exists.
That the measured edge is indistinguishable from zero is what theory predicts.
So the cycles should test information the current signal does not use at all:

| cycle | hypothesis family | why it is not already in |
|---|---|---|
| 18–22 Aug | **cross-sectional** — rank the universe by relative strength and trade only the extremes | every rule today is single-symbol; the universe is never compared against itself |
| 25–29 Aug | **regime conditioning** — condition on SMH/XLK/XLF/XLE/VIXY, already fetched and barely used | `market_regime` is derived from the symbol's own bars, not from the market |
| 1–4 Sep | **event exclusion and time-of-day** — earnings proximity, and the hour of entry as a measured factor rather than a fixed window | neither is currently a factor at all |

These are candidates, not commitments. The dataset from Phase 0 makes each one
cheap enough that being wrong three times is affordable.

### Gate 1 — Mon 7 Sep
**Question:** does any candidate reach **≥ +0.155% captured move per trade on
the holdout split**, with the comparison count recorded?

* **Pass** → Phase 2.
* **Fail** → stop research. The honest options then are: change the instrument
  (the edge required for shares is 2bp, not 6bp), change the universe (26
  megacaps is the hardest possible pond), or stop. That is a decision for you,
  and it should be taken on the evidence rather than by continuing to spend.

**Stopping rule, agreed in advance:** three failed cycles is information, not
bad luck. It is not a reason for a fourth on the same feature set.

### Phase 2 — Priced validation · Mon 7 Sep → Fri 18 Sep *(only if Gate 1 passes)*
Replay the winning configuration with **real option quotes** — the first step in
this plan that consumes Polygon quota, roughly 1–2.5 hours of calls. Confirms
the underlying edge survives real contracts, real spreads and real theta rather
than the fitted model.

### Phase 3 — Paper forward · Mon 21 Sep → Fri 16 Oct
About 20 live sessions, no subscribers, circuit breaker armed, signal version
stamped. This is the only test that cannot be replayed: it catches the gap
between what the harness believes and what the worker does.

### Gate 2 — Mon 19 Oct
**Question:** does live paper match the replayed envelope, and is capture
complete for every session?

### Phase 4 — Subscriber return · from Mon 19 Oct
Requires all of: Gate 2 passed, circuit breaker live, weekly performance report
built from database truth rather than from alert history, and the known
display-only defects closed.

---

## 5. What this schedule is honest about

**Phases 0, 2, 3 and 4 are schedulable. Phase 1 is not.** Edge discovery has no
guaranteed endpoint, and no amount of engineering converts a signal with no edge
into one that has it. The dates above describe how quickly the question gets
*answered*, not that the answer is yes.

If Gate 1 passes, subscriber return is roughly **ten weeks out (mid-October)**.
If it fails, the useful outcome is a decision taken in early September on
measured evidence, rather than another quarter spent tuning inside a boundary
that has already been measured.

Nothing in Phases 0 or 1 changes production behaviour, and nothing before
Phase 2 costs API quota.
