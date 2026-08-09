# Open items

The running list, kept here rather than in a conversation so it survives one.
Status changes with evidence, not with intent: an item moves to done when a
measurement says so, and the measurement is named.

Related: [TRADE_QUALITY_PLAN.md](TRADE_QUALITY_PLAN.md) holds the reasoning and
every experiment's verdict; [../POST_CHANGE_WATCHLIST.md](../POST_CHANGE_WATCHLIST.md)
holds the per-item watch thresholds for changes already deployed.

Last reviewed: 2026-08-09.

---

## Closed 2026-08-09

### 1. Higher-timeframe stop anchor — **built, measured, CLOSED as insufficient**
`app/risk/swing_anchor.py`, `SWING_STRUCTURE_ENABLED`, default off. Leave it off.

It does what it was built to do: median stop 0.54% → 2.27%, win rate 32% → 43%,
and the underlying move captured flips sign from −0.0123% to **+0.0436%** of
price per trade. Before carrying cost the book improves 0.70 points, −1.27% →
−0.57% at spread ceiling 2 — the largest gain any lever has produced.

It still loses. Collecting those 0.70 points takes 2.1 sessions of holding, which
costs **5.07%** of premium in theta. Net **−5.64%**, worse than the −1.12% the
app books today. 46% of swing trades exited on time rather than at stop or
target — full decay paid on an unresolved position.

Measured by `tools/swing_anchor_geometry.py` over 21 sessions and 5,590
candidates, from cached bars at no quota cost. Detail in
[TRADE_QUALITY_PLAN.md §2.2d](TRADE_QUALITY_PLAN.md).

### 2, 3, 4 — **dropped, they only existed to serve item 1**
MULTIDAY holding, `EXIT_MOMENTUM_ENABLED=false` and the backtest selector's
`--max-dte 45` were all prerequisites for running the anchor arm properly. The
geometry study answered the question without needing any of them, and answered
it against. Building them now would be work in service of a lever already
measured as negative.

Kept on record because the underlying facts remain true and will matter if a
multi-session holding profile is ever revisited for another reason: 285 of 291
archived trades are INTRADAY and force-closed at the bell, and
`derive_holding_profile` requires expiration bucket ≥14 DTE **and** setup score
≥76 **and** RR ≥1.8 **and** option quality ≥75, which six of 291 cleared.

---

## The item that replaced them

### 8. The signal is not the problem; the instrument is — **open, and it is a product decision**
The same 21-session study, read the other way: the swing arm captured **+14.43%
of underlying across 342 trades**, +0.0436% per trade. Net of 2bp round-trip
costs that is roughly **+7.8% total** — thin, but positive, and the first
positive number this project has produced.

Every lever inside the options framing is now measured and bounded below
break-even: spread ceiling +0.41% at zero toll, perfect exits +0.20%, larger
moves −5.64%. The toll and the decay are 148–344bp and ~240bp/session against an
edge of about 4bp per trade.

This is not a parameter. It is a decision about what the product is, and it is
the user's to make. Nothing should be tuned further until it is made.

---

## Blocked on the exits being settled

### 5. Is the signal late?
The original diagnosis, still untested: entries arriving after the move has run.
Now feasible — the exit-timestamp defect is fixed, so alert and database times
agree. Needs entry timestamps compared against the bar sequence that triggered
them.

Note the one thing already known: `resample_timeframe` emits the final bucket
while still forming, so data is within ~5 minutes of the tape, not 20–26. That
refutes the *mechanism* originally proposed, not the symptom.

### 6. Does any setup have edge?
EMA_PULLBACK (183) and EMA_REJECTION (123) are indistinguishable today, both at
about −24/trade. That comparison means nothing while 83% of losses come from two
exit rules applied to both. Re-run after items 1–3 settle.

---

## Deployed, awaiting observation

### 7. Monday 2026-08-10 verification
Three things to read off the first full session after the fixes:

| check | where | what would be wrong |
|---|---|---|
| memory leak fixed | `rss_mb` series in `scanner_runs.payload` | a rising series across the session |
| session recorded completely | `tools/daily_report.py` integrity section | missing scans, early stop, orphaned positions |
| spread config reached the worker | `required_value` for the spread rule | anything other than the deployed ceiling |

Also open, display-only, deferred to the pre-subscriber-return checklist:
duplicate close alerts.
