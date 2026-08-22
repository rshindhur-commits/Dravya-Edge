# Change impact map

**Read this before changing any rule, gate, threshold or environment variable.**

Organised by *what you might change*, not by module. For each lever: what reads
it, what else moves with it, which measurement stops being valid, and what has
already been tested about it.

Written 2026-08-13 by tracing the code. Every claim below cites the file and line
it came from, so it can be re-checked rather than trusted. Verdicts from
experiments live in [TRADE_QUALITY_PLAN.md](TRADE_QUALITY_PLAN.md); this file
says what a change *touches*, not whether it is a good idea.

---

## 0. Five traps that have already caused wrong conclusions

**0.1 — `settings.*` is frozen at import; `get_float_env()` is live.**
`app/config/settings.py:314` runs `settings = get_settings()` at module import
into a `@dataclass(frozen=True)`. There is no refresh function. So:

* `settings.option_max_spread_pct` — the value of `OPTION_MAX_SPREAD_PCT` **as it
  was when the process started**. Setting `os.environ[...]` mid-process does
  nothing.
* `get_float_env("OPTION_MAX_SPREAD_PCT", 6.0)` in `app/gates/entry_gate.py:59` —
  re-read on every call.

An A/B arm that sets the variable on the command line changes both. One that sets
it in Python after import changes only the second. This is the most likely
mechanism behind arms that "came back byte-identical to their baselines."

**0.2 — Every filter short-circuits on first failure.**
Both `evaluate_entry_gate` and `_evaluate_option_liquidity` return at the first
failed check. **A count of any failure code is a count of "failed here *first*",
not "would fail here."** This is why volume/OI appeared to be the binding
constraint and were later measured inert at any value: open interest is checked
before spread, and cost is checked last of all.

To ask what a rule really costs, re-test the recorded contracts jointly. Never
read `CHAIN_BINDING_CODE` counts as cause.

**0.3 — The auto-paper path does not use the scanner's gate config.**
`app/runtime/paper_automation_support.py:984` builds its own `EntryGateConfig`
with two **hardcoded module constants**:

```
DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY = 65.0   # line 65
DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT     = 6.0    # line 66
```

Neither reads an environment variable. `OPTION_MAX_SPREAD_PCT` and
`OPTION_MIN_QUALITY_SCORE` reach live trades **only through contract selection**
(`options_filter`, which uses `settings.*`), not through this gate. Tightening
them below 6.0/65 works — but via a different code path than the one the failure
code names.

**0.4 — Two RR bars exist and only the higher one binds.**
`SCANNER_GATE_MIN_RR` defaults to **2.0** (`entry_gate.py:56`); `AUTO_PAPER_MIN_RR`
defaults to **1.8** (`paper_automation_support.py:64, 200`). The scanner refuses
first, so raising `AUTO_PAPER_MIN_RR` has no effect until it exceeds the scanner
floor. The code says so at `paper_automation_support.py:789-793`.

The same applies to setup: `SCANNER_GATE_MIN_SETUP` (70.0) vs `AUTO_PAPER_MIN_SETUP`
(`MIN_SETUP_BASE` = 62.0).

**0.5 — Cooldown and per-symbol limits are implemented twice.**
`paper_automation_support.py:1006-1010` and `dashboard.py:4431-4455` each carry
their own copy. Changing one does not change the other. The worker uses the
first; the dashboard entry path uses the second.

---

## 1. The path a trade takes

Six stages. A candidate must survive all six. Each stage names the file that owns
it.

```
1  UNIVERSE      app/config/watchlist.py        which symbols are scanned
2  SETUP         app/strategies/*               does a setup fire on the 15m frame
3  RISK          app/risk/risk_manager.py       stop, target, RR, trade_allowed
4  CONTRACT      app/options/options_filter.py  is there a tradeable option
5  ENTRY GATE    app/gates/entry_gate.py        does the candidate clear the bars
6  AUTO-PAPER    app/runtime/paper_automation_support.py   book-level limits
```

Then the position lives under `app/state/paper_trade_manager.py` and is closed by
`app/exit/exit_engine.py`.

**Where the funnel actually breaks:** contract selection (stage 4). Measured
2026-08-12 — 77 of 2,990 candidate rows were priced. Stages 5 and 6 see almost
nothing, so tuning them moves very little. Count *symbols*, not rows.

---

## 1a. The hard block nobody has a knob for: `avoid_chasing`

