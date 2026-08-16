# What a serious signal product has that this app does not

Written 2026-08-16, at the operator's request: compare the app against how
market signal-detection systems are actually built, and name what is missing in
signal quality, directional accuracy, and the action suggested.

**On the comparison itself.** This is against published, standard practice for
systematic signal systems — the methods are not secret, the implementations are.
Nothing here claims inside knowledge of any specific commercial product, and
anywhere this document says "top systems do X" it means "X is standard practice
and we can verify we do not do it".

Every claim about *our* code below was checked in the source on 2026-08-16 and
is marked **[verified]**. Everything forward-looking is marked **[proposal]**
and has not been measured. Per §5.6, a proposal is not a finding.

---

## 0. The frame, before any feature list means anything

The app's entry signal is **measurably worse than entering at random**.

| | reaches +10% on the option |
|---|---|
| random minute in the entry window | **20.7%** |
| the app's entries (§6, 2026-08-16) | **18.4%** |
| the app's entries (§5.14, pre-cutoff) | 15.6% |

The null model (§2.2h) puts the same result on the underlying: the entry rules
give up about a quarter of a percentage point against a random minute, at 8–20
standard deviations, agreeing between train and holdout, with 20/20 random draws
beating the signal at every horizon.

**This is the fact that orders everything else.** The question is not "which
feature would add edge to a working signal". There is no working signal to add
to. A feature list that ignores this produces a more elaborate way to lose.

The diagnosis in §2.2h is specific and is the thread this whole document pulls:
all five setups — EMA_PULLBACK, BREAKOUT, VWAP_REJECTION, BREAKDOWN_SHORT,
EMA_REJECTION_SHORT — **trigger after a move**, buying strength and selling
weakness, at horizons where liquid megacaps mean-revert.

---

## 1. What the signal actually sees today **[verified]**

Inventoried from source, not from documentation.

| layer | what exists |
|---|---|
| indicators | ATR, EMA, MACD, RSI, VWAP, relative volume (`technical_indicators.py`) |
| timeframes | 15m primary, with 5m, 1h and daily available |
| setups | 5, all momentum-continuation (`entry_engine.py`) |
| daily context | trend BULL / BEAR / NEUTRAL from EMA9 vs EMA20, plus realised vol |
| regime | TRENDING_BULL / BEAR labels; escalation only ever tightens |
| options data | full chain with delta, gamma, theta, vega (`live_options_chain.py`) |
| options use | **contract selection only** — ranking, affordability, spread, DTE |
| risk | earnings calendar, event blocker, stop viability, IV/RV richness gate |

And what returned **zero matches** across `app/`:

- no implied-volatility **rank or percentile** against the symbol's own history
- no **skew**, no **term structure**
- no **put/call ratio**, no **open-interest change**, no flow or unusual activity
- no **news, headline, sentiment or catalyst** input to the signal
- no cross-sectional ranking of the universe against itself
- no calibrated probability attached to a signal

The greeks are computed and then used to *pick a contract*. They never inform
*whether to trade*.

---

## 2. The gaps, ordered by evidence × cost

### 2.1 "Relative strength" is not relative to anything — **a live bug** [verified]

`app/strategies/momentum_strategy.py:211-241`:

```python
symbol_move = latest["SYMBOL_MOVE_PCT"]

# Compare against market benchmark
# Tech names → QQQ style behavior
benchmark_move = 0

if symbol_move > benchmark_move + 0.5:
    relative_strength += 1
    bullish_reasons.append("Strong relative strength")
elif symbol_move < benchmark_move - 0.5:
    relative_strength -= 1
    bearish_reasons.append("Relative weakness vs market")
```

`benchmark_move` is hardcoded to `0` and never reassigned. The comment names
QQQ; no benchmark series is ever fetched.

So the rule reads **"is the symbol up more than 0.5% today"** and calls that
relative strength. On a day when QQQ is up 1.5%, a symbol up 0.6% is lagging the
market badly and this scores it **bullish**.

Verified live, not dead code:

- `app/main.py:59` imports `analyze_setup` from this module
- `SYMBOL_MOVE_PCT` is genuinely populated at `technical_indicators.py:1346`, so
  the `try/except` does not silently swallow it
- `score += relative_strength` at line 241 feeds the composite setup score

Two consequences. The score is polluted by an input that does not measure what
it is named — and the setup score is already measured **non-predictive and
inverted** (low setup wins more, p=0.059). A mislabelled component is one
plausible contributor, though this has not been isolated. The string
**"Relative weakness vs market"** is also a reason line, so a claim the app
cannot support can reach a subscriber.

