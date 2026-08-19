# The partial-profit alert reports a trade that cannot happen

**Status: diagnosed, not fixed. For the post-close batch of 2026-08-19.**

At `rr_progress >= 1.5` the exit engine sets `partial_profit_taken = True` and a
`PARTIAL PROFIT` alert goes out saying "Position: Partial closed / Runner: Still
Open". **`MAX_CONTRACTS_PER_TRADE` is 1.** Half a contract cannot be sold. No
position is reduced, no profit is realised, nothing is written to the book -- a
flag flips and a message claims an execution that did not occur.

---

## 1. The code

`app/exit/exit_engine.py:1044-1048`:

```python
if not exit_signal and rr_progress >= 1.5:

    partial_profit_taken = True
    trade_action = "PARTIAL_PROFIT"
    adjustment_reason = "Partial profit threshold reached"
```

`not exit_signal` -- so this fires on a trade that is explicitly *not* exiting. It
sets a flag and nothing else. There is no contract arithmetic, no call into
`close_paper_trade`, no partial close path anywhere beneath it.

`app/alerts/telegram_alerts.py:2574-2586` then renders:

```
🟡 PARTIAL PROFIT
Result: {r_multiple}R
Position: Partial closed
Runner: Still Open
```

"Partial closed" and "Runner" are both false. The runner *is* the whole position.

## 2. Observed, AMZN #343 on 2026-08-19

Alert at 10:53 ET: `Result: 1.91R · Position: Partial closed · Runner: Still Open`.

The row at that moment:

```
status                OPEN          close_price    None
partial_profit_taken  true          r_multiple     None
option_contracts      1             stop_loss      261.05  (breakeven)
highest_price         263.56        take_profit    263.66
```

Nothing closed. Nothing booked. The stop was still at entry. The trade went on to
close **in full** at 10:58 on `HARD_TARGET` for +1.99R, so the "runner" and the
"partial" were the same single contract for the whole life of the trade.

The 1.91R in the alert is `highest_price` 263.56 -- note that is *below* the 263.66
target. The app had not yet registered its own target being hit when it announced
taking profit at it.

## 3. Why it matters more than the other two alert defects

The divergence in ALERT_BOOK_DIVERGENCE.md misreports a number. This reports an
**event**. A subscriber reading "Partial closed / Runner: Still Open" believes
they hold a reduced position with profit banked, and that belief is wrong in both
halves: nothing was banked and the full position is still exposed.

On AMZN the exposure was real. At 10:55 the position showed +2.92R unrealised
(option mid 10.90 against a 9.20 entry, about +$170) with the stop still at
breakeven 261.05. Had it reversed, the entire $170 would have gone and the trade
would have booked 0.00R -- while the subscriber believed profit had already been
taken off the table.

## 4. Every trade that reaches 1.5R fires it

`MAX_CONTRACTS_PER_TRADE=1` is the deployed setting, so no trade in this book can
ever be partially closed. The threshold is `rr_progress >= 1.5`. Therefore **every
trade that reaches 1.5R emits a false execution notice**, and none of them can
ever be true.

## 5. Options

**Suppress it while one contract is the norm.** Gate the alert on
`option_contracts > 1`. Smallest change, honest output, and it costs nothing today
because the branch cannot do anything with a single contract.

**Or make it real.** A partial exit needs a position size above one, a close of
part of it through `close_paper_trade`, the realised premium booked, and the
remainder carried with its own stop. That is a feature, not a fix, and it is
pointless until `MAX_CONTRACTS_PER_TRADE` is above 1 -- which is a capital
question, not an exit-rule question.

**Either way, keep the state change.** `partial_profit_taken` also feeds
`_trade_update_reason` and the trailing logic. Only the claim of an execution is
false; the flag itself may be doing legitimate work elsewhere and should not be
ripped out with the message.

## 6. Watch alongside

The same trade showed `highest_price` lagging the market badly -- 263.56 recorded
while the tape printed 264.87. That is the blind position monitor
(`get_live_price` fix, commit 19e6ce9, undeployed), and it is why the app
announced a partial at a peak it had already exceeded and missed its own target
for nine minutes. Fixing the monitor does not fix this defect, but it does remove
the stale peak that made the alert look even stranger than it was.
