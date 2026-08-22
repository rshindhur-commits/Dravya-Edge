# Options quality plan — the active tracked plan

**Goal:** an options algo that selects trades worth their cost. Not more trades.

Supersedes [DELIVERY_PLAN.md](DELIVERY_PLAN.md) as the live plan on 2026-08-10,
after its Gate 1 answered **no**. That file stays as the record of what was
tried; this one is what is being tried now.

**Status key:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
· `[-]` dropped, with a reason

**Last updated:** 2026-08-12

**How the app is measured now.** Trade-outcome metrics cannot work here: 0–1
trades a day leaves `daily_engine_summary` a table of nulls. The daily reading is
instead the **candidate resolution** — 30–40 refused candidates a session,
scored against the bars that followed, written nightly by the scan loop. Read
target-first % against the **29.3%** baseline, then per-rule `winners_blocked`
against the same. A gate materially below base rate is discriminating; at base
rate it is only removing volume. `roi: None` means the rule fires before a thesis
exists and cannot be scored at all.

---

## The bar, stated once

```
mean R per trade   >   toll in R,   toll = spread% ÷ (stop% × leverage)

measured today:    +0.0073R   95% CI [-0.080, +0.095]   (includes zero)
toll today:         0.19R      3.4% spread ÷ (0.50% stop × 35.9x leverage)
```

Every step either raises the left side or lowers the right. **A step that does
neither does not get done**, however reasonable it sounds.

The second number is the one to keep in view: a trade begins 0.19R down. Over
291 archived trades the book returned **−3.15% of premium**, 95% CI
[−4.09, −2.18] — an interval that never touches zero — while mean R sat at
roughly nothing. That is the whole problem in two lines.

---

## Phase 0 — See what we are blind to · Mon 10 Aug → Fri 14 Aug
*No behaviour change. No quota.*

- [x] **S-A1** Record the thresholds each scan enforced
  *Evidence:* `scanner_runs.payload->'config'`, `app/runtime/config_snapshot.py`

  **First produced data 2026-08-11**, confirmed live: non-null for all 113 runs
  that day and null for every session before it. Merged on 10 Aug is not the
  same as recording, and only the second of those can be checked. It paid for
  itself immediately — see the 11 Aug finding, where it is the reason today's
  rejections could be graded against the 2.0% ceiling that actually ran rather
  than the 6.0% sitting in a local `.env`.
- [x] **S-A2** Record contract leverage on every priced contract
  *Evidence:* `app/risk/option_leverage.py`, off by default, records regardless
- [x] **S-A3** Capture full chain quality per candidate — best available spread, OI,
  delta and cost, not only the contract selected
  *Evidence:* `app/options/chain_quality.py`, `CHAIN_*` fields on every row.

  **Capture was already complete** and this is worth recording, because it was
  not what the step assumed. Since `6af092e`/`65361cb` every attempt carries 21
  fields — strike, dte, open interest, volume, bid, ask, delta, iv, cost, quote
  status, threshold — durably in `scanner_snapshot.decision_payload`. It is
  stored there as a **JSON string inside JSONB**, so Postgres cannot see into
  it and each question needed a 233 MB table pulled and parsed in Python. The
  gap was accessibility, not capture, and `summarize_chain` closes it.

- [x] **S-A4** Resolve outcomes for candidates that were never entered.
  *Evidence:* `tools/resolve_candidate_outcomes.py`, `app/runtime/outcome_scheduler.py`

  Opened and closed 11–12 Aug. The cause was one line: `Replay Outcome` is
  computed only where `risk_setup["trade_allowed"]` is true, so a candidate a
  gate rejected never had an outcome derived — **NO_REPLAY on 16,614 of 16,614
  scanner_snapshot rows, every row ever written.**

  Refused candidates are now replayed against the bars that followed. 239
  resolved across 9 sessions and bridged into `candidate_evidence`, whose
  `winner` went 0 → 70. Runs nightly from the scan loop's idle branch, so it
  does not depend on anyone remembering a command.

  Intrabar ties resolve to the stop, as `ceiling_test.py` does. It cost nothing:
  **zero** of the 239 resolved on a bar touching both levels, so the figure is
  the same under either convention.

### ▶ Finding, 2026-08-10 — the constraint is not what the rejection counts say