**This is the entry-side twin of the stop anchor in §2.2a of TRADE_QUALITY_PLAN,
and it is a harder constraint.**

`app/strategies/entry_engine.py:190-208` sets `avoid_chasing = True` when either:

```
|close - VWAP| / VWAP  >  1.5 %
|close - EMA9| / EMA9  >  1.2 %
```

`app/risk/risk_manager.py:891-897` then refuses the trade outright:

```python
if entry_setup.get("avoid_chasing", False):
    trade_allowed = False
    reasons.append("Avoid chasing extended move")
```

**Consequences, stated plainly:**

* A candidate more than **1.2% away from its own EMA9 cannot be traded, at all.**
  Not down-weighted — refused.
* This is the mechanism behind the observation that price sits 1.5–2.9 ATR above
  EMA9 during real trends while the app can only enter near the EMA. It is why a
  symbol that moved 5.67% (MU) or 7.33% (SMCI) in a session can produce no
  tradeable candidate.
* It is a plausible contributor to §2.2h — entry timing measuring **worse than a
  random minute**. The rule admits only moments when price has *not* moved, which
  at these horizons is where liquid megacaps mean-revert.

**Switched 2026-08-14 (Phase A task 1). Defaults are the old constants exactly,
so nothing moved.**

| knob | default | effect |
|---|---|---|
| `AVOID_CHASING_BLOCKS` | `true` | `false` lifts the refusal; the flag is still computed and archived |
| `AVOID_CHASING_MAX_EMA_DISTANCE_PCT` | 1.2 | **the binding one** — tighter than the VWAP band |
| `AVOID_CHASING_MAX_VWAP_DISTANCE_PCT` | 1.5 | |

**The two thresholds refuse independently.** Widening one alone leaves the other
binding — the failure mode that made three earlier arms identical to their
control. At 1.4% out, VWAP still permits and EMA9 refuses, so a VWAP-only arm
reads as a null. Pinned in
`tests/test_avoid_chasing.py::test_widening_one_band_alone_leaves_the_other_binding`.

**Changing any of them invalidates every archived candidate set**, because it
changes which candidates *exist*, not merely which ones pass. No archive spans a
change here.

The comment at `entry_engine.py:180-188` records that this block was long-broken
for shorts, so short setups had no chase protection at all until it was fixed.
Archived runs from before that fix are not comparable to later ones.

---

## 2. Stage 3 — Risk: stops, targets, RR

`app/risk/risk_manager.py::calculate_risk`

### The five setups, and which ones the scale reaches

`app/strategies/setup_registry.py` — `detect_entry` emits exactly five. Names
outside this list (VWAP_RECLAIM, COILED_BREAKOUT, …) appear in the code but their
detectors are commented out and **cannot occur**.

| setup | direction | stop anchor | `ATR_DISTANCE_SCALE` reaches it? |
|---|---|---|---|
| `EMA_PULLBACK` | CALL | `min(Low−atr·0.15s, EMA9−atr·0.10s)` | **yes** |
| `BREAKOUT` | CALL | `min(recent_low, entry−atr·mult·s)` | yes, via the multiplier |
| `EMA_REJECTION_SHORT` | PUT | `max(High+atr·0.15s, EMA9+atr·0.10s)` | **yes** |
| `BREAKDOWN_SHORT` | PUT | `max(recent_high, entry+atr·mult·s)` | yes, via the multiplier |
| `VWAP_REJECTION` | PUT | `max(VWAP+atr·0.15, High+atr·0.10)` | **no — hardcoded** |

`VWAP_REJECTION` (line 277) applies no scale at all. An arm setting
`ATR_DISTANCE_SCALE` leaves that setup completely unchanged, which dilutes an
already weak lever further.

The `structure_stops` branches require `stop_anchor="STRUCTURE"`, which **no
production caller passes** — every call site uses the `"SWING"` default. Those
branches are dead in live trading and in replay.

### Geometry per setup, and what scales

`ATR_DISTANCE_SCALE` (line 246) multiplies `stop_atr_multiplier`,
`target_atr_multiplier`, and the ATR *offsets* — but **not the bar's own high/low**:

```python
# EMA_PULLBACK, line 338
stop_loss = min(latest["Low"]  - atr*0.15*scale,
                latest["EMA9"] - atr*0.10*scale)
```

