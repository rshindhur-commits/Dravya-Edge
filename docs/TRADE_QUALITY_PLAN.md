# Trade quality: what is wrong, what to do, and how we will know

Written 2026-08-08, after five parameter changes failed to improve the money.
Grounded in 601 archived replay trades (`data/forward_runs/phase1_21day_cap1200.json`
and `phase1_21day_20260803_202603.json` pooled, 21 sessions).

Subscribers are off the channel. Nothing here needs to ship in a hurry; it needs
to be right.

---

## 0. The frame: what the numbers actually say

**The book loses ~3% of whatever is deployed, at a rate that does not change
with configuration.**

| | trades | deployed | total | return |
|---|---|---|---|---|
| cost cap $500 | 291 | $115,377 | −$3,466 | **−3.00%** |
| cost cap $1,200 | 310 | $244,548 | −$7,399 | **−3.03%** |

That is why every knob so far has failed. Cost cap, breakeven trigger, leverage
weight, min RR and the sub-floor stop rejection all change *how much* is
deployed or *how many* trades are taken. None changes the rate.

**The payoff structure requires a win rate the strategy does not have.**

| outcome | mean premium |
|---|---|
| reaches the 2R target | **+17.0%** |
| reaches +0.5R or better | +9.7% |
| stopped out | **−7.6%** |

Break-even needs roughly 30% of trades at target. **23 of 601 (3.8%) get there.**

**The entire loss is in the exit mix.**

| exit rule | n | mean R | mean premium | share of total loss |
|---|---|---|---|---|
| HARD_TARGET | 38 | +1.91 | +16.97% | — |
| FAILED_BREAKOUT | 33 | +0.72 | +3.76% | — |
| FORCE_EOD_EXIT | 34 | +0.43 | −2.55% | 5% |
| TIME_EXIT | 55 | +0.17 | −2.69% | 8% |
| MACD | 177 | +0.07 | −3.03% | 28% |
| **EMA** | **151** | −0.42 | **−6.81%** | **55%** |
| VWAP | 38 | −0.31 | −7.42% | 15% |
| HARD_STOP | 75 | −0.52 | −7.62% | 30% |

Weighted, this reproduces the −3.13% headline exactly. **The EMA exit fires
twice as often as the hard stop and costs nearly as much per trade.** It is
functioning as an early second stop, not as protection.

**And the book is two different broken halves.**

| | n | mean premium | win% |
|---|---|---|---|
| never travels past 0.10R | 287 (48%) | **−7.05%** | 2% |
| does travel | 314 (52%) | +0.45% | 35% |

The half that moves peaks at 0.80R on average and keeps +0.45%. The half that
never moves is where the money goes, and **nothing observable at entry predicts
membership** — tested with a first-half/second-half holdout.

---

## 1. Short term — provable on data already on disk

No new trades needed. Each is a replay A/B over the existing 21 sessions,
judged by section 3.

### 1.1 Revert the contract cost cap to $500 — **proven, do it now**
Measured on 21 shared days: −$3,466 against −$7,399, about **$187 a session**,
at an identical loss rate. It does not fix anything; it halves the stake on a
book that loses 3% of its stake. This was my recommendation to raise and it was
wrong.

### 1.2 The EMA exit — **RUN 2026-08-08, no effect**

Two arms over the same 21 sessions, against the cap-$500 baseline:

| | baseline | confirm 1 bar | disabled |
|---|---|---|---|
| return on capital | −3.00% | −3.23% | −3.24% |
| per trade | −$11.9 | −$12.8 | −$12.8 |
| vs baseline | — | **−0.40sd** | **−0.38sd** |

Both inside noise — about 1.4 points of premium are needed to clear 2sd at this
sample size, and the difference is 0.27. The point estimates are slightly
negative, which matches the prediction recorded before the run: disabling it
would probably be worse.

**The mechanism is redistribution, not removal.** EMA exits went 75 → 12 → 0
while MACD went 88 → 122 → 129 and VWAP 16 → 38 → 37. Take the rule away and
another fires at nearly the same bar. A one-day smoke test had already shown
this — two of three EMA exits were matched by MACD at an identical price.

**This retracts the framing in section 0.** "The EMA exit is 55% of all losses"
is true as accounting and false as cause. Those losses occur regardless; the
rule that gets named changes. Any claim that a single exit rule is responsible
for the book's losses should be read the same way until tested.

### 1.3 The MACD exit — **superseded**
Was to be tested the same way. There is no point: 1.2 shows EMA, MACD and VWAP
are substitutes, so removing one redistributes to the others and the result will
be another null. The meaningful experiment is the class, not the member —
see 1.6.

### 1.6 Momentum exits as a class — **RUN 2026-08-08, no effect, and it settles the exits**

EMA, MACD, VWAP and failed-breakout removed together, against a fresh control
that reproduces the archived baseline exactly (291 trades, −$3,466, −3.00%):

| | control | no momentum |
|---|---|---|
| return on capital | −3.00% | −3.37% |
| trades | 291 | 164 |
| vs control | — | **−0.37sd** |

Null, like the other two. Total P&L improved by $1,197 purely because 42% less
capital was deployed; per trade it is worse.

**What it settles is more useful than the headline.** Splitting by whether a
trade ever travelled:

| | moved | never moved |
|---|---|---|
| control | n=145, **+1.15%** | n=146, **−7.41%** |
| no momentum | n=106, **+1.27%** | n=58, **−12.31%** |

The winners are untouched — +1.15% against +1.27% is nothing. The losers are far
worse: without momentum exits a dead trade runs to its hard stop and loses 12.3%
instead of 7.4%.

**So momentum exits are loss-limiters, not profit-takers.** They are not cutting
winners short; they are the only thing currently containing the half of the book
that never works. The original hypothesis — that the exits eat the profits — is
refuted from both directions: individually (substitution, §1.2) and as a class.

**The exits are not the problem, and §2.4 is answered without running it.** Any
remaining exit tuning is bounded by §1a and now also by this: the winners' +1.15%
is what the exits already deliver, and no exit rule can improve a trade that
never moved. What is left is entry selection, and entry-time features have
already failed to predict the never-moves group.

### 1.4a Spread ceiling 6% -> 3% — **RUN 2026-08-08, the first thing that works**

| | ceiling 6 | ceiling 3 |
|---|---|---|
| return on capital | −3.00% | **−2.29%** |
| per trade | −$11.9 | −$8.3 |
| cash win rate | 20% | 24% |
| trades | 291 | 260 |
| mean spread paid | 3.44% | **2.29%** |

R and dollars agree, and it is the only change tested that improves return on
capital rather than merely deploying less.

**Holdout:** positive in both halves — +1.10 points in the first, +0.42 in the
second. It shrinks, which is what a partly-overfit effect does, but it does not
change sign. Three findings died at this gate on the same day; this one did not.

**The statistics alone would not carry it.** The full-sample effect is +1.05sd,
below the 2sd bar in section 3. What carries it is the mechanism:

* the control pays a mean spread of **3.44%**, which independently reproduces
  the −3.40 intercept fitted in section 1a from 601 different trades. The toll
  is the spread, established twice by unrelated methods.
* tightening the ceiling cuts the measured toll by 1.16 points.
* the book improves by 0.75 of those 1.16, the shortfall being winners the
  tighter ceiling also excluded.

Cause, size and direction all line up. That is worth more here than a t-statistic
on 550 trades.

**It does not make the strategy profitable.** −2.29% is less bleeding, not a
gain, and trade shape is untouched: 48% still never travel, mean peak is still
0.39R. This buys back a quarter of the toll and nothing else.

### 1.4b Ceiling 2% — **RUN 2026-08-09, and it bounds the whole approach**

| | 6% | 3% | **2%** |
|---|---|---|---|
| return on capital | −3.00% | −2.29% | **−1.12%** |
| per trade | −$11.9 | −$8.3 | **−$3.4** |
| mean R | +0.007 | +0.009 | **+0.048** |
| trades | 291 | 260 | 193 |
| mean spread paid | 3.44% | 2.29% | **1.48%** |

**+1.85 points, +2.33sd** on the full sample — the only result all weekend to
clear the 2sd bar in section 3. Positive in both halves (+2.89 discovery, +0.86
holdout; the decay is real and worth watching). Toll cut 1.96 points, book
improved 1.85 — the mechanism accounts for essentially all of it.

