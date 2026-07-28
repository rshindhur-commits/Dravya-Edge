# Decisions

Durable decision record. One entry per decision that constrains future work:
what was decided, why, what it rules out, and what would reverse it.

**A decision belongs here if reversing it would cost real work.** Parameter
values live in [`STRATEGY.md`](STRATEGY.md); this file records *choices*, not
settings. Never delete an entry — supersede it with a new one and mark the old
`SUPERSEDED`.

Status values: `ACTIVE` · `SUPERSEDED` · `PROPOSED` · `REVERSED`

---

## D1 — V1 decides; everything else observes

**Status:** `ACTIVE` · **Date:** pre-2026-07 (retroactively recorded 2026-07-25)

All analytics, V2 engines, RuleEvaluation, waterfalls, and the Learning engine
are read-only with respect to trading behavior.

**Why:** an observational layer that can also act cannot be trusted as evidence
about the thing it acts on. The separation is what makes the evidence trail
worth collecting.

**Rules out:** analytics feeding back into entry eligibility, risk, option gates,
alert eligibility, or trade state — however tempting the signal looks.

**Reversed by:** nothing short of abandoning the evidence program. A specific
feature may graduate out of shadow via
[D4](#d4--three-distinct-evidence-gates-not-one-threshold),
but the *layer* boundary stays.

---

## D2 — Paper trading is the authoritative P&L record

**Status:** `ACTIVE` · **Date:** pre-2026-07 (retroactively recorded 2026-07-25)

`paper_trade_state.json` (Path B) is the store every evidence surface reads.
The legacy `trade_state.json` (Path A) is not.

**Why:** Path B has one open path, one close path, and one P&L formula
(`_paper_trade_result`), so realized R is comparable across every trade.

**Rules out:** treating Path A numbers as performance; adding a third store.

**Open issue:** Path A can still open trades and send Telegram messages despite
having no evidence consumer — see
[OPERATIONS.md §12](OPERATIONS.md#12-open-questions). Resolving that may
supersede this entry with an explicit "Path A removed" decision.

**Reversed by:** real broker integration, which would introduce a genuine live
store and demote paper to one of several.

---

## D3 — Telegram is a transport, not a second decision engine

**Status:** `ACTIVE` · **Date:** 2026-07 (contract formalised), amended 2026-07-25

Six subscriber messages only. Scanner rows never send; `NEW TRADE` fires when a
trade actually opens.

**Why:** an alert layer that re-derives gates becomes a shadow strategy with its
own silent selection bias.

**Amendment (2026-07-25):** the principle is *not* fully honored today.
`maybe_send_paper_entry_alert` re-runs `evaluate_entry_gate` with three stricter
thresholds (`TELEGRAM_MIN_RR` 2.0, `TELEGRAM_MIN_OPTION_QUALITY_SCORE` 70,
`TELEGRAM_MAX_SPREAD_PCT` 8), so **a paper trade can open and produce no
message**. The other 11 policy keys are computed and discarded.

**Rules out:** adding new alert-side gates. New suppression logic belongs in the
decision layer where it is measurable.

**Reversed by:** a decision to make Telegram a filtered feed rather than a trade
log — which would need its own entry here, and an explicit statement of the
selection bias it introduces.

---

## D4 — Three distinct evidence gates, not one threshold

**Status:** `ACTIVE` · **Date:** 2026-07-25 (documented; gates pre-existed)

| Gate | Threshold | Scope | Enforced |
|---|---|---|---|
| `rule_change_allowed` | ≥ 20 evidence days **and** ≥ 80 completed trades | Whole strategy | Coded flag, blocks nothing mechanically |
| V1.0 Evidence Freeze | 100–200 trades across ≥ 20 days, multiple regimes | Changing V1 at all | Documentation discipline only |
| Feature promotion | n ≥ 100, confidence ≥ 95%, positive lift | One shadow feature | Coded in `evaluate_promotion` |

**Why:** these read as contradictory thresholds but are three questions at three
scopes. Naming them separately is what makes each enforceable.

**Rules out:** citing "20 days / 80 trades" as license to change a V1 rule — that
flag permits *controlled review*, not a change.

**Known weakness:** all three are almost certainly underpowered. At a per-trade R
standard deviation of ~1.0–1.3, n=80 distinguishes only ~0.3–0.4R/trade from
zero. Superseding this with power-based targets is planned work
([EXECUTION_PLAN.md](EXECUTION_PLAN.md) S6.1).

**Reversed by:** S6.1 replacing these with computed required-n figures.

---

## D5 — Best-effort persistence, never on the decision path

**Status:** `ACTIVE` · **Date:** pre-2026-07 (retroactively recorded 2026-07-25)

Every database write is best-effort and routed through a `RuntimeJob`. A DB,
cache, or report failure cannot block a decision, an alert, or file-backed state.
The scanner never executes DDL.

**Why:** the database is a research mirror, not the system of record. Files are
the durable record; Postgres is queryable convenience.

**Rules out:** synchronous DB reads in a decision path; migrations applied
automatically at runtime; treating a DB row as authoritative over its file.

**Reversed by:** making Postgres the system of record — which would require
transactional guarantees the current best-effort repositories do not provide.

---

## D6 — Measurement freeze

**Status:** `ACTIVE` · **Date:** 2026-07-25 (per
[EXECUTION_PLAN.md](EXECUTION_PLAN.md) §0)

No new analytics modules, dashboard pages, scoring layers, or telemetry tables
until Phase 5 completes.

**Why:** the strategy freeze created a loophole that routed all available effort
into observability. The result is 36 analytics modules and 7 dashboard pages
measuring a sample too small to support any of them. Adding a 31st instrument
while expectancy is unmeasurable is the failure this exists to stop.

**Rules out:** "while I'm in here, let me also add a panel for…"

**Reversed by:** Phase 5 gate G5 — backtest reconciling trade-by-trade against a
live paper day.

---

## D7 — Black-Scholes repricing before historical quote replay

**Status:** `PROPOSED` · **Date:** 2026-07-25 (decide in
[EXECUTION_PLAN.md](EXECUTION_PLAN.md) S1.1)

Option P&L should first be modelled by repricing from stored Greeks (~1 day of
work, roughly ±15% over an intraday hold) rather than waiting on historical
option-quote replay (weeks, high fidelity).

**Why:** the current linear `R × risk_at_stop` map is not merely imprecise, it is
*directionally* wrong about long premium — it cannot represent theta or convexity
at all. An approximate model that has the right shape beats an exact model of the
wrong quantity. Quote replay lands in Phase 5 when the backtester needs fills.

**Rules out:** shipping net-of-cost expectancy that still measures the underlying.

**Reversed by:** S1.1 review concluding the Greeks stored at entry are too sparse
or stale to reprice from, in which case quote replay becomes a Phase 1 blocker.

**Not yet decided** — this entry records the recommendation and its reasoning so
S1.1 starts from a stated position rather than a blank page.

---

## D8 — `docs/` supersedes README and Project_state

**Status:** `ACTIVE` · **Date:** 2026-07-25

On any conflict, `docs/` wins. Corrections are tracked in
[ARCHITECTURE.md §7](ARCHITECTURE.md#7-corrections-vs-legacy-docs).

**Why:** two overlapping ~900-line hand-maintained documents were the single
largest source of contradictions *and* the largest per-session context cost.

**Rules out:** adding new material to `README.md` or `Project_state.md`.

**Reversed by:** retiring the legacy docs entirely, which is preferable to
maintaining both and currently unscheduled.