**Correction, 2026-08-16, found while implementing the fix.** The first version
of this section said the benchmark would have to be fetched. It does not.
`main.py:1656` already has `_sector_strength()`, which computes
`symbol_move_pct − sector_reference_move_pct` against the symbol's sector ETF
(SMH / XLK / XLF / XLE) and labels it LEADING / LAGGING / NEUTRAL at ±0.75. It
runs at line 4590, **before** `analyze_setup` at 4648, and every watchlist
symbol maps to a sector whose reference is genuinely fetched.

So the app has computed correct relative strength all along, and **records it as
telemetry without ever feeding it back into the score.** Consumers are
`v2_learning_dataset`, the dashboard, `paper_trade_manager` and
`entry_snapshot` — display and analytics only. It gates nothing.

That makes this cheaper than stated and stranger than stated: the correct
measure exists and is inert, while the broken one feeds the score.

**Fixed 2026-08-16**, behind `RELATIVE_STRENGTH_BENCHMARK_ENABLED`, default
**false**. `analyze_setup(df, benchmark_move_pct=None)` treats None as zero, so
with the switch off the score is byte-identical to before and the change is
inert in a live session. No extra API call: the value was already in hand.

#### A/B run 2026-08-16 — **the corrected version does not win. Leave it off.** [verified]

`tools/relative_strength_ab.py`. Both inputs are archived per snapshot —
`Symbol Move %` and `Sector Reference Move %` — so neither arm is
reconstructed. 3,850 usable rows across 9 sessions (2026-07-28..08-14); 13,219
snapshots dropped for having no forward bar, mostly at or after the close. The
arms disagree on **1,402 rows, 36.4%**.

Measured as the spread in the underlying's forward return between the rows the
arm scored +1 and the rows it scored −1. A directional input has to show
forward return rising with its own value.

| horizon | OLD (benchmark 0) | NEW (sector) | delta | sessions NEW wins |
|---|---|---|---|---|
| 15 min | +0.1073 | +0.0776 | **−0.0298** | 4/9 |
| 30 min | +0.1943 | +0.1371 | **−0.0572** | 4/9 |
| 60 min | +0.3003 | +0.2328 | **−0.0674** | 6/9 |
| 120 min | +0.3923 | +0.3906 | −0.0017 | 7/9 |
| 180 min | +0.4367 | +0.4578 | +0.0212 | 7/9 |

**It loses at every horizon the app actually trades** and only edges ahead at
three hours. Every delta is far inside the noise: the 95% CI on the 60-minute
NEW spread, resampled by day, is [+0.0257, +0.4704] — a width of 0.44pp against
a delta of 0.067pp.

On the 1,402 rows where the two disagree — the only rows the switch changes —
the 60-minute NEW spread is **−0.1375pp**, the wrong sign, against OLD's
+0.2083pp. That CI is [−0.4254, +0.1829] and straddles zero, so the honest
statement is **"not better", not "actively worse"**.

**Why, and this is the part worth keeping.** The old arm is
"is this name up today" — pure absolute momentum, which carries the market and
sector move inside it. The corrected arm subtracts the sector move, which at
15–60 minute horizons is *most of the forward return*. It removes the component
that predicts and keeps the idiosyncratic residual, which does not.

So this is not "relative strength is worthless". It is: **sector-relative
strength is not an intraday directional input at this horizon, and the app
trades intraday.** If it is worth anything it is at multi-hour horizons.

Third result in a row consistent with the composite score being non-predictive
(see also [[setup-score-is-not-predictive]] and §2.2h). The switch stays off,
and the bug stays fixed behind it — the code is now correct and the correctness
is measurably not an improvement, which is worth knowing separately.

### 2.2 The universe is never ranked against itself [verified]

**Narrower than first written** — see the correction in §2.1. The app does
compare each symbol to its *sector ETF*. What it never does is compare the 23
**to each other**: it never asks which of them is strongest right now.

This is the largest structural difference from standard practice. In the
published equity literature momentum is a **cross-sectional** effect — long the
strongest names relative to peers, short the weakest — not a time-series one.
The app implements the time-series version, which is the version that works
least well, and does so on megacaps, where it works least well of all.

Sector-relative and cross-sectional are not the same thing. A name can beat its
sector while the whole sector lags every other one in the universe.

**[proposal]** Rank the 23 by return relative to the index over a lookback each
scan, and require a candidate to be in the top or bottom band of its own
universe. Costs nothing in data — all 23 symbols' bars are already in hand each
cycle. Unmeasured.

