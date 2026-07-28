# Dravya Trade Works — Remediation Execution Plan

**Purpose:** convert the project review into an ordered set of Claude Code sessions, each with a fixed model, a fixed scope, and a binary done-condition.

**Drop this at `docs/EXECUTION_PLAN.md`.** Reference it at the start of every session:
`Read docs/EXECUTION_PLAN.md. We are executing session S1.3 only. Do not touch anything outside its scope.`

---

## 0. Read this first

Three rules that make the rest work:

1. **Quality is protected by process, not by model tier.** With the invariants in §1 enforced, a Sonnet session cannot damage the app. Without them, an Opus session can.
2. **Opus sessions produce documents. Sonnet sessions produce code.** Session token cost is dominated by file reading, not by thinking. Never let Opus read the repo — feed it an artifact a cheaper model generated.
3. **One session, one scope, one branch.** `/clear` between sessions. Merge only on a green suite.

**Measurement freeze, effective now.** No new analytics modules, dashboard pages, scoring layers, or telemetry tables until Phase 5 completes. The existing strategy freeze created a loophole that routed all effort into observability; this closes it. Adding a 31st analytics module while expectancy is unmeasurable is the failure mode this plan exists to stop.

---

## 1. Global invariants

Put these verbatim in `CLAUDE.md`. They are the quality guarantee.

| # | Invariant |
|---|---|
| I1 | **No V1 entry / exit / risk logic changes** in Phases 0–6. Everything before Phase 7 is measurement. Any diff touching `momentum_strategy`, `entry_engine`, `risk_manager`, or `exit_engine` decision logic is out of scope and must be refused. |
| I2 | **Dual-compute before replace.** Every new number is emitted *alongside* the old one, reconciled on archived data, and only then promoted. Nothing is swapped in place. |
| I3 | **Characterization tests first.** Pin current behaviour before refactoring it. |
| I4 | **`REAL_TRADING_ENABLED=false`** for the entire program. No exceptions, no temporary flips. |
| I5 | **Test count only rises.** 134 is the floor. Full suite green before any merge. |
| I6 | **Behaviour changes ship dark.** Anything that could alter trade selection lands behind a flag, defaulted off, logging the counterfactual. |

---

## 2. Model routing policy

Set `/model opusplan` as your default. It runs Opus during plan mode and switches to Sonnet at execution automatically — which matches the shape of most sessions here.

**Three exceptions where you should run a dedicated Opus session instead**, because the output is a document and Opus should never read the repo to produce it: **S0.2, S1.1, S5.1**.

**Session archetypes:**

| Archetype | Model | Effort | Reads repo? | Output |
|---|---|---|---|---|
| **Survey** — inventory, call-graph, grep-at-scale | Haiku or Sonnet | default | Yes, heavily | A markdown artifact |
| **Design** — specs, architecture, invariants | Opus | high | No — reads the survey artifact | A markdown spec you review |
| **Build** — implement an approved spec | Sonnet | default | Yes | Code + tests |
| **Reconcile** — explain divergence between old and new numbers | Opus | high | No — reads a diff report | A written judgement |
| **Chore** — config edits, renames, doc fixes | Haiku or Sonnet | default | Narrow | Small diffs |

Subagents inherit their own model. Configure a fast explorer subagent for Survey work so the main session's context stays clean.

**Effort:** default for Build and Chore. High only for Design and Reconcile. Raising effort on a Build session buys almost nothing and costs a lot.

---

## 3. Phase map

| Phase | Theme | Review items closed | Sessions | Gate |
|---|---|---|---|---|
| 0 | Foundation | 19 (docs) | 4 | Docs split, contradictions resolved, characterization suite green |
| 1 | Trade economics | 1, 2 | 6 | Net-of-cost, option-denominated P/L reconciled and default |
| 2 | Evidence integrity | 3, 4 | 5 | Headless recording, versioned evidence, enforced freeze |
| 3 | Metric realignment | 17 | 3 | Expectancy primary, capture demoted |
| 4 | Universe & risk hygiene | 12, 13, 15 + universe findings | 5 | Sector cap, auto earnings, kill-switch |
| 5 | Replay & backtest validity | 5, 6, 7 | 7 | Backtest reconciles against a live paper day |
| 6 | Statistical governance | 16, 18 | 3 | Power-based targets, FDR, holdout, kill criterion |
| 7 | Rule simplification | 8, 9, 10, 11, 14 | 6 | Baseline simplified, weights derived from data |

