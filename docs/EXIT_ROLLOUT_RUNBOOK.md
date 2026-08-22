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

**Enabled 2026-08-21**, together with the trail arm at 1.0.

**What to check:** `adjustment_reason` and the `adjustment_reasons` history on
the trade row — `Profit ladder: X.XXR locked`. Until 2026-08-21 neither field was
ever written, so this instruction pointed at nothing; see §5.

```sql
select symbol, payload->>'adjustment_reason',
       jsonb_array_length(coalesce(payload->'adjustment_reasons','[]'::jsonb))
from paper_trades where opened_at::date = current_date;
```

Measured before enabling, on the ten trades of 08-20 and 08-21: **+0.64R and
+$55**, turning −$35 into +$20, firing on three of ten. Six rung sets were tested
and all six were positive, which is what made the on/off call safe — the
best-scoring set was fitted to three firing trades and was deliberately not
taken.

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

### Session 3b — soft-exit confirmation (built, off, do not enable yet)

```
SOFT_EXIT_CONFIRM_BARS=0        # 1 would require a second sighting
```

**RETRACTED 2026-08-21, same day it was written.** This section originally
justified itself with a replay showing that holding to stop and target with every
soft exit removed booked +0.22R / +$128 against −1.11R / −$35 as booked, and
concluded "doing nothing beat the exit engine."

That is the **hold-to-stop-or-target counterfactual**, which
`docs/CHANGE_IMPACT_MAP.md` §6 has already measured on a far larger sample at
**−18.6R (bull) / −23.8R (bear)** and marks *"Settled, and do not re-open:
momentum exits are loss-limiters, not profit cutters."* The same document records
that this exact method is what made the regression harness report +3.22R for a
day the book took −0.65R, and it was repaired on 2026-08-14 for that reason.

Ten trades do not overturn it. The number was arithmetically right and the
inference from it was wrong, and it should never have gone into this file — the
standing rule in CHANGE_IMPACT_MAP §9 is to read that file before proposing a
lever, and it was not read.

What survives: the **one-scan-deferral** measurement, which was **−0.51R / −$80**
and is unaffected, because it is a deferral rather than a removal. That number
argues against enabling this, and it is why the switch ships at 0.

**Also: this may be redundant.** `EXIT_EMA_CONFIRM_BARS` already exists, is listed
in CHANGE_IMPACT_MAP §6's knob table, and requires the invalidation to have held
on the previous n bars — acting immediately rather than deferring. That is the
better design and it has never been measured. `tools/exit_trend_vs_pnl.py` exists
to measure it. Do that before considering this switch again.

**Why the existing guards do not already cover this.** All three of the
five-minute exits fired at `bars_in_trade = 0`.
`_should_guard_early_exit` returns False once `abs(rr_progress) >= 0.25`, so a
trade half an R underwater falls straight through it, and
`resolve_soft_exit_hold` requires profit, which none of them had.

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

## 5. Two instruments that recorded nothing, fixed 2026-08-21

Both shipped on 08-19 as part of this work and neither ever wrote a row.

**`adjustment_reason`** was returned by `evaluate_exit` on every call and no
write path stored it. Every "what to check" above was unrunnable. Now persisted
by `update_paper_trade` as a deduplicated `adjustment_reasons` history plus the
latest value, and by `close_paper_trade` for the closing verdict.

**`target_touch_r`** could only ever be written when `EXIT_TARGET_EXTEND_ENABLED`
was already on: with extension off the target is taken on the scan that reaches
it, and that scan closes through `close_paper_trade`, which never received
`exit_state`. The instrument built to decide the switch required the switch.
`close_paper_trade` now takes `exit_state` and both call sites pass it.

Check both are alive before trusting any session above:

```sql
select count(*) filter (where payload->>'adjustment_reason' is not null) reasons,
       count(*) filter (where payload->>'target_touch_r' is not null) touches,
       count(*) filter (where payload->>'soft_exit_streak' is not null) streaks
from paper_trades where opened_at::date >= current_date - 3;
```

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