**The section 1a model predicted all three arms before they ran:**

| ceiling | 8.59 × R − toll | measured |
|---|---|---|
| 6% | −3.38% | −3.15% |
| 3% | −2.21% | −2.40% |
| 2% | −1.07% | −1.30% |

A model fitted on 601 unrelated trades forecasting three interventions to within
0.25 points is the strongest evidence produced here. It also means the model can
now be trusted to answer the question the experiments were circling.

### 1.4c What the model says about the ceiling of this lever

Set the toll to **zero** — better than any ceiling can achieve — and the arm at
2% returns:

```
8.59 x 0.048 - 0 = +0.41%
```

**+0.41% is the theoretical maximum of all transaction-cost work.** Not the
practical maximum; the arithmetic one, with fees abolished. Two thirds of the
available reduction has already been taken, and what remains is worth about 1.5
points at best.

Combined with section 1a's other bound — perfect exits plus perfect loss-cutting
reach +0.20% — **every lever except one is now measured and bounded below
break-even.**

The exception is mean R itself. At the current 0.048 nothing works. At 0.40,
with today's toll, the same book returns +1.96%. That is the only remaining
question worth asking, and it is section 2.2.

### 1.4 Measure the entry spread distribution, then test a ceiling
Not yet measured. Live payloads carry `option_entry_spread_pct` (SMCI on 08-05
showed 5.43% at entry, 13.3% at 15:55). If a meaningful share of trades enter
through spreads above ~5%, a ceiling attacks the toll directly rather than the
stake.

### 1.5 Raise `MIN_STOP_SPREAD_MULTIPLE` above 1.0
Currently 1.0, meaning the stop distance need only equal one round-trip spread.
At that ratio a trade must travel its full stop distance simply to break even.
This is the ratio that sets the required win rate, and it has never been A/B'd.

**Explicitly not doing:** further entry-threshold tuning. Setup score, RR floor,
regime gates and rank cutoffs have all been tested or shown inert. The evidence
says the entry gate is not where this is lost.

---

## 1a. The equation everything else is judged against

Fitting realised premium against realised R over all 601 trades:

```
premium % = 8.59 x R - 3.40          R2 = 0.80
```

Three readings, all of them consequential:

* **The round trip costs 3.40 points** before the underlying moves at all.
* **1R is worth 8.6 points** of premium.
* **A trade must reach +0.40R just to pay for itself.**

Against that, the two halves of the book: the 287 that never travel realise
−0.403R, and the 314 that do peak at 0.803R and keep +0.428R — 53% of peak, and
barely past the 0.40R break-even.

**This bounds every improvement in section 2.** Substituting into the equation:

| scenario | book return |
|---|---|
| today | −3.13% |
| exits capture 100% of peak instead of 53% | −1.45% |
| that, **and** every loser cut at exactly breakeven | **+0.20%** |

Perfect exit timing and perfect loss-cutting — both unattainable — reach
roughly zero. **No amount of exit or stop work makes this profitable**, because
the 3.40-point toll is charged 601 times regardless. Only two things move that
number: paying a smaller toll, or making the moves big enough that the toll
stops mattering.

---

## 2. Long term — needs more sessions and cleaner capture

*Reordered after the equation above. The original order put the largest
prize first; the equation says the largest prize is capped below breakeven.*

### 2.1 Cut the 3.40-point toll — **now the first priority**
It is charged on every trade, it is the largest single number in the system, and
it has never been attacked. Three routes, in order of directness:

* **Spread ceiling at entry.** Live payloads carry `option_entry_spread_pct`;
  SMCI on 08-05 entered at 5.43%. Refusing entries above ~3% trades volume for
  toll and is testable on the archive today.
* **Fewer round trips.** Every entry pays 3.40 points whatever it does. 601
  trades in 21 sessions is ~29 a day; each one is a toll payment.
* **More liquid contracts.** Related to the cost cap but not the same lever:
  cheaper is not necessarily tighter, and it is the spread that is charged.

### 2.2 Can this strategy produce larger-R trades at all? — **the existential one**
At 8.59 points per R, a book averaging 0.4R cannot clear a 3.40-point toll, but
one averaging 1.5R clears it comfortably. 38 trades did reach the target and
paid +16.97%. The question is whether the strategy can be reconfigured to
produce more of those rather than 601 small ones — different holding profile,
different setup, or a different instrument.

If the answer is no, no further tuning is warranted, and that is a finding
worth having early rather than after another quarter.

#### 2.2a Why it cannot, as built — **the anchor, not any knob**
The blocker is one line. `EMA_PULLBACK`, 183 of 310 archived trades, sets its
stop to `min(bar low − atr*0.15, EMA9 − atr*0.10)` on the **15m** frame. For a
liquid megacap that bar's own low sits a fraction of a percent from price, so
the stop lands at 0.5–0.75%, a 2R target at ~1.5%, and mean peak is 0.39R ≈
0.3% of price — inside the ordinary noise band of the names traded. The option
round trip costs 1.5–3.4%. The strategy is structurally unable to reach a move
that pays for the instrument expressing it.

Three things that looked like the constraint and are not:

| suspected | measured |
|---|---|
| `max_stop_distance_pct` cap | raising it alone changes nothing — nothing produces stops that wide |
| `stop_atr_multiplier` | `EMA_PULLBACK` never reads it |
| the ATR offset | 4× offset buys a **1.53×** stop; the bar's low dominates and does not scale. 3% would need ~14× |

Pinned in `tests/test_distance_scale.py`.

#### 2.2b The higher-timeframe anchor — **built 2026-08-09**
`app/risk/swing_anchor.py`, behind `SWING_STRUCTURE_ENABLED` (default off).
Anchors the stop to a swing pivot on the **1h** frame, which sits 1–4% away
because that is what a swing pivot is. Scanner, gates, entry rules and contract
selection are untouched.

Four things move together or the arm silently runs the control — the failure
that made three earlier arms byte-identical to their baselines:

* the anchor (1h pivot ± `SWING_STOP_ATR_BUFFER` × 1h ATR)
* `SWING_MIN_STOP_DISTANCE_PCT` 1.0 replaces the 0.50 floor
* `SWING_MAX_STOP_DISTANCE_PCT` 4.0 replaces the 0.75–1.15 ceiling
* `EXIT_MOMENTUM_ENABLED=false` — otherwise a 3% stop is decided by a
  nine-period EMA within minutes and the wider anchor is paid for and never used

The first three are applied together by `calculate_risk` so they cannot be
half-set; the fourth is named in `swing_anchor.describe_mode()` so a run that
forgot it is visible in its own output. A missing or too-short 1h frame
**rejects the trade** rather than falling back to the intraday anchor, because a
silent fallback blends treatment and control inside one arm.

**Known side effect, and it is a loss of filtering.** Reward is a fixed multiple
of risk, so RR is constant at `SWING_TARGET_RR` and the 1.5 floor can never
fire. On a 4-day smoke test that took candidates clearing the gates from 40 to
73 of 83. More trades at equal R is not an improvement. `SWING_HEADROOM_MULTIPLE`
(default 0, off) restores filtering in the term that matters at this timeframe —
whether there is room to the next 1h level to reach the target at all — and is
kept separable so the two effects do not arrive mixed together.

#### 2.2c Measuring it without spending quota
`tools/swing_anchor_geometry.py` answers this from **cached underlying bars, no
option quotes, no network**. Each candidate is walked forward bar by bar on real
5m data to first touch of stop or target; a bar containing both scores as the
stop, since intrabar order is unknowable at 5m and assuming otherwise
manufactures exactly the edge being looked for. One position per symbol at a
time, tracked per arm, because the control exits in minutes and can re-enter
while the swing arm cannot — that difference in trade count is half of what
return on capital measures. R is then converted with the §1a model.

Limitation, stated because it favours the treatment: the walk uses pure
stop/target and bypasses the exit engine, so it does not give the control its
measured loss-limiting from momentum exits. Compare the swing arm against the
**actually measured** control (−1.12% at ceiling 2), not against this run's
control column.

#### 2.2d RUN 2026-08-09, 21 sessions, 5,590 candidates — **the anchor works and it is not enough**

