# Gate visibility plan

**Status: designed, not built. Revisit week of 2026-08-24.**

A set of screens that answer, for any given day: which gates a candidate crossed,
which one stopped it, on what number, against what threshold — and for the ones
that were stopped, what would have happened if they hadn't been.

Written 2026-08-19. The audit of what the ledger currently records (§3) was run
against live data the same day and is the part that decides what is buildable.

---

## 1. Why this is not just "add a chart"

The book's central problem is that the direction carries information and the
trades still lose money. Every lever proposed against that has been argued from
aggregates — block counts, win rates, mean R — and four of those arguments turned
out to be artefacts of *how the blocking was recorded* rather than facts about the
market:

* **2026-07-29** — the Telegram rule claimed to have blocked all 884 rows,
  including the one trade that opened. Fixed by `resolve_blocked_trade`, which
  refuses to let an operational outcome count as an entry gate.
* **2026-08-03** — 2,913 fabricated `Option Quality` failures against 77 real
  evaluations, because a candidate that never got a contract priced defaulted to
  quality 0.0 and was recorded as failing. That is why the option rules are
  emitted conditionally today (§3.2).
* **2026-08-13** — "regime escalation is the constraint", from counting rows
  instead of symbols. The same name is re-scanned every 5 minutes, so row counts
  inflate the funnel roughly 40x.
* **2026-08-15** — `avoid_chasing` was believed to be the entry boundary. It is
  protective: the trades it blocks lose ~3x the book average.

So the requirement is not "show the funnel". It is **show the funnel in a way that
cannot produce a fifth one of these.**

---

## 2. The four screens

### 2.1 Daily funnel — Validation page

A staircase, one step per gate, showing how many *symbols* survived each.

```
26  symbols scanned
 7  had a direction              19 no signal
 5  passed setup score >= 62      2 scored 54, 58
 4  passed risk:reward >= 1.8     1 at 1.6
 1  found a buyable contract      3 blocked   <-- the wall
 1  ENTERED
```

Names the survivors and the casualties by symbol, not just counts. **Counts
distinct symbols, never rows.**

### 2.2 The boarding pass — Validation page

One row per candidate, one column per gate, showing the actual value against the
threshold in force that day.

```
        setup   RR     spread   cost     OI      DTE   chase  ->  verdict
AMAT    71 ok   2.1 ok  1.4% ok  $780 ok  1.2k ok 12 ok  ok   ->  ENTERED
AMD     68 ok   1.9 ok  3.4% X   $410 ok   900 ok  9 ok  ok   ->  blocked: spread
PLTR    58 X    2.2 ok  2.1% X   $290 ok    4k ok  7 ok  X    ->  blocked: setup, spread, chase
```

Two display rules carry the whole point of the screen:

* A cell shows the number **and** the threshold, so nobody has to read `.env` to
  interpret a red mark.
* A gate that was never evaluated renders as a **grey dash, never a red X**. Those
  are different facts. See §3.2 — this is the 2026-08-03 failure mode.

The verdict column must distinguish **sole blocker** from **one of several**.
Loosening a threshold only gains the trades where it was the *only* thing in the
way, and that distinction is the difference between a useful screen and a
generator of bad ideas.

### 2.3 The counterfactual ladder — Research page

For each blocked candidate, three rows, in this order:

```
MRVL  blocked: contract cost $1,340 vs $1,000 cap   (sole blocker)
      underlying moved      : +1.4% in our direction   signal was right
      the option would have : -6.2%                    spread + theta ate it
      after exit rules      : -4.1%                 <-- the only quotable number
```

The middle row is what stops this feature becoming a machine for regret. The signal
is real and worth roughly a third of what the options cost, so a counterfactual
that stops at the underlying will recommend loosening every gate, forever. **A
"would have helped" figure that has not been priced on the actual option and
replayed through the exit engine does not get displayed.**

### 2.4 The gate price tag — Research page

The screen that actually drives decisions. Per gate, over a trailing window:

```
gate             blocked  sole blocker   those trades would have returned
spread <= 2%         64        21         -2.8%   earning its keep
cost <= $1,000       58        31         -4.1%   earning its keep
setup >= 62          40         9         -0.4%   barely doing anything
avoid_chasing        19        19         -3.8%   protective, leave alone
RR >= 1.8            22         4         +0.9%   costing money
```

Green blocks losers, red blocks winners. Every figure carries a bootstrap CI and a
mean-with-the-top-5-stripped; small samples in this book have reversed on more data
more than once (see the stop-floor hypothesis, decisive on 12 trades and dead on
310).