**Phases 0–4 are safe to run back-to-back.** Phase 5 is a project. Phase 6 is analysis. **Phase 7 must not start until Phase 5 produces a validated sample** — arriving there early is failure, not progress.

---

## Phase 0 — Foundation

Nothing else is efficient until this is done. S0.2 is the single highest-leverage Opus spend in the program: it shrinks the context cost of every later session.

| ID | Session | Model / effort | Input | Done when |
|---|---|---|---|---|
| **S0.1** | Read-only repo survey → `docs/_inventory.md`: module list, call graph for every P/L computation site, full config surface with current values, test map | Haiku or Sonnet / default | Repo | Artifact exists. **Zero files modified.** |
| **S0.2** | Author `STRATEGY.md`, `METRICS.md`, `ARCHITECTURE.md`, `OPERATIONS.md`, `DECISIONS.md`, and a ~100-line `CLAUDE.md` | **Opus / high** | `_inventory.md` + existing README/Project_state **only** | Every contradiction in §7 resolved; parameter table exists with a provenance column |
| **S0.3** | Characterization tests: pin current outputs of risk manager, exit precedence, price geometry, option selection, position sizing on fixed inputs | Sonnet / default | Repo + `ARCHITECTURE.md` | Suite green, +20–40 tests |
| **S0.4** | HSR storage audit: measure actual `market_payload` growth/day; check whether `candles_5m.csv` re-appends overlapping windows | Sonnet / default | Repo + a real day's archive | Measured MB/day written into `OPERATIONS.md`; duplication confirmed or refuted |

**Human gate:** you read and edit the parameter table in `STRATEGY.md`. Any parameter you cannot justify gets marked `PROVENANCE: none` — that list becomes the Phase 7 deletion candidates.

---

## Phase 1 — Trade economics (review items 1 & 2)

The phase where a silent error is most expensive. A cost model that is off by a factor produces plausible expectancy numbers and no error message — reproducing your current situation with more confidence attached.

| ID | Session | Model / effort | Done when |
|---|---|---|---|
| **S1.1** | **Design only.** Spec for `app/economics/trade_costs.py` + `app/economics/option_pnl.py`: signatures, inputs, Black-Scholes repricing assumptions, spread-crossing model, commission model, rounding, error behaviour, full fixture list. **No code.** | **Opus / high** | You have read and edited the spec |
| **S1.2** | **You, on a calculator.** Hand-compute three trades: one winner, one loser, one stop-out. Explicit premium, bid/ask, contracts, commission per leg, resulting net R. | **Human** | Numbers written into the spec before any code exists |
| **S1.3** | Implement both modules + unit tests asserting S1.2's numbers | Sonnet / default | Tests pass against **your** arithmetic, not the model's |
| **S1.4** | Wire in behind `COST_MODEL_ENABLED`. **Dual-emit** `r_multiple_gross` and `r_multiple_net`, `pnl_underlying_est` and `pnl_option_est`, everywhere P/L is computed | Sonnet / default | Both columns present; no existing field altered |
| **S1.5** | Replay an archived day through both paths; produce a divergence report; explain every material gap | **Opus / high** | Divergences *explained*, not merely listed |
| **S1.6** | Flip defaults; mark the linear `R × risk_at_stop` path deprecated (do not delete) | Sonnet / default | Suite green; old path warns on use |

**Design decision you must make in S1.1:** Black-Scholes repricing from stored Greeks (≈1 day, ±15% accurate over an intraday hold) versus historical option quote replay (weeks, high fidelity). **Recommendation: BS first.** It is directionally honest in a way the current linear map is not. Add quote replay in Phase 5 when the backtester needs it.

**Also produce in this phase:** the DTE × delta study — realized option return per 1R of underlying move. You already hold the data. It answers whether 14–30 DTE contracts on an EOD-closed intraday strategy make any sense, and it is the input to review item 14.

