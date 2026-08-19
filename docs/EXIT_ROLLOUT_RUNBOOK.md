# Staged rollout of the 2026-08-19 exit work

**Six bug fixes go live with the merge. Five behaviour features ship off and are
enabled one per session.**

Four of the five default ON in code. Merging without the `.env` block that
disables them puts four unmeasured behaviour changes into the same session and
makes that day unattributable — if the book moves, nothing says which change
moved it.

---

## 0. What goes live immediately, with no switch

These are repairs, not bets. Each replaces something demonstrably broken, and
none of them changes what the app *decides* — only whether it decides on true
inputs.

| fix | what it replaces |
|---|---|
| Live price for the monitor and the sub-scan log | the previous session's close |
| Opening-range scan gap | sleeping through 09:30–09:45 |
| Retroactive stop | a stop tested against pre-move lows |
| Option peak persisted | a give-back floor that had never once run |
| Partial-profit gate | an execution claimed on a 1-contract position |
| Entry fill recorded | no record of the decision-to-fill drift |

## 1. Merge and confirm a clean session

```
git checkout main && git merge Claude_POA && git push
```

Both Render services redeploy. Then, **before enabling anything**:

* `candidate_price_log` fills with *moving* prices — check
  `select count(distinct price) from candidate_price_log where trading_day = current_date`.
  More than one value per symbol means the monitor is finally reading the tape.
  One value per symbol means it is still on `/prev`. Purge the day's frozen rows
  first: they were written by the broken path and will otherwise look like a
  motionless market.
* The worker scans the opening range. A scan between 09:30 and 09:45 that is not
  the one that opened at 09:29 means `interval_after_scan` is working.
* `entry_price_at_fill` and `entry_fill_gap_r` are populated on any new trade.
  This is the first real measurement of the 8-minute entry lag.

Do not enable a feature on the same day as the merge. One change at a time, and
the merge is already six.

## 2. Enable one per session, in this order

Order is by strength of evidence, not by expected size.

### Session 1 — the profit ladder

```
EXIT_PROFIT_LADDER=1.0:0.25,1.5:0.75,2.0:1.25,2.5:1.75,3.0:2.25
EXIT_TRAIL_ARM_R=1.0
```

First because it is the only one with a validated measurement behind it. The
NVDA fixture in `tests/test_backtest_exit_parity.py` moves **+0.596R → +1.384R**
against the same 2.03R peak, cutting the give-back from 1.43R to 0.65R. The trail
arming moves with it: on its own it changed only the exit *label*, because the
ATR trail lands where the EMA9 touch would have.

**What to check:** `Profit ladder: X.XXR locked` in the adjustment reasons. Count
how many trades reach a rung at all. On 2026-08-19's five trades, two would have
(TSLA peaked 1.18R, PLTR 1.24R) — so expect this to fire rarely and matter a lot
when it does.

**Back out if:** trades that previously ran to target start stopping out at a
rung. That is the ladder locking too close to the peak, and the rungs are one env
var.

### Session 2 — the soft-exit hold

```
SOFT_EXIT_HOLD_ENABLED=true
```

**What to check:** `Soft exit held: trend health NN at X.XXR` in the adjustment
reasons, and whether held trades go on to do better than the soft exit would
have. Compare against the 14 soft exits on record: 5 positive, mean −0.059R.

**Back out if:** losing trades are being held. They should not be — the rule
requires `rr_progress > 0` — so if you see one, the profit reading is wrong and
that is a bug, not a tuning problem.

### Session 3 — the structure trail

```
EXIT_STRUCTURE_TRAIL_ENABLED=true
```

**What to check:** `Structure trailing stop active`. This only bites on symbols
whose ATR is wide relative to their risk — AMZN's 15-minute ATR was 1.32–1.44
against a 1R of 1.31, so the ATR arm sat a full R below the high and did nothing.

**Back out if:** stops tighten so far that trades are cut inside normal noise.
`EXIT_STRUCTURE_TRAIL_LOOKBACK` (5) and `EXIT_STRUCTURE_TRAIL_BUFFER_PCT` (0.05)
are the knobs before the off switch.

### Session 4 — the entry slip refusal

```
ENTRY_MAX_FILL_SLIP_R=0.35
```

Held back to last because it refuses trades rather than managing them, and
because sessions 1–3 need entries to measure. By the time you turn it on you will
have several sessions of `entry_fill_gap_r` and can pick the cap from the
distribution instead of from the 0.35 judgement.

**What to check:** `ENTRY_FILL_SLIPPED:+X.XXR>0.35R` blocks. On 2026-08-19 this
would have refused TSLA (+0.59R, lost $65) and allowed the other four.

### Session 5 — target extension

```
EXIT_TARGET_EXTEND_ENABLED=true
```

Last, and the only one where the available evidence says **on is worse**. AMZN
#343 hit its target at +1.99R and ran to +3.08R, but the path dips to +1.50R at
11:20 and the ladder rung at 1.75R takes it out *below* the target it declined.

Turn it on only when you want to settle the question, and settle it with
`target_touch_r`: it is recorded on every trade regardless of the switch, so
`final r_multiple − target_touch_r` is exactly what extending won or lost. That
is a query, not an analysis.

## 3. The rule for all of them

Every one of these is a judgement, not a result. The rungs, the 0.35 cap, the 70
health floor — none has been measured on this book. What *has* been established
is that each replaces something that was broken or unreachable.

So the criterion is not "did the day make money", which five trades cannot
answer. It is:

* **did it fire at all**, and on how many trades
* **did it fire where it was meant to** — read the adjustment reasons
* **did anything it touched get worse** — compare against the same rule's
  behaviour the week before

`docs/PROFIT_PROTECTION_DEAD_ZONE.md` has the measurement this work came from:
6.51R of favourable movement, 1.66R booked, a 25% capture.

## 4. Why not just replay three days and decide

Tried on 2026-08-19; `tools/exit_config_ab.py` is the tool. It does not settle it,
for two reasons worth recording so the next attempt does not repeat them.

**Sample.** 13 closed trades, of which perhaps two ever reach the range these
rules operate in. The stop-floor hypothesis looked decisive on 12 trades and died
on 310.

**Fidelity.** The control arm does not reproduce the book — SPCX booked −1.28R and
replays +0.75R. The existing parity harness works because it replays *frozen
fixtures* against a cached market snapshot; a tool that rebuilds frames live
diverges. Until the control arm reproduces the booked result, neither arm means
anything.

Two bugs were found in that tool before it produced even that much: it replayed
every PUT as a CALL by hardcoding the entry type, and it stripped `tzinfo` from a
UTC `opened_at` so the replay grid read 14:08 UTC as 14:08 ET and walked a
different part of the session. Both are fixed; the fidelity gap is not.

**The harness that does work is the parity suite**, and it is the evidence behind
session 1.
