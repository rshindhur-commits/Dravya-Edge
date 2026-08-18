# `Setup %` records two different things in one number

Found 2026-08-17, from a live scan. **Nothing here changes a trading decision**,
and the proposal at the end does not either — `SETUP_GATE_ENABLED` defaults to
`False` (`app/gates/entry_gate.py:589`) and is unset in `.env`, so `Setup %`
currently gates nothing. This is about what the archive can answer later.

---

## 1. What was seen

Scan `2026-08-17_095250`, 23 symbols. Twelve of them stored **exactly 49.0**.
That uniformity is what prompted the look.

It is not a scoring bug. `app/gates/setup_quality.py:66-69` defines two ceilings:

```python
UNVALIDATED_SETUP_CEILING = 59   # applied when Setup Valid is false
UNTRADEABLE_ACTION_CEILING = 49  # applied when Action Status is AVOID
```

They apply in sequence, so an `AVOID` row that also failed setup validation is
clamped twice — to 59, then to 49. Twelve rows landing on the same number is the
ceiling doing exactly what it says.

The tell that it is the clamp and not the arithmetic: `GOOGL` (44), `PANW` (12)
and `JPM` (12) are also `AVOID` on the same scan and are untouched, because they
scored below the ceiling on their own merits.

## 2. What the clamp costs

Recomputing each row's conviction with no ceilings applied — same composition,
same weights, `app/gates/setup_quality.py:135-140`:

| symbol | action | stored | uncapped | hidden | uncapped grade |
|---|---|---|---|---|---|
| SMH | AVOID | 49 | **88** | +39 | A+ |
| AVGO | AVOID | 49 | **85** | +36 | A+ |
| PLTR | WAIT | 59 | **85** | +26 | A+ |
| AMAT | AVOID | 49 | 78 | +29 | A |
| MSFT | AVOID | 49 | 74 | +25 | A |
| AMZN | AVOID | 49 | 74 | +25 | A |
| SPCX | AVOID | 49 | 72 | +23 | A |
| NFLX | AVOID | 49 | 67 | +18 | B |
| ORCL | AVOID | 49 | 67 | +18 | B |
| SMCI | AVOID | 49 | 65 | +16 | B |
| TSLA | AVOID | 49 | 64 | +15 | B |
| NVDA | AVOID | 49 | 61 | +12 | C |
| TSM | AVOID | 49 | 55 | +6 | C |

**13 of 23 rows are clamped. The twelve stored at 49 span a true range of 55 to
88** — two A+, four A, four B, two C, all recorded as a D.

`PLTR` shows the other ceiling binding on its own: it is `WAIT`, not `AVOID`, and
still loses 26 points to `UNVALIDATED_SETUP_CEILING`. This is not only an `AVOID`
effect.

## 3. Why this is worth fixing, and why it is also defensible as-is

The module docstring is explicit about intent:

> Ceilings for rows that are not tradeable at all. Not conviction credit --
> floors that keep unusable rows below usable ones for ranking and analytics.

For **live ranking** that intent is served correctly and there is nothing wrong
here. An untradeable row should not outrank a tradeable one in a list the
operator reads top-down.

For **analytics** it works against the stated purpose, because the stored field
answers two questions at once — *how good was this setup* and *was it tradeable*
— and once written there is no way to separate them. The specific question it
cannot answer is the counterfactual one: **of the candidates we refused, were any
of them good?** On this scan the honest answer from the archive is "twelve 49s",
when the truth is that the day's two strongest setups are both inside that block.

### It does not reach the alerts — measured, not assumed

The obvious worry is that a clamped score suppresses or misreports a Telegram
alert. It does not, on two independent grounds.

**The threshold is not enforced.** `maybe_send_paper_entry_alert` passes
`TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE` (default 70) into `evaluate_entry_gate`,
but both refusal sites — `app/gates/entry_gate.py:954` and `:1074` — are guarded
by `and setup_gate_blocks()`, which reads `SETUP_GATE_ENABLED` (default `False`,
unset in `.env`). The bar is computed and recorded, never applied.

