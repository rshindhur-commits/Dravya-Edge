# Configuration changelog

Every change to a **runtime setting** that affects trading, on any host. Not code
changes — those are in git. This file exists for the settings git cannot see.

## Why this file exists

On 2026-08-10 a full session was spent working out why the app had stopped
taking trades. The answer was that `OPTION_MAX_SPREAD_PCT` had been changed from
6 to 2 in Render five days earlier, on a recommendation from this project's
assistant, and nothing anywhere recorded it. The database showed the effect —
every accepted contract capped at exactly 2.00% spread — but not the cause, and
the code showed neither, because the value lives in Render and Render is not in
git.

The investigation cost hours and reached the right answer only after the Render
environment was exported by hand. With this file it would have been one lookup.

**A recommendation is not a record.** Anyone changing a setting writes it here,
in the same sitting, whoever suggested it.

## Rules

1. **Never commit a secret.** API keys, database URLs, bot tokens and passwords
   do not belong here. Trading thresholds do.
2. Record the **old value**. "Set X to 2" is half a record; the question later is
   always what it was before.
3. Record **who asked and why**, with the evidence if there was any. A number
   with no reason cannot be reviewed, only guessed at.
4. Record the **measured effect** once a session has run under it. That is the
   line that tells the next reader whether the change worked.
5. Newest first.

---

## 2026-08-10

### `OPTION_MIN_LEVERAGE` — introduced, default 0 (off)

| | |
| --- | --- |
| Host | code default only, not set in Render |
| Old | did not exist |
| New | `0.0` (disabled; records the figure without blocking) |
| Asked by | assistant |
| Why | Leverage — underlying price over premium — was never measured. On the 291-trade archive, contracts under 20x returned −5.10% of premium with a 3% win rate over 31 trades. |
| Status | Ships off. `tools/gate_ab.py` confirmed a floor of **25x** on the holdout (+0.28% per trade, CI [+0.02, +0.62]) — a marginal pass. Not enabled. |

---

## Between 2026-08-05 close and 2026-08-10 open

The exact date is not recoverable — Render does not expose environment history,
and the two sessions in between (06 and 07 Aug) recorded nothing because the
worker was down. Both changes below were applied in this window.

### `OPTION_MAX_SPREAD_PCT` — 6 → 2 ⚠️ **the cause of the trade-count collapse**

| | |
| --- | --- |
| Host | Render (worker) |
| Old | `6` |
| New | `2` |
| Asked by | assistant, 2026-08-09 |
| Applied by | operator |
| Why | Round-trip spread was measured as the dominant cost: ~3.4% of premium against a target move worth ~0.5% of the underlying. `docs/TRADE_QUALITY_PLAN.md` (commit `b214157`) proposed tightening the ceiling to **3**. It was set to **2**, tighter than the analysis proposed. |

**Measured effect** — maximum spread of any contract the filter accepted:

```
2026-08-03    77 accepted    max 5.68%
2026-08-04   122 accepted    max 5.88%
2026-08-05    26 accepted    max 5.83%
2026-08-10    24 accepted    max 2.00%   <- the new ceiling, fingerprinted
```

Contracts accepted fell 122 → 24. Roughly 11% of quoted contracts are inside a
2% spread on a typical day (range 3.8%–21.6%), so this is a severe constraint:
expect on the order of one trade a day.

It did **not** make trades worse — 2026-08-10 produced a single trade, which
carries no statistical content, and a tighter ceiling should improve per-trade
economics. It made the app trade far less.

**Open decision.** 2 was probably not intended; the analysis said 3 and the
prior was 6. Choose deliberately and record it here.

### `OPTION_MAX_CONTRACT_COST` — 1200 → 500

| | |
| --- | --- |
| Host | Render (worker) |
| Old | `1200` |
| New | `500` |
| Asked by | assistant |
| Applied by | operator |
| Why | Over 601 archived trades no profitable subset existed at any cap; the cap changed the size of the stake and not the rate of loss, so the smaller stake was preferred. |
| Measured effect | As predicted, no change to win rate. `tools/gate_ab.py` later tested cost floors on the holdout and found the improvement **not distinguishable from zero** (CI [−0.65, +1.58]). Not a lever in either direction. |