Because `latest["Low"]` does not scale and dominates on a 15m megacap bar, **a 4x
scale buys roughly a 1.5x stop.** Pinned in
`tests/test_distance_scale.py::test_the_scale_is_a_weak_lever_because_the_bar_dominates`.
Reaching a 3% stop needs a scale near fourteen.

**Do not propose `ATR_DISTANCE_SCALE=4` as a way to hunt larger moves.** The
higher-timeframe version of that idea was built (`app/risk/swing_anchor.py`),
measured, and closed — §2.2d of TRADE_QUALITY_PLAN: it works on the underlying
and loses to theta 7:1.

### The stop band

| regime | `max_stop_distance_pct` |
|---|---|
| default | 0.75% |
| HIGH_VOLATILITY | 1.15% |
| LOW_VOLATILITY | 0.50% |
| TRENDING | 0.95% |

Multiplied by `MAX_STOP_DISTANCE_SCALE` (line 654). Replaced outright — not
scaled — when `SWING_STRUCTURE_ENABLED` is on (line 661).

**`ATR_DISTANCE_SCALE` and `MAX_STOP_DISTANCE_SCALE` must move together.**
Widening alone is rejected by the cap and the arm returns unchanged; that failure
mode is pinned in `test_distance_scale.py:58`.

### Floors, and the order they apply

1. ATR floor: `atr * EMA_PULLBACK_ATR_STOP_MULT` (default 0.25), line 512
2. Price floor: `entry * MIN_STOP_DISTANCE_PCT/100` (default 0.50%), line 535
3. The wider of the two wins (line 538)
4. `REJECT_SUB_FLOOR_STOPS` (default **off**) refuses instead of inventing a stop

**Measured and refuted** on 310 trades: the floor binds on 178, and those trades
lose $23.6 each against $24.3 for structure-stopped ones. Do not re-open it from
the 12-trade sample that first looked decisive.

### Target floor

`TARGET_MIN_RR` (default 0.0 = off) extends the target to at least that multiple
of risk — **capped** by `TARGET_MAX_REWARD_ATR` (default 2.5), line 747.

**The cap is the whole safety of the feature.** Uncapped, it sets RR to exactly
`TARGET_MIN_RR` for every candidate that reaches it, which makes the RR gate check
a number the target was just adjusted to satisfy.

### Other hard refusals in this stage

* `reward < atr * 1.2` → `trade_allowed = False` (line 777)
* invalid price geometry → returns with `risk_reward: 0`
* `risk_per_share <= 0` → `"Invalid risk calculation"`

---

## 3. Stage 4 — Contract selection

`app/options/options_filter.py::_evaluate_option_liquidity`

**Exact order. First failure returns; nothing after it is evaluated.**

| # | code | threshold |
|---|---|---|
| 1 | `MISSING_BID_ASK` | `OPTION_REQUIRE_BID_ASK` (default false) |
| 2 | `DELAYED_QUOTE` | `quote_timeframe` |
| 3 | freshness code | `OPTION_REQUIRE_FRESH_QUOTE` (default false) |
| 4 | `STALE_QUOTE` / `DELAYED_QUOTE` | `quote_status` |
| 5 | `INVALID_BID_ASK` / provider codes | bid or ask <= 0 |
| 6 | `CROSSED_MARKET` | ask < bid |
| 7 | `INVALID_SPREAD` | spread not computable |
| 8 | **`LOW_OPEN_INTEREST`** | `OPTION_MIN_OPEN_INTEREST` (500) |
| 9 | **`LOW_VOLUME`** | `OPTION_MIN_VOLUME` (100) |
| 10 | **`WIDE_SPREAD`** | `OPTION_MAX_SPREAD_PCT` (6) |
| 11 | `EXPIRATION_0DTE_BLOCKED` | `OPTION_ALLOW_0DTE` (false) |
| 12 | `EXPIRATION_1DTE_BLOCKED` | `OPTION_ALLOW_1DTE` (false) |
| 13 | `LOW_OPTION_QUALITY` | `OPTION_MIN_QUALITY_SCORE` (65) |
| 14 | affordability | HARD mode + `OPTION_MAX_CONTRACT_COST` |

**Settled:** `OPTION_MIN_VOLUME` and `OPTION_MIN_OPEN_INTEREST` are **inert at any
value including zero** when re-tested jointly. Spread and cost are the real
constraints. Do not re-derive this from first-failure counts.

