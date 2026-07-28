# Strategy

What the system trades, and the exact rules and parameters that decide it.
Companion docs: [`ARCHITECTURE.md`](ARCHITECTURE.md) (how it's wired),
[`METRICS.md`](METRICS.md) (how it's measured),
[`OPERATIONS.md`](OPERATIONS.md) (how it's run).

Verified against code on 2026-07-25. Where this contradicts `README.md` or
`Project_state.md`, this is correct — see
[ARCHITECTURE.md §7](ARCHITECTURE.md#7-corrections-vs-legacy-docs).

---

## 1. Universe and timeframes

**Watchlist** (`app/config/watchlist.py`) — 26 liquid optionable names, static by
default: QQQ, SPY, NVDA, AAPL, MSFT, AMZN, META, TSLA, AMD, AVGO, MU, PLTR,
NFLX, CRWD, SMCI, SPCX, SMH, ARM, TSM, INTC, AMAT, LRCX, MRVL, ORCL, PANW, SOXL.
`DYNAMIC_WATCHLIST_ENABLED=true` merges Polygon snapshot movers after the core
symbols.

**Non-trade reference symbols**: SMH, SOXX, XLK, and VIX — fetched as **VIXY**,
because the current Polygon plan lacks index-aggregate entitlement for `I:VIX`.

**Timeframes**: 5-minute candles are fetched; 15m and 1h are resampled from them
(`app/utils/timeframe_resampler.py`). Multi-timeframe bias weights 15m and 1h
more heavily than 5m.

## 2. Session gating

Entry eligibility is a function of clock time before any setup quality matters.

| ET window | Status | Can enter? |
|---|---|---|
| 04:00–09:30 | `PREMARKET_WATCH` | No — watch only, `Realtime Ready` stays false |
| 09:30–09:45 | `OPENING_RANGE_CONFIRMATION` | No — waiting for regular-session confirmation |
| 09:45–15:30 | `ENTER` / `ENTER_PAPER` | Yes, if every gate passes |
| 15:30–15:55 | — | Management only; no new auto-paper entries |
| 15:55 | EOD | `INTRADAY` profiles force-close; `MULTIDAY` positions stay open |

The opening range itself uses the regular-session 09:30–10:00 ET window, not the
first rows of the dataframe (which would be premarket-contaminated).

## 3. Setup detection

`app/strategies/momentum_strategy.py::analyze_setup` scores bullish/bearish/
neutral evidence from EMA, MACD, RSI, VWAP, relative volume, ATR expansion,
candle body strength, market structure, breakout/breakdown behaviour, failed
reclaims, and regime. `app/strategies/entry_engine.py::detect_entry` then picks
a setup family.

### Active setup families (5)

| Family | Direction | Core trigger |
|---|---|---|
| `BREAKOUT` | Long | Close above prior 10-bar resistance |
| `EMA_PULLBACK` | Long | Bullish signal, close > EMA9, EMA9 > EMA20, latest low within `0.40 × ATR` of EMA9 |
| `EMA_REJECTION_SHORT` | Short | EMA9 touched within last 3 bars, close < EMA9, EMA9 < EMA20 |
| `BREAKDOWN_SHORT` | Short | Breakdown with body-strength and relative-volume confirmation |
| `VWAP_REJECTION` | Short | Rejection at VWAP |

### Disabled setup families (5) — commented out in source

`VWAP_RECLAIM`, `HIGHER_LOW_CONTINUATION`, `BREAKOUT_CONTINUATION`,
`COILED_BREAKOUT`, `COILED_BREAKDOWN` are **fully commented out** in
`entry_engine.py` (lines ~229, ~376, ~405, ~430, ~455) and cannot fire.
`Project_state.md` still lists them as detected setups; that is stale.
`entry_diagnostics.py` correctly mirrors only the 5 active families.

### Regime blocking

Regimes are classified as `TRENDING_BULL`, `TRENDING_BEAR`, `RANGE_BOUND`,
`HIGH_VOLATILITY`, `LOW_VOLATILITY`, `UNKNOWN`. A regime can veto a setup family
before options are considered: long breakout/reclaim styles are blocked outside
bullish/high-volatility regimes; breakdown/VWAP-rejection shorts are blocked
outside bearish/high-volatility regimes. Aliases (`TRENDING_BEARISH` /
`TRENDING_BULLISH`) normalise to the same treatment.

## 4. Risk geometry

`app/risk/risk_manager.py::calculate_risk` — entry is the latest close; stop and
target are ATR multiples adjusted by regime:

| Regime | Stop × ATR | Target × ATR |
|---|---|---|
| `HIGH_VOLATILITY` | 1.8 | 4.0 |
| `LOW_VOLATILITY` | 0.6 | 1.8 |
| everything else | 1.3 | 3.0 |

Stop-distance floors: breakout-style entries use a full-ATR floor;
`EMA_PULLBACK` uses a smaller **`0.25 × ATR`** floor so structure-based pullback
stops are not widened into RR rejections.

**Hard price-geometry gate** (`gates/entry_gate.py`): CALL requires
`stop < entry < target`; PUT requires `target < entry < stop`. Violations are
blocked as `INVALID_PRICE_GEOMETRY` before suggestions, auto-paper, alerts, or
display. The risk manager applies a second intended-direction invariant so a
bearish setup cannot return a bullish structure.

**Minimum RR to allow a trade: 1.5** (`RR_MIN_THRESHOLD`, with a 1e-9 epsilon for
float safety). Note this is *lower* than the 1.8 entry-gate default and the 2.0
Telegram default — a setup can pass risk and still be blocked downstream.

## 5. Option selection

1. `contract_ranker.py` scores by direction, DTE bucket, volume, OI, strike
   proximity, delta, IV, gamma, theta, spread readiness, quality score.
2. `options_recommender.py` returns `primary` (best quality), `affordable`, and
   `active` (what the scanner validates first).
3. `option_affordability.py` computes cost, risk-at-stop, and the cap.
4. `options_filter.py` hard-rejects: missing bid/ask, crossed market,
   stale/delayed quote, low OI, low volume, wide spread, 0DTE/1DTE when
   disabled, low quality score, and — in `HARD` mode — unaffordable contracts.

Fallback order when the active contract fails liquidity: `active` → `primary` →
`affordable` → `short_dte` → `longer_dte` → ranked list (duplicates skipped, so a
fallback may surface as `ranked #2`).

**DTE preference**: 2–6 heavily penalised, 7–13 low-priority fallback, **14–30
preferred**, 31–45 acceptable, 46+ de-prioritised, 0/1 DTE blocked.

**Quote freshness**: `LIVE_QUOTE` < 10 min, `DELAYED_QUOTE` 10–30 min,
`STALE_QUOTE` > 30 min. See [§5.1](#51-the-three-freshness-thresholds-are-not-one-concept)
— three different thresholds exist and are routinely confused.

### 5.1 The three freshness thresholds are not one concept

Legacy docs describe `MAX_STOCK_DATA_DELAY_MINUTES=2`,
`REAL_MAX_QUOTE_AGE_MINUTES=3`, and `OPTION_DELAYED_QUOTE_MINUTES=10` in
adjacent prose as if they were one "freshness" setting, which makes the 2-minute
value look impossible. They act on **two different objects at three different
scopes**:

| Threshold | Object | Clock | Scope |
|---|---|---|---|
| `MAX_STOCK_DATA_DELAY_MINUTES` = 2 | Stock **aggregate bar** | **Interval-adjusted**: `max(0, (now − bucket_start) − interval)` | Marks a symbol `LIVE`/`STALE` during a scan |
| `OPTION_DELAYED_QUOTE_MINUTES` = 10 | **Option quote** | Absolute wall-clock age | `LIVE_QUOTE` vs `DELAYED_QUOTE` boundary |
| `REAL_MAX_QUOTE_AGE_MINUTES` = 3 | **Option quote** | Absolute wall-clock age | *Additional* tightening layered on top of `LIVE_QUOTE`, for **real-money readiness only** |

**The 2-minute stock gate is achievable by construction**, contrary to the claim
that a 5–15 minute scan cadence makes it unsatisfiable. Two reasons, both
verified in `main.py:1675-1698`:

1. Freshness is evaluated **at fetch time** against just-fetched data. Scan
   cadence never enters the formula — each scan re-fetches and re-evaluates.
2. Polygon aggregate timestamps are **bucket starts**, so the aggregate interval
   is subtracted before comparison. A currently-forming 5-minute candle yields
   `raw_delay ∈ [0, 5)` → `delay = max(0, raw_delay − 5) = 0` → `LIVE`.

`STALE` therefore means what it should: `delay > 2` requires the newest bucket
start to be more than **7** minutes old — i.e. Polygon has not produced a bar it
owes. That is a real staleness signal, not a cadence artifact.

The 3-vs-10 pair *is* a deliberate two-tier design on the same object: a quote
must be **both** classified `LIVE_QUOTE` (< 10 min) **and** ≤ 3 minutes old to
clear real-money readiness (`dashboard.py:2472-2477`). Paper trading uses only
the 10-minute tier.

**Affordability cap** — the number that actually binds:

```
max_allowed_cost = min(
    OPTION_MAX_CONTRACT_COST,
    DAILY_START_CAPITAL × OPTION_MAX_RISK_PER_TRADE_PCT / OPTION_STOP_LOSS_PCT
)
```

With the **live `.env`** (1000 × 0.12 / 0.20 = **$600** vs static $650) the
**risk-based cap controls**. With the `SMALL_ACCOUNT`/`.env.example` baseline
(2000 × 0.10 / 0.20 = $1000 vs static $500) the **static cap controls**. These
are opposite regimes; confirm which is intended before changing either value.

## 6. Exit rules (V1)

`app/exit/exit_engine.py::evaluate_exit` collects every triggered exit and
selects the highest priority:

| Priority | Code | Meaning |
|---|---|---|
| 100 | `HARD_STOP` | Stop hit |
| 95 | `HARD_TARGET` | Target reached |
| 80 | `EMA` | EMA9 invalidation |
| 70 | `VWAP` | VWAP invalidation |
| 60 | `MACD` | Adverse MACD crossover |
| 50 | `FAILED_BREAKOUT` | Breakout failed |
| 40 | `TIME_EXIT` | Stagnation |
| 30 | `NEAR_CLOSE` | Near close without sufficient profit |

Two softeners: an **early weak-exit guard** lets near-flat EMA/VWAP/MACD/
failed-breakout exits hold during the first few bars while trend is intact, and
the **Grace Zone** lets V1 hold one extra candle on an EMA-only break when the
trade is profitable and confidence finds healthy remaining trend
(`v1_ema_grace_pending`). Hard stops, targets, VWAP loss, MACD reversal, and
multi-factor deterioration bypass both.

⚠️ **`evaluate_exit` does not close paper trades.** See
[ARCHITECTURE.md §3](ARCHITECTURE.md#3-the-two-trade-state-stores). Paper exits
run through `_auto_exit_reason`, which checks stop/target directly, reads this
engine's `Live Exit Signal` column, and adds profit-threshold and EOD triggers.

## 7. Telegram gating

Telegram is a transport, not a second decision engine — with one real exception.

Scanner rows **never** send. `classify_scanner_entry_alert` only labels a row
with why it isn't a message (`ACTIVE_TRADE_SUPPRESSED`,
`ENTRY_AWAITING_TRADE_OPEN`, `REVIEW_ALERT_SUPPRESSED`, `ACTION_NOT_ALERTABLE`).
`NEW TRADE` fires only when a trade actually opens.

**The exception**: `maybe_send_paper_entry_alert` re-runs `evaluate_entry_gate`
with three Telegram-specific thresholds — `TELEGRAM_MIN_RR` (2.0),
`TELEGRAM_MIN_OPTION_QUALITY_SCORE` (65), `TELEGRAM_MAX_SPREAD_PCT` (8). **A
paper trade can open and produce no `NEW TRADE` message** if it clears the paper
gate (RR ≥ 1.8) but not the Telegram gate (RR ≥ 2.0). The other 11 policy keys
(scores, caps, cooldowns) are computed and discarded.

Six-message subscriber contract: `NEW TRADE`, `TRADE UPDATE`, `PARTIAL PROFIT`,
`POSITION CONTINUES`, `TRADE CLOSED`, `TRADE CANCELLED`.

## 8. V1 / V2 split

V1 is the only engine allowed to open/close trades or send alerts. V2 runs in
shadow with its own state file, its own entry proposal, and shared risk
geometry; it records trend age, pullback number, bars since breakout, EMA/VWAP
extension, Entry Efficiency, live trend health, MFE in R, and exit phase.

Promotion requires 2–3 weeks of paper evidence showing improved **Trend Capture
%** without degrading win rate, average R, TES, left-on-table, or false-positive
rate. Feature flags `ENTRY_ENGINE=v1|v2` / `EXIT_ENGINE=v1|v2` are a future
Phase-3 controlled switch and are **not active**.

---

## 9. Parameter table

`Provenance` says where the effective value comes from:

- **hardcoded** — literal in source, no env override
- **code default** — `settings.py` / profile default, not overridden locally
- **live .env** — this working tree's `.env` overrides the code default
- **profile** — from `CAPITAL_PROFILES[OPTION_CAPITAL_PROFILE]`

⚠️ marks a live value that differs from the documented/`.env.example` baseline.

### Risk and sizing

| Parameter | Effective | Code default | Provenance | Defined in |
|---|---|---|---|---|
| Stop × ATR (normal / high-vol / low-vol) | 1.3 / 1.8 / 0.6 | same | hardcoded | `risk_manager.py:170-181` |
| Target × ATR (normal / high-vol / low-vol) | 3.0 / 4.0 / 1.8 | same | hardcoded | `risk_manager.py:170-181` |
| `EMA_PULLBACK` stop floor | `0.25 × ATR` | same | hardcoded | `risk_manager.py:317` |
| Minimum RR to allow trade | 1.5 | same | hardcoded | `risk_manager.py:475` |
| `ACCOUNT_SIZE` | ⚠️ **1000** | 2000 | live .env | `.env:123`, `settings.py:189` |
| `RISK_PERCENT` | ⚠️ **2** (→ 2% risked) | 10 | live .env | `.env:124`, `settings.py:196` |
| `MAX_CONTRACTS_PER_TRADE` | 1 | 1 | code default | `settings.py:203` |
| `OPTION_STOP_LOSS_PCT` | 0.20 | 0.20 | live .env (= default) | `.env:95` |

### Affordability

| Parameter | Effective | Profile (`SMALL_ACCOUNT`) | Provenance | Defined in |
|---|---|---|---|---|
| `OPTION_AFFORDABILITY_MODE` | `HARD` | `HARD` | live .env (= default) | `.env:84` |
| `OPTION_CAPITAL_PROFILE` | `SMALL_ACCOUNT` | — | live .env (= default) | `.env:85` |
| `DAILY_START_CAPITAL` | ⚠️ **1000** | 2000 | live .env | `.env:92` |
| `OPTION_MAX_RISK_PER_TRADE_PCT` | ⚠️ **0.12** | 0.10 | live .env | `.env:96` |
| `OPTION_MIN_CONTRACT_COST` | 100 | 100 | live .env (= profile) | `.env:98` |
| `OPTION_PREFERRED_MAX_CONTRACT_COST` | ⚠️ **500** | 400 | live .env | `.env:99` |
| `OPTION_MAX_CONTRACT_COST` | ⚠️ **650** | 500 | live .env | `.env:100` |
| `OPTION_MIN_AFFORDABLE_DELTA` | 0.25 | 0.25 | live .env (= profile) | `.env:103` |
| **Effective contract cap** | ⚠️ **$600** (risk-based) | $500 (static) | derived | `option_affordability.py:89-92` |
| `MAX_ACTIVE_PAPER_TRADES` | 1 | 1 | live .env (= profile) | `.env` |
| `MAX_DAILY_ENTRIES` | 3 | 3 | live .env (= profile) | `.env` |

### Option quality gates

| Parameter | Effective | Code default | Provenance | Defined in |
|---|---|---|---|---|
| `OPTION_MIN_VOLUME` | 100 | 100 | live .env (= default) | `settings.py:223` |
| `OPTION_MIN_OPEN_INTEREST` | 500 | 500 | live .env (= default) | `settings.py:227` |
| `OPTION_MAX_SPREAD_PCT` | 10 | 10 | live .env (= default) | `settings.py:231` |
| `OPTION_MIN_QUALITY_SCORE` | 65 | 65 | live .env (= default) | `settings.py:235` |
| `OPTION_DELAYED_QUOTE_MINUTES` | 10 | 10 | live .env (= default) | `settings.py:239` |
| `OPTION_MAX_QUOTE_AGE_MINUTES` | 30 | 30 | live .env (= default) | `settings.py:243` |
| `OPTION_ALLOW_0DTE` / `1DTE` | false / false | false / false | live .env (= default) | `settings.py:247-251` |
| DTE min / preferred / max | 10 / 14–30 / 45 | same | live .env (= default) | `settings.py:275-289` |

### Entry gate (`EntryGateConfig`)

| Parameter | Effective | Provenance | Defined in |
|---|---|---|---|
| `min_rr` | 1.8 | hardcoded default | `entry_gate.py:16` |
| `min_setup_percent` | 70.0 | hardcoded default | `entry_gate.py:17` |
| `min_option_quality` | 65.0 | hardcoded default | `entry_gate.py:18` |
| `max_spread_pct` | 10.0 | hardcoded default | `entry_gate.py:19` |
| `RANGE_BOUND` tightening | setup ≥ 90, RR ≥ 2.0, spread ≤ 5 | hardcoded | `entry_gate.py:403-407` |
| Weak-breadth tightening (breadth < −20 or above-EMA20 < 40) | setup ≥ 88, RR ≥ 2.0 | hardcoded | `entry_gate.py:409-412` |

### Telegram (only the first three gate anything)

| Parameter | Effective | Code default | Provenance | Consumed? |
|---|---|---|---|---|
| `TELEGRAM_MIN_RR` | 2.0 | 2.0 | live .env (= default) | ✅ gates `NEW TRADE` |
| `TELEGRAM_MIN_OPTION_QUALITY_SCORE` | ⚠️ **70** | 65.0 | live .env | ✅ gates `NEW TRADE` |
| `TELEGRAM_MAX_SPREAD_PCT` | 8 | 8.0 | live .env (= default) | ✅ gates `NEW TRADE` |
| `TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE` | 70 | 70.0 | live .env (= default) | ✅ gates `NEW TRADE` |
| `TELEGRAM_MIN_ENTRY_ALERT_SCORE` | 85 | 85.0 | live .env | ❌ computed, discarded |
| `TELEGRAM_INSTANT_ENTRY_ALERT_SCORE` | 92 | 88.0 | live .env | ❌ computed, discarded |
| `TELEGRAM_AFTERNOON_MIN_ENTRY_ALERT_SCORE` | 90 | 90.0 | live .env | ❌ computed, discarded |
| `TELEGRAM_MAX_ENTRY_ALERTS_PER_DAY` | 3 | 3 | live .env (= default) | ❌ computed, discarded |
| `TELEGRAM_MAX_ACTIVE_ALERTED_TRADES` | 2 | 2 | live .env (= default) | ❌ computed, discarded |
| `TELEGRAM_ENTRY_COOLDOWN_MINUTES` | 60 | 60 | live .env (= default) | ❌ computed, discarded |
| `TELEGRAM_SYMBOL_COOLDOWN_MINUTES` | 60 | 60 | live .env (= default) | ❌ computed, discarded |
| `TELEGRAM_TOP_CANDIDATE_LIMIT` | 3 | 3 | live .env (= default) | ❌ computed, discarded |
| `TELEGRAM_MAX_MORNING/MIDDAY/AFTERNOON_ENTRY_ALERTS` | 2 / 1 / 1 | 2 / 1 / 1 | live .env (= default) | ❌ computed, discarded |
| `TELEGRAM_EXIT_PRICE_MISMATCH_PCT` | 0.03 | 0.03 | live .env (= default) | ✅ blocks mismatched exit alert |

### Auto-paper automation

| Parameter | Effective | Code default | Provenance | Defined in |
|---|---|---|---|---|
| Entry window | 09:45–15:30 ET | same | hardcoded | `paper_automation_support.py:49-50` |
| EOD close | 15:55 ET | same | hardcoded | `paper_automation_support.py:51` |
| `AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES` | ⚠️ **45** | 60 | live .env | `.env` |
| `MAX_TRADES_PER_SYMBOL_PER_DAY` | ⚠️ **2** | 1 | live .env | `.env` |
| `ENABLE_MANUAL_PAPER_ENTRIES` | false | false | live .env (= default) | `.env` |
| `ALLOW_MANUAL_PAPER_CLOSE` | true | true | live .env (= default) | `.env` |

### Market data

| Parameter | Effective | Code default | Provenance | Defined in |
|---|---|---|---|---|
| `USE_MOCK_MARKET_DATA` / `USE_MOCK_OPTIONS` | false / false | false / false | live .env (= default) | `settings.py:131-138` |
| `REALTIME_MARKET_DATA_REQUIRED` | ⚠️ **true** | false | live .env | `.env` |
| `REALTIME_OPTIONS_REQUIRED` | ⚠️ **true** | false | live .env | `.env` |
| `MAX_STOCK_DATA_DELAY_MINUTES` | 2 | 2 | live .env (= default) | `settings.py:263` |
| `SCANNER_MAX_WORKERS` | 5 | 5 | code default | `main.py` |
| `POLYGON_RATE_LIMIT_PER_MINUTE` | ⚠️ **1200** | — | live .env | `.env` |
| `POLYGON_CACHE_TTL` | 30 s | — | live .env | `.env` |