---

## Settings believed unchanged, recorded as a baseline

Captured from Render on 2026-08-10. Trading-relevant only; secrets omitted
deliberately. Anything not listed here was not reviewed.

```
OPTION_MIN_OPEN_INTEREST=500     OPTION_MIN_VOLUME=100
OPTION_MIN_QUALITY_SCORE=65      OPTION_MIN_CONTRACT_COST=100
OPTION_PREFERRED_MAX_CONTRACT_COST=400
OPTION_MIN_DTE=5                 OPTION_MAX_DTE=30
OPTION_ALLOW_0DTE=false          OPTION_ALLOW_1DTE=false
MIN_STOP_SPREAD_MULTIPLE=1.0     STOP_VIABILITY_ENFORCE=true
MIN_STOP_DISTANCE_PCT=0.5        MAX_DAILY_ENTRIES=5
AUTO_PAPER_MIN_RR=1.8            AUTO_PAPER_MIN_SETUP=62
AUTO_PAPER_MAX_CANDIDATE_RANK=5  MAX_TRADES_PER_SYMBOL_PER_DAY=2
DAILY_START_CAPITAL=5000         RISK_PERCENT=10
SCAN_ENGINE_OWNER=worker         AUTO_PAPER_ENABLED=true
```

Two worth noting, both **absent from Render** and therefore running on their
code defaults:

* `SCANNER_GATE_MIN_RR` → **2.0**
* `SCANNER_GATE_MIN_SETUP` → **70.0**

Absent settings are still settings. A value nobody chose is the same defect that
made the spread ceiling unreadable for a week.

---

## The permanent fix

From 2026-08-10 every scan writes the thresholds it actually enforced into
`scanner_runs.payload` under `config` — see `app/runtime/config_snapshot.py`.
That makes the database self-documenting: "what was the spread ceiling on
2026-08-04" becomes a query rather than an investigation, and it does not depend
on anyone remembering to update this file.

This file is still the place for **why**. The payload records what; only a
person records the reasoning.

## 2026-08-16 — `EXIT_MOMENTUM_ENABLED` → false ⚠️ **load-bearing; the day's exit work does nothing without it**

| | |
| --- | --- |
| Host | Render (worker) |
| Old | unset, so the **code default `true`** applied |
| New | `false` |
| Asked by | assistant |
| Applied by | operator, deployed 2026-08-16 |

**Why.** MACD, EMA9 and VWAP exits fire at a 21-minute median hold. The two exit
rules shipped the same day — the two-tier give-back floor and the volume-flush
reversal — can only act on a position that is still open, so with momentum exits
running they **never engage**. On the live-book replay of 41 closed trades the
`ema9_like` arm is the worst of five, at −41.9% total.

It is also the only thing that lets a MULTIDAY position survive its first
session: 8 of 9 MULTIDAY trades closed the same day they opened.

**Watch:** §1.6 of `docs/TRADE_QUALITY_PLAN.md` measured momentum exits as a class
and found them to be **loss-limiters, not profit-takers** — removing them let dead
trades run to the hard stop and lose 12.3% instead of 7.4%. The give-back floor is
supposed to be the replacement containment. **If Monday's losers come in near
−12% rather than −7%, that replacement is not working and this reverts.**

**Verification, first thing Monday:** if `MACD` or `EMA` appears in any exit
reason, the variable did not take and nothing else deployed that day is being
tested.

---

## 2026-08-16 — `OPTION_MAX_SPREAD_PCT` 2 → 3, reversing the 2026-08-09 change

| | |
| --- | --- |
| Host | Render (worker) |
| Old | `2` |
| New | `3` |
| Asked by | assistant |
| Applied by | operator, deployed 2026-08-16 |

Measured on 2,169 archived chains, every other gate held fixed, walked to an exit
under the rules shipped the same day:

