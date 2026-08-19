# No entry-quality signal separates winners from losers

**Measured 2026-08-19, prompted by AVGO #351. Includes a retraction.**

AVGO lost $105 and was wrong from the first minute. The question asked was whether
it should have been blocked at entry on the signals the app already had. It should
not have been -- not because the trade was fine, but because **none of those
signals predicts anything**, and one of them does not exist at entry at all.

---

## 1. Retraction: trend health is not an entry signal

An earlier reading in this session banded closed trades by
`payload->trend_health_score` and reported a clean monotonic result -- 0% win
below 60, 35% at 60-85, 56% at 85+, with the top band the only profitable one. It
looked like the answer.

**It is circular and the result is withdrawn.**

`trend_health_score` is overwritten on every scan
(`paper_trade_manager.py:348`); it is never frozen at entry. It is also only
computed when a position is already open -- `main.py:5166` guards it with
`if active_trade and active_trade["status"] == "OPEN"`, and candidates get
`NO_ACTIVE_TRADE` with score `None`. The archive confirms it: **`trend_health` is
NULL on all 3,967 `candidate_evidence` rows** and present on 214 of 28,404
`scanner_snapshot` rows.

So the number banded on was the reading near **exit**, not entry. Trades that
ended badly had unhealthy trends at the end because that is partly what the
outcome *is*. The same error invalidated an "entry quality" comparison of the day's
four trades presented earlier in the same session; AVGO's "trend health 40" was a
late reading, not something knowable at 11:03.

**The rule this earns:** before banding outcomes by a field, check whether the
field is frozen at entry or refreshed during the trade. `option_entry_spread_pct`
and `initial_stop_loss` exist precisely because that distinction has bitten here
before.

## 2. Option quality: no separation, and mildly inverted

This one is legitimate to test -- `option_quality` is fixed when the contract is
chosen and never refreshed. 129 candidates carry it with a resolved outcome,
across 13 sessions:

| option quality | n | win % | median RR |
|---|---|---|---|
| 65-80 | 15 | **26.7%** | 1.78 |
| 80-90 | 28 | 17.9% | 1.87 |
| 90-101 | 86 | 19.8% | 1.83 |

```
  >=80  (n=114)  19.3% win
  <80   (n= 15)  26.7% win
  bootstrap P(>=80 better) = 0.287
```

The lower band wins more, and the bootstrap says the ordering is noise in either
direction. Median RR is flat across bands (1.78 / 1.87 / 1.83), so this is not the
targets-are-further confound that had to be ruled out for the setup score.

n=15 in the low band is thin and this does not establish inversion. What it does
establish is that **there is no evidence for a minimum-option-quality entry gate**,
which is what was being asked for.

## 3. Setup score: already measured, already off

Unchanged from 2026-08-12, over 244 resolved candidates:

```
    setup <50    n=136   33.8% win   +0.233R
    setup 50-70  n= 47   29.8% win   -0.171R
    setup 70+    n= 61   21.3% win   -0.300R
```

Inverted at z = 1.89, p = 0.059, median RR flat across bands. `setup_gate_blocks()`
returns `SETUP_GATE_ENABLED` which defaults false, so the check runs, records the
failure and deliberately does not act. That is why AVGO entered at setup 60 against
a floor of 81, and why 18 of 35 entries in the archive sit below their recorded
floor.

Re-enabling it would have blocked AVGO -- and also AMZN at 74 against 81, the
+$160 winner of the same session.

## 4. So this is the seventh null

It joins the six already on file: 56 single features, 2,278 feature pairs, a
regularised model at holdout AUC 0.433, entry delay, limit-order pullback, and the
inverted trigger. Nothing found so far separates the winners from the losers before
the fact.

That is not a reason to stop looking, but it is a strong prior against installing a
gate on 36 trades and a circular predictor.

## 5. What was actually wrong with AVGO

Everything knowable at 11:03 that was *not* null:

```
  setup            60   (floor 81, gate deliberately off)
  RR             2.00   (floor 2.00 -- passed at exactly the minimum)
  option quality    50
  entry spread   1.81%  -> 2.37% by exit, widening 1.31x
  contract      $1,938  (allowed only by the AVGO:2500 per-symbol override)
```

The loss decomposes as **-$65 of price move and -$40 of crossing the spread**, on a
contract whose premium is twice the book's typical size. The direction was wrong;
no entry filter tested here would have known that.

**The actionable lever is the contract, not the signal.** Contract choice was
already measured at 6.5 points a trade (0-10d OTM -10.36% against 11-25d ITM
-3.86%), and a $1,938 premium makes every basis point of spread cost twice what it
does on a $900 contract. That is where AVGO was avoidable.

## 6. What to record so this can be answered properly

Both entry-quality tests were limited by coverage, not by method:

* `option_quality` -- 129 of 3,967 `candidate_evidence` rows. It is written when a
  contract is priced, and contracts are priced for a small minority of candidates,
  so the population is small by construction rather than by omission.
* `trend_health` -- 0 of 3,967. Computing it pre-entry is a **feature**, not a
  recording change: the V2 shadow only runs on open positions. Whether an
  entry-time trend-health reading is worth building is an open question, and it
  should not be assumed from the retracted result above.

Neither is fixed by a quick write-path change, which is the honest answer to
"record it and measure later". The population that can answer the option-quality
question grows only as fast as contracts get priced.