Of **247** candidates on 10 Aug that took no contract, **244 (99%) had a
contract quoted at ≤3% spread available.** What refused that tightest contract:

| refused the best contract by | n | share |
| --- | --- | --- |
| `OPTION_TOO_EXPENSIVE` | 82 | **34%** |
| `LOW_OPEN_INTEREST` | 70 | 29% |
| `LOW_VOLUME` | 63 | 26% |
| `LOW_OPTION_QUALITY` | 18 | 7% |
| `WIDE_SPREAD` | 8 | **3%** |

The spread ceiling is almost never what stops the best available contract. By
raw attempt counts `LOW_OPEN_INTEREST` dominates at 11,048 of 18,775 — but
those are the far-OTM strikes the selector walks past. **The contract you would
actually want is refused for price.**

And it cannot be bought by raising the cap. Those 82 tight contracts cost
**median $3,825**, min $600, max $9,185:

```
cap $500   admits  0 of 82        cap $1500  admits  6 of 82
cap $1000  admits  3 of 82        cap $2000  admits 18 of 82
```

A $3,825 contract is 77% of a $5,000 account in one position.

**This is the vice, stated exactly:** on 26 megacaps at $5,000 of capital, tight
spreads and affordability are mutually exclusive. Near-ATM contracts are liquid
and cost thousands; contracts under $500 are far OTM, wide and thin. The app has
been choosing the only thing it can afford, and that thing is structurally
unprofitable.

It sharpens S-C: the universe filter is not "tight spreads" but **"tight spreads
at a premium this account can hold"**, which points at lower-priced liquid
underlyings rather than megacaps. One session; needs confirming across the
archive before the 17 Aug decision, per rule 1.

### ▶ Finding, 2026-08-11 — the same constraint, confirmed by a second method

The 10 Aug read above came from asking what refused the *best available*
contract. 11 Aug asks the opposite question — of the contracts that were
refused, how many would trading actually recover — and lands in the same place.
Two methods, two sessions, one answer. This satisfies the "needs confirming
across the archive" caveat on S-C for a second session.

`evaluate_option_liquidity` is a **short-circuit chain** — OI → volume → spread →
DTE → quality → **cost, last** — so a rejection code is only an attempt's *first*
failure. A contract stamped `LOW_OPEN_INTEREST` was never measured against the
bars after it, which is precisely why OI dominates the raw counts and means
nothing. Re-testing every other bar, on 13,846 attempts:

| relax the OI floor to | recovered by label | **actually tradeable** |
| --- | --- | --- |
| 250 | 1,982 across 18 symbols | **0, across 0 symbols** |
| 100 | 4,090 across 18 symbols | **0, across 0 symbols** |

Not one. They failed cost (1,797), spread (1,764) and volume (1,284) as well.
**The open interest floor cannot be the constraint at any setting.** Meanwhile
558 contracts were refused on cost *having already cleared every other bar* —
cost being last in the chain makes that count exact rather than indicative.

Five symbols carried a fully-qualified contract within $65 of the cap: PLTR
$505, TSLA $515, NVDA $525, ORCL $555, SPCX $565. That is a much narrower miss
than the $3,825 median of 10 Aug, and it is what pulled NVDA and PLTR into the
S-C ranking below.

`tools/option_rejection_report.py` was fixed to print a joint-tested `tradeable`
column beside `by label`; reading the old column is what made the OI floor look
worth relaxing. For `OPTION_TOO_EXPENSIVE` the two columns agree exactly, which
is a standing self-check that the chain order still holds.

### ▶ Finding, 2026-08-11 — RR, not cost, is what refuses the final step

The finding above is about which *contract* gets bought. This one is a layer up,
and it changes what Phase 2 has to prove.

On 11 Aug the chain delivered: **63 candidate-moments carried a contract at ≤2%
spread and ≤$500 — at the live ceiling, not a hypothetical one** — and 25 cleared
every liquidity, cost and quality bar. Every one of the 24 that reached a
decision was refused for the same reason: **RR below 2.0**.

```
NFLX 1.88    PLTR 1.82    NVDA 1.60    TSLA 1.59
```

It is not a one-day artefact. Of candidates that got an option priced:

| session | RR-blocked | option-blocked | entered |
| --- | --- | --- | --- |
| 08-03 | 90 | 97 | 3 |
| 08-04 | 123 | 23 | 5 |
| 08-05 | 109 | 32 | 3 |
| 08-10 | 312 | 224 | 1 |
| 08-11 | **274** | 194 | **0** |