All fourteen read `settings.*` — see trap 0.1. They are fixed for the life of the
process.

---

## 4. Stage 5 — Entry gate

`app/gates/entry_gate.py::evaluate_entry_gate`. **Short-circuits.**

| # | code | source |
|---|---|---|
| 1 | `NOT_ACTIONABLE_STATUS` | `ENTER`/`ENTER_PAPER`/`REVIEW_TV_CHART` only |
| 2 | `INVALID_PRICE_GEOMETRY` | stop<entry<target (CALL), inverse (PUT) |
| 3 | `RR_BELOW_THRESHOLD` | `SCANNER_GATE_MIN_RR` (2.0), regime-raised |
| 4 | `SETUP_BELOW_THRESHOLD` | only if `SETUP_GATE_ENABLED` — **default off** |
| 5 | `ENTRY_TIMING_TOO_EARLY` | only if `ENTRY_TIMING_GATE_ENABLED` — **default off** |
| 6 | `OPTION_QUALITY_BELOW_THRESHOLD` | `config.min_option_quality` |
| 7 | `OPTION_QUOTE_NOT_LIVE` | must equal `LIVE_QUOTE` |
| 8 | `OPTION_NOT_AFFORDABLE` | `Affordable` column |
| 9 | `UNKNOWN_SPREAD` | paper mode passes if quality >= 80 |
| 10 | `SPREAD_TOO_WIDE` | `config.max_spread_pct`, regime-lowered |

**The spread check is last.** Its count therefore undercounts how many candidates
a tighter ceiling would remove.

### Regime escalation — only ever tightens

`apply_regime_entry_thresholds` (line 440). No combination can admit a trade the
base config refuses.

| condition | setup | RR | spread |
|---|---|---|---|
| `RANGE_BOUND` | >= 85 | >= 2.0 | <= 5.0 |
| breadth < -20 or above-EMA20 < 40 | >= 83 | >= 2.0 | — |
| reference `TRENDING_BEAR` + CALL | >= 83 | >= 2.0 | — |
| reference `TRENDING_BULL` + PUT | >= 83 | >= 2.0 | — |
| reference `HIGH_VOLATILITY` | >= 81 | >= 2.0 | — |
| VIX move >= `VIX_SPIKE_PCT` (8) | >= 83 | >= 2.2 | <= 5.0 |
| daily BEAR + CALL | >= 81 | >= 2.0 | — |
| daily BULL + PUT | >= 81 | >= 2.0 | — |

**Settled:** regime escalation is *not* the trade constraint. The funnel breaks at
contract selection.

**If `OPTION_MAX_SPREAD_PCT` < 5, the regime spread tightening is dead code** —
`min(2, 5)` is 2 either way.

### Setup thresholds

Defined once in `app/gates/setup_quality.py:89-93`. Do not restate them at a call
site; they are only meaningful against that scale.

```
MIN_SETUP_BASE          62.0
MIN_SETUP_ELEVATED      81.0
MIN_SETUP_WEAK_BREADTH  83.0
MIN_SETUP_RANGE_BOUND   85.0
MIN_SETUP_MULTIDAY      76.0
```

**Settled:** the setup score is **inverted** — low-scoring candidates win more,
p = 0.059, with the RR confound ruled out. That is why the gate defaults off.
Lowering the bar is not the fix; the band still loses net of the spread.

---

## 5. Stage 6 — Auto-paper book limits

`_auto_paper_entry_reason`, line 956. **Short-circuits.**

| # | reason | knob | default |
|---|---|---|---|
| 1 | `auto paper disabled` | `AUTO_PAPER_ENABLED` | |
| 2 | session block | window **09:45–15:30 ET** (lines 48-49) | |
| 3 | `not top candidate` | `AUTO_PAPER_MAX_CANDIDATE_RANK` | 5 |
| 4 | *entry gate* | see trap 0.3 — its own config | |
| 5 | realtime not ready | | |
| 6 | missing option bid/ask | | |
| 7 | `SPREAD_EXCEEDS_RISK` | `MIN_STOP_SPREAD_MULTIPLE` | 1.0 |
| 8 | `event blocked` | `EVENT_BLOCKER_ENABLED` | true |
| 9 | `regime blocked` | | |
| 10 | direction filter | `AUTO_PAPER_DIRECTION` | Both |
| 11 | `ALREADY_HOLDING_NO_ADDITIONAL_ENTRY` | — hard, no knob | |
| 12 | `SYMBOL_COOLDOWN_ACTIVE` | `AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES` | **60** |
| 13 | `MAX_TRADES_PER_SYMBOL_PER_DAY_REACHED` | `MAX_TRADES_PER_SYMBOL_PER_DAY` | **1** |
| 14 | `MAX_ACTIVE_PAPER_TRADES_REACHED` | `MAX_ACTIVE_PAPER_TRADES` | 3 |

