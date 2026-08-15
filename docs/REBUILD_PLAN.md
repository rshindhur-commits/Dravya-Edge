
---

## 11. Phase 0 result, 2026-08-15 — the gate says no on this universe

Run with `tools/planb_horizon.py`. **The answer is no, and the reason is not the
one this plan assumed.**

### 11.1 Holding longer does not help

1,491 candidates carrying a chain IV, over 7 archived days. Both panels on one
sample, intervals bootstrapped over **days** rather than candidates.

```
UNDERLYING MOVE %, following the signal      SAME WINDOWS, ALWAYS LONG
  close   -0.08  [-0.27, +0.10]   46% win     close   -0.08  [-0.46, +0.23]  49%
  +1d     -0.56  [-1.24, +0.20]   42%         +1d     +0.15  [-1.04, +1.42]  42%
  +2d     -0.76  [-1.90, +0.39]   40%         +2d     +1.38  [-0.41, +3.02]  67%
  +3d     -0.50  [-1.94, +0.70]   45%         +3d     +1.75  [-0.22, +2.98]  67%
```

The signal is **negative at every horizon** and **loses to always-long at every
horizon past the same day**. Win rate falls as the hold lengthens, 46% → 40%.

The hypothesis was that the move grows while the toll stays fixed. On this
evidence the move does not grow — it decays. Note the always-long column is
itself inside its own interval, so it is drift in a 12-day window rather than a
finding; what it does establish is that the signal does not beat it.

### 11.2 The option loses at every horizon, decisively

```
OPTION, 3% ITM at 35 DTE, ask in / bid out, % of premium
  close   -4.57  [-5.38,  -3.62]   30% win
  +1d     -8.02  [-11.10, -4.15]   28%
  +2d     -9.47  [-15.00, -3.80]   29%
  +3d     -8.17  [-13.97, -2.56]   34%
```

**Every interval lies entirely below zero.** This is not a wide-interval "cannot
tell" result; it is a clear negative, and it gets worse with time held.

### 11.3 The real constraint is the spread, and it is much larger than assumed

The −4.57% at *zero days held* is the giveaway: nothing has decayed yet, so that
is almost entirely the round trip. Measured on **fresh quotes recorded during
market hours** (94% of records are `QUOTE_OK`), on the exact contract this plan
proposed buying:

```
26-60 DTE, ITM, QUOTE_OK, n=2,312     p25 3.45%   median 6.92%   p75 10.00%
```

Per symbol, same bucket: PLTR **2.95%** at best, ORCL **10.03%** at worst.
**Zero of nine symbols are under 2%.**

A system holding for days needs a round trip near 1% to have room. This universe
charges seven. §1's toll model said the same thing in R units; this says it in
the currency that gets spent, and identifies the cause as the *universe* rather
than the hold, the entry, or the contract.

### 11.4 What this does to the plan

§4 proposed 8–10 mega-caps on the assumption their chains are tight. **That
assumption is now the only thing standing between Plan B and cancellation, and it
is still unmeasured** — those symbols are not scanned, so no chain for them was
ever recorded.

A weekend snapshot was attempted and **discarded as unusable**: it returned SPY
at 5.84% and QQQ at 5.96%, which is impossible for the most liquid options
listed. Closing quotes widen as market makers step away, so every number in that
run reflects the clock rather than the instrument.

**The outstanding measurement is twelve API calls during market hours**, Monday,
comparing SPY, QQQ, AAPL, MSFT, NVDA, AMZN, META, GOOGL against the 6.92%
baseline. Negligible quota, safe to run alongside the live worker.

- If mega-caps come in near **1%**, §4 survives and Phase 1 proceeds with the
  universe as the central change rather than a detail.
- If they come in at **4–7%** like the scanned universe, **buying options
  directionally cannot work at any hold or any contract**, and Plan B should not
  be built. The honest response then is to stop, not to search further.

### 11.5 What Phase 0 settled regardless

**The hold is not the lever.** That hypothesis is now tested and dead, which
removes the last untested entry-side idea. Whatever happens Monday, no future
session should re-run a longer-hold experiment on this universe expecting a
different answer.

---

## 12. Phase 0b — "risk more to get more", tested and refuted

The operator challenged §11's 2% figure: the toll is a *percentage* of premium,
not a fixed fee, so a big enough winner absorbs it. **That reasoning is correct**,
and the 2% gate was an assumption rather than a law. Convex books — long
volatility, tail hedges, trend following — run win rates in the twenties and pay
worse tolls than ours, and they work because the winners are multiples.

`tools/planb_convexity.py` tests it properly: **4 stop widths × 7 target widths =
28 combinations**, 1,209 candidates. Widening the target alone would have been
half a test, because with the app's own stop a trade dies in 0.6 days at a 3% win
rate — the stop resolves it long before a wide target is reachable. So the stop is
widened too, which is the half that actually raises risk per trade.

```
OPTION RETURN, % of premium, 3% ITM 35 DTE, ask in / bid out
stop        1R      2R      3R      5R      8R     12R    none    held
1x       -6.31   -6.82   -7.41   -7.48   -8.19   -8.98   -9.08    0.4d
2x       -7.90   -9.17  -10.38  -11.33  -11.67  -11.68  -11.69    0.7d
3x       -9.31  -11.21  -11.75  -12.39  -12.56  -12.57  -12.57    1.1d
5x       -9.10  -11.73  -12.17  -12.34  -12.35  -12.35  -12.35    1.9d
```

**Every one of the 28 cells is negative, and it worsens monotonically in both
directions.** The best cell is the tightest stop with the smallest target,
−6.31%, CI [−6.8, −5.6], entirely below zero. Convexity would have shown as the
mean *rising* toward the bottom right. It falls.

### Why more risk does not help here

Leverage multiplies expectancy; it does not create it. The underlying signal's
best cell is **+0.02R**, and widening the stop makes it worse (−0.22R at 2×),
because a wider stop loses more when wrong without the winners growing to match.

The break-even arithmetic, which is the number to remember:

```
1R move  ->  ~0.4% underlying  x 0.65 delta  ->  ~5-6% of premium
round trip toll                              ->   6.92%
                                    break-even ~ 1.3R
```

**It costs about 1.3R just to open and close the position**, and only 30% of
trades reach 2R. The signal's edge is roughly a tenth of its own transaction
cost. No risk setting changes that ratio, because risk scales both sides of it.

### What this does and does not close

**Closed:** the hold (§11), and now risk-taking in both dimensions. Nobody should
re-run either on this universe.

**Still open, and unchanged:** whether an options market exists where the toll is
1–2% rather than 7%. That is the only remaining variable, because it is the only
one that moves the break-even rather than the edge.

**Honest about the ceiling, in advance.** At a 1% toll, break-even falls to about
+0.2R against a measured edge of +0.134R. That is closer, and **still short.** So
even the best plausible Monday result does not by itself produce a profitable
system — it produces the first version of this problem where the gap is small
enough that entry quality could close it. That is the most §11.4 can deliver, and
saying so now is the point of writing it down.
