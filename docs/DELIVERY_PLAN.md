# Delivery plan — every step from here to subscribers

Single tracked checklist. The last step is adding subscribers back to the
Telegram channel. Reasoning behind the phases is in [ROADMAP.md](ROADMAP.md);
this file is state — tick a box only when evidence says so, and name the
evidence.

**Status key:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
· `[-]` dropped, with a reason

**Last updated:** 2026-08-09

---

## Read this before reading the list

This list is **exhaustive in the steps we control**. It is **not a guarantee of
the outcome**, and the difference is the whole project:

* Steps S01–S07, S12–S27 are engineering. They will be completed.
* Steps S08–S10 are *research*. They may all fail, and failing is a legitimate
  result, not a sign the work was done badly.
* **Gate 1 (S11) can answer no.** If it does, no later step rescues it — an app
  cannot alert its way to an edge that is not in the data. The branch is written
  into the plan rather than left as an unpleasant surprise.

Measured position as of 2026-08-09: captured move is **−0.012% ± 0.037%** per
trade against a **+0.155%** break-even. Everything before Gate 1 exists to find
out whether that number can be moved.

---

## Phase 0 — Harness · Mon 11 Aug → Fri 15 Aug
*No production behaviour changes. No API quota.*

- [ ] **S01** Verify `rss_mb` series across Mon 11 Aug session — flat means the memory leak is fixed
  *Evidence:* `scanner_runs.payload` series for the session
- [ ] **S02** Verify capture completeness for Mon 11 Aug — no missing scans, no early stop, no orphaned positions
  *Evidence:* `tools/daily_report.py` integrity section
- [ ] **S03** Verify the spread ceiling reached the worker
  *Evidence:* `required_value` on the spread rule equals the deployed value
- [x] **S04** Build the feature research dataset — indicator snapshot at decision time joined to forward outcome
  *Evidence:* `research/candidates_21day.json`, 5,590 rows, 38 feature columns, zero missing, **100% label coverage** after `tools/label_candidates.py` (first cut covered 14% — only the rows the current rules already select)
- [x] **S05** Build the out-of-sample framework — fixed session split, declared arms, comparison ledger, and a report that refuses to call a winner unconfirmed on holdout
  *Evidence:* `app/research/holdout.py`, 11 tests; split committed (train 07-06→07-23, holdout 07-24→08-03) before anything was evaluated; overfit and includes-zero cases both refused
- [x] **S05a** Build the null model — random-timing benchmark holding symbol, session and direction constant
  *Evidence:* `tools/null_model.py`; comparison ledger entry 1
- [ ] **S06** Fix duplicate close alerts (display-only, long deferred)
- [ ] **S07** Stamp a signal version on every emitted signal, persisted with the trade

## Phase 1 — Edge research · Mon 18 Aug → Fri 4 Sep
*Three cycles. Each: state the hypothesis, fit on train, confirm on holdout, log
the comparison. No quota.*

- [ ] **S08** Cycle 1 — cross-sectional relative strength (rank the universe against itself; today every rule is single-symbol)
- [ ] **S09** Cycle 2 — regime conditioning on SMH/XLK/XLF/XLE/VIXY (already fetched, barely used; `market_regime` today comes from the symbol's own bars)
- [ ] **S10** Cycle 3 — event exclusion and time-of-day as measured factors (neither is a factor at all today)

### ▶ GATE 1 · Mon 7 Sep — **this gate can answer no**
- [ ] **S11** Does any candidate reach **≥ +0.155% captured move per trade on the holdout split**, with the comparison count recorded?
  * **Pass** → S12.
  * **Fail** → stop research. Decide between: change the instrument (shares need ~2bp of edge, not 6bp), change the universe (26 megacaps is the hardest possible pond), or stop. **Agreed in advance: three failed cycles is information, not bad luck, and not a reason for a fourth on the same feature set.**

## Phase 2 — Priced validation · Mon 7 Sep → Fri 18 Sep
*Only if S11 passes. **First quota spend in this plan** — approval required.*

- [ ] **S12** Priced replay of the winning configuration with real option quotes (~1–2.5h of Polygon calls)
- [ ] **S13** Confirm the edge survives real contracts, spreads and theta end-to-end rather than the fitted model
- [ ] **S14** Contract selection review — DTE, moneyness and cost distribution a person would defend

## Phase 3 — Pre-live hardening · Mon 21 Sep → Fri 2 Oct
*Required before anything is shown to a subscriber, regardless of edge.*

- [ ] **S15** Circuit breaker — halt alerting on a measured condition (consecutive losses, capture failure, performance outside the replayed envelope)
- [ ] **S16** Manual kill switch that does not require a deploy
- [ ] **S17** Weekly performance report built from database truth, not from alert history
- [ ] **S18** Alert delivery review — entry persistence guard is done; confirm the exit path cannot alert what it cannot persist

## Phase 3b — Paper forward · Mon 21 Sep → Fri 16 Oct
*~20 live sessions. The only test that cannot be replayed.*

- [ ] **S19** Run 20 live paper sessions on the validated config, no subscribers, circuit breaker armed
- [ ] **S20** Weekly capture-integrity check, each Monday
- [ ] **S21** Compare live against the replayed envelope, per week

### ▶ GATE 2 · Mon 19 Oct — **this gate can answer no**
- [ ] **S22** Does live paper match the replayed envelope, with complete capture for every session?
  * **Fail** → the harness disagrees with the worker. Fix and re-run Phase 3b; do not proceed on a disagreement.

## Phase 4 — Subscriber readiness · from Mon 19 Oct

- [ ] **S23** Subscriber-facing performance transparency — what they see, and it must reconcile to the database
- [ ] **S24** Expectations and risk disclosure — win rate, average loss, drawdown, what the signals are and are not
- [ ] **S25** Alert format review — entry, stop, target, contract and sizing precise enough to act on without interpretation
- [ ] **S26** Dry run to a private test channel for 5 sessions

### ▶ GATE 3 · final go/no-go
- [ ] **S27** All gates passed, hardening live, dry run clean

## ▶ The last step
- [ ] **S28** **Add subscribers back to the Telegram channel**

---

## Branch points, stated in advance

| if | then |
|---|---|
| Gate 1 fails (S11) | Stop research on this feature set. Choose: different instrument, different universe, or stop. Early September, on evidence. |
| Gate 2 fails (S22) | Harness disagrees with the worker. Fix, re-run Phase 3b. Never ship a disagreement. |
| Any capture check fails | That session is not evidence. Fix capture before drawing any conclusion from the period. |

## Timeline at a glance

| phase | dates | can fail? |
|---|---|---|
| 0 — harness | 11–15 Aug | no |
| 1 — edge research | 18 Aug – 4 Sep | **yes** |
| **Gate 1** | **7 Sep** | **yes** |
| 2 — priced validation | 7–18 Sep | yes |
| 3 — hardening | 21 Sep – 2 Oct | no |
| 3b — paper forward | 21 Sep – 16 Oct | yes |
| **Gate 2** | **19 Oct** | **yes** |
| 4 — subscriber readiness | from 19 Oct | no |
| **S28 subscribers back** | **~19–23 Oct**, if every gate passed | — |