Also: `MAX_DAILY_ENTRIES` (5), `MAX_ACTIVE_PER_DIRECTION` (2).

**Note the code default for `MAX_TRADES_PER_SYMBOL_PER_DAY` is 1**, not 2. The
deployed value is 2. Check the environment, not the default.

**Settled:** the per-symbol cap earns its keep (~2R plus ~85% option return per
window). Rule 11 (`ALREADY_HOLDING`) has no knob at all — a second entry while
holding is impossible regardless of the cap.

**EOD:** `AUTO_PAPER_EOD_CLOSE` is 15:55 ET (line 50), on by default. INTRADAY
profiles set `force_eod_exit=True`; MULTIDAY sets it False and holds.

---

## 6. Exit engine

`app/exit/exit_engine.py::evaluate_exit`

### Priority — highest wins when several fire

```
HARD_STOP          100
HARD_TARGET         95
PROFIT_PROTECTION   85
EMA                 80
VWAP                70
MACD                60
FAILED_BREAKOUT     50
TIME_EXIT           40
NEAR_CLOSE          30
```

### The momentum class

EMA, VWAP, MACD, FAILED_BREAKOUT. `EXIT_MOMENTUM_ENABLED=false` removes all four
at once (line 442) — the only way to remove them as a class.

**Settled, and do not re-open:** momentum exits are **loss-limiters, not profit
cutters**. Holding all momentum-closed trades to stop or target costs **−18.6R
(bull), −23.8R (bear)**. Removing them individually only redistributes: EMA exits
went 75 → 0 while MACD went 88 → 129 at nearly the same bar.

Any claim that one exit rule causes the losses is accounting, not cause.

### Knobs

| knob | default | effect |
|---|---|---|
| `EXIT_MOMENTUM_ENABLED` | true | all four momentum rules |
| `EXIT_EMA_ENABLED` | true | EMA rule alone |
| `EXIT_EMA_CONFIRM_BARS` | 0 | bars of confirmation before EMA fires |
| `EARLY_EXIT_GUARD_MAX_BARS` | 1 | suppresses weak exits in the first bars |
| `MULTIDAY_MOMENTUM_EXIT_MIN_BARS` | 2 | momentum disabled before this many bars |
| `EXIT_BREAKEVEN_TRIGGER_R` | 1.0 | move stop to breakeven at this R |
| `EXIT_BREAKEVEN_ON_PEAK` | false | trigger on MFE instead of current |
| `TIME_EXIT_BARS` | 8 | fires only when `rr_progress < 0.5` as well |
| `PROFIT_LOCK_MIN_MFE_R` | 1.0 | peak R needed before profit lock engages |
| `PROFIT_LOCK_MIN_TREND_HEALTH` | 70 | health below this and it does not engage |
| `PROFIT_LOCK_MAX_GIVEBACK_R` | 1.0 | R of the peak allowed to be given back |
| `PROFIT_LOCK_MAX_EXIT_CONFIDENCE` | 25 | above this the exit is honoured instead |

`PROFIT_PROTECTION` does not close the trade — it converts a momentum exit into a
ratcheting stop that only ever moves in the trade's favour.

`PROFIT_LOCK_ELIGIBLE_EXITS = {EMA, VWAP, MACD}` (line 94) — profit lock can only
convert those three.

---

## 7. What each measurement can and cannot see

| tool | reads | can judge | **cannot** judge |
|---|---|---|---|
| `tools/replay_forward.py` | fresh bars + chains from Polygon | the full pipeline including live `evaluate_exit` | anything needing the live book — **it never reads `paper_trades`** |
| `app/regression/historical_scanner.py` | `scanner_snapshot` from Postgres, parquet fallback | changes downstream of a recorded frame | anything upstream of the frame; **and it is optimistic twice over — see below** |
| `tools/null_model.py` | cached 5m bars | edge over a random entry minute | absolute returns — its levels are lookahead-contaminated, read only the difference |
| `tools/resolve_candidate_outcomes.py` | `scanner_snapshot` + later bars | did a refused candidate reach its target | whether the option would have paid |
| `tools/replay_option_leg.py` | recorded chains | what the contract returned | the underlying decision |
| live book (`paper_trades`) | production writes | what actually happened | anything, until every trade has option pricing — currently 19 of 37 |

