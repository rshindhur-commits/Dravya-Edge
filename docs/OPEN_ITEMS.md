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

### 8. No edge is measurable, in options or in the underlying — **open, and it is a product decision**

An earlier version of this item claimed the signal worked and only the
instrument was wrong, on the strength of +14.43% of underlying captured across
342 trades. **Withdrawn.** A hold sweep over the same 21 sessions put the sign
positive → negative → positive across adjacent hold caps, |t| < 1.2 on every
arm, and the +14.43% came from 5 trades of 331 — without them the total is
negative, and the median trade loses 0.75% of price. Detail in
[TRADE_QUALITY_PLAN.md §2.2f](TRADE_QUALITY_PLAN.md).

What is established:

* Every lever inside the options framing is bounded below break-even — spread
  ceiling +0.41% at zero toll, perfect exits +0.20%, larger moves −5.64%.
* An edge big enough to pay for options is **ruled out** for the strategy as it
  runs today: break-even needs +0.155% per trade, 792 trades give SE 0.037%, so
  it would show at t = 4.2. Observed −0.33.
* Whether a smaller, share-sized edge exists is **unresolved** — 21 sessions
  cannot separate it from zero either way.

Two directions remain, and the choice is the user's:

1. **Accumulate sessions.** Subscribers are already paused, so this costs only
   the Render/Neon bill. 21 sessions is too few to conclude much; the same
   study re-run at 60–90 sessions would separate a small edge from zero.
2. **Treat raising the edge as the project.** New features, different setups, or
   a different universe. Open-ended, and the honest path if an options product
   specifically is the goal.

Nothing should be tuned further until this is decided.

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