**RR refuses more than options on four of five sessions.** Both findings are
true and they are not in conflict — cost decides which contract is buyable, RR
decides whether the candidate is taken at all — but only the second one is
currently gating trade count.

*Config note:* `AUTO_PAPER_MIN_RR=1.8` is dead on the live path. The scanner
gate's 2.0 wins by design (`app/runtime/paper_automation_support.py`, the
`ENTRY_GATE_MIN_RR` fallback). NFLX at 1.88 and PLTR at 1.82 both clear the
configured 1.8 and were refused anyway. Anyone tuning that knob will measure
nothing and conclude the wrong thing.

### ▶ Finding, 2026-08-11 — no outcome exists for candidates never entered

`candidate_outcome` holds 888 rows, **every one `became_neutral`, zero
`target_hit`, zero `stop_hit`**, and nothing written since 31 July.
`candidate_evidence.winner` is false on all 2,600 rows.

So the question "would a looser bar have admitted winners?" **cannot be answered
from the archive for any threshold** — RR, spread, OI or cost. There is outcome
data only for trades that were entered. Every loosening proposal is therefore a
forward experiment, never a backtest, until this is repaired. That is a
precondition for the observe-only pattern S-E already relies on: shadowing a
threshold into a store that resolves everything to neutral records nothing.

### ▶ Finding, 2026-08-12 — the candidate pool has no edge, and no gate discriminates

The refused candidates, replayed on the app's own target/stop geometry:

```
win rate            29.3%   (70 of 239, 95% Wilson CI [23.9, 35.3])
mean RR on winners   2.42
break-even at 2.42  29.3%

GROSS expectancy    0.000 R
less the toll      -0.321 R
```

**Exactly zero, gross.** Not a small edge eaten by cost — no edge. And it
reproduces Gate 1 from disjoint data by a different method: the ceiling test put
`target 2.0R / stop 1R` at −0.004R over 291 *accepted* trades; this is 0.000R
over 239 *refused* candidates.

Per gate, of what each one blocks:

| gate | resolved | winners blocked | rate |
| --- | --- | --- | --- |
| `OPTION_REJECTED` | 120 | 33 | 27.5% |
| `DELAYED_DATA_CONFIRM_LIVE` | 35 | 10 | 28.6% |
| `RR_BELOW_THRESHOLD` | 32 | 10 | 31.2% |
| **base rate** | **239** | **70** | **29.3%** |

**Every gate blocks winners at the rate winners occur.** None of them
discriminate; they remove volume, not losers. That is why no profitable subset
was ever found across 601 trades — there is no subset to find, and it is the
mechanism behind "the cap changes the stake, not the rate".

Two corrections this forced:

* `learning_engine` counted *unresolved* as *not a winner*, so `losses_prevented`
  was the block count and every rule scored positively. `LOW_RR` ranked first at
  ROI 1000 across 1,000 rows of which **zero** were resolved — on the day it
  refused every trade. Rules are now scored on resolved rows only.
* `LOW_RR` and `RR_BELOW_THRESHOLD` are different rules and were conflated here
  earlier. `LOW_RR` fires **before a direction is chosen** and can never be
  scored: its candidates carry no direction, entry, stop or target. Every row
  that does have a direction has a full triplet — 1,585 of 1,585 — so the split
  is structural, and unscoreable rules now report `roi: None` rather than a
  flattering number. The 2.0 gate the plan actually argues about is
  `RR_BELOW_THRESHOLD`, and it is measurable: 31.2% against a 29.3% base.

**What this means for Phase 2.** Cutting the toll moves −0.321R toward 0.000R.
That is worth having — it stops the bleeding — but its ceiling is break-even, and
Gate 2 as written can pass on both criteria with expectancy still at zero. The
question worth carrying into 17 Aug is not whether cost fell but whether **any
subset of this pool beats 29.3%**, which is Phase 3's question and is now
answerable nightly without risking money or waiting for trades.

## Phase 1 — The ceiling test · Fri 14 Aug · **this gate can answer no**
*Offline, existing data, no quota.*