| | control | swing |
|---|---|---|
| median stop | 0.54% | **2.27%** |
| trades taken | 796 | 342 |
| win rate | 32% | **43%** |
| mean R | −0.025 | −0.007 |
| **underlying move captured** | **−0.0123%** | **+0.0436%** |
| median hold | 13 bars | 188 bars (~2.4 sessions) |
| premium from delta | −0.15% | **+0.55%** |
| theta over the hold | −0.83% | **−5.07%** |
| **at ceiling 2** | −1.27% before theta | **−0.57% before theta, −5.64% after** |

**The anchor does what it was built to do.** Stops move 0.54% → 2.27%, win rate
32% → 43%, and the underlying move captured flips sign, −0.0123% → +0.0436% of
price. Before carrying cost the book improves by 0.70 points, from −1.27% to
−0.57%. That is the largest single improvement any lever has produced.

**And it loses to theta by seven to one.** Collecting that 0.70 points requires
holding 2.1 sessions, which costs 5.07% of premium in decay. Net −5.64%, against
the measured −1.12% the app books today. *Widening the stop makes the options
book worse*, and it does so through the one cost the §1a model never contained,
because that model was fitted on a book that held for two hours.

46% of swing trades (151 of 331) exited on time rather than at stop or target:
the target is too far to be reached inside three sessions, so they pay full
decay for an unresolved position.

**A correction worth recording.** The first version of this study reported
−1.18% for the swing arm by applying `premium% = 8.59 × R − toll` directly to
its R. That slope was fitted where stops averaged 0.68%; R is the move *divided
by* the stop, so it cannot be carried to an arm whose stops average 2.5%. Every
arm must be converted through the underlying move it actually captured. The
model is now stated as **12.6 premium points per 1% of underlying** — which
cross-checks against a ~50 delta contract costing ~3% of notional (16.7 points)
and, with theta excluded, reproduces the measured control to within 0.15 points.

#### 2.2e What this closes
§2.2 is answered: **no.** Three independent bounds sit below break-even — spread
ceiling +0.41% at zero toll, perfect exits +0.20%, and larger moves −5.64%.
Larger moves is not merely bounded, it is *negative*, and it was the last lever
with a ceiling above zero. No further tuning of this as an options strategy is
warranted.

#### 2.2f RUN 2026-08-09, hold sweep — **and the +14.43% does not survive it**

The obvious follow-up: the anchor creates edge but needs 2.1 sessions to collect
it, while the intraday version holds two hours and has none. Is there a hold in
between where the edge has arrived and the decay has not? Five arms, same 21
sessions, each cap its own arm with its own position lock.

| hold | trades | captured/trade | t |
|---|---|---|---|
| control (13 bars) | 792 | −0.0123% | −0.33 |
| 20 bars | 1026 | +0.0144% | +0.46 |
| 39 bars | 718 | −0.0538% | −1.15 |
| 78 bars | 468 | −0.0713% | −0.86 |
| 156 bars | 438 | −0.1332% | −1.16 |
| 234 bars | 331 | +0.0436% | +0.28 |

**The sign flips positive → negative → positive across adjacent hold caps on the
same data.** A real effect varies smoothly with hold length. |t| < 1.2 on every
arm; none is distinguishable from zero.

And the number this document reported an hour earlier as the project's first
positive result:

```
swing_h234:  mean +0.0436%   95% CI [-0.2709, +0.3420]
             without its top 5 trades (of 331):  -0.0735%
             median trade: -0.75% of price
```

Five trades of 331 carry 266% of the total. The median trade loses 0.75% of
price. **The claim that the signal works and only the instrument is wrong was
built on the mean of a lottery-ticket distribution and is withdrawn.** The check
that would have caught it — bootstrap the mean, and re-read it without the top
few trades — costs seconds and now runs beside every arm.

#### 2.2g The one thing that does survive, and it is worth having
For the intraday control, 792 trades give SE = 0.037% per trade. An edge large
enough to break even at spread ceiling 2 (+0.155%) would appear as **t = 4.2**.
Observed: **−0.33**.

So an edge sufficient to pay for options is *ruled out* for the strategy as it
runs today — a real conclusion, not an absence of one, and the one that stops
money being spent looking for it.

For the swing arm nothing is ruled out either way: SE 0.157% against a 0.492%
requirement. Several hundred more sessions would be needed to say anything, and
the theta arithmetic in §2.2d says it loses even if the edge is real.

### 2.2h The null model, 2026-08-09 — **the entry timing is worse than random**

Every number this project produced before today was compared against zero. Zero
is the wrong benchmark. The right one holds constant everything except the thing
under test: same symbol, same session, same direction, same horizon, **random
entry minute** inside the entry window.

Edge over random, in percentage points of underlying move:

| horizon | train | holdout | random draws beating the signal |
|---|---|---|---|
| 12 bars (1h) | −0.116 | −0.175 | 20/20 both halves |
| 78 bars (1 session) | −0.297 | −0.308 | 20/20 both halves |
| 234 bars (3 sessions) | −0.247 | −0.263 | 20/20 both halves |

Draw-to-draw sd is 0.012–0.036, so the gap is roughly 8–20 standard deviations,
and train and holdout agree to within 0.06 points at every horizon. Candidates
sit slightly *earlier* in the session than uniform (168 min vs 187), so
overnight-gap exposure cannot account for it — and would push the other way.

**The entry rules are not uninformative. They are actively costly**, giving up
about a quarter of a percentage point relative to firing at a random minute.
That is consistent with what the five setups are: EMA_PULLBACK, BREAKOUT and
their mirrors all trigger *after* a move, buying strength and selling weakness at
horizons where liquid megacaps mean-revert.

**Read the difference, never the levels.** The random arm reuses the symbol and
direction the scanner chose later in the session, so its absolute return is
lookahead-contaminated and is not achievable by anyone. The contamination is
identical in both arms and cancels in the difference; `tools/null_model.py` now
says so in its own output.

This reframes Phase 1. The question is no longer only "can a new feature add
edge" but "does removing the entry trigger add edge" — the cheapest experiment
available, and one the harness can already answer.

### 2.3 Is the signal late?
Your original diagnosis, still untested: entries arriving after the move has
run. Needs entry timestamps compared against the bar sequence that triggered
them. Now feasible: the exit-timestamp defect is fixed, so alert and database
times agree.

### 2.4 Does any setup have edge once the exits are fixed?
EMA_PULLBACK (183) and EMA_REJECTION (123) are indistinguishable today, both at
−24/trade. That comparison is meaningless while 83% of losses come from two exit
rules applied to both. Re-run it after section 1.

### 2.5 A predictor for the 48% that never move — **deprioritised**
Was first on this list. The equation demotes it: even cutting every one of those
trades at exactly breakeven, with perfect exits on everything else, reaches
+0.20%. It is a large prize with a ceiling barely above zero, and one holdout
test has already failed to find a predictor among entry-time features. Worth
returning to only if 2.1 and 2.2 succeed and it becomes the binding constraint.

---

## 3. How every change is judged

**Primary metric: return on capital deployed.** Not total R, not trade count,
not win rate. R is blind to the spread crossed twice, which is where this book
bleeds; trade count and R have both already pointed the wrong way on real
decisions.

**Every change passes four gates before it ships:**

1. **Replay first, live never.** Tested against the archive. Live trades cost
   ~$24 each to learn from and the archive is free.
2. **Holdout.** Candidate chosen on the first half of sessions, judged on the
   second. A slice that cannot survive days it was not chosen from is a story
   about the past. This has already killed three findings, including one I was
   about to recommend.
3. **Beat its own noise.** The improvement must exceed the standard error of the
   difference. At ~600 trades the SE of mean premium is ~0.6 points, so an
   improvement under ~1.2 points is not distinguishable from luck.
4. **Same days both arms.** Runs that cover different sessions are not
   comparable; `tools/compare_runs.py` intersects shared days by default because
   an earlier arm looked $1,889 better purely on which days it finished.

**Reported per session** by `tools/daily_report.py`:
- capture completeness first — a session that did not fully record is not
  evidence, and 08-05/06/07 all looked like quiet days
- return on capital, beside R, never instead of it
- the never-moved share

