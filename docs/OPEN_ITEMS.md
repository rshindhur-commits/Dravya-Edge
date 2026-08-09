# Open items

**Superseded by [DELIVERY_PLAN.md](DELIVERY_PLAN.md)** on 2026-08-09 — the
tracked checklist of every step from here to subscribers returning, S01–S28.
Track work there, not here.

This file is kept for the closed findings below, which are the reason the plan
looks the way it does. Reasoning and experiment verdicts are in
[TRADE_QUALITY_PLAN.md](TRADE_QUALITY_PLAN.md); the phase rationale is in
[ROADMAP.md](ROADMAP.md).

---

## Closed 2026-08-09

### Higher-timeframe stop anchor — **built, measured, CLOSED as insufficient**
`app/risk/swing_anchor.py`, `SWING_STRUCTURE_ENABLED`, default off. Leave it off.

It does what it was built to do: median stop 0.54% → 2.27%, win rate 32% → 43%.
It still loses. Collecting the wider move takes 2.1 sessions, which costs
**5.07%** of premium in theta. Net **−5.64%**, worse than the −1.12% booked
today. 46% of swing trades exited on time rather than at stop or target.

### MULTIDAY holding, `EXIT_MOMENTUM_ENABLED=false`, backtest `--max-dte 45` — **dropped**
All three were prerequisites for running the anchor arm properly. The geometry
study answered the question without them, and answered it against.

Facts worth keeping if a multi-session profile is ever revisited: 285 of 291
archived trades are INTRADAY and force-closed at the bell, and
`derive_holding_profile` requires expiration bucket ≥14 DTE **and** setup score
≥76 **and** RR ≥1.8 **and** option quality ≥75 — six of 291 cleared it.

### "The signal works, only the instrument is wrong" — **WITHDRAWN**
Claimed on the strength of +14.43% of underlying captured across 342 trades. A
hold sweep over the same sessions put the sign positive → negative → positive
across adjacent hold caps, |t| < 1.2 on every arm, and the +14.43% came from **5
trades of 331** — without them the total is negative and the median trade loses
0.75% of price.

What survives, and it is worth more: for the intraday control, 792 trades give
SE 0.037% per trade against a +0.155% break-even, so a real edge that size would
show at **t = 4.2**. Observed **−0.33**. An edge large enough to pay for options
is *ruled out* for the strategy as it runs today. Whether a smaller, share-sized
edge exists is unresolved.