### 2.3 Direction and movement are still not separated at the gate [verified]

§5.14 established the point and it is worth restating, because the app still
does not act on it: **for a bought option, direction and movement are different
properties.** A 50/50 trade that travels is a good option trade; a 60/40 trade
that barely moves is a bad one.

The app gates on direction and on setup quality. It does not gate on **expected
movement against what the option charges for movement**.

This is the one genuinely options-specific edge available, and the seed is
already in the repo: `app/risk/iv_richness.py` computes an IV-to-realised-vol
ratio. It is a diagnostic with an optional gate, and `IV_RICHNESS_ENFORCE`
is **false**.

**[proposal]** Promote it from diagnostic to signal: compare the move the setup
implies to the move the option's IV is pricing, and refuse when the option is
charging more for movement than the setup can plausibly deliver. This is the
standard test for whether buying premium is justified at all. Unmeasured here,
and §5.14's warning applies — `iv` was the least stable of the six conditions
tested, best quintile Q2 in discovery and Q1 in holdout.

### 2.4 There is no mean-reversion family at all [verified]

Five setups, all continuation. The measured failure mode is **buying strength
into mean reversion**. The app has no strategy that profits from the behaviour
it is demonstrably losing to.

**[proposal]** This is the most direct answer to §2.2h and also the largest
piece of work, because a new setup family needs its own stop geometry, its own
target logic and its own replay validation. It is not a knob.

### 2.5 There is no catalyst layer [verified]

Nothing in the signal pipeline reads news, headlines, sentiment or an events
feed. Confirmed by search; §6 already names it "the largest untouched item", and
records that Polygon serves news on the current plan.

Purely technical intraday signals on liquid megacaps are the most crowded and
most arbitraged corner of the market. A catalyst layer is what separates "this
chart looks like it will go up" from "there is a reason this will move today".

**[proposal]** Largest gap by the operator's own requirement list, and a genuine
integration rather than a tuning change.

### 2.6 The output is a score, not a probability [verified]

The app emits a setup score and a rank. It does not emit **P(target before
stop)**, calibrated against outcomes on a holdout.

Standard practice attaches a calibrated probability to every signal, because
without one there is no principled way to set a bar, size a position, or tell a
subscriber how much to trust a given alert. The app has the machinery —
`rank_outcome_calibration.py` and `replay_calibration.py` exist — but the
calibration is analysis after the fact, not a number the signal carries.

**[proposal]** Correctly sequenced **last**. Calibrating the current score would
faithfully reproduce a signal that is worse than random. This becomes worth
doing only once something upstream has edge to calibrate.

---

## 3. Where the app is already ahead of typical practice

Recorded so the list above is not read as uniformly negative.

- **It has a null model.** `tools/null_model.py` compares against a matched
  random entry rather than against zero. Most retail-facing systems never do
  this, and it is the single most valuable piece of measurement infrastructure
  here. It is also what produced the uncomfortable finding in §0.
- **Honest fills.** Buy the ask, sell the bid. Mid-to-mid backtests are the
  standard way options strategies look profitable and are not.
- **Top-5 strip, day-resampled bootstrap CI, and per-symbol concentration
  checks** as survival tests on every result.
- **A complete lever map.** `CHANGE_IMPACT_MAP.md` §8 documents every knob, what
  it also moves, and what it invalidates.

That last point matters for reading this document: **the app has not run out of
ideas, it has run out of knobs.** Every lever in §8 has been measured. Nothing
proposed above is a setting — each is information the app currently never sees.

---

## 3a. Metrics — what is measured, and what standard practice measures too

Asked 2026-08-16. Checked by searching `app/` and `tools/`, not assumed.

**Already computed**, and it is a respectable set: `win_rate`, `average_r`,
`total_r`, `profit_factor`, `max_drawdown_r`, `net_win_rate`,
`total_option_pnl_pct`, `average_spread_cost_pct` (`performance_statistics.py`),
plus expectancy, rank-outcome calibration by rank bucket, spread calibration,
AUC inside the A/B tools, and MFE / MAE / capture in `trade_exit_analysis`.

It gets the hardest part right: **R and premium are reported separately**, which
is the lesson [[r-multiple-flatters-the-book]] cost 291 trades to learn.

**Returning zero matches:** `information_coefficient`, `spearman`, `rank_corr`,
`turnover`, `sharpe`, `sortino`, `calmar`, `kelly`, `brier`.

### 3a.1 Information Coefficient — the one that matters here [verified absent]