**Standing rule:** no rule change goes live on a live-trade sample. The repo's
own bar is `evidence_days >= 20 and completed_trades >= 80`; the archive clears
it today, live trading will not for months.

---

## 4. Order of work — **superseded 2026-08-13 by section 5**

Kept as written. Items 1 and 2 were done; item 3 was answered *no* by §2.2e; items
4, 5 and 8 were closed by §1.2 and §1.6. What this table could not know is
§2.2h, which arrived after it and moves the whole question upstream of every row
here. Read section 5 for what is actually being worked on.

| | what | needs | ceiling |
|---|---|---|---|
| 1 | Revert cost cap to $500 | nothing — proven | halves the stake |
| 2 | **Entry spread ceiling** — measure, then A/B | replay | attacks the 3.40 toll |
| 3 | **Can it produce larger-R trades?** | replay | decides whether any of this is worth doing |
| 4 | EMA exit A/B (running) | replay, ~2h | bounded by 1a |
| 5 | MACD exit A/B | replay, ~2h | bounded by 1a |
| 6 | `MIN_STOP_SPREAD_MULTIPLE` A/B | replay | bounded by 1a |
| 7 | Signal latency | more sessions | unknown |
| 8 | Re-test setups with winning exit config | replay | bounded by 1a |
| 9 | Never-moves predictor | new features, more sessions | +0.20% at perfect play |

Items 4, 5, 6 and 8 are all capped below breakeven by section 1a. They are worth
doing because they are cheap and because the ceiling assumes the toll stays at
3.40 — but **nothing in that group can make this profitable on its own.** Items
2 and 3 are the only ones that can, which is why they moved to the top.

Subscribers return when a configuration shows positive return on capital across
a holdout, and a month of sessions captures cleanly. Not before.

---

## 5. The plan to 2026-10-30

Written 2026-08-13. The purpose of this section is to fix, in advance, what will
be worked on, when it will be judged, and what each answer means — so that no
single day's trades can change the subject. It runs to end of October and then a
decision is made either way.

### 5.0 Where the project actually stands

Three doors are closed, each by a measurement in this document:

| | finding | where |
|---|---|---|
| entry timing is **worse than random** | −0.12 to −0.31 points; 20/20 random draws beat it in *both* halves; 8–20 sd | §2.2h |
| an edge big enough to pay the option toll is **ruled out** | needs t = 4.2, observed **t = −0.33** on 792 trades | §2.2g |
| every remaining lever is **bounded below break-even** | +0.41% at zero toll, +0.20% at perfect exits, −5.64% for larger moves | §2.2e |

One door is open, and this document named it itself:

> *"The question is no longer only 'can a new feature add edge' but 'does
> removing the entry trigger add edge' — the cheapest experiment available, and
> one the harness can already answer."* — §2.2h

That experiment has never been run. It is the whole of Phase A.

### 5.0a A correction, recorded because it cost a day

On 2026-08-13 I recommended a boundary A/B — `ATR_DISTANCE_SCALE` and
`MAX_STOP_DISTANCE_SCALE` at 4 — describing it as "the only untested change with
a ceiling above break-even." **That was stale on both halves.**

* `tests/test_distance_scale.py::test_the_scale_is_a_weak_lever_because_the_bar_dominates`
  pins a 4× offset at a **1.53× stop**, because the 15m bar's own low dominates
  and does not scale. Reaching a 3% stop needs a scale near fourteen.
* §2.2d already ran the *strong* version of that idea — the 1h swing anchor,
  median stop **2.27%** — and it lost to theta seven to one.

Two arms were launched on it and were killed unrun. Four more switches were
committed the same day (entry timing gate, target floor, extension cap, spread
ceiling 3), all off, none measured, none of which touch §2.2e. **That day is the
failure mode this section exists to prevent:** tuning inside a system whose
enclosing question is already answered *no*.

### 5.1 Phase A — clear the board, run the two free experiments · **Aug 14–21**

*Config already proven and currently mis-set — restore first, no measurement needed:*

| setting | now | correct | authority |
|---|---|---|---|
| `OPTION_MAX_SPREAD_PCT` | 3 | **2** | §1.4b, +2.33sd, holdout positive |

`OPTION_MAX_CONTRACT_COST` was checked on 2026-08-13 and is **already 500**;
§1.1 was applied on 2026-08-09 and needs nothing. An earlier draft of this
section listed it as still at 1200, which was wrong.

*Instrument repairs — nothing downstream is trustworthy until these land:*

1. **Force-close orphaned intraday positions.** The only item that can lose real
   money while unfixed; it already cost ~$200 (SMCI, 9 sessions unmanaged).
2. **Regression evaluator must call `evaluate_exit`.** It reported +3.22R on a
   day the app booked −0.65R. Fix or retire — a lying instrument is worse than none.
3. **Option pricing on every closed trade.** 19 of 37 carry cash today, so
   §3's primary metric is unmeasurable on the live book.

*The two experiments, both on cached bars, both free:*

- **A-i · no entry trigger.** Same universe, same exits, entry at a random
  qualifying minute. §2.2h predicts this *gains* ~0.25 points.
- **A-ii · inverted trigger.** Fade instead of follow. §2.2h's mechanism — the
  setups buy strength at horizons where liquid megacaps mean-revert — predicts
  this is positive.

**A dependency found 2026-08-13, after this section was written.** Neither
experiment can run as described until `avoid_chasing` has a switch.
`entry_engine.py:190-208` refuses any candidate more than **1.2% from its EMA9**
or **1.5% from VWAP**, and `risk_manager.py:891` turns that into an outright
refusal. So "no entry trigger" is not reachable by removing the setup detectors —
this block sits behind them and would still veto every entry into a move that had
already started. Both thresholds are hardcoded. See §1a of
[CHANGE_IMPACT_MAP.md](CHANGE_IMPACT_MAP.md).

Adding the switch is the first task of Phase A, ahead of the experiments. It is
also, on its own, the most direct test of §2.2h available: a rule admitting only
moments when price has *not* moved is exactly the shape that would make entry
timing measure worse than a random minute.

#### GATE A RESULT, 2026-08-15 — **the boundary is protective. Hypothesis refused.**

Control against `AVOID_CHASING_BLOCKS=false`, 22 matched sessions, contracts
priced at real fills with the spread crossed both ways:

| | control | no-chase | diff |
|---|---|---|---|
| trades | 191 | 202 | +11 |
| **total premium** | **−227.7%** | **−260.6%** | **−32.9** |
| mean premium | −1.19% | −1.29% | −0.10 |
| median premium | −3.00% | −3.00% | 0.00 |
| total R | +13.64 | +12.93 | −0.71 |
| cash win rate | 28% | 28% | — |
| mean without top 5 | −2.33% | −2.37% | −0.04 |

The overall difference is inside noise — bootstrap of the mean difference is
−0.11% with a 95% CI of [−2.04, +1.78]. **What is not inside noise is the trades
the rule was blocking.** Isolating the 19 trades that exist only in the no-chase
arm:

```
mean premium     -3.83%       against -1.19% for the book
median           -4.71%
total            -72.7%
winners           3 of 19  =  16%      against 28% for the book
```

**`avoid_chasing` blocks candidates that lose roughly three times the book
average, at little more than half its win rate.** It is doing its job.

#### What this retracts

On 2026-08-13 I recorded `avoid_chasing` as "the entry-side twin of the stop
anchor and a harder constraint", "the single hardest constraint in the system",
and the explanation for MU's 5.67% and SMCI's 7.33% producing no candidate.
**The mechanism was real and the conclusion was wrong.**

Both halves failed:

* **It barely gates anything.** Lifting it entirely sent 86 more candidates to
  contract selection and produced **2** more contracts. On 11 of 16 logged days
  the trade count was identical.
* **What it does gate is worse than what it admits.** See the 19 above.

The observation behind it survives — entries do arrive ~3% into a move (§5.7
issue 1). The proposed cause does not. Price being far from EMA9 is a *symptom*
of a move that has run, and buying it is measurably bad; the rule is not what
stops the app trading MU-shaped moves.

#### What both arms agree is the real constraint

```
selection attempts    2,478        no liquid contract    2,284
became trades           191        fill rate              7.7%
```

