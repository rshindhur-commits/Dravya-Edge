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