### How the regression harness scores an exit — **repaired 2026-08-14**

It used to close a trade **only** on a stop or target touch, which was optimistic
twice over: it never applied the exit engine (so it reported the
hold-to-stop-or-target counterfactual, measured at **−18.6R bull / −23.8R bear**),
and when a snapshot interval touched both levels it tested `hit_target` first and
scored a **win**. Together those are the whole of the +3.22R it reported on
2026-08-13 for a day the book took −0.65R.

Both are fixed:

| | now |
|---|---|
| soft exits | `exit_engine_evaluator` drives the live `evaluate_exit`, rebuilding indicators from the archived `bars_15m` |
| ambiguous bar | **the stop wins** — intrabar order is unknowable at ~5-minute sampling |
| R | measured from the price it exited at, not hardcoded −1.0 / full target |
| priority | hard levels are tested before any soft exit, matching `EXIT_PRIORITY` |
| missing bars | evaluator returns `None`, caller falls back to stop/target — degrades, never guesses |

**`exit_evaluator=None` restores the old scoring**, which is a legitimate thing to
want: §1.6 of TRADE_QUALITY_PLAN measures that counterfactual deliberately. It is
simply not what the app does, so it is not the default.

**Regression results produced before 2026-08-14 are not comparable to results
produced after it**, and neither is comparable to a pre-repair live number.

**Measured immediately after the repair:**

| day | old | new | app booked |
|---|---|---|---|
| 2026-08-13 | +2.21R (n=3) | **−0.08R** (n=3) | −0.65R |
| 2026-08-14 | +6.71R (n=7) | +5.18R (n=12) | +0.10R |

The exit mix is now realistic — 2026-08-13 closes on two EMA exits and one stop,
where before it closed on two stops and a target — and the 08-13 gap falls from
2.86R to 0.57R.

**But a second divergence remains, and it is not about exits.** On 2026-08-14 the
harness opens **12 trades against the app's 5**. `_default_evaluator` calls
`evaluate_candidate` and never applies the stage-6 book limits — the entry
window, `MAX_TRADES_PER_SYMBOL_PER_DAY`, `MAX_ACTIVE_PAPER_TRADES`, the cooldown,
or `ALREADY_HOLDING`. So the harness still answers "what would these signals have
done" rather than "what would this app have done".

**Until that is closed, regression totals remain incomparable to live totals.**
Per-trade comparisons and A/B differences between two regression arms are
unaffected, since the same limits are missing from both.

**The critical one:** `replay_forward.py` contains no reference to `paper_trades`.
It generates its own trades from bars. So the two corrupted trades of 2026-08-13
never touched any replay result — but they did contaminate every analysis drawn
from the live book, including a withdrawn "+41.27R from removing momentum exits."

**Retention:** `scanner_snapshot`, `candidate_evidence` and `candidate_outcome`
keep **90 days** (`app/db/retention.py`); `activity_trace_event` keeps 7. A day
never frozen while its snapshots are alive can never be regressed afterwards —
which is why `maybe_freeze_regression_baselines` runs nightly.

---

## 8. Quick lookup: if you change X