```
ceiling  trades   mean    -top5      total   win  med spread   95% CI (by day)
2.0         224  -0.64%  -1.97%   -144.2%   33%      1.20%   [-7.10, +4.77]
3.0         354  -0.05%  -1.07%    -16.8%   34%      1.56%   [-4.89, +4.78]
4.0         470  -2.13%  -2.97%  -1000.2%   31%      2.12%   [-6.65, +2.71]
6.0         723  -3.30%  -4.89%  -2384.9%   31%      3.08%   [-7.83, +2.26]
10.0        919  -5.55%  -7.66%  -5099.1%   29%      3.88%   [-9.85, +2.87]
```

3.0 wins every column and the degradation above it is steep and monotonic.

**This contradicts the entry above it, and the contradiction is real.** The
2026-08-09 change to 2 rested on §1.4b, which scored *return on capital* through
the **old** exit engine and the old ranker. This scores *percent return on
premium* under the give-back floor, the volume flush and tightest-qualified
contract selection. Different exits change which contracts are worth holding, so
the optimum moving is expected — but only one of those two systems is deployed,
and it is this one.

**Not a finding that 3% works.** Every arm is negative and 3.0's interval spans
zero. This is the smaller of two losses.

**Revert if:** Monday's median entry spread lands above ~2% *and* return on
capital is worse than the weeks run at ceiling 2. Re-run
`tools/spread_ceiling_ab.py` against a freshly-run control — the exit code moved
on 2026-08-15/16, so no arm from before then is comparable.

---

## 2026-08-16 — contract cost caps lowered, to serve the subscriber bands

```
OPTION_PREFERRED_MAX_CONTRACT_COST   1200 -> 800
OPTION_MAX_CONTRACT_COST             1500 -> 1000
OPTION_AGGRESSIVE_MAX_CONTRACT_COST  2000 -> 1500
OPTION_MIN_CONTRACT_COST              100    unchanged
```

**Why: this is a subscriber affordability rule now, not a paper-account one.**
The operator has customers in two bands, under $500 and $500–1000. A $1,400
contract is an alert half the audience cannot act on, and an alert nobody can
take is worth nothing.

Measured on 2,169 archived chains, picking the tightest contract that passes
every gate the app already enforces:

```
cap      chains with a pick   med cost   med spread   <$500  500-1k  >$1k
$1000        379 (17%)          $445       1.56%       61%     39%     1%
$1500        477 (22%)          $595       1.59%       46%     26%    28%
$2500        627 (29%)          $940       1.85%       34%     19%    47%
$4000        659 (30%)          $985       1.74%       33%     18%    49%
```

**Raising the cap buys coverage, not quality.** Spread is 1.59% at $1,500 against
1.74% at $4,000 — the expensive contracts are not tighter. Broken out by band,
cheap contracts are the *tightest* of the three:

```
band            chains served   med cost   med spread   med DTE
under $500        314 (14%)       $275       1.59%         4
$500-1000         280 (13%)       $770       1.77%        11
$1000-2500        407 (19%)      $1410       1.95%        11
```

Paying more buys **tenor**, not spread — 4 days against 11.

### The correction this supersedes

Earlier the same day I argued for raising the cap to $4,000, citing
`TRADE_QUALITY_PLAN` §14 where the best contracts ran $2,664–$3,696. **Those were
38 and 99 DTE and are blocked by `OPTION_MAX_DTE = 30`, not by the cost cap.**
Cost is not the lever that reaches them; tenor is. Raising the cap would have
moved half the alerts above $1,000 and unlocked nothing.

### What it costs

Coverage falls 22% → 17% of chains producing a tradeable contract. That is the
price of keeping essentially every alert inside both customer bands.

### Pending, deliberately not done yet

**Per-band contract selection.** The ranker picks one contract, which cannot
serve a $2,000 account and a $10,000 account at once. Both the sub-$500 and the
$500–1000 tier can be filled on 216 chains (10%), so an alert could carry a
"small" and a "standard" contract. Deferred past Monday: it changes the ranker
and the alert format, and Monday is already carrying four rule changes.
