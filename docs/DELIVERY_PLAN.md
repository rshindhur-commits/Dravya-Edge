# Delivery plan — every step from here to subscribers

> **Superseded on 2026-08-10 by [OPTIONS_QUALITY_PLAN.md](OPTIONS_QUALITY_PLAN.md).**
> Gate 1 (S11) was reached four weeks early and **answered no**: all three
> research cycles were rejected on the holdout. Track work in the new plan.
>
> This file is kept as the record of what was tried and why, which is the reason
> the new plan looks the way it does. Steps below are not being worked.

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

### ▶ GATE 1 · reached Mon 10 Aug, four weeks early — **ANSWERED NO**
- [x] **S11** Does any candidate reach **≥ +0.155% captured move per trade on the holdout split**, with the comparison count recorded?
  * **Answer: no.** Five arms in `research/comparisons.jsonl`. S08a (remove the
    entry trigger) was better on train at every horizon and worse on holdout at
    every horizon — the overfit shape. S08 (cross-sectional rank) and S09 (regime
    conditioning on SPY/QQQ) mostly preferred *no filter at all* on train, so the
    features do not separate winners from losers even in sample. Where a threshold
    was chosen the holdout delta was +0.02% to +0.06% with an interval spanning
    zero. Both arms miss the bar at every tradeable horizon, with the trigger or
    without it.
  * **S10 was not run.** The pre-agreed rule below is explicit that three failed
    cycles is not a reason for a fourth on the same feature set.
  * *Note:* one arm returned CONFIRMED at 234 bars and was spurious — holdout longs
    paid +1.48% and shorts −0.15%, market drift rather than signal. A drift guard
    now downgrades that to `DRIFT_NOT_EDGE`; 234 bars is also ~3 sessions, which
    285 of 291 archived trades could never hold.
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

## Daily log

One line a working day: what moved, what is blocked, on or behind timeline.
Newest first. A day with no movement gets an entry saying so — a silent gap is
indistinguishable from a day nobody looked.

### Mon 10 Aug — **Gate 1 answered no; this plan is superseded**
The August dates in this file were wrong by a day — "Mon 11 Aug" is a Tuesday —
so Phase 0 ran today, not tomorrow.

* **S01 failed.** `rss_mb` is not flat: 231 → 752 MB across the session,
  +23 MB/h premarket rising to +63 MB/h in session. The instrumentation works;
  the answer it gives is "not fixed".
* **S02 passed.** 115 scans, 04:06 → 16:16 ET, zero failures, zero exceptions,
  no gaps, no orphaned positions. `tools/daily_report.py`: "nothing to flag".
* **S03 was unanswerable as written** and is now answerable. `required_value` on
  the spread rule recorded `EntryGateConfig` defaults, not the deployed bar —
  50 of 50 rows at 10.0 against a configuration saying 6. Fixed in `d2fb9c6`.
* **S08a, S08, S09 all rejected.** See Gate 1 above.
* **Cause of the trade collapse found**, and it was not a code change:
  `OPTION_MAX_SPREAD_PCT` moved 6 → 2 in Render between 5 and 10 Aug, capping
  every accepted contract at exactly 2.00% and cutting acceptances 122 → 24.
  Recorded in [CONFIG_CHANGELOG.md](../CONFIG_CHANGELOG.md).
* **Two sessions lost.** 6 and 7 Aug have no rows in any table; the worker was
  down and no deploy occurred in that window.

Work continues in [OPTIONS_QUALITY_PLAN.md](OPTIONS_QUALITY_PLAN.md).

### Sun 9 Aug — **on timeline**
Pushed and deployed (branch `Claude_POA`, merged to `main`). **Production
behaviour is unchanged** — every flag added this weekend is inert at its default
and the research package is not imported by the scanner.

* **Done:** S04 (dataset, 5,590 rows, 100% label coverage), S05 (out-of-sample
  framework, split fixed and committed before anything was evaluated), S05a
  (null model).
* **Found:** entry timing is **worse than random** by 0.12–0.31 points, 20/20
  draws at every horizon, train and holdout agreeing within 0.06. See
  TRADE_QUALITY_PLAN §2.2h.
* **Consequence:** a Cycle 0 is added ahead of S08 — *remove* the entry trigger
  and keep the direction call. If the trigger costs a quarter of a point,
  deleting it is worth more than any feature currently planned, and the harness
  can already answer it.
* **Withdrawn:** the "+14.43% captured, signal works" claim from earlier the
  same day. It was 5 trades of 331.

- [ ] **S08a** Cycle 0 — does removing the entry trigger beat keeping it? *(added 9 Aug, runs first in Phase 1)*

---

## Deferred — revisit when the data grows

Not blocking anything today. Recorded so they are found by looking rather than by
hitting them.

| item | trigger to act | why it can wait |
|---|---|---|
| Index `paper_trades.closed_at` | ~a few thousand closed trades | The Validation page filters every window read on `closed_at` and there is no index, so it is a sequential scan. 42 rows today; the read is 211 ms and all of it is the Neon round trip, not the scan. |
| Index `scanner_runs.started_at` | ~50k runs, or if the config panel slows | The config-change panel range-scans it. 1,204 rows today, and the result is cached for 15 minutes, so it runs at most ~4×/hour. Lower priority than the above. |
| Per-row `.iloc` in `build_spread_calibration` | if a 90-day window gets slow | Row-at-a-time `.iloc` over the priced trades. Measured 60 ms per render at 38 trades, 272 ms at 1,000 — acceptable on a page that redraws every 5 minutes, so not worth the risk of rewriting working analytics yet. |

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
