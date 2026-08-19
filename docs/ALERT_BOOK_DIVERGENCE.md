# The exit alert and the book report different trades

**Status: diagnosed, not fixed. For the post-close batch of 2026-08-19.**

On a level-triggered exit the Telegram alert reports the price that *triggered*
the exit; `paper_trades` records the price it *filled* at. On a stop those are
never the same number, and the gap always runs in the flattering direction:
losses go out smaller than they were booked, wins larger.

Found 2026-08-19 when TSLA #340 was delivered as a win and the ledger had it
flat.

---

## 1. The trade that exposed it

```
                    alert said        book recorded
  R                 +0.36R  WIN       +0.00R
  option P/L        +$17.50           -$50.00
  contract cost     $970.00           $962.50   (entry mid 9.625)
```

TSLA CALL, entry 338.31, initial stop 336.62 (1R = $1.69). The stop was moved to
breakeven at 10:13 on a +0.59R update, the trade peaked at 340.03 (+1.02R), and
the retrace took it out at the breakeven stop at 10:18.

A breakeven stop fills at entry by construction, so the booked result is exactly
0.00R. The alert reported the price that *tripped* the stop -- 338.93 -- which is
+0.36R. Same for the option: the alert used the live chain mid at decision time
(9.80, implied by +$17.50 against an entry mid of 9.625) while the book recorded
`option_close_mid` 9.125.

A $67.50 swing on one contract, and a flipped verdict.

## 2. Mechanism

The scan loop sends the alert *before* the trade is closed, from live scan values
(`app/main.py:5298-5308`):

```python
current_price      = current_symbol_close                   # the live bar close
option_current_mid = active_option_snapshot["mid_price"]    # the live chain quote
r_multiple         = exit_setup["rr_progress"]              # the engine's running R
```

`close_paper_trade` then books the trade independently, at the level that was
actually hit, and records `option_close_mid` from the exit snapshot.

Neither is a bug on its own. The defect is that they are two different quantities
wearing the same labels, and only one of them reaches the subscriber.

**`app/state/paper_trade_manager.py:1370` is not the path in play here** -- it
passes `current_price=close_price` and `r_multiple=trade["r_multiple"]`, both
correct. Only the scan-loop path diverges. Any fix must not "fix" the one that is
already right.

## 3. Measured scope

29 closed trades carry both `payload->exit_decision_rr` (what the alert reports)
and `r_multiple` (what is booked).

| exit class | trades | diverging | mean overstatement |
|---|---|---|---|
| soft rules (EMA, MACD, VWAP, TIME_EXIT, FAILED_BREAKOUT) | 16 | **0** | 0.000R |
| level-triggered (HARD_STOP, HARD_TARGET) | 13 | **8** | **+0.158R** |

Perfect agreement on every soft rule, because a soft rule exits *at* the price
that triggered it -- decision and fill are the same event. A level rule forces the
fill to the level.

Every diverging trade:

```
  SMCI  2026-08-03  alert -0.95  booked -1.00   decision 28.41  fill 28.40
  SMCI  2026-08-03  alert +1.46  booked +1.00   decision 28.82  fill 28.76
  SPCX  2026-08-17  alert -0.65  booked -1.00   decision 147.88 fill 147.59
  TSLA  2026-08-18  alert -0.77  booked -1.00   decision 335.79 fill 335.40
  ORCL  2026-08-18  alert -0.75  booked -1.00   decision 143.45 fill 143.27
  TSLA  2026-08-19  alert +0.36  booked  0.00   decision 338.93 fill 338.31
```

Two shapes, both flattering:

* **Losses understated.** Three -1.00R stops went out as -0.65, -0.75 and -0.77 --
  a quarter to a third of a full R lighter than the book has them.
* **Wins overstated.** SMCI's +1.00R was delivered as +1.46R.

Mean across all 29 is +0.071R. Confined to 45% of trades, so it does not move the
aggregate much -- but the aggregate is not what a subscriber reads.

## 4. Why the verdict flip will get more common

Only 1 of 29 flipped win/loss, and that looks like a rarity worth deprioritising.
It is not.

The flip happens when the overstatement crosses zero, which requires the booked
result to sit within ~0.2R of flat. **A breakeven stop books at exactly 0.00R by
construction** -- it is the one exit that lands on the boundary every single time.
`EXIT_BREAKEVEN_TRIGGER_R` is 0.5 in production, so breakeven exits are common and
getting more so.

Every future breakeven stop that is tripped from above will be delivered as a win.

## 5. The fix

Send the alert from the booked trade, not from the decision snapshot: move the
scan-loop call to after `close_paper_trade`, and feed it `close_price`,
`option_close_mid`/`option_close_bid`, and the persisted `r_multiple` -- which is
exactly what the `paper_trade_manager` path already does.

If the alert must stay where it is, then it has to price the level rather than the
trigger: for HARD_STOP and HARD_TARGET the fill is the stop or target level, and
both are known at decision time.

**What not to do:** change the WIN/LOSS label alone. That was tried. The comment at
`app/alerts/telegram_alerts.py:2509-2526` records the last round -- alerts reported
**$399 of profit against $119 the fills produced**, $14.74 a trade, calling 8 of 19
winners where the fills made 5. The response moved the verdict from R onto premium,
but the premium it moved onto is still a live mid rather than an exit fill, which
is why TSLA #340 still went out as a win. Relabelling a wrong number produces a
differently-worded wrong number.

## 6. Open question

`exit_slippage` reads **0.0** on all six diverging trades, including TSLA #340
where the decision price and fill differ by $0.62. Either it measures something
else, or it is broken and would otherwise have surfaced this weeks ago. Worth
resolving alongside, because a working slippage field is the natural regression
guard for this defect.