- [x] **S-B** Perfect-foresight replay. Exit all 291 archived trades at their actual
  MFE — the best price ever available — net of real spread and theta. Then the same
  at fixed targets, taking the stop whenever MFE and MAE both cleared, because
  intrabar order is unknowable and assuming otherwise manufactures the edge.
  *Evidence:* `tools/ceiling_test.py`, run 2026-08-10.

### ▶ GATE 1 · answered Mon 10 Aug — **PASS, and only just**

`premium% = 10.046 x R − 3.221`, correlation **+0.914**. The intercept is the
toll: **3.22% of premium before the underlying moves**, so break-even needs
**0.321R**. Mean R is **+0.007**. That is the gap, and it is a factor of ~46.

| policy | mean R | mean % | median % | 95% CI | win% |
| --- | --- | --- | --- | --- | --- |
| actual | +0.007 | −3.15 | −4.91 | [−4.09, −2.17] | 20% |
| **ceiling: exit at MFE** | +0.394 | **+0.74** | **−2.22** | [+0.10, +1.41] | 40% |
| 75% of MFE | +0.295 | −0.25 | −2.47 | [−0.73, +0.26] | 31% |
| 50% of MFE | +0.197 | −1.24 | −2.72 | [−1.56, −0.90] | 22% |
| target 2.0R / stop 1R | −0.004 | −3.26 | −4.35 | [−4.08, −2.42] | 27% |
| target 1.0R / stop 1R | −0.032 | −3.54 | −4.34 | [−4.25, −2.81] | 29% |

**Pass by the stated criterion** — the ceiling's interval excludes zero — so
Phase 2 proceeds. Three things must be read alongside it:

1. **The median trade loses even at the ceiling.** −2.22%, selling at the exact
   peak. The positive mean is carried by a minority of trades.
2. **Capturing 75% of the maximum favourable excursion on every trade still loses**
   (−0.25%, interval spanning zero). No real system captures 75% of MFE.
3. **Every implementable fixed-target policy is worse than what actually happened.**
   The existing exit engine beats all of them, which is the third independent
   confirmation that the exit is not the problem.

**What makes Phase 2 worth doing** is that the toll is the term the ceiling is
most sensitive to. At a 2% toll instead of 3.22%, the same 75%-of-MFE exit
returns **+0.96%** rather than −0.25%. Cutting cost does not create an edge, but
it is what decides whether the edge that exists at the ceiling is reachable.

## Phase 2 — Cut the toll · Mon 17 Aug → Fri 28 Aug
*Only if Gate 1 passes. This is the half of the arithmetic we can actually move.*

- [~] **S-C** Universe by **joint** contract viability, not by spread and not by
  market cap. Instrument built: `tools/universe_quality.py`. Decision Mon 17 Aug.

  ⚠️ **The keep/drop list previously written here was wrong and is withdrawn.**
  It ranked symbols by *median spread across all contracts examined*, which
  measures the strikes the selector walks past rather than the contract it could
  have bought. On that basis ORCL was proposed for dropping. Measured on the
  joint constraint, **ORCL has the highest viable rate in the universe.**

  *Viable* = at least one contract, at the same moment, that is both **≤3%
  spread and ≤ the cost cap**. Either half alone misleads: reading spread says
  the chain was fine, reading rejection counts says open interest, and the
  binding constraint is neither.

  Second read — **4 sessions** (08-04, 08-05, 08-10, 08-11); four earlier days
  excluded because their attempts predate the `65361cb` evidence fix:

  | symbol | n | viable % | best spread | cheapest tight contract |
  | --- | --- | --- | --- | --- |
  | **NVDA** | 20 | **100%** | 0.92% | — |
  | **PLTR** | 28 | **100%** | 0.61% | — |
  | ORCL | 56 | **66%** | 1.15% | $720 |
  | TSLA | 54 | 35% | 0.90% | $1,345 |
  | CRWD | 49 | 22% | 2.08% | $1,365 |
  | MSFT / AMD / META / AVGO / MU | 22–121 | 2–9% | 1.1–2.3% | $1,400–2,445 |
  | AMAT, MRVL, PANW, SMH, TSM, ARM | 40–83 | **0%** | 1.7–3.4% | $2,020–11,625 |

  Six symbols returned **zero viable contracts across four sessions**. They
  cannot be traded by this account at any threshold setting, and no spread
  ranking would have said so. META left that group on a single viable moment
  (4%), which is noise, not a promotion.

  **The 11 Aug session moved this ranking**, and it is the first movement that
  points somewhere better than ORCL. NVDA (12→20) and PLTR (10→28) crossed the
  20-moment bar and both read **100% viable** — the two cheapest tight contracts
  of the day were theirs, at $525 and $505. Per rule 1 this is *worth testing*,
  not a keep-list: 20 and 28 moments are thin, and a rate of exactly 100% is the
  shape small samples produce most easily. It does mean the 17 Aug decision
  should not be framed as "ORCL or nothing".

  *Not yet ranked, too few moments:* GOOGL (16), NFLX (16), SPCX (14), AAPL (9),
  AMZN (6), XOM (2), JPM (1), SMCI (1), QQQ (0).

  *Open question for 17 Aug,* raised by the cap sweep: viability across the
  universe runs **19.3% at $500, 25.7% at $1,000, 49.9% at $2,000** (was 13.5 /
  20.4 / 48.6 on three sessions — the floor rose, the ceiling did not). Cost is
  clearly a lever on *availability* — but $2,000 is 40% of the account in one
  position, and a larger cap was already measured not to change the loss rate.
  Availability and sizing have to be decided together.

  *Target unchanged:* mean spread paid per trade **≤ 2.5%**, from 3.4% today.