**The clamp never applies to an alertable row anyway.** Over the retained
archive — 30 days, 24,011 `scanner_snapshot` rows — 382 carry an alertable
status (`ENTER`, `ENTER_PAPER`, `REVIEW_TV_CHART`). Of those, **zero** have
`Setup Valid` falsey and **zero** sit at the 59 ceiling. Both ceilings require
conditions an alertable row does not have: `UNTRADEABLE_ACTION_CEILING` needs
`AVOID`, and `UNVALIDATED_SETUP_CEILING` needs a failed setup validation, which
has not co-occurred with an entry status once in 30 days.

The alert body does print the value — `Setup strength {score}%`,
`app/alerts/telegram_alerts.py:1863` — so this mattered enough to check. Since
alerted rows are never clamped, subscribers see the true conviction.

**This is the strongest statement in this note: the clamp is confined to rows
that were already refused.** It cannot cost a trade or distort an alert. That is
also why the fix is not urgent.

### What this does not establish

**It does not explain the inverted setup score.** §4 of `CHANGE_IMPACT_MAP.md`
records that low-scoring candidates win more (p = 0.059, RR confound ruled out).
That was measured on *executed* trades, which by construction were never `AVOID`,
so the clamp never touched them. Different population; the finding stands
untouched by this. Do not use this note to reopen it.

**It does not establish that the clamped rows were opportunities.** They were
refused for reasons unrelated to setup quality — on this scan, mostly `LOW_RR`.
A high uncapped score means the setup had conviction, not that the trade would
have paid. Whether refused-but-high-conviction rows would have made money is a
separate measurement that needs resolved outcomes, and it is precisely the
measurement the current field makes impossible.

## 4. Proposed change

Persist the uncapped conviction score **alongside** the clamped one. Do not
change the clamp, the ceilings, the thresholds, or any gate.

1. Add `setup_percent_raw` to the `candidate_snapshot` table (nullable float) via
   a migration in `app/db/migrations/`.
2. Export the unceilinged computation from `app/gates/setup_quality.py` as its
   own function — the arithmetic already exists inline in `compute_setup_percent`
   at lines 135-140; lift it so both callers share one definition rather than the
   second being a copy that drifts.
3. Populate it in `app/analytics/candidate_snapshot_writer.py` beside the
   existing `setup_percent` mapping.

### Why this is low-risk

* `Setup %` itself is unchanged, so every threshold, grade band and archived
  comparison keeps its current meaning.
* The setup gate is off, so even a wrong value in the new column cannot refuse or
  admit a trade.
* It is additive — a new nullable column and one extra write per candidate row.

### What it buys

The counterfactual question becomes answerable going forward: rank refused
candidates by true conviction, and check whether the refusals concentrated on
weak setups (the gates are working) or on strong ones (the gates are refusing the
wrong rows). Neither answer is available today.

### What it does not buy

Nothing retroactive. `scanner_snapshot.payload` retains `15m Score`, `Alignment
Score`, `Entry` and `Setup Valid` per row, so the archive **can** be recomputed
without a migration — that is how the table above was produced. If the only goal
is to answer the question once, a tool over the existing payloads is cheaper than
a schema change and should be preferred. The column is worth adding only if this
becomes a standing measurement.

---

## 4a. Adjacent, noticed while checking the alert path

`calculate_entry_alert_score` (`app/alerts/telegram_alerts.py:1104`) is imported
at `app/main.py:168` and **never called** anywhere in `app/`; only
`tests/test_telegram_alert_policy.py` invokes it. Worth recording rather than
silently deleting, because its setup term is
`min(abs(setup_score) / 10, 1) * 25` — the saturating form that
`setup_quality.py` was rewritten to escape, and which flattens everything at
`score >= 10` on a field now scaled 0-100. It affects nothing today. If it is
ever wired up, that term has to be rescaled first or it reintroduces exactly the
defect the rewrite removed.

## 5. Status

**Proposed, not built.** Raised 2026-08-17 during a pre-open health check; the
measurement above is one scan, which is enough to characterise the mechanism and
not enough to say how often it bites. Before building anything, run the recompute
over the retained archive (90 days as of 2026-08-13) and see how many rows are
clamped and by how much. If the hidden spread is routinely this wide, the column
earns itself; if this scan was unusual, a one-off tool is the right answer.