---

## 3. What the ledger records today

Audited 2026-08-19 against `decision_waterfall` for 2026-08-18 (23 symbols, 2,185
evaluations). **The entry gates do not short-circuit.** That was the assumption
going in and it is wrong, in the helpful direction.

### 3.1 Always recorded — full vector

`build_entry_gate_rule_evaluations` (`app/gates/entry_gate.py:1185-1189`) emits
these unconditionally for every candidate on every scan, pass and fail alike:

| rule | rows | symbols | passed | failed |
|---|---|---|---|---|
| `Setup` | 2,185 | 23 | 20 | 2,165 |
| `RR` | 2,185 | 23 | 293 | 1,892 |
| `Affordability` | 2,185 | 23 | 2,185 | 0 |

The audit is built from an already-scored scanner row — a post-hoc read of every
value, not a sequential evaluation that stops at the first failure. **Screens 2.1,
2.2 and 2.4 are buildable on these today with no write-path change.**

Two observations from the table: `Affordability` has never blocked anything, and
`Setup` fails 99.1% of evaluations while the funnel still delivers trades — which
is only possible because contract pricing is not gated behind it (§3.4).

### 3.2 Conditionally recorded — absent, not failed

`Option Quality`, `Option Spread` and `Quote Freshness` are emitted only when a
contract was actually priced (`entry_gate.py:1200-1205`): **245 rows, 17 symbols** —
11% of the 2,185 evaluations. Six of 23 symbols got no option rule at all.

The conditionality is deliberate and must be preserved. Filling these in defaulted
option quality to 0.0 and produced the 2,913 fabricated failures of 2026-08-03,
which is how `Option Quality` came to head the rule tables every day while costing
nothing.

Consequence for the design: option columns render blank for ~89% of rows, and
**sole-blocker analysis is not possible for the option gates** on candidates that
never reached pricing.

### 3.3 Not recorded at all — the layer that matters most

The gates that actually stop this book — **open interest, volume, contract cost,
DTE** — are not in `decision_waterfall`. They live in
`scanner_snapshot.decision_payload` as JSON, read only by
`tools/option_rejection_report.py`.

That path genuinely short-circuits: **OI is tested first, cost last.** So a cost
failure is invisible whenever OI failed first, and only the cost table in that tool
can be quoted as-is. This is the layer where the funnel breaks — the 2026-08-15
measurement puts it at 77 of 2,990 candidates ever getting priced, a 7.7% fill rate
that two independent A/B arms agreed on.

### 3.4 The useful surprise

`Setup` passed only 20 of 2,185 evaluations, yet 245 evaluations still priced a
contract. **Contract pricing is not gated behind Setup passing.** The two run more
independently than a funnel diagram implies, which means the archive already holds
option data on candidates that failed Setup — exactly the overlap needed to test
whether those gates are redundant or additive. That test needs no new data.

---

## 4. Build order

| screen | data status |
|---|---|
| 2.1 daily funnel, entry gates | buildable now |
| 2.2 boarding pass, entry-gate columns + sole blocker | buildable now |
| 2.2 boarding pass, option columns | buildable, blank for ~89% |
| 2.4 gate price tag, entry gates | buildable now |
| 2.3 counterfactual ladder | needs option pricing + exit replay per candidate |
| any sole-blocker claim about option gates | **needs a write-path change** |
| contract-selection breakdown (OI/volume/cost/DTE) | **needs a write-path change** |

**Start with the entry gates.** The write-path change is needed only for contract
selection, and it is the narrow one of recording every liquidity check instead of
returning at the first failure. Recording a month of data before making that change
does not waste the month — the entry-gate half is already complete and correct.

---

## 5. Rules any implementation must follow

1. **Count symbols, not rows.** Row counts inflate the funnel ~40x.
2. **Never evaluated is not failed.** Grey dash, not a red X.
3. **Sole blocker is the number that drives decisions**, not total blocked.
4. **No counterfactual without the option price and the exit replay.** The
   underlying moving the right way is not a trade that would have made money.
5. **Cash beside R, always.** R has flattered this book repeatedly — 291 trades at
   +0.01R were -3.1% in premium.
6. **Bootstrap CI and mean-without-top-5 beside every mean.**
7. **Operational outcomes are not entry gates.** Telegram, Paper and Review
   describe what happened after the decision; `resolve_blocked_trade` enforces this
   and it exists because of 2026-07-29.