- [ ] **S-D** Set the spread ceiling **and** the universe together, never apart.
  A 2% ceiling on a wide universe produces one trade a day (10 Aug) — and **zero
  on 11 Aug**, from a clean 113-scan session with no failures. Two sessions now
  read 1 and 0. A 3% ceiling on the S-C universe passes 46–81% of contracts.
  They are one decision.
  *Proposed:* ceiling **3**, with S-C, not before it.

  Note for Gate 2: its floor is ≥3 trades per session. The ceiling alone is not
  drifting toward that number, it is drifting away from it.

- [ ] **S-E** `OPTION_MIN_LEVERAGE=25` observe-only, then enforce if it holds live.
  Built and holdout-confirmed (+0.28%/trade, CI [+0.02, +0.62] — a marginal pass).

- [ ] **S-F** Re-derive `MIN_STOP_SPREAD_MULTIPLE`. It sits at **1.0**, which admits
  a trade whose round-trip spread eats the entire stop. Arithmetic says 3–5.

  ~~Blocked on S-A3: no archive records spread and delta together.~~
  **Not blocked — the blocker was never true after S-A3.** Measured 2026-08-11,
  every attempt carries both fields: 100% of 14,264 attempts on 08-11, 100% of
  18,775 on 08-10, 74% of 4,597 on 08-05. That is ~33k paired observations
  already sitting in `scanner_snapshot`. The note was written before S-A3 turned
  out to be complete and was never revised against it.

  **This is now the cheapest live item on the board** — offline, no quota, no
  behaviour change, and it targets the toll term directly.

### ▶ GATE 2 · Fri 28 Aug
* Mean spread paid ≤ 2.5% **and** ≥ 3 trades per session under S-C + S-D.
* **State the RR bar the new toll justifies, and measure the trade count at that
  bar** — not at 2.0. Break-even RR *is* the toll: 0.321R at 3.22% today. If the
  toll falls to 2%, the bar that a trade must clear to be worth taking falls with
  it, and that is what should release trade count. Amended 11 Aug.
* **Fail** → cost was not the binding constraint. Reassess before Phase 3.

  ⚠️ **Read the two criteria separately.** They fail independently and the
  second one is not gated by cost. RR refused more candidates than options on
  four of five sessions to 11 Aug, so the plausible bad outcome is: the toll is
  cut successfully, spread comes in under 2.5%, trade count stays at 0–1 because
  the RR bar never moved, and the gate reads *fail* on volume. The stated
  inference — "cost was not the binding constraint" — would then be wrong. Cost
  would have been cut exactly as intended and the scoreboard misread.

  This is the same error as the open-interest counterfactual of 11 Aug, one
  level up: **the loudest blocker is not the binding one.** Before concluding
  anything from Gate 2, check which gate actually refused the marginal candidate.

## Phase 3 — Attack the edge · Mon 31 Aug → Fri 18 Sep
*Only if Gate 2 passes. Mechanism first — no more feature ranking.*