| change | also moves | invalidates | already known |
|---|---|---|---|
| `AVOID_CHASING_BLOCKS` / `..._MAX_EMA_DISTANCE_PCT` / `..._MAX_VWAP_DISTANCE_PCT` | **which candidates exist at all** — a hard refusal in `calculate_risk` | every archived candidate set and every funnel count | switched 2026-08-14, defaults unchanged, never yet A/B'd; §1a |
| `OPTION_MAX_SPREAD_PCT` | contract selection **and** the entry gate — but **not** the auto-paper gate constant (trap 0.3) | every archived run at a different ceiling | 6→3→2 measured; 2 is best (+2.33sd). A1 says 3 loses on bull |
| `OPTION_MAX_CONTRACT_COST` | affordability, stage 14 of contract selection | option P&L comparisons | 1200→500 worth ~$187/session at an identical loss *rate* |
| `SCANNER_GATE_MIN_RR` | stage 5 only; regime can raise it further | every gate-pass count | 2.0 stands — raising and lowering both die on the outlier check |
| `AUTO_PAPER_MIN_RR` | nothing, while below the scanner floor (trap 0.4) | — | — |
| `ATR_DISTANCE_SCALE` | stop **and** target together; needs `MAX_STOP_DISTANCE_SCALE` too | all stop-distance and RR statistics | 4x buys 1.53x; the strong version lost to theta 7:1 |
| `MIN_STOP_DISTANCE_PCT` | the price floor, and RR through it | trades whose stop was invented | floor binds on 178/310; per trade indistinguishable |
| `TARGET_MIN_RR` | targets only, capped by `TARGET_MAX_REWARD_ATR` | RR distributions; risks making the RR gate a tautology | committed off, unmeasured |
| `SETUP_GATE_ENABLED` | stage 5 refusal; ranking is unaffected | — | score is inverted; leave off |
| `ENTRY_TIMING_GATE_ENABLED` | stage 5 refusal | — | score is inverted and survives controls, but 2-right-2-wrong on the one live day checked |
| `EXIT_MOMENTUM_ENABLED` | all four momentum rules at once | every exit-mix comparison | removing them costs 18.6R/23.8R |
| `MAX_TRADES_PER_SYMBOL_PER_DAY` | stage 6 only; rule 11 still blocks while holding | per-symbol frequency stats | earns its keep at 2 |
| `AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES` | stage 6, **and the duplicate in `dashboard.py`** | re-entry timing | 60 default, unmeasured |
| `SESSION_INTERVALS` | scan cadence **and the Neon bill** | scan-count comparisons across days | idle windows halved 2026-08-13; REGULAR left alone deliberately |
| `SCAN_PREMARKET_FROM` | **whether 04:00-09:00 is scanned at all**, so premarket rows stop existing | premarket funnel counts and any per-day scan-count comparison across the switch | 09:00 from 2026-08-22. 155 premarket scans over 08-10..21 passed 0 of 3,859 Decision rows; costs 1.20 CU-h/day, 97% autosuspend timer. Cannot touch entries — they open at 09:45 |
| `SCAN_IDLE_DB_INTERVAL_SECONDS` | how often an **idle** pass talks to Postgres; **not** scan cadence | nothing measured — idle passes scan nothing | 3600 from 2026-08-21; batch jobs keep their own once-per-date gates |
| `POSITION_MONITOR_MOMENTUM_ENABLED` | **when** EMA/VWAP/MACD/failed-breakout/time are asked, never what they may answer; adds a Polygon 1m call per held symbol per minute | exit-mix and time-to-exit comparisons across the switch | committed off, unmeasured |

`POSITION_MONITOR_MOMENTUM_ENABLED` deserves the same warning as any exit lever.
The scan's 15-minute frame is resampled from its own **5-minute** pull, so its
forming bar changes once every five minutes and running the rules more often
against it cannot make anything arrive sooner. Resampling from 1-minute bars
does: verified on ORCL over 3 sessions, all 80 completed 15m buckets are
identical to the last decimal from either source, so every measured result about
these rules still applies — the same signal, up to four minutes earlier.

What it buys those minutes from is a bar that has not finished, and the engine's
own note is that VWAP and MACD are bare state comparisons evaluated against
exactly that. The grace zone, profit lock and trend-health guard are left in
place unchanged for this reason. `MOMENTUM_EXIT_RULES` also bars this path from
closing on HARD_STOP, HARD_TARGET or NEAR_CLOSE: those are price levels, and the
20-second loop judges them against a live quote instead of a bar up to a minute
old.
| retention `keep_days` | what any archive tool can still see | every measurement older than the new window | raised 21→90 on 2026-08-13 |

---

## 9. Standing rules

1. **Check this file and TRADE_QUALITY_PLAN before proposing a lever.** Four
   proposals on 2026-08-13 were already closed in the repo.
2. **Never read a first-failure count as cause** (trap 0.2).
3. **Quote cash beside R, always.** R has flattered this book repeatedly — most
   recently by 100x on a single trade.
4. **Every mean carries a bootstrap CI and a mean-without-top-5.** Five trades of
   331 once carried 266% of a total.
5. **Judge against random, not zero** — `tools/null_model.py`.
6. **Same days in both arms.** `tools/compare_runs.py` intersects shared days
   because an arm once looked $1,889 better purely on which days it finished.