**92% of candidates never get a contract**, in both arms, and lifting the entry
boundary moved that by 0.2 points. This restates §2.2 of
[CHANGE_IMPACT_MAP.md](CHANGE_IMPACT_MAP.md) and the earlier finding that the
funnel breaks at contract selection — which was on record and which I failed to
connect to the entry question for two days.

Read the rejection table with §0.2's short-circuit warning in force: `LOW_VOLUME`
at 63.3% is a first-failure count for a filter already measured **inert at any
value including zero**, and `OPTION_TOO_EXPENSIVE` at 5.2% is checked last and so
is badly undercounted.

**Gate A is therefore answered NO on the boundary, and Phase B must not spend
time widening entry filters.** The binding constraint is that acceptable
contracts do not exist for 92% of candidates, and the two levers that touch it —
the spread ceiling and the cost cap — pull against per-trade economics. That
tension is the first thing Phase B should measure.

**Gate A (Aug 21) — remaining.** The null-model re-run and the inverted trigger.
Does either beat the live trigger by more than draw-to-draw sd, in both holdout
halves? If yes, the trigger is confirmed as a cost and Phase
B searches for a replacement. If neither does, §2.2h is weaker than it reads and
Phase B starts from the universe instead of the timing.

### 5.2 Phase B — find a timing signal that beats random, or conclude there is none · **Aug 24 – Sep 18**

**The benchmark is random, never zero** (§2.2h). Every candidate goes through
`tools/null_model.py`.

**The requirement is already known exactly:** +0.155% of underlying per trade to
break even at spread ceiling 2, which at ~790 trades is **t = 4.2**. Any
hypothesis not on a path to that number is not worth a second run.

The hypothesis list is fixed **now**, at four, so it cannot drift as results
arrive. Each gets one run on the discovery half, and one on the holdout:

| | hypothesis | why it is on the list |
|---|---|---|
| B-i | mean-reversion at 1h / 1-session horizons | §2.2h's mechanism, read forwards |
| B-ii | opening-range break | a different clock from every setup tested so far |
| B-iii | gap fade | the only regime where a megacap reliably travels multiple percent |
| B-iv | overnight hold | §2.2d showed the move needs sessions, not hours; this is the version that pays no intraday theta |

**Gate B (Sep 18).** Any hypothesis clearing the requirement on the holdout half,
with bootstrap CI excluding zero and a positive mean without its top 5 trades
(§2.2f). Yes → Phase C. No → §5.5.

### 5.3 Phase C — the instrument decision · **Sep 21 – Oct 9**

Only reached with a signal that beats random on the underlying. The question
becomes whether any instrument can express it profitably.

1. Convert through **12.6 premium points per 1% of underlying** (§2.2d), then
   subtract theta over the hypothesis's *actual* hold — the term the original
   §1a model never contained and which reversed §2.2d's result.
2. If options cannot carry it, test **shares**. A signal worth +0.155% of price
   is a losing options trade and a viable equity one; that is a product change,
   not a rewrite.

**Gate C (Oct 9).** A configuration with positive return on capital across a
holdout — §3's existing bar, unchanged.

### 5.4 Phase D — forward validation, frozen · **Oct 12–30**

Whatever passes Gate C runs live, **untouched**, for 15 sessions.

To be explicit about what this phase is and is not: at 1–3 trades a day it is far
too small to *discover* edge — that evidence comes from Phase B's several hundred
replayed trades. Phase D exists to confirm **live behaves like replay**. Parity
is what has actually been failing: replay and the live book gave opposite answers
on exits as recently as 2026-08-13.

**Gate D (Oct 30).** Live P&L within tolerance of replay's prediction →
subscribers may be told something honest. Outside tolerance → the harness is
wrong, and that is the finding, and it outranks any strategy result.

### 5.5 The failure branch, named in advance

If Gate B fails on 2026-09-18, the honest conclusion is that **this strategy
family has no timing edge on liquid megacaps.** October is then spent on a
different universe or a different product, and subscribers do not return in 2026
on this signal. That is a real outcome with a real date, and it is preferable to
another quarter of parameter changes inside a refuted frame.

### 5.6a Validation log

**2026-08-14 — the §1a model reproduces on live trades it was never fitted to.**

22 closed live trades carrying both an R and a net premium, with the two
corrected trades excluded:

| | fitted on | slope | intercept | R² |
|---|---|---|---|---|
| §1a | 601 replay trades | 8.59 | −3.40 | 0.80 |
| live | 22 live trades | **8.08** | **−2.62** | **0.77** |

A model fitted on replayed candidates predicting the live book to within half a
point of slope is the strongest evidence so far that replay and production are
measuring the same system. It also means §1a can be used to forecast live
outcomes, which is what Phase C's instrument decision depends on.

The lower toll is expected: the spread ceiling has come 6 → 3 since those 601
trades, and the intercept is the round trip.

**Break-even is now +0.32R** (2.62 / 8.08). The book does not get there — and
the mean that suggests it might does not survive §2.2f:

```
mean premium         -1.01%     95% CI [-3.88, +2.55]   spans zero
mean without top 5   -4.50%
median trade         -2.84%
cash win rate        5 of 22  =  23%
```

Five trades of 22 carry 245% of the total. Same lottery shape as the 601-trade
replay, so the live book is not a different animal — it is a smaller sample of
the same one. **Nothing here contradicts §2.2e/§2.2g/§2.2h.**

**One config finding:** mean entry spread paid is **2.37%**, so
`OPTION_MAX_SPREAD_PCT` is not at the 2 §5.1 calls for. Three of the five trades
on 2026-08-14 paid above 2%.

**A prediction to test rather than a result:** at a ceiling of 2 the mean spread
paid fell to 1.48% in the §1.4b arm. Carrying that reduction into the live fit
puts the intercept near −1.7 and break-even near **+0.21R**. That is worth
recording now precisely so it can be checked later, not quoted as an outcome.

**2026-08-14 — the live book, in cash, for the first time.**

`option_pl_dollars` existed on **2 of 42** closed trades, so every statement about
this book had been made in R or percent. `tools/backfill_option_cash.py` computes
it from the two legs already recorded — no fetching — and took coverage to 21.

19 trades, excluding the two corrected ones:

```
total                $+119.00
mean per trade       $+6.26        95% CI [-$14.68, +$33.11]   spans zero
median trade         $-7.00
cash win rate        5 of 19  =  26%
best five            $8, $20, $65, $85, $195
total without them   $-254.00
```

**The headline is positive and the book is not.** Five trades of nineteen carry
$373 of a $119 total; the median trade loses $7. Including the two corrected
trades the total is **−$170.50**.

Same shape as §2.2f found on 331 replayed trades and as §5.6a found in premium.
Three independent measurements of this book — R, percent and now dollars — agree
that the mean is carried by a handful of outcomes and the typical trade loses.

**21 trades opened before 2026-07-31 carry no option quotes at all** and are
reported as unrecoverable rather than estimated. Recovering them needs a Polygon
fetch of historical chains; a guessed figure would be worse than a missing one,
because only the missing one is visibly missing.

**2026-08-14 — shares versus options on the same 22 live trades.**

§2.2g ruled out an edge large enough to pay for options and left open "whether a
smaller, share-sized edge exists". This is the same question asked of the live
book: for each closed trade, the underlying move it captured beside the option
return it booked.

| | underlying (shares) | options |
|---|---|---|
| mean per trade | **+0.126%** | **−1.01%** |
| median | −0.104% | −2.84% |
| mean without top 5 | −0.108% | −4.50% |
| win rate | **45%** | 23% |
| mean 95% CI | [−0.086%, +0.373%] | [−4.00%, +2.69%] |

**Two findings, and they must not be collapsed into one.**

**The instrument explains the losses.** Moving the same trades to shares takes the
mean from −1.01% to +0.126% and nearly doubles the win rate. That is the option
round trip, charged on every trade, doing what §1a says it does.

**The entry explains the absence of profit.** Shares are *not* a profitable
strategy here — the mean's interval spans zero, the median trade still loses, and
stripping the best five turns it negative. Removing options stops the bleeding; it
does not produce an edge.

**This is the plan's ordering, confirmed on live data.** Phase B fixes entry
because the instrument decision in Phase C cannot rescue a signal with no edge —
it can only stop one from being taxed. Switching to shares now would convert a
losing strategy into a roughly break-even one, which is not a business.

