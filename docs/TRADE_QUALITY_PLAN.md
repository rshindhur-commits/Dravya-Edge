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

**Open:** whether 2% is better still. The trend says tighten; the volume cost so
far is mild (11% fewer trades), but it will not stay mild.

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

## 4. Order of work

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
