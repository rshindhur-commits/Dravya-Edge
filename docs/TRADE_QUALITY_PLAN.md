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

### 1.2 Test removing or tightening the EMA exit — **highest expected value**
151 trades, −6.81% each, 55% of all losses, mean R −0.42. The hypothesis is that
it exits positions that the hard stop would not have taken out, at close to
hard-stop cost. Arms: disabled, and confirmation-delayed by one bar.

*Risk:* it may be preventing worse losses that the replay will now realise.
The A/B answers that directly — if disabling it makes things worse, that is the
answer and the exit stays.

### 1.3 Test the MACD exit
177 trades, −3.03%, mean R +0.07. It exits almost exactly flat on R and loses
the spread. That is the signature of an exit with no informational content:
it is paying 3% to close positions at breakeven.

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

## 2. Long term — needs more sessions and cleaner capture

### 2.1 A predictor for the 48% that never move
The single largest prize: this half loses 7% each at a 2% win rate. Removing it
would take the book to roughly breakeven before any other change. One holdout
test has already failed to find a predictor among entry-time features. The next
attempt needs features not currently recorded — order-book state at entry,
time-since-signal, and whether the move had already happened before the scan saw
it.

### 2.2 Is the signal late?
Your original diagnosis, still untested: entries arriving after the move has
run. Needs entry timestamps compared against the bar sequence that triggered
them. Now feasible: the exit-timestamp defect is fixed, so alert and database
times agree.

### 2.3 Does any setup have edge once the exits are fixed?
EMA_PULLBACK (183) and EMA_REJECTION (123) are indistinguishable today, both at
−24/trade. That comparison is meaningless while 83% of losses come from two exit
rules applied to both. Re-run it after section 1.

### 2.4 Whether this strategy clears its own costs at all
The honest question behind everything. A directional signal with no edge, paying
3% a round trip, cannot be rescued by tuning. If sections 1 and 2 do not produce
a configuration with positive return on capital across a holdout, the answer is
that this approach does not work at these transaction costs, and the response is
fewer, longer, larger-conviction positions — or a different instrument.

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

| | what | needs |
|---|---|---|
| 1 | Revert cost cap to $500 | nothing — proven |
| 2 | EMA exit A/B | replay, ~2h |
| 3 | MACD exit A/B | replay, ~2h |
| 4 | Entry spread distribution, then ceiling A/B | replay |
| 5 | `MIN_STOP_SPREAD_MULTIPLE` A/B | replay |
| 6 | Re-test setups with the winning exit config | replay |
| 7 | Never-moves predictor | new features, more sessions |
| 8 | Signal latency | more sessions |

Subscribers return when a configuration shows positive return on capital across
a holdout, and a month of sessions captures cleanly. Not before.