---

## Phase 2 — Evidence integrity (review items 3 & 4)

Your evidence record is currently a function of browser uptime. Fix that before collecting another day.

| ID | Session | Model / effort | Done when |
|---|---|---|---|
| **S2.1** | Extraction plan: exact boundary between auto-paper decision logic and the Streamlit caller | **Opus / high** (Plan mode, short) | Plan reviewed |
| **S2.2** | Extract to a standalone headless entrypoint + scheduler | Sonnet / default | Runs with the dashboard closed |
| **S2.3** | **Parity harness:** identical scanner input → dashboard path and headless path must produce byte-identical decisions | Sonnet / default | Parity test in the suite |
| **S2.4** | Run both paths in parallel for 3–5 sessions, diff daily, then retire the dashboard path | Sonnet / default | Zero diffs across all sessions |
| **S2.5** | Stamp `strategy_version` (hash of V1 decision-relevant config + logic) on every trade, alert, and evidence row. Segment all evidence counters by version. Convert the validation freeze into a **code gate** that fails CI on an unapproved V1 logic diff. | Sonnet / default | Counters reset per version; CI blocks V1 edits |

**S2.3 is the entire quality guarantee for this phase.** Do not skip it — this path records the evidence everything else depends on.

**Backfill note:** existing trades pre-date versioning. Stamp them `v0-unversioned` and exclude them from any promotion decision rather than guessing.

---

## Phase 3 — Metric realignment (review item 17)

| ID | Session | Model / effort | Done when |
|---|---|---|---|
| **S3.1** | Migration inventory: every module, dashboard, report and verdict keyed on Trend Capture % → **keep / demote / delete** decision each | **Opus / high** | Decision recorded per item in `METRICS.md` |
| **S3.2** | Add net-of-cost expectancy per trade as primary; demote capture, TES, left-on-table to diagnostics | Sonnet / default | Both visible; capture explicitly labelled "diagnostic — underlying only" |
| **S3.3** | Update Validation, Reports, Trade Doctor, and Engineering Recommendation surfaces | Sonnet / default | Suite green; no surface presents capture as performance |

**Why:** `max(0, min(100, final_R / MFE_R))` scores a +0.1R trade at 100%, floors losers at zero, and rewards exiting early. Optimising it pushes you away from the asymmetric tails that pay for long premium.

---

## Phase 4 — Universe & risk hygiene (items 12, 13, 15)

Mostly cheap. One item is a genuine behaviour change and ships dark under I6.

| ID | Session | Model / effort | Note |
|---|---|---|---|
| **S4.1** | Ticker audit — resolve or remove `SPCX`; reconcile the "16 names" text against the 26-symbol list; add a startup validation that every watchlist symbol returns data | Haiku or Sonnet / default | Chore |
| **S4.2** | Sector map + concurrent sector-exposure cap | Sonnet / default | **Flagged off. Shadow-log what it would have blocked.** Semis are ~half your list; concurrent NVDA + AMD + SOXL is one trade at 3× size. |
| **S4.3** | Replace the VIXY regime proxy with SPY realized volatility / ATR% | Sonnet / default | Dual-emit both regime labels first (I2) |
| **S4.4** | Automated earnings & event calendar replacing manual `EVENT_BLOCKER_DATES` | Sonnet / default | Manual list becomes an override, not the source |
| **S4.5** | Code-enforced kill-switch + daily loss limit + max open risk | Sonnet / default | Enforced in the risk path, not just config |

**S4.2 is the only Phase 0–4 change that can alter trade selection.** Flag off, log the counterfactual, decide with data in Phase 7.

---

## Phase 5 — Replay & backtest validity (items 5, 6, 7)

This is the phase that breaks the sample-size constraint. At 1–3 paper trades/day you need years to reach statistical power; the backtester is the only escape, and it is currently your least developed component.

