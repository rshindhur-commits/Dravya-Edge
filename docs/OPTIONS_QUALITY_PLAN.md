# Options quality plan — the active tracked plan

**Goal:** an options algo that selects trades worth their cost. Not more trades.

Supersedes [DELIVERY_PLAN.md](DELIVERY_PLAN.md) as the live plan on 2026-08-10,
after its Gate 1 answered **no**. That file stays as the record of what was
tried; this one is what is being tried now.

**Status key:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
· `[-]` dropped, with a reason

**Last updated:** 2026-08-10

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

  First read — 3 sessions (08-04, 08-05, 08-10); four earlier days excluded
  because their attempts predate the `65361cb` evidence fix:

  | symbol | n | viable % | best spread | cheapest tight contract |
  | --- | --- | --- | --- | --- |
  | ORCL | 46 | **65%** | 1.15% | $912 |
  | TSLA | 46 | 24% | 0.89% | $1,345 |
  | CRWD | 25 | 12% | 1.88% | $850 |
  | AVGO / MSFT / AMD / MU | 21–90 | 2–5% | 1.2–2.4% | $1,400–2,445 |
  | AMAT, META, MRVL, PANW, SMH, TSM, ARM | 20–72 | **0%** | 1.1–3.4% | $1,825–12,715 |

  Seven symbols returned **zero viable contracts across three sessions**. They
  cannot be traded by this account at any threshold setting, and no spread
  ranking would have said so.

  *Not yet ranked, too few moments:* NVDA (12), GOOGL (16), PLTR (10), AAPL (9),
  SPCX (4), NFLX (2), AMZN (2), QQQ (0) — which is most of the old keep-list.

  *Open question for 17 Aug,* raised by the cap sweep: viability across the
  universe runs 13.5% at $500, 20.4% at $1,000, 48.6% at $2,000. Cost is
  clearly a lever on *availability* — but $2,000 is 40% of the account in one
  position, and a larger cap was already measured not to change the loss rate.
  Availability and sizing have to be decided together.

  *Target unchanged:* mean spread paid per trade **≤ 2.5%**, from 3.4% today.

- [ ] **S-D** Set the spread ceiling **and** the universe together, never apart.
  A 2% ceiling on a wide universe produces one trade a day (10 Aug). A 3% ceiling
  on the S-C universe passes 46–81% of contracts. They are one decision.
  *Proposed:* ceiling **3**, with S-C, not before it.

- [ ] **S-E** `OPTION_MIN_LEVERAGE=25` observe-only, then enforce if it holds live.
  Built and holdout-confirmed (+0.28%/trade, CI [+0.02, +0.62] — a marginal pass).

- [ ] **S-F** Re-derive `MIN_STOP_SPREAD_MULTIPLE`. It sits at **1.0**, which admits
  a trade whose round-trip spread eats the entire stop. Arithmetic says 3–5.
  Blocked on S-A3: no archive records spread and delta together.

### ▶ GATE 2 · Fri 28 Aug
* Mean spread paid ≤ 2.5% **and** ≥ 3 trades per session under S-C + S-D.
* **Fail** → cost was not the binding constraint. Reassess before Phase 3.

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
| `OPTION_MAX_SPREAD_PCT` currently **2** | Live and throttling everything to ~1 trade/day. Holds until S-C lands; changing it alone restores volume at bad quality. |
| Six commits on `Claude_POA` | Unpushed. Nothing recorded until deployed. |
| Credentials exported 2026-08-10 | Rotate — Neon, Polygon, OpenAI, Telegram, AlphaVantage. |

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