22 trades is a small sample and the intervals are wide; this is directional, and
Phase B's requirement of **+0.155% of underlying per trade** remains the bar. Note
the observed +0.126% sits just below it, which is worth watching rather than
celebrating.

### 5.7 The three behaviours the operator asked to prioritise — 2026-08-14

Raised after five live trades on 2026-08-14 showed a consistent shape: entries
arriving ~3% into a move, four of five continuing in the traded direction after
the exit, and three of five exiting below their own peak. Each was measured
against the 291-trade archive before being accepted, and **one of the three did
not survive that.**

#### Issue 1 — entries arrive late. **CONFIRMED, already in flight.**

Four of the five entered after ~3% of the move had run from that session's swing.
That is not a coincidence of one day: `avoid_chasing` refuses any candidate more
than 1.2% from EMA9 (§1a of CHANGE_IMPACT_MAP), so the app can only enter once
price has come *back* to the average — by which time the first leg is over.

**Where it sits:** the switch was built 2026-08-14 and the control/treatment arms
over 22 sessions are running now. **Gate A, 2026-08-21.**

#### Issue 2 — exiting while the trend continues. **REFUTED.**

The 2026-08-14 observation was real — CRWD ran another 1.84R after we left, TSLA
1.23R, SPCX 1.05R. Across **202 momentum exits** it does not hold:

```
continuation   mean +0.878R   median +0.682R    95% CI [+0.751, +1.029]
reversal       mean +1.029R   median +0.748R
net            mean -0.151R                     95% CI [-0.387, +0.081]
kept going our way more than against:  98/202 = 49%
```

Price does keep moving after a momentum exit — **it just moves against us slightly
more often than for us.** The net is a coin flip and its interval spans zero. Only
`Failed breakout` is decisively away from neutral, at **−0.821R**, meaning it
saves considerably more than it costs.

So the exits are firing at genuinely ambiguous moments, not early. This is the
same shape as the stop-floor hypothesis, which looked decisive on twelve trades
and died on 310. **Closed. Do not reopen from a single session.**

Measured by `tools/post_exit_continuation.py`.

#### Issue 3 — profit is given back before the exit fires. **CONFIRMED, and the mechanism is visible.**

Across all 291 archived trades:

```
mean peak reached (MFE)      +0.394R
mean booked                  +0.007R
mean given back              +0.387R
```

Of the 145 trades that reached +0.10R or better, they kept **24% of their peak**,
and **40 of 145 (28%) went green and closed red.**

The mechanism is `EXIT_BREAKEVEN_TRIGGER_R`, which is **1.0**. A trade only gets
its stop pulled to breakeven after reaching +1R — and the trades that get there
behave completely differently:

| | n | kept of peak | closed red |
|---|---|---|---|
| peaked ≥ +1.0R | 38 | **76%** | 2 of 38 |
| peaked +0.1R to +1.0R | 107 | far less | 38 of 107 |

**Half the book peaks below the level at which any protection engages.** That is
a specific, mechanical explanation for a specific, measured loss, which neither of
the other two issues has.

