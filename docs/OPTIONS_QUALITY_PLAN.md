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
- [ ] **S-A3** Capture full chain quality per candidate — best available spread, OI,
  delta and cost, not only the contract selected
  *Done when:* a session can answer "what was the best contract available for NVDA
  at 10:15, and why was it not taken?"

## Phase 1 — The ceiling test · Fri 14 Aug · **this gate can answer no**
*Offline, existing data, no quota.*

- [ ] **S-B** Perfect-foresight replay. Exit all 291 archived trades at their actual
  MFE — the best price ever available — net of real spread and theta. Then the same
  at fixed targets, taking the stop whenever MFE and MAE both cleared, because
  intrabar order is unknowable and assuming otherwise manufactures the edge.

### ▶ GATE 1 · Fri 14 Aug
* **Pass** (a perfect-exit book profits after costs) → Phase 2. The gap between
  ceiling and actual is what exit and entry work can address.
* **Fail** → **stop.** If the best exit that ever existed still loses, no entry
  rule, exit rule or threshold rescues it, and options are the wrong instrument
  for this signal at this horizon.

## Phase 2 — Cut the toll · Mon 17 Aug → Fri 28 Aug
*Only if Gate 1 passes. This is the half of the arithmetic we can actually move.*

- [ ] **S-C** Universe by option quality, not market cap. Median spread over 21
  sessions, measured 2026-08-10, spans **14×** across the current 26:

  | keep | median spread | | drop | median spread |
  | --- | --- | --- | --- | --- |
  | TSLA | 1.87% | | AMD | 10.63% |
  | QQQ | 1.92% | | AVGO | 12.50% |
  | NVDA | 2.13% | | AMAT | 15.18% |
  | NFLX | 2.72% | | PANW | 18.23% |
  | AMZN | 3.10% | | SMH | 26.41% |

  *Target:* mean spread paid per trade **≤ 2.5%**, from 3.4% today.
  *Side effect:* roughly half the option-chain API work disappears.

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