The standard metric for a *signal* product, and the app has no form of it. IC is
the rank correlation between the signal's strength and the subsequent forward
return, computed per period and averaged across periods.

Why it matters more than anything else on this list: **it is computable on every
candidate the scanner ever emitted, not only on trades that were taken.**

| population | rows |
|---|---|
| `scanner_snapshot` with full decision payload | **17,069** |
| `candidate_snapshot` | 25,457 |
| `candidate_outcome` | 883 |
| `paper_trades` — what the headline stats read | **42** |

`rank_outcome_calibration` buckets outcomes by rank, which is adjacent, but it
reads realised trades. **The app grades its signal on 42 trades while holding
17,069 scored snapshots.** IC uses the large population and is a continuous
measure rather than a bucketed one.

**[proposal]** Compute IC per session over the archive, against forward returns
at several horizons. Cheap — `tools/relative_strength_ab.py` already assembles
the forward-return series it needs.

### 3a.2 IC decay by horizon [verified absent]

Nothing measures *which holding horizon the signal works at*. This is not
theoretical: the relative-strength A/B run on 2026-08-16 produced exactly this
curve as a by-product and it was the informative part — the sector-relative arm
was negative at 15–60 minutes and only turned positive at 180.

A standing horizon curve answers a question nobody has asked directly: **are the
exits matched to the horizon at which the signal actually predicts?** §0 says
entries are worse than random at 12, 78 and 234 bars, so the honest expectation
is that no horizon works. That is worth establishing rather than assuming.

### 3a.3 Signal turnover and stability [verified absent]

How often the signal flips direction on the same name, and how long a candidate
survives before being re-rated. `candidate_persistence` exists as a field, so
this is half-instrumented. High turnover on a weak signal is a mechanism for
bleeding capital that none of the current metrics would show.

### 3a.4 Sharpe, Sortino, Calmar — named, and ranked low [verified absent]

Standard everywhere, and genuinely less useful here. They describe a continuous
equity curve; this book takes ~3.4 trades a session with no continuous
exposure, and **return on capital deployed** is already the primary metric by
§3 of the trade quality plan — which is the more honest measure for an
instrument whose cost is paid per trade.

Worth adding once there is a positive return to risk-adjust. Not before, because
the risk-adjusted version of a negative number tells you nothing new.

### 3a.5 The survivorship problem, stated plainly

The headline statistics describe **trades the app chose to take, that passed
every gate**. A signal product's quality is a property of everything it emitted.
Fixing this is mostly reporting rather than new computation: `candidate_outcome`
already holds 883 rows, and the snapshot tables hold far more.

This is the cheapest large improvement to measurement on the list, and it should
land alongside the Phase 1 instrumentation in `PATH_TO_PRODUCTION.md`.

---

## 4. Do not revisit these

Already refuted, with the checks recorded (§7.4, §2.5, §5.14):

- a correlation gate on concurrent positions — 10 overlapping pairs, median
  effective correlation −0.13
- a range-**placement** filter — quintiles flat, dropped trades did better.
  Note this is *not* the same as "range already used", which survives as a
  time-of-day effect
- raising the cost cap to $4,000 — those contracts are blocked by
  `OPTION_MAX_DTE=30`, not by cost
- a predictor for the 48% that never move — one holdout already failed, and the
  ceiling is +0.20% even with perfect execution
- lowering the setup bar because the score is inverted — the band still loses

---

## 5. Suggested order

Judged by evidence it matters, divided by cost. Each still has to clear §3 of
the trade quality plan before it ships.

| | item | cost | why here |
|---|---|---|---|
| 1 | fix the hardcoded benchmark (§2.1) | hours | it is a bug, and it is emitting a claim to subscribers |
| 2 | cross-sectional ranking (§2.2) | days | data already in hand; the version of momentum that actually works |
| 3 | expected vs implied move (§2.3) | days | data already in hand; the only options-specific edge available |
| 4 | mean-reversion family (§2.4) | weeks | answers §2.2h directly, but needs its own geometry and validation |
| 5 | catalyst layer (§2.5) | weeks | biggest gap by the requirement list; a real integration |
| 6 | calibrated probability (§2.6) | after 1–5 | nothing worth calibrating until something upstream has edge |

**The honest summary.** Items 1 to 3 are cheap and use data the app already
holds. None of them is likely to be sufficient on its own, because §0 says the
deficit is structural rather than a matter of a missing feature. Items 4 and 5
are where a real signal would have to come from, and both are projects rather
than changes.