| ID | Session | Model / effort | Done when |
|---|---|---|---|
| **S5.1** | Architecture + correctness invariants: no-lookahead guarantees, **intrabar stop-first rule**, fill model consuming Phase 1 economics, dataset schema, walk-forward design | **Opus / high** | Invariants written down and reviewed |
| **S5.2** | Remove the "ignore stop hits in the first 2 candles" exemption from replay | Sonnet / default | Removed, or reported as a separate labelled sensitivity |
| **S5.3** | **MAE analysis** — distribution of maximum adverse excursion for eventual winners vs current stop distance, ATR-normalised | Sonnet / default | Chart + table produced |
| **S5.4** | Multi-day, multi-symbol dataset ingestion | Sonnet / default | ≥6 months across ≥2 regimes |
| **S5.5** | Fill engine + no-lookahead scanner integration | Sonnet / default | Backtest uses live indicator code |
| **S5.6** | Walk-forward harness with a **held-out period never used for tuning** | Sonnet / default | Holdout is enforced in code, not convention |
| **S5.7** | **Acceptance test:** replay a day you traded live; reconcile backtest output against the paper record trade-by-trade | **Opus / high** | Every divergence explained |

**S5.3 is the highest-value analysis available to you and needs data you already have.** If the median MAE of eventual winners exceeds your current stop distance, your stop sits inside the noise band and no amount of entry refinement will help. Run it early in the phase, not last.

**S5.7 is the acceptance test for the entire program.** If backtest and live paper do not reconcile on a known day, nothing downstream is trustworthy and Phase 7 does not start.

**Also record in the backtest report:** the watchlist was selected in 2026 with knowledge of which names were liquid and trending. Every historical result is optimistic by an unquantified margin. State it; do not hide it.

---

## Phase 6 — Statistical governance (items 16, 18)

Analysis and policy, minimal code.

| ID | Session | Model / effort | Done when |
|---|---|---|---|
| **S6.1** | Replace "20 evidence days / 80 completed trades" with power-based targets. Compute required n for the effect size you actually expect. Define "lift" precisely. | **Opus / high** | `METRICS.md` states required n and the detectable effect size at your current sample |
| **S6.2** | Implement promotion guards: minimum n≥30 per grouped cell, FDR control across the feature registry, mandatory holdout evaluation | Sonnet / default | Promotion is blocked in code when guards fail |
| **S6.3** | Write the **kill criterion** into `DECISIONS.md` | **Opus / high** (short) | Explicit rule, e.g. *if after N net-of-cost trades the expectancy CI upper bound is below 0.05R, the setup family is retired* |

**Sizing reality to write down in S6.1:** with per-trade R standard deviation around 1.0–1.3, detecting a true 0.1R edge takes on the order of a thousand trades. At n=80 you can only distinguish roughly 0.3–0.4R/trade from zero — far larger than intraday momentum on mega-cap tech plausibly offers. The current promotion thresholds are not conservative; they are underpowered by an order of magnitude.

---

## Phase 7 — Rule simplification (items 8, 9, 10, 11, 14)

**Entry condition: S5.7 passed and a backtest sample of ≥1,000 trades across ≥2 regimes exists.** Not before.

| ID | Session | Model / effort | Note |
|---|---|---|---|
| **S7.1** | Collapse the validation baseline to three exits — hard stop, hard target, time/EOD. Every soft exit moves to shadow with counterfactual logging. | **Opus / high** design, Sonnet build | Ten exit paths across ~80 trades gives ~8 samples each; attribution is impossible. This is the highest-value strategy-side simplification available. |
| **S7.2** | Measure each shadow exit overlay independently against the simplified baseline | Sonnet / default | Each overlay gets its own expectancy delta with CI |
| **S7.3** | Replace hand-weighted TQS (25/20/20/15/10/10) and Entry Timing (35/20/20/10/10/5) with outcome regression — or drop them | **Opus / high** design | Weights were invented before a single outcome existed |
| **S7.4** | Collinearity audit: EMA alignment, price-above-EMA9 and MACD are near-redundant. Cut the duplicates. | Sonnet / default | Composite scores stop double-counting one signal |
| **S7.5** | Convert the conjunctive gate stack to score-and-rank + a minimal set of hard safety gates (geometry, liquidity, event) | **Opus / high** design, Sonnet build | ~8 AND-gates at 70–85% each ⇒ 10–25% joint pass. The first-blocker attribution infrastructure is a *symptom* of this architecture. |
| **S7.6** | Delete every parameter marked `PROVENANCE: none` in Phase 0; consider debit spreads based on the Phase 1 DTE × delta study | **Opus / high** | Fifty free parameters against <100 observations is a curve-fit even with no optimiser involved |

