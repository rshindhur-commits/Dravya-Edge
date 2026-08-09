# Open items

The running list, kept here rather than in a conversation so it survives one.
Status changes with evidence, not with intent: an item moves to done when a
measurement says so, and the measurement is named.

Related: [TRADE_QUALITY_PLAN.md](TRADE_QUALITY_PLAN.md) holds the reasoning and
every experiment's verdict; [../POST_CHANGE_WATCHLIST.md](../POST_CHANGE_WATCHLIST.md)
holds the per-item watch thresholds for changes already deployed.

Last reviewed: 2026-08-09.

---

## The blocking pair

These two are one change. A wider stop the position cannot survive to reach is
not an improvement, it is the same trade with a worse fill — so neither is worth
deploying alone, and an arm that tests one without the other measures nothing.

### 1. Higher-timeframe stop anchor — **built 2026-08-09, validating**
`app/risk/swing_anchor.py`, `SWING_STRUCTURE_ENABLED`, default off.

The 15m anchor pins the stop to the current bar's own low, which for a liquid
megacap is a fraction of a percent from price. Stops land at 0.5–0.75%, 2R
targets at ~1.5%, against an option round trip of 1.5–3.4%. A 1h swing pivot
sits 1–4% away. Smoke test moved median stop 0.55% → 2.20%.

**Next:** `tools/swing_anchor_geometry.py` over the 21 archived sessions. Costs
nothing — cached underlying bars, no option quotes, no network. It decides
whether items 2–3 are worth building at all.

### 2. MULTIDAY holding under swing mode — **not built, and it is the blocker**
Measured on the archived 21-day run, not assumed:

```
291 trades → INTRADAY 285, MULTIDAY 6
```

INTRADAY sets `force_eod_exit=True`. The swing arm's median hold in the smoke
test was ~117 bars of 5m, about 1.5 sessions. So 98% of trades would be
force-closed at the bell before a 2R swing target could resolve, and the wider
anchor would be paid for and never used — the same failure as leaving momentum
exits on, by a different route.

`derive_holding_profile` requires expiration bucket ≥14 DTE **and** setup score
≥76 **and** RR ≥1.8 **and** option quality ≥75. Six of 291 cleared it. Note that
swing mode pins RR at `SWING_TARGET_RR`, so that clause now always passes; the
binding conditions are setup score and option quality.

---

## Required for the arm to mean anything

### 3. `EXIT_MOMENTUM_ENABLED=false` in any swing arm
The switch exists and removes all four momentum rules at once. Not applied. With
it on, a 3% stop is decided by a nine-period EMA within minutes. Named in
`swing_anchor.describe_mode()` so a run that forgot it is visible in its own
output rather than discovered afterwards.

### 4. Backtest selector `--max-dte 45`
Live is `OPTION_MIN_DTE=10`, preferred 14–30, max 45. The backtest selector caps
at `DEFAULT_MAX_DTE = 30`, so the bundle's `longer_dte` slot (31–45) is always
empty there and the arm cannot reproduce live. Costs roughly half again the
requests per scan.

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
