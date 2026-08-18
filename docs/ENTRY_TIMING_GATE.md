# The entry-timing ceiling: live since 2026-08-17, and unmeasured

`ENTRY_TIMING_GATE_ENABLED=true` is set in Render. It is **not** in `.env` and the
code default is `False` (`app/gates/entry_gate.py:589`), so production is the only
place it is on. `ENTRY_TIMING_TOO_EARLY` appears on **exactly one day in the whole
16-day archive** — 2026-08-17, 8 blocks across PLTR, SPCX and ORCL. Today was its
first live session.

This note records what it did on that first day, the channel through which it did
it, an A/B that does not settle the question, and the recommendation anyway.

---

## 1. PLTR, 2026-08-17 — the worked example

The operator's chart and Telegram run on CT; the app and this document use ET.
Subtract one hour to read this against the alert.

| ET | CT | Price | Signal | Setup valid | RR | Timing score | Result |
|---|---|---|---|---|---|---|---|
| 10:48 | 09:48 | **174.75** | HIGH CONVICTION | yes | **2.06** | **70.56** | refused `ENTRY_TIMING_TOO_EARLY` |
| 11:03 | 10:03 | 175.06 | HIGH CONVICTION | yes | 2.11 | 60.56 | refused `ENTRY_TIMING_TOO_EARLY` |
| 11:08 | 10:08 | 175.24 | HIGH CONVICTION | yes | 2.15 | 58.27 | refused `ENTRY_TIMING_TOO_EARLY` |
| 11:13 | 10:13 | 175.70 | HIGH CONVICTION | yes | 1.64 | 56.03 | refused, RR had decayed |
| **11:22** | **10:22** | **175.69** | HIGH CONVICTION | yes | 2.23 | **53.45** | **entered** |

A complete EMA_PULLBACK, valid, clearing the RR bar, was available at **10:48 at
174.75** with entry 174.74 / stop 173.87 / target 176.55. It was refused for one
reason: the timing score was above the ceiling of 55. Thirty-four minutes later
the same setup was taken 94 cents higher.

## 2. The cost is not the 94 cents — it is the breakeven trigger

This is the part that was missed twice on the day, and the reason this document
exists.

`EXIT_BREAKEVEN_TRIGGER_R` is **1.0** (`app/exit/exit_engine.py:1012`). Once a
trade reaches +1R the stop moves to the entry price and the trade can no longer
lose. `EXIT_BREAKEVEN_ON_PEAK` is off, so the trigger is judged on a scan's own
price, not the running peak.

PLTR's high after 10:48 was **175.93**. The same move, measured from two entries:

```
REFUSED 10:48  entry 174.74  stop 173.87  peak +1.37R  breakeven ARMED      exit 12:19 @174.72  -0.02R
TAKEN   11:22  entry 175.68  stop 174.81  peak +0.20R  breakeven NOT armed  exit 12:19 @174.72  -1.10R
```

**Same signal, same high, same exit minute. The entire loss sits in the gap.**

The refused entry sits low enough that the move the stock actually made carries
it past the trigger. The taken entry does not, so the protection never engages
and the trade gives everything back to its stop.

Any comparison that stops at "a worse entry price" cannot see this. Two separate
readings of this trade concluded "it loses either way" — from the exit rule
(`HARD_STOP`, textbook) and from the price path (target never reached, stop
breached at 13:23). Both were true and both missed that the stop is not static.

### What this does **not** show

The target at 176.55 was never reached in either arm. The refused entry is a
**scratch, not a win**. The claim here is bounded: the gate converted a −0.02R
trade into a −1.10R trade on this instance. It is not that the gate cost a
winner.

## 3. The A/B, and why it does not settle it

`tools/entry_timing_gate_ab.py` — archive only, no Polygon quota.

The archive predates the gate, so every recorded entry is unfiltered. Entries
whose timing score was at or above 55 are exactly what the gate would now refuse.
Arm A is the entry as it happened; arm B is the next scan of that same symbol and
day clearing the ceiling. Both are walked forward through the real stop and
breakeven rules on the archived price series.

```
entries the ceiling would refuse:            13
  no qualifying re-entry the same day:        5
  re-entered later at a lower score:          8

MATCHED PAIRS (same symbol-day both arms, n=8)
  A (as taken)            total +4.85R   mean +0.606R   CI [-0.579, +2.042]
  B (gate's later entry)  total +5.30R   mean +0.662R   CI [-0.459, +1.828]

  delta (B - A)  mean +0.056R   95% CI [-1.379, +1.558]
  gate made it worse on 3/8
  breakeven armed -- A 4/8   B 5/8
```

**Eight matched pairs and an interval from −1.38R to +1.56R. This is no evidence
in either direction.** It does not vindicate the gate and it does not convict it.
The PLTR case above is not among the eight — today's data already has the gate
on, so the entries it refused never became rows with an entry status.

Both arms also carry `mean without best 5` below zero (−1.16R and −0.96R), which
is the standing warning on this book: a handful of trades carry the totals.

## 4. Recommendation: turn it off

**Set `ENTRY_TIMING_GATE_ENABLED=false` in Render.** Effective on the next scan,
no deploy, reversible in one click.

The argument is not that the gate is proven harmful. It is:

1. It went live with **no live A/B**, and `CHANGE_IMPACT_MAP.md` §8 already said
   so — *"score is inverted and survives controls, but 2-right-2-wrong on the one
   live day checked."*
2. The direct test of what it actually does — refuse now, take the same name
   later — rests on **8 pairs** and cannot separate it from noise.
3. It refuses roughly **half the book** (5,327 → 12,517 of 22,954 candidates when
   the ceiling moved 70 → 55). That is a very large intervention to run on a
   study that never tested the dynamic behaviour.
4. The **code default is off**. Production is the only place it is on, which
   means the shipped, tested, reviewed configuration is the one without it.

### The study behind it is not being discarded

The finding is real and was measured on 22,954 resolved candidates: a low
entry-timing score wins more, stable across both halves and five of six regimes.
What is disputed is only the *operation*. The study scored each candidate once,
where it stood, and supports **preferring low-score candidates**. Running it as a
live ceiling does something the study never measured: it re-evaluates the same
name every scan and enters it later, at a price whose relationship to the
breakeven trigger has changed.

Those are different interventions and only one of them has been tested.

## 5. What would settle it

The gate earns a live slot again when the matched-pair delta has an interval that
excludes zero. At the current rate — 13 refusable entries in 30 days — that needs
far more sessions than exist. Two ways to get there sooner:

- **Replay, not live.** `tools/replay_forward.py` over the retained archive with
  the ceiling on and off, one arm at a time. Both arms compete for the same
  Polygon rate limit, so they must not be run in parallel, and `--out` is written
  only at the very end.
- **Score the channel directly.** The mechanism proposed here is specific and
  cheaper to test than the P&L: does a refused-then-retaken entry cross
  `EXIT_BREAKEVEN_TRIGGER_R` less often than the entry it replaced? Arm A 4/8
  against arm B 5/8 is the wrong sign on a hopeless sample, and that single number
  on several hundred pairs would be worth more than another month of live trading.

A second lever sits behind the same mechanism and is already scheduled at Gate B:
`EXIT_BREAKEVEN_TRIGGER_R` at 1.0 protects only trades that reach 1R. The exit
engine's own comment records that it fired on 38 of 291 trades, and that 108 of
the 145 that moved at all peaked below 1R and gave every bit of it back. Lowering
it to 0.25 simulates at +2.1R → +12.2R, but that simulation assumes no trade is
cut at breakeven that would have recovered — the cost the replay has to price.
**It does not move on this document.**

---

## 6. Corrections recorded, because both cost time on the day

**"The loss was direction."** Diagnosed from the exit — all three of the day's
trades closed `HARD_STOP`, which is the textbook path — without reading the
funnel that produced the entry. The exit was working correctly. The entry had
been gated late by a rule that went live that morning.

**"It loses either way."** Checked whether the stop or the target was hit first
and concluded the earlier entry lost too. True as far as it goes, and wrong,
because the stop is not static: §2 shows the earlier entry arms the breakeven and
exits flat. **A counterfactual entry must be replayed through the exit engine,
not against the entry's own fixed stop.**