Three cycles already failed on this feature set (S08a entry trigger, S08
cross-sectional rank, S09 regime conditioning). Each hypothesis below has a
documented reason to pay **before** it is tested.

- [ ] **S-G** Post-earnings drift. `earnings_calendar` holds 4,777 unused rows and a
  multi-day horizon can carry an options toll.
- [ ] **S-H** Implied vs realised volatility. Points at *selling* premium, which is
  structurally the opposite of what the app does and has a documented reason to pay.
- [ ] **S-I** Overnight vs intraday decomposition. Tests whether intraday directional
  trading was fighting the base rate from the start.

### ▶ GATE 3 · Fri 18 Sep · **this gate can answer no**
* Any hypothesis clearing **+0.155% captured move on the holdout**, interval
  excluding zero, and not one-sided long or short.
* **Fail** → no edge on this feature set. Change instrument, change universe
  properly, or stop. Agreed in advance, as before: three failed cycles is
  information, not bad luck.

---

## Rules carried from 2026-08-10, all of them earned

1. **Nothing enters a "do this" list until it has been through a holdout.**
   Grouped-data observations go in "worth testing". Four of five recommendations
   made from grouped data that day did not survive testing.
2. **Report cash, never R.** R said break-even; cash said −3.15% with an interval
   excluding zero.
3. **Drift guard on every arm.** The holdout window rose, so any long-tilted arm
   looks like an edge. It confirmed two false positives before the guard existed.
4. **Test net of costs, always.** A gross-profitable arm that loses net is worse
   than useless — it looks like progress.
5. **Pre-agree the kill criterion** before seeing results.
6. **Record a config change when it is made**, in [CONFIG_CHANGELOG.md](../CONFIG_CHANGELOG.md).
   A spread ceiling moved 6 → 2 and cost a full session to rediscover.

## Open decisions

| decision | state |
| --- | --- |
| `OPTION_MAX_SPREAD_PCT` currently **2** | Live and throttling everything to ~1 trade/day, and to 0 on 11 Aug. Holds until S-C lands; changing it alone restores volume at bad quality. |
| **Is Phase 2 worth its eleven days?** | Opened 12 Aug. Its ceiling is break-even: the candidate pool is 0.000R gross, so perfect cost reduction reaches zero and no further. Decide on **Mon 17 Aug** alongside S-C, deliberately rather than by default. |
| `daily_engine_summary.v1_trades` reports 0 | On all 7 days that had trades, 19 in total. A number that says 0 when it means 19 sits in a table something may later trust. Fix or delete the join. |
| ~~`avg_exit_confidence` stopped after 07-31~~ **CLOSED 2026-08-22** | Cause: the 5th argument to `build_daily_learning_summary` was switched from `entry_exit_v2_shadow.csv` to the decision-waterfall source so `blocking_stages` would have a `stage` column. The confidence went with it — `decision_waterfall` has no `v2_exit_confidence_score`, so the mean read a column that was never there. Now taken from `paper_trades.payload->>'last_exit_confidence_score'`, the **live** engine's score rather than the V2 shadow's. 12 days backfilled by `tools/backfill_exit_confidence.py`; 07-31 deliberately left holding the old metric. |
| V2 has never been scored | `trades_compared` is 0.0 on all 15 shadow days and its own promotion engine reads `INSUFFICIENT_SAMPLE`. It is markedly more permissive than V1 — 17 shadow trades on the day V1 took none. Now testable against the 239 resolved outcomes; still untested. |
| Reports are dormant, not live | Two artifacts in six weeks (one validation 07-06, one post-market 07-30), generated by a dashboard button while `Project_state.md` describes them as live infrastructure. Decide whether they are dormant or dead. |
| ~~Local `.env` held `OPTION_MAX_SPREAD_PCT=6`~~ | **Closed 11 Aug** — corrected to 2. It had produced a false "45 recoverable contracts" that day. All 15 threshold keys were then diffed against `scanner_runs.payload->config`; this was the only drift. Re-run that diff whenever a local result surprises you. |
| ~~Six commits on `Claude_POA`~~ | **Closed** — merged to `main` in PR #139 and live. The proof is S-A1 recording config for 113 runs on 11 Aug, which undeployed code cannot do. |
| ~~Credentials exported 2026-08-10~~ | **Closed 11 Aug** — rotated. |

## Timeline at a glance