---

## 4. Token optimisation

| Practice | Effect |
|---|---|
| Do Phase 0 first | Two overlapping 900-line docs loading into every session is the largest avoidable waste in the project |
| `/clear` between sessions, always | Phase 1 context has no business in Phase 2 |
| `/compact` at ~60% context | Not at 90%, when quality has already degraded and you are paying for a confused agent |
| Opus sessions read artifacts, never the repo | If an Opus session opens more than ~5 files, stop it — that work belongs to a Survey session |
| Subagent with a fast model for all Survey work | Keeps the main context clean |
| `/model opusplan` as default | Automatic Opus-plan → Sonnet-execute switch; start execution from a compacted context so the handoff re-read is small |
| One branch per session | Failed sessions are discarded cheaply instead of contaminating later ones |
| `/status` at every session start | Confirms model, billing route, and context headroom before you commit to a long run |

**Rough spend shape:** ~12 short Opus sessions across the whole program; everything else Sonnet or Haiku. Phases 0–4 should fit inside a Pro plan. Phase 5 is where volume climbs — re-evaluate your plan tier there, with a month of real meter data rather than a guess.

---

## 5. Phase gates

Do not advance until the gate passes.

- **G0 → 1:** docs split, parameter table with provenance, characterization suite green
- **G1 → 2:** net-of-cost, option-denominated P/L is the default and reconciles against archived data
- **G2 → 3:** headless recording verified by parity across ≥3 sessions; freeze enforced in CI
- **G3 → 4:** no surface presents Trend Capture % as performance
- **G4 → 5:** kill-switch enforced in the risk path; earnings automated
- **G5 → 6:** **backtest reconciles trade-by-trade against a live paper day**
- **G6 → 7:** power targets, FDR guards, holdout and kill criterion all in code and documented
- **G7:** simplified baseline has a net-of-cost expectancy with a confidence interval

---

## 6. Session prompt template

```
Read docs/EXECUTION_PLAN.md and CLAUDE.md.

We are executing session <ID> ONLY.
Scope: <one line from the table>
Model: <archetype>  Effort: <level>

Constraints:
- Invariants I1-I6 apply. I1 especially: no V1 decision logic changes.
- Do not modify files outside the scope of this session.
- Run the full test suite before proposing any commit.
- If you believe the scope is wrong, stop and tell me. Do not expand it.

Done when: <done-condition from the table>
```

---

## 7. Documentation contradictions to resolve in S0.2

| Issue | Location |
|---|---|
| "Neon tables are small event/state tables only: `alert_events`, `paper_trades`, `scanner_runs`, `gate_decisions`" vs ~15 tables and 15 migrations listed elsewhere | PS L552 vs L411; README L669–681 |
| Telegram score / cap / cooldown settings documented as live config but declared non-functional legacy elsewhere — an operator believes a 3-alerts/day cap is enforced when it is not | README L790–805 vs PS L557, L560 |
| "Expanded active watchlist to **16** liquid option names" followed by a list of **26** | PS L975 |
| "Test coverage is still sparse" vs "134 tests pass" | PS L728 vs L236 |
| `trade_state.json` "is currently empty" — a runtime fact embedded in an architecture document | PS L407 |
| Windows-absolute paths baked into validation commands | PS L320; README L745 |
| Three freshness regimes — `MAX_STOCK_DATA_DELAY_MINUTES=2`, `REAL_MAX_QUOTE_AGE_MINUTES=3`, `OPTION_DELAYED_QUOTE_MINUTES=10` — with a 5–15 min scan cadence, the 2-minute gate is unachievable by construction | README L417, L825; PS L544 |

---

## 8. One-line summary

**Stop building instruments; start building sample size — with costs in the model, and the option rather than the stock as the thing being measured.**