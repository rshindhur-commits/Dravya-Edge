# Nothing protects a gain between +0.5R and +1.2R

**Status: diagnosed, not fixed. For the post-close batch of 2026-08-19.**

Breakeven arms at +0.5R and puts the stop at entry. The profit lock cannot engage
until the peak passes +1.2R. Between those two points the stop sits at entry and
**no code path can hold any part of the gain** -- a trade that peaks in that band
and retraces books exactly 0.00R on the underlying and loses the option spread.

This is not a tuning miss. There is no setting that protects that band today.

---

## 1. The arithmetic

`resolve_profit_lock` (`app/exit/exit_engine.py:97-176`):

```python
locked_r = mfe_r - _env_float("PROFIT_LOCK_MAX_GIVEBACK_R", 1.0)

if locked_r <= 0:
    return None, None
```

```
  peak 0.6R  ->  locked_r -0.4R   does nothing
  peak 0.8R  ->  locked_r -0.2R   does nothing
  peak 1.0R  ->  locked_r  0.0R   does nothing
  peak 1.2R  ->  locked_r +0.2R   engages
  peak 2.0R  ->  locked_r +1.0R   engages
```

`EXIT_BREAKEVEN_TRIGGER_R` is 0.5 in production. So the protection map is:

| peak reached | stop sits at | gain protected |
|---|---|---|
| below 0.5R | initial stop | none (full 1R at risk) |
| **0.5R - 1.2R** | **entry** | **none** |
| above 1.2R | entry, until a soft rule fires | `peak - 1.0R`, and only then |

## 2. Three independent reasons the lock cannot cover the band

**It is not a trailing stop.** `exit_engine.py:133` returns immediately unless an
exit signal is already firing, and only for `{EMA, VWAP, MACD}`. It converts a
"close now" verdict into "ratchet the stop instead". It never acts on its own, so
a trade that simply reverses is never touched by it.

**It subtracts a full 1R of permitted giveback**, which is what makes it dead
below a 1.2R peak.

**Lowering `PROFIT_LOCK_MIN_MFE_R` alone does nothing.** That is the lever carried
in the plan as the untried alternative to the breakeven trigger, and on its own it
cannot work: at a 0.6R peak, `0.6 - 1.0` is still negative and the function still
returns `(None, None)`. It needs `PROFIT_LOCK_MAX_GIVEBACK_R` cut with it. Anyone
A/B-ing the single variable will measure "no effect" and conclude the idea is
dead, when the second variable was the binding one.

Two further gates apply even once the arithmetic passes: trend health must be
>= 70 and exit confidence < 25.

## 3. It explains the measurement already on file

Trades peaking under +1R give back 76% of their best price, and 108 of the 145
trades that travelled at all peaked below 1R. That has been read as the breakeven
trigger firing too late or too early. It is neither: for those 108 trades there
was no mechanism that could have held anything. The band where most of this book
peaks is the band with no protection in it.

## 4. Two trades on 2026-08-19, one in each band

**TSLA #340 -- inside the dead zone, lost everything it made.**
Entry 338.31, 1R = 1.69. Breakeven armed at 10:13 on a +0.59R update, moving the
stop from 336.62 to 338.31. Peak 340.03 = **+1.02R** -- inside the band, so the
lock was 0.02R short of even being arithmetically eligible, and no soft rule
fired. Price dipped to 338.01 at 10:15, took the breakeven stop, and was back at
340.30 by 10:18. Booked 0.00R; the option went 9.625 -> 9.125, **-$50**.

Had the stop stayed at its original 336.62 the trade was never threatened -- the
low of the entire move was 337.50.

**AMZN #343 -- passed through the band and out the other side.**
Entry 261.05, 1R = 1.31. Breakeven armed at 10:43 at +0.6R. Fifteen minutes later
it was at 263.245 = **+1.68R**, which finally makes the lock eligible at
`1.68 - 1.0 = +0.68R`. The difference between the two trades is not the rules; it
is whether the retrace arrived before or after the peak cleared 1.2R.

## 5. Do not expect soft exits to cover this

The obvious answer -- "a momentum exit will bank it" -- does not survive the
record. Every soft-rule exit ever booked:

```
  14 exits (EMA/MACD/VWAP):  5 positive R, 9 negative
  mean R                     -0.059
  option premium             -1.15% mean, 4 of 14 positive
```

Quoting the winners (CRWD +0.79, TSLA +0.46, ORCL +0.30) gives the opposite
impression and is how this was nearly mis-stated on the day. Soft exits are close
to a coin flip that loses slightly. They are not a profit-taking mechanism.

## 6. The proposal, to be measured not deployed

Arm breakeven at 0.5R as now, then let the lock take over with a **giveback of
about 0.3R instead of 1.0R**, which makes it eligible from roughly a 0.8R peak.
It needs both variables moved together:

```
PROFIT_LOCK_MIN_MFE_R      1.0 -> 0.5
PROFIT_LOCK_MAX_GIVEBACK_R 1.0 -> 0.3
```

Counter-evidence to respect: dropping `EXIT_BREAKEVEN_TRIGGER_R` from 1.0 to 0.25
produced 222 trades against 191 at the same mean R and a **worse** total premium
(-280.0% against -227.7%), because closing early frees the symbol to re-enter. The
lock is a different mechanism -- it ratchets rather than closes, so it should not
produce that re-entry effect -- but "different mechanism" is a hypothesis, not a
result.

Also unresolved: the lock only fires on `{EMA, VWAP, MACD}`. A trade that reverses
without tripping any of the three is still unprotected at whatever the stop is,
however high the peak was. Whether the lock should become a true trailing stop is
a larger question and should not be smuggled into this A/B.

**Rule the user set for switches like this: commit only if it wins on both replay
windows** -- BULL 2026-07-30..08-12 and BEAR 2026-07-15..07-29. Quote cash beside
R, with a bootstrap CI and the mean without the top five.