**Not yet a fix.** `phase1_21day_be025.json` already holds a breakeven-at-0.25 arm
and it moves the right way — giveback 0.387R → 0.328R, retention 24% → 30%,
green-to-red 28% → 22%. But it took 333 trades against 291, so the arms are not
on matched days (§3 gate 4), and it is scored in R rather than return on capital
(§3's primary metric). **Neither number may be quoted until it is re-run properly.**

#### Where issue 3 goes in the timeline

It is an exit change, and §1.6 settled that the momentum exits as a class earn
their keep — so this is a *protection* change, not an exit-removal change, and
nothing in §1.2 or §1.6 speaks to it.

**It becomes hypothesis B-v in Phase B, judged at Gate B on 2026-09-18.** Not
earlier, for two reasons. The standing rule in §5.6 forbids committing another
switch before the one ahead of it is measured, and Phase A's entry arms are
mid-flight. And issue 1 and issue 3 interact directly: if the entry boundary
moves, every MFE distribution behind this measurement changes, and a breakeven
level fitted to today's distribution would be fitted to a book that no longer
exists.

**Sequence, therefore:** Gate A (Aug 21) settles the entry boundary → Phase B
re-measures giveback on whatever entry survives → B-v is A/B'd on matched days in
return on capital → Gate B (Sep 18) decides.

If Gate A shows the boundary changes nothing, B-v can be brought forward
immediately, since the MFE distribution would then be stable.

### 5.8 Changes shipped during Phase A, and what each invalidates

Kept because this plan is revisited daily and a change that quietly breaks
comparability is worse than one that never shipped. Every entry names what it
makes incomparable.

| commit | change | invalidates |
|---|---|---|
| `b44a9e3` | `avoid_chasing` made switchable (`AVOID_CHASING_BLOCKS`, `..._MAX_EMA_DISTANCE_PCT`, `..._MAX_VWAP_DISTANCE_PCT`) | nothing — defaults are the previous constants exactly |
| `2dcc57f` | an intraday position outliving its session is force-closed, stamped `RECONSTRUCTED_AT_FORCE_CLOSE` | nothing forward; **past** live P&L still contains the two corrected trades |
| `5a55a62` | regression harness drives the real `evaluate_exit`; an ambiguous bar scores a **stop** | **every regression result produced before 2026-08-14** |
| `0577f66` | `option_pl_dollars` backfilled from recorded legs, 2 → 21 of 42 trades | nothing — it only fills absent values |
| `966a3ef` | MFE ratchets from the bar's **high/low**, not its close | **every MFE-derived number in this document**, see below |

#### `966a3ef` is the one that needs care

`highest_price`/`lowest_price` were ratcheted from `latest["Close"]`, so the
recorded peak was the highest *close* and every intrabar excursion was discarded.

**Two consequences, in opposite directions.**

**Backwards:** every MFE figure recorded before this is **understated**. §5.7
issue 3 reported that trades reaching +0.10R keep 24% of their peak and give back
0.387R. The direction of that finding is unaffected — a larger true peak means a
*larger* giveback, not a smaller one — but the magnitudes are floors, not
estimates. The same applies to the +1R cliff: 16% of trades were recorded booking
a peak above +1R while 34% actually reach it within an hour, so the population
sitting below the protection threshold was overstated and the population being
denied protection was understated.

**Forwards:** `mfe_r` gates `resolve_profit_lock`, the multiday profit rules and
breakeven-on-peak, so all three now engage earlier and more often. The change is
one-directional by construction — reading the bar's extremes can only raise the
peak and lower the trough — so no rule can fire *later* than before. That bound
is pinned in `tests/test_mfe_uses_bar_extremes.py`.

#### Arm comparability, stated explicitly

`CTRL_default`, `TREAT_nochase` and the running `BE025` arm were all launched
**before** `966a3ef` reached the working tree (09:27 and 09:27 against a 09:51
edit), so all three loaded the old MFE code and are **mutually comparable**.

**No arm launched after 2026-08-15 09:51 is comparable to them.** Any future A/B
needs its own freshly-run control. This is §3 gate 4 — same days both arms — in a
form the day list alone does not catch, because here the *code* moved rather than
the calendar.

### 5.9 2026-08-15 — Phases A, B and C largely collapsed into one day

Six entry levers and the whole contract question were measured in a single
session. The plan assumed these would take from mid-August to early October; the
data to answer them already existed. **This section supersedes the phase dates in
§5.1–§5.4 and states what replaces them.**

#### The finding that reframes everything

**The signal is real, and it is roughly a third of what options cost.**

```
edge, 2,027 candidates    +0.134R    95% CI [+0.069, +0.199]   without top 5: +0.118R
options break even at     ~+0.40R
```

The interval excludes zero and it survives the strip that has killed every other
result in this document. That single line reconciles three findings that looked
contradictory: the direction carries genuine information, nothing predicts which
individual trade wins, and the book still loses. The edge is thin, real, and
spread evenly — so there is no subset to select and no filter to build.

#### What was tested and returned null

| test | result | tool |
|---|---|---|
| 56 single features, Bonferroni-corrected | nothing survives with its sign | `feature_sweep.py` |
| 2,278 feature pairs, best quadrant | 54.3% on discovery, **31.1% on holdout against a 31.3% base** | `feature_combination_sweep.py` |
| regularised model, all 68 features | discovery AUC 0.740, **holdout AUC 0.433** — below a coin flip | `feature_combination_sweep.py` |
| inverting the trigger | **−0.955R, 1% win rate.** The direction is firmly right | `inverted_trigger.py` |
| entry delay, 5 windows | all worse than entering at the signal | `entry_timing_sweep.py` |
| limit-order pullback, 5 windows | all worse; the first version was lookahead and is documented as such | `entry_timing_sweep.py` |

**Selection cannot fix a generator.** Every one of these tried to pick winners
from a pool where the edge is uniformly thin.

#### The two things that did work

**Entry timing score, threshold 55 not 70.** 22,954 resolved candidates:

```
<55     n=10,437    36% win    (35% / 36% across halves)
55-70   n= 7,190    26% win    (23% / 27%)
70+     n= 5,327    25% win    (25% / 25%)
```

Monotonic, stable in both halves, and stable across **five of six market
regimes** — so one global number is defensible and per-regime fitting is
unnecessary. Shipped as the code default in `60c5cb1`. It is a *ceiling*: the
score predicts inversely, so lowering it refuses more (5,327 → 12,517).

**Contract choice, worth 6.5 points a trade.** Every contract on every recorded
chain, 1,944 candidates:

```
0-10d OTM   -10.36%   <- what the app was buying
11-25d ITM   -3.86%   <- best
```

**100% of 11-25d ITM contracts cost more than the $500 cap**, so the ranker's own
preference was unbuyable. Cap raised to 1500, preferred DTE 7-21 → 14-25. No arm
is positive, so this reduces the bleed rather than curing it — the best arm at
−3.86% is close to simply paying the §1a toll, which is what a +0.13R signal
cannot cover.

#### Issue 3's fix failed

`EXIT_BREAKEVEN_TRIGGER_R` 1.0 → 0.25 gave 222 trades against 191, identical mean
R, and a worse total (−280.0% against −227.7%). Closing early frees the symbol to
re-enter and the extra trades give the gain back. The median did improve, −2.0%
against −3.0%.

**Untried, and different in mechanism:** `PROFIT_LOCK_MIN_MFE_R` 1.0 → 0.5. It
ratchets a stop rather than closing a position, so it cannot create the extra
trades that broke the breakeven test. Both protections currently share a cliff at
1.0R, leaving the 0.1–1.0R band — half the book — with nothing.

#### What this does to the dates

| gate | was | now |
|---|---|---|
| **A** — entry boundary | Aug 21 | **answered.** Boundary protective (§5.1), six further levers null, one threshold shipped |
| **B** — does anything beat random enough to pay for options | Sep 18 | **largely answered no.** The four hypotheses were pre-empted; selection is closed |
| **C** — instrument | Oct 9 | **answered for options.** No contract on these chains carries a +0.13R signal. Shares are out of scope by the operator's decision |

**The decisive evidence arrives Monday, not in October.** Three changes are live —
timing ceiling 55, cost cap 1500, preferred DTE 14-25 — and all three are
attributable afterwards: timing refusals log `ENTRY_TIMING_TOO_EARLY`, and cost
and DTE are captured per trade in `trade_review`.

**What Gate D becomes.** If Monday's sessions show the surviving entries behaving
like the 36% band and the contracts shifting to 11-25d, the frozen forward run of
§5.4 can start in early September rather than October. If they do not, the honest
conclusion is available immediately: a +0.13R signal cannot be traded through
options, and the remaining question is whether the signal can be made larger —
which nothing measured on 2026-08-15 suggests it can.

### 5.6 Standing rules for the period

§3's four gates continue to apply. These are added, each because it was violated:

- **Judge against random, not zero.** §2.2h.
- **Every mean carries a bootstrap CI and a mean-without-top-5.** §2.2f, where
  5 trades of 331 carried 266% of the total.
- **No new switch is committed until the previous one is measured.** 2026-08-13
  produced four unmeasured switches in a day.
- **No config change during Phase D.** A mid-flight tweak resets the sample to
  zero, and this is the phase whose entire value is that it was not touched.
- **Before proposing a lever, check whether this document already closed it.**
  §5.0a is what skipping that costs.
- **Re-baseline the control after any change to the exit or risk engines.** §5.8
  records which arms share which code. A day list alone does not establish
  comparability once the code has moved underneath it.
- **State the check that could kill a finding in the same breath as the finding.**
  Three claims were withdrawn within hours on 2026-08-13/15 — the entry boundary,
  exits firing early, and "47% of trades never move" — each because the first cut
  of a number was reported before its obvious confound was tested. The confound
  was cheap to check every time.

## §5.10 — 2026-08-15 validation replay: the deployed changes land, and lose

Old settings against new, sequentially, 10 sessions (2026-07-31 to 2026-08-13),
real chains and real fills. `tools/replay_forward.py`.

```
                trades   mean opt   median   total    win    R mean   days
old settings        91     -1.54%   -3.11%  -140.0%   29%    +0.113    10
new settings       127     -1.69%   -2.74%  -214.1%   24%    +0.038    10

old CI by day  [-3.13, +0.37]   -top5 -3.05%
new CI by day  [-2.44, -0.90]   -top5 -2.50%
```

**The changes did what they were designed to do.** Contract DTE moved 9 → 16,
into the 14-25 band. Contract cost moved $298 → $820, so the raised cap is
reaching contracts it previously refused. Moneyness moved +2.5% OTM → −0.2%.
Selection attempts fell 1,187 → 636, which is the timing ceiling at 55 refusing
candidates as intended, while the fill rate rose 7.7% → 20% as the cost cap
predicted.

**And the result is worse.** Per trade the two are within noise of each other, but
the new arm takes 40% more trades at that rate, so the total loss grows by half.
The new arm's interval **excludes zero**; the old arm's does not. On this evidence
the change is reliably negative rather than merely unproven.

Two things it did not achieve. Moneyness landed at −0.2%, which is at the money,
not the ITM the sweep measured as best (< −2%). And **spread was never a
selection criterion in either arm** — see `REBUILD_PLAN.md` §13, where the
tightest contract in a chain runs 1.70% against 2.18% for what the app accepts.
The lever that matters most to per-trade economics is the one neither arm pulled.

**Recommendation:** do not treat Monday as a test of these settings on their own.
The comparison to make is against a selector that ranks by spread, which is
cheaper to build than either change already deployed and is the only one with a
measured effect on break-even.

## §5.11 — Entry quality measured as the product is actually sold

Every earlier measurement scored a **fixed entry-and-exit policy** and reported
its average. That is not the product. The product is: the app gives an entry, the
subscriber takes profit at their own level, and the app signals an exit only when
the trade goes against them. Under that design the round-trip cost is close to
irrelevant — a couple of percent on an instrument held for twenty or more.

`tools/entry_quality.py` measures the right thing: buy at the ask, track forward,
and record **how high the bid gets before the protective stop fires**. A level
counts only if it was available before the stop, because after that the
subscriber is out.

The control is a **random entry on the same day, same symbol, same contract, same
stop distance, and the same number of forward bars.** The horizon match matters:
signals arrive later in the session, so an unmatched control would hold more clock
and clear any level more often for that reason alone. Correcting that bias made
the result worse for the app, not better.

```
arm                 n     +10%   +20%   +30%   +50%   +100%   median   stopped
app entry        1,315     16%     6%     3%     2%      1%    +1.7%      58%
random entry     6,569     21%     9%     5%     2%      1%    +2.0%      50%
```

**The app's entries are worse than random at every profit level**, and they get
stopped out more often — 58% against 50%.

Only **6% of signals ever offer the subscriber a chance to take 20%**. A random
moment on the same contract offers it 9% of the time.

### What this rules out and what it opens

**Ruled out:** that transaction cost was hiding a good entry. Cost plays no part
in this measurement. The entry is the problem on its own terms.

**Opened, and this is the useful part:** the entries are not merely uninformative,
they are **worse than random**. Something is being read backwards. That is
consistent with two findings already in the repo — the entry timing score predicts
inversely (§5.9), and `avoid_chasing` blocks candidates that lose three times the
book average. Both say the app fires late in a move, after the easy part is gone,
which is exactly when an immediate reversal is most likely and is why the stop
rate is 8 points above random.

A signal that is reliably worse than random contains information. The open
question is whether it can be turned around without inverting the direction, which
is already known to fail (1% win rate, §5.9).

**Next test, on this metric rather than on average return:** entry delay was
measured null against a fixed-exit average, which is a different question from
whether waiting improves the chance of reaching +20%. It is worth re-running here.

## §5.13 — The exit rules replayed on the real book, with real option prices

`tools/exit_replay_live.py`. All 41 closed trades with an option ticker, replayed
on **the traded contract's own 5-minute bars**. No Black-Scholes, no synthetic
contract, no assumed spread. Every arm starts from the same recorded
`option_entry_mid`, so the only thing that differs is the exit.

Two contaminants were found and handled before reading anything.

**The book's headline loss is one broken trade.** The 9-day SMCI orphan booked
−27.45%, which is 43% of the entire recorded loss, from a position the system was
never supposed to hold overnight. Every arm here exits it on day one, so leaving
it in would credit these rules for fixing a bug rather than for exiting better.
Excluding it and the one other multi-day trade:

```
rule           n     mean    -top5   median     total   win   hold  ROUNDTRIP
ACTUAL        39   +1.34%   -0.71%   -0.22%    +52.4%   41%     --     48%
ema9_like     39   -1.07%   -3.37%   -2.36%    -41.9%   38%    33m      9%
atr_only      39   +2.09%   -4.02%   -2.06%    +81.4%   44%   154m     25%
giveback_50   39   +2.45%   -1.05%   +2.59%    +95.5%   56%   106m      0%
giveback_33   39   +2.66%   -0.32%   +2.86%   +103.8%   56%    92m      0%
```

**The real book was positive without the orphan.** +52.4% total rather than
−63.9%. The app's recorded trades are close to break-even carried by a few
winners — the median is −0.22% — not the disaster the raw total suggests.

### What survives the top-5 strip, and what does not

**Nothing does, on the profit figures.** Every arm including ACTUAL goes negative
once five trades are removed from thirty-nine. The profit improvement from
+52.4% to +95.5% is real in this sample and **is not established** — 39 trades is
too few, and 13% of them carry the result.

**The round-trip rate does survive, and it is the number that matters here.**
48% → 0% is a count over 20 events, not a mean over outliers, so it is not an
artifact of a few large winners. Twenty trades were ever up 10% or more; under the
current rules about ten of them finished at or below zero, and under a give-back
rule none did.

### The honest summary

- **Profit effect: promising, unproven.** Do not quote the +43-point improvement
  as a result.
- **Gain protection: demonstrated.** The specific failure the operator described
  — a winner turning into a loser — goes from half the winners to none, on real
  option prices.
- **`ema9_like` is the worst arm by total**, at −41.9%, which is consistent with
  the live exits firing on MACD and EMA9 wiggles at a 21-minute median hold.

The give-back rule should be judged on the protection it demonstrably provides,
not on the profit swing it has not earned the right to claim.

## §5.14 — Why there is no entry fix, and the two filters that do survive

The question worth answering first: **if nothing fixes entry, is entry really the
problem?** Yes, and the proof is that the app does *worse than nothing*. A random
moment reaches +10% on the option 20.7% of the time; the app's entries reach it
**15.6%**. Switching the entry logic off would improve the product. That is a
negative signal, not an absent one.

The reason no fix appeared: every previous test asked **"which candidates win"**.
For a bought option that is the wrong question. A 50/50 trade that travels is a
good option trade; a 60/40 trade that barely moves is a bad one. Direction and
movement are different properties and only direction had ever been tested.

`tools/entry_movement.py` scores conditions at the signal against **the share
reaching +10%**, split by date.

```
                    DISCOVERY (< 2026-08-11)          HOLDOUT (>= 2026-08-11)
condition      Q1    Q2    Q3    Q4    Q5        Q1    Q2    Q3    Q4    Q5
minute       32.3  21.5  16.1  12.9   2.1      15.1  24.3  21.7   7.2   5.8
range_today  24.7  23.7  17.2  15.1   4.2      11.8  25.0  17.1  13.2   7.1
iv           20.4  33.3  15.1   3.2  12.5      38.2  10.5   2.6  14.5   8.4
atr_pct      20.4   6.5  18.3  14.0  25.0      13.2  13.2  17.1  15.1  15.5
ext_ema9     21.5  17.2  16.1  14.0  15.6      11.8  13.8  12.5  18.4  17.4
rvol         15.1  17.2  20.4  20.4  11.5       8.6  21.1  13.2  17.8  13.5
```

**Two survive both halves, and they agree with each other.**

- **Time of day.** The last two quintiles of the session collapse — 12.9% and
  2.1% in discovery, 7.2% and 5.8% in holdout. Early entries run 21–32%.
- **Range already used.** When the day's range is mostly spent, the rate falls to
  4.2% and 7.1%. Both halves, same direction.

These are the same fact seen twice: **late in the session the day's move has
already happened, and there is nothing left for the option to capture.**

**Four fail.** `iv` looked like the strongest single cell at 30.1% overall but is
unstable — best quintile Q2 in discovery, Q1 in holdout, with the middle jumping
around. `atr_pct` peaks at Q5 in discovery and is flat in holdout, which kills the
"select for volatility" hypothesis in the simple form it was posed. `ext_ema9` and
`rvol` are noise.

### What the fix is worth, stated honestly

Removing the bad quintiles lifts the rate from **15.6% to roughly 22%**. The
random baseline is **20.7%**.

**So the filter recovers the deficit and does not create an edge.** It stops the
app doing something actively harmful; it does not make it better than chance. That
is a real improvement to the product -- about 40% more signals that give the
subscriber something to work with -- and it is not a trading edge, and should
never be described as one.

**Concrete rule:** do not open in the final third of the session, and do not open
when the session's range is already largely spent. Both are computable at scan
time from data the app already holds.

## §6 — What this app is, stated by the operator and binding on future work

Re-stated on 2026-08-16 after a session that repeatedly drifted away from it.

**This is a signal-detection product.** It reads the market and tells a subscriber
when to enter and when to get out. The subscriber holds the position and decides
when to take profit. Round-trip cost is not the subject and is not to be
reintroduced as a framing: it is already enforced inside the option gates as a
spread ceiling and a cost cap, and re-deriving it as a reason the product cannot
work is how three days were spent.

The requirement, in the operator's terms:

1. read the technicals, market conditions, indicators and news, and produce a
   **correct entry signal**
2. **detect the entry moment**, not merely the direction
3. once in, **detect when the signal has changed**
4. **do not give back profit that was made**
5. **do not let a trade run into a large loss**
6. **signal the exit** at the right moment
7. **distinguish chop and small reversals from real ones** — know when to hold
   and when to alert

Item 7 is the hardest and is the one today's work actually addressed.

### Where each item stands, 2026-08-16

| | state |
| --- | --- |
| 1 entry signal | **weakest part.** Only the 14:05 cutoff survived testing. Entries reach +10% on the option 18.4% of the time against 20.7% for a random moment — still below chance. |
| 1 news | **not implemented at all.** The app reads no news. Polygon serves it on the current plan (verified 2026-08-15) and nothing consumes it. |
| 2 entry moment | seven experiments null. Delay, features, ranking, generators all failed. |
| 3 signal changed | partly — the volume flush detects a turn; nothing detects a thesis breaking. |
| 4 give back profit | **53% → 32%** of winners finishing at or below zero. |
| 5 large loss | hard stop, unchanged, working. |
| 6 exit timing | flush + floor, both measured on the live book. |
| 7 chop vs real | **this is what the flush does.** Heavy volume with real range separates a conviction turn from drift; every structural definition tried (swing break, lower low, EMA9, EMA20, 15m EMA) fires after the money has gone. |

### Two honest gaps

**News is absent.** It is in the requirement, the data is available, and nothing
in the pipeline touches it. That is the largest untouched item on this list.

**Item 7 is measured on intraday trades only.** The flush is armed for MULTIDAY
positions too, where a single heavy bar could end a multi-day thesis. There are 9
MULTIDAY trades and 8 closed the same session, so there is nothing yet to measure
it against. Revisit once `EXIT_MOMENTUM_ENABLED=false` lets them actually run.