| phase | dates | can fail? |
| --- | --- | --- |
| 0 — see | Mon 10 – Fri 14 Aug | no |
| **Gate 1 — ceiling test** | **Fri 14 Aug** | **yes** |
| 2 — cut the toll | Mon 17 – Fri 28 Aug | no |
| **Gate 2 — cost** | **Fri 28 Aug** | yes |
| 3 — edge research | Mon 31 Aug – Fri 18 Sep | **yes** |
| **Gate 3 — edge** | **Fri 18 Sep** | **yes** |

*Every weekday above was checked against a calendar. Mon 7 Sep is Labor Day and
carries no session; Phase 3 is scheduled around it.*

## Log

One entry per working day. A day with no movement says so — a silent gap is
indistinguishable from a day nobody looked.

### Mon 10 Aug
Gate 1 of DELIVERY_PLAN answered **no**: S08a, S08 and S09 all rejected on the
holdout. Cause of the trade collapse established — `OPTION_MAX_SPREAD_PCT` moved
6 → 2 between 5 and 10 Aug, capping accepted contracts at exactly 2.00% and
cutting acceptances 122 → 24. Two sessions (6 and 7 Aug) lost to a worker outage.
Shipped: gate audit records enforced thresholds, incremental activity trace,
leverage recorder, config snapshot, config changelog. This plan opened.

### Tue 11 Aug
A clean session that took no trade: 113 scans, 0 failures, health 95–100,
2,938 rows over 26 symbols, **0 entries** and nothing held overnight. Nobody
missed a signal — subscribers have been paused since 8 Aug.

S-A1 produced its first data (above). The 10 Aug constraint finding was
confirmed by a second and independent method: relaxing the OI floor to 250, or
to 100, recovers **zero** tradeable contracts, because the liquidity filter
short-circuits and OI is its first gate. Cost is last, so its 558 refusals are
exact. `tools/option_rejection_report.py` was fixed to report joint-tested
recovery — its previous output would have justified an OI change worth nothing.

S-C gained a 4th session and its ranking moved for the first time: NVDA and
PLTR crossed the sample bar at 100% viable. Universe viability at the $500 cap
rose 13.5% → 19.3%. Six symbols remain at 0% across all four sessions.

S-F found to be unblocked and mis-labelled since 10 Aug; ~33k paired
spread/delta observations were already in hand. It is the next thing to do.

Closed today: the six `Claude_POA` commits were merged to `main` in PR #139 and
are live — **today's session is itself the proof**, since S-A1 could not have
recorded config for 113 runs on undeployed code. Credentials rotated. The local
`.env` spread ceiling corrected 6 → 2, closing the analysis trap the same day it
was found.

Two findings that change the aim, both above: **RR, not cost, refuses the final
step** on four of five sessions, and **no outcome exists for any candidate that
was never entered**, which makes every loosening proposal a forward experiment
rather than a backtest. Gate 2 amended to state its RR bar and to read its two
criteria separately. S-A4 opened, so Phase 0 is *not* complete after all —
correcting the claim made earlier in this entry.

Asked whether to drop the RR bar 2.0 → 1.8. **No, not yet, and not blind** — the
reasoning is under S-F/S-E; the short version is that Gate 1 already measured
lower targets as monotonically worse, and the bar should fall out of a reduced
toll rather than be set by hand ahead of it. Shadow it once S-A4 lands.

### Wed 12 Aug
S-A4 closed the day after it opened, and it changed the question. Refused
candidates are now replayed against the bars that followed — 239 resolved, 70
target-first — and the pool turns out to be **0.000R gross**, with every gate
blocking winners at the base rate. The finding is above; the short version is
that nothing here discriminates, and Phase 2's ceiling is break-even.

Two measurement defects fell out of it, both the same error at different depths:
`learning_engine` scored rules by counting unresolved candidates as prevented
losses, and zero coverage printed identically whether a rule had never been
replayed or could never be replayed at all. Both fixed; `LOW_RR` no longer ranks
first for having blocked a thousand things nobody measured.

Resolution now runs nightly from the scan loop rather than from a command
someone has to remember. **This is the daily measurement from 13 Aug onward** —
see the header. Still open: the `v1_trades` join, the exit-confidence stop, V2
never having been scored, and whether Phase 2 earns its eleven days.

Not done today: nothing deployed — four commits sit unpushed on `Claude_POA`.
