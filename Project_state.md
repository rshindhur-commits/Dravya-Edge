# Project State

Last updated: 2026-06-30

## Project Purpose

This workspace is a Python-based intraday algo/options trading scanner. It scans a configured stock watchlist, fetches or falls back to market aggregate data, computes technical indicators across multiple timeframes, scores momentum setups, evaluates entries and risk, ranks options contracts, projects trade outcomes, manages open trade state, and writes scan telemetry for later analysis.

The current v2 intraday options watchlist is defined in `app/config/watchlist.py`:

- QQQ
- SPY
- NVDA
- AAPL
- MSFT
- AMZN
- META
- TSLA
- AMD
- AVGO
- MU
- PLTR
- NFLX
- CRWD
- SMCI
- SPCX

Non-trade market reference symbols are also defined in `app/config/watchlist.py`:

- SMH
- SOXX
- XLK
- VIX, fetched as VIXY because the current Polygon plan does not include index aggregate entitlement for `I:VIX`

## Current Entrypoint

Run path from the README:

```powershell
python -m app.main
```

The older direct command `python app/main.py` is not the recommended path because package imports such as `from app...` require running from the workspace as a module.

The main scanner function is `run_scanner()` in `app/main.py`.

At a high level, each scan does this per symbol:

1. Fetches 5-minute aggregate candles through Polygon logic.
2. Resamples 5-minute candles into 15-minute and 1-hour candles.
3. Computes indicators for each timeframe.
4. Scores each timeframe through the momentum strategy.
5. Detects entry setups when no active trade exists.
6. Calculates risk, stop loss, take profit, and risk/reward.
7. Applies market-session action gating: premarket candidates are watch-only, 9:30-9:45 ET waits for opening-range confirmation, and paper entry is only allowed after 9:45 ET when all gates pass.
8. Checks option quote freshness, bid/ask spread, volume, open interest, quality score, expiration bucket, and event risk.
9. Opens, updates, or closes trade state.
10. Builds a multi-timeframe final signal.
11. Recommends/ranks an option contract and blocks stale/delayed or low-quality option quotes from execution-ready status.
12. Projects target, stop, probability, expected option gain, and grade.
13. Replays the projection over recent candles when possible.
14. Adds explicit market regime, sector-reference strength, watchlist breadth, relative strength rankings versus QQQ and SPY, relative volume, ATR %, premarket gap %, top 5 strongest/weakest leaderboards, and top 3 bullish/bearish candidate tags.
15. Optionally calls OpenAI for a trade summary on strong setups.
16. Saves actionable scan telemetry and paper-trade outcome/context telemetry to CSV.
17. Exports a scanner report to `scanner_output.xlsx`; stale/no-data/error symbols now still write dashboard-readable rows.

## Main Components

### Scanner Orchestration

- `app/main.py` coordinates the scanner loop, table output, Excel export, telemetry save, OpenAI summary calls, trade state updates, and projection replay.
- `app/trade_manager.py` manages live trade adjustments such as breakeven stops, EMA9 trailing stops, failed breakout exits, EMA20 exits, momentum breakdown exits, time exits, and partial-profit signals.

### Market Data

- `app/indicators/technical_indicators.py` owns `get_polygon_data()` and `compute_indicators()`.
- `app/utils/polygon_client.py` owns low-level Polygon HTTP aggregate calls, retry/backoff handling, a token-bucket rate limiter, rate-limit header tracking, metrics, and a short TTL cache that is currently disabled in `get_aggs_cached()`.
- `app/mock/load_mock_aggs.py` loads fallback aggregate data from `app/mock/*.json`.
- Runtime market data mode is controlled by `USE_MOCK_MARKET_DATA` in environment/settings. Current intended operating mode is live Polygon/Massive aggregate data with the user's full real-time subscription.
- Stock aggregate freshness is session-aware in `app/main.py`. Polygon aggregate timestamps represent candle bucket starts, so freshness subtracts the inferred aggregate interval before comparing against `MAX_STOCK_DATA_DELAY_MINUTES`. This prevents a current 5-minute candle from being mislabeled `STALE_STOCK_DATA` solely because its timestamp is the candle start.

### Market Session Entry Gating

- Premarket, 4:00-9:30 ET: strong call/put candidates can surface as `PREMARKET_WATCH`, but `Realtime Ready` remains false and auto paper entry is not allowed.
- Opening range, 9:30-9:45 ET: candidates use `OPENING_RANGE_CONFIRMATION`; the scanner waits for regular-market confirmation before entry.
- Regular session after 9:45 ET: `ENTER` or `ENTER_PAPER` is allowed only if stock freshness, risk, timing, option quality, quote freshness, event, regime, and dashboard auto-entry gates pass.
- Regular session auto paper entries remain constrained to the dashboard's 9:45-15:30 ET entry window.

### Timeframes And Indicators

- The scanner fetches fresh 5-minute candles, then creates higher timeframes through `app/utils/timeframe_resampler.py`.
- `compute_indicators()` adds trend, momentum, volume, volatility, VWAP, opening-range, structure, breakout, breakdown, consolidation, support/resistance, and related derived columns.
- Timeframe minimum candle checks exist for 5m, 15m, and 1h.

### Strategy And Entry Logic

- `app/strategies/momentum_strategy.py` scores bullish, bearish, and neutral evidence using EMA, MACD, RSI, VWAP, relative volume, ATR expansion, candle body strength, market structure, breakout/breakdown behavior, failed reclaim patterns, and market regime.
- `app/strategies/entry_engine.py` detects entries including breakout, VWAP reclaim, EMA pullback, higher-low continuation, coiled breakout, coiled breakdown, bearish breakdown short, and VWAP rejection.
- Multi-timeframe bias is combined in `app/main.py`, with heavier weighting on 15m and 1h signals.

### Risk And Position Sizing

- `app/risk/risk_manager.py` calculates entry price, stop loss, take profit, risk/reward, trade allow/deny state, and max risk percentage.
- Long and short setups use different ATR-based stop/target logic.
- `app/risk/position_sizing.py` estimates contracts, max risk, estimated loss, estimated profit, and aggressiveness for options sizing.
- Current position sizing in `app/main.py` assumes an account size of 25000 and risk percent of 2.

### Options

- `app/options/live_options_chain.py` fetches Polygon options snapshots when mock mode is disabled.
- Runtime options mode is controlled by `USE_MOCK_OPTIONS` in environment/settings. Current intended operating mode is live Polygon option snapshots with delayed-quote safety gates.
- `app/options/option_metrics.py` computes option mid-price, spread %, DTE, expiration bucket, expiration risk, quote age/freshness, option quality score, liquidity grade, quality reasons, and option P/L.
- `app/options/contract_ranker.py` scores contracts by direction, DTE/expiration bucket, volume, open interest, strike proximity, delta, IV, gamma, theta, spread readiness, and option quality score.
- `app/options/options_filter.py` hard-rejects unavailable bid/ask, crossed markets, stale/delayed quotes, low open interest, low volume, wide spread, disabled 0DTE/1DTE contracts, and low option quality score.
- `app/options/options_recommender.py` includes both a simple recommendation function and a live/mock chain ranking path.

### Market References, Regime, And Breadth

- `app/config/watchlist.py` separates trade candidates from non-trade reference symbols.
- Reference symbols are SMH, SOXX, XLK, and VIX. VIX is fetched through VIXY as a volatility proxy under the current Polygon subscription.
- `app/main.py` classifies explicit regimes as `TRENDING_BULL`, `TRENDING_BEAR`, `RANGE_BOUND`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`, or `UNKNOWN`.
- Regime rules can block setup families before options are considered. For example, VWAP reclaim/breakout style longs are blocked outside bullish/high-volatility regimes, and breakdown/VWAP rejection shorts are blocked outside bearish/high-volatility regimes.
- Sector strength compares symbols against reference ETFs, especially semis versus SMH and mega-cap/software tech versus XLK.
- Breadth is currently a watchlist breadth proxy, not official NASDAQ/QQQ advancer-decliner data. It tracks watchlist advancers/decliners, above VWAP %, above EMA20 %, and top 5 strongest/weakest names.

### Exits

- `app/exit/exit_engine.py` evaluates stop loss, momentum weakening, failed breakout, VWAP failure, MACD bearish crossover, EMA20 breakdown, and target hit.
- `app/trade_manager.py` separately evaluates trade management actions and trailing/updated stops.

### Projections And Replay

- `app/projections/trade_projection.py` estimates expected move percentage, projected option gain, target price, stop price, probability, best expiry bucket, and trade grade.
- `app/analytics/replay_engine.py` replays recent candles against projected target/stop.
- `app/analytics/trade_outcome_tracker.py` determines whether target, stop, no-hit, unknown, or error occurred during replay.

### AI Summary

- `app/ai/trade_analyzer.py` uses `OPENAI_API_KEY` and the OpenAI Python client.
- The requested model is `gpt-4.1-mini`.
- `app/state/state_manager.py` throttles AI calls by symbol, signal changes, score improvements, and a 20-minute cooldown.
- Core scanner decisions are rules-first; OpenAI is explanation-only.
- Scanner refreshes do not call AI unless `SCANNER_AI_SUMMARY_ENABLED=true`; keep this false during market hours.
- Dashboard on-demand AI explanations use `OPENAI_API_KEY_APP` when provided, a small dashboard model, a max-token cap, and local cache file `app/state/ai_summary_cache.json`.
- Dashboard AI explanations are gated to top candidates with setup >= 70, RR >= 2.0, `Action Status=REVIEW_TV_CHART`, and no event/regime block.

### Persistence And Output

- `app/state/trade_state.json` stores active open trade state. It is currently empty.
- `app/state/signal_memory.json` stores the last AI-call signal state per symbol. It currently contains QQQ, SPY, AVGO, TSLA, and NVDA history.
- `telemetry/trade_telemetry.csv` stores scan/projection telemetry and paper-trade close telemetry with scanner context snapshots.
- Dashboard sidebar export buttons provide quick downloads for `scanner_output.xlsx`, `telemetry/trade_telemetry.csv`, `app/state/paper_trade_state.json`, and `app/state/trade_state.json`.
- `scanner_output.xlsx` is produced by the scanner at runtime.
- `logs/` and `data/` exist as storage directories, but no specific runtime ownership is clear from the current code pass.

## Tools And Diagnostics

- `tools/dump_header_history.py` prints the in-memory Polygon rate-limit header history.
- `tools/get_suggested_rate.py` prints a suggested `POLYGON_RATE_LIMIT_PER_MINUTE` based on recent header observations.
- `tools/diag_fetch.py` fetches raw 5m Polygon data, resamples to 15m, computes indicators correctly, and prints latest close / move diagnostics.

## Dependencies

The project uses a UTF-8 encoded `requirements.txt`. Important packages include:

- `pandas`, `numpy`, `ta`
- `requests`, `urllib3`, `python-dotenv`, `pytz`, `tzdata`
- `polygon-api-client` for Polygon REST client imports
- `openai` for optional AI trade summaries
- `rich` for console table output
- `openpyxl` for Excel report export
- `fastapi`, `uvicorn`, `plotly`, `yfinance`, and `websockets` are installed/listed but are not central to the current scanner path based on the inspected files.

## Environment Variables

Known environment variables used by the code:

- `POLYGON_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_API_KEY_APP`
- `OPENAI_DASHBOARD_MODEL`
- `OPENAI_SUMMARY_MAX_TOKENS`
- `AI_SUMMARY_CACHE_FILE`
- `SCANNER_AI_SUMMARY_ENABLED`
- `USE_EXTENDED_HOURS`
- `POLYGON_RATE_LIMIT_PER_MINUTE`
- `POLYGON_CACHE_TTL`
- `POLYGON_HEADER_HISTORY_MAX`
- `USE_MOCK_MARKET_DATA`
- `USE_MOCK_OPTIONS`
- `ENABLE_AI_SUMMARY`
- `SCANNER_DEBUG`
- `SCANNER_OUTPUT_FILE`
- `ACCOUNT_SIZE`
- `RISK_PERCENT`
- `EVENT_BLOCKER_ENABLED`
- `EVENT_BLOCKER_DATES`
- `EVENT_BLOCKER_DAYS_BEFORE`
- `OPTION_MIN_VOLUME`
- `OPTION_MIN_OPEN_INTEREST`
- `OPTION_MAX_SPREAD_PCT`
- `OPTION_MIN_QUALITY_SCORE`
- `OPTION_DELAYED_QUOTE_MINUTES`
- `OPTION_MAX_QUOTE_AGE_MINUTES`
- `OPTION_ALLOW_0DTE`
- `OPTION_ALLOW_1DTE`
- `OPTION_MIN_DTE`
- `OPTION_PREFERRED_MIN_DTE`
- `OPTION_PREFERRED_MAX_DTE`
- `OPTION_MAX_DTE`

## Current Operating Mode

- Intended Monday mode is `USE_MOCK_MARKET_DATA=false` and `USE_MOCK_OPTIONS=false`, using the user's current full Polygon/Massive real-time subscription.
- Real-time mode is available with `REALTIME_MARKET_DATA_REQUIRED=true`, `REALTIME_OPTIONS_REQUIRED=true`, `OPTION_REQUIRE_BID_ASK=true`, `OPTION_REQUIRE_FRESH_QUOTE=true`, and `MAX_STOCK_DATA_DELAY_MINUTES=2` after Polygon/Massive entitlement upgrade.
- Intended market-hours AI mode is `ENABLE_AI_SUMMARY=false` and `SCANNER_AI_SUMMARY_ENABLED=false`; use dashboard rules only.
- Runtime settings load `.env` with override enabled so local config changes, including `ENABLE_AI_SUMMARY=false`, win over stale process variables after restart.
- Streamlit Cloud must be configured through Streamlit Secrets; local `.env` is not automatically available in deployed Streamlit. The dashboard syncs Streamlit Secrets into env before scanner imports and shows non-sensitive sidebar key status.
- Premarket real-time mode surfaces strong candidates as `PREMARKET_WATCH` but does not mark them execution-ready. The scanner waits for opening-range confirmation from 9:30-9:45 ET and only allows `ENTER`/`ENTER_PAPER` after 9:45 ET when all gates pass.
- Delayed-data mode remains acceptable for scanning and paper trading with manual confirmation. Real-time mode blocks truly stale stock aggregates and missing/stale/delayed option quotes.
- Current option gate defaults: minimum volume 100, minimum open interest 500, max spread 10%, minimum option quality score 65, delayed quote threshold 10 minutes, stale quote threshold 30 minutes, 0DTE disabled, 1DTE disabled.
- Current DTE preference defaults: minimum 10 DTE, preferred 14-30 DTE, max fallback 45 DTE. The ranker heavily penalizes 2-6 DTE, allows 7-13 DTE as lower-priority short swing/fallback, favors 14-30 DTE, treats 31-45 DTE as acceptable fallback, and de-prioritizes 46+ DTE unless otherwise justified.
- Event blocker is configurable and enabled by default through environment settings.
- Polygon aggregate cache lookup and cache set are currently commented out in `app/utils/polygon_client.py`.
- Polygon rate limiting is implemented in `app/utils/polygon_client.py` and active through `acquire_rate_limit()` in `get_aggs_cached()`.
- Polygon request URL logs redact the API key before printing.
- `POLYGON_API_KEY` loading now defaults to an empty string so missing env vars do not crash the module at import time.
- OpenAI summaries can run if `OPENAI_API_KEY` is present and `should_call_ai()` allows the call.
- `tools/diag_realtime_entitlements.py` verifies stock aggregate freshness, stock snapshot entitlement, ranked option contract selection, option snapshot quote, `/v3/quotes` quote fallback, bid/ask, spread, and quote freshness.

### Mock Data State

Mock aggregate JSON files exist for fallback/testing:

- QQQ
- SPY
- NVDA
- TSLA

## Telemetry Schema

Current telemetry fields include:

- symbol
- final_signal
- alignment_score
- probability
- trade_grade
- risk_reward
- market_regime
- replay_outcome
- bars_to_outcome
- option_delta
- option_gamma
- option_iv
- option_theta
- option_mid_price
- option_spread_pct
- option_volume
- option_open_interest
- expiration_risk
- expiration_bucket
- option_quality_score
- option_liquidity_grade
- option_quote_freshness
- option_quote_age_minutes
- event_blocked
- event_block_reason
- market_regime
- reference_regime
- regime_blocked
- regime_block_reason
- sector_group
- sector_reference
- sector_rs
- sector_strength
- strength_rank
- weakness_rank
- top_5_strongest
- top_5_weakest
- watchlist_advancers
- watchlist_decliners
- above_vwap_pct
- above_ema20_pct
- target_price
- stop_price
- setup_grade
- setup_percent
- scanner_final_signal
- scanner_context_source
- scanner_score_15m
- planned_rr
- market_data_delay_minutes
- realtime_confirmation_needed
- realtime_ready
- realtime_block_reason
- stock_data_freshness
- stock_data_age_minutes
- option_bid
- option_ask
- option_quote_timestamp
- option_quote_timeframe
- option_quote_source
- tradingview_check_status
- action_status
- blocked_by
- action_reason
- next_condition
- pnl_pct
- r_multiple
- exit_reason
- paper_trade
- live_confirmed
- opened_at
- closed_at

Paper trade telemetry captures scanner context at entry when opened from the dashboard. If a trade was opened before context capture existed, the dashboard can attach a close-time scanner snapshot when the trade is closed.

## Current System Maturity

Current status:

- Multi-timeframe orchestration: stable
- Projection engine: stable
- Replay engine: stable
- Telemetry analytics: stable
- Expectancy calibration: early-stage
- Automated testing: minimal but now includes focused market-session scanner decision tests
- Live-trading readiness: experimental
- Delayed-data paper-trading readiness: improved for supervised use
- Paper-trade EOD review readiness: improved with context-rich close telemetry

## File Responsibility Matrix

| File | Responsibility |
|---|---|
| `main.py` | Orchestration |
| `replay_engine.py` | Replay simulation |
| `trade_outcome_tracker.py` | Target/stop evaluation |
| `trade_projection.py` | Projection generation |
| `contract_ranker.py` | Option scoring |
| `option_metrics.py` | Option quote, expiration, quality, and P/L metrics |
| `options_filter.py` | Option liquidity/quality hard gates |
| `event_blocker.py` | Manual event-risk entry blocker |

## Important Risks And Gaps

1. Automated test coverage is still sparse; current coverage includes focused market-session scanner decision tests in `tests/test_market_session_decisions.py`.
2. Some modules contain heavy debug printing, including request windows, system time, redacted Polygon URLs, and market data details.
3. Live/mocked behavior is now mostly settings-driven, but some module-level settings are still loaded at import time and should be restarted after `.env` edits.
4. Telemetry is still CSV-based and may need schema/version handling as fields evolve.

## Known Replay Calibration Findings

Current replay outcomes are heavily skewed toward `STOP_HIT`.

Likely causes:

- Stops too tight
- Targets too ambitious
- Replay horizon too short
- Immediate entry assumptions
- Noisy intraday volatility

Future improvements:

- ATR-based adaptive stops
- Delayed entry confirmation
- Dynamic replay windows
- Regime-aware replay calibration

## Replay Engine Current Behavior

Replay currently:

- Simulates historical entries ~40 candles before the end of the dataset
- Evaluates target and stop hits candle-by-candle
- Ignores stop hits during the first 2 candles to reduce noise
- Currently produces predominantly `STOP_HIT` outcomes

## Current Technical Debt

- `run_scanner()` in `main.py` is becoming orchestration-heavy.
- Replay/debug logging is verbose.
- Mock/live config is scattered across modules.
- Telemetry storage still uses CSV, though writes now handle empty existing files correctly.
- No centralized config management exists yet.

## Monday Operating Workflow

Use this workflow while keeping the current delayed Polygon subscription:

1. Before market open, confirm `.env` has `USE_MOCK_MARKET_DATA=false`, `USE_MOCK_OPTIONS=false`, `ENABLE_AI_SUMMARY=false` unless summaries are explicitly wanted, and conservative option gates.
2. Add known macro/earnings/Fed/OPEX dates to `EVENT_BLOCKER_DATES` using `SYMBOL:YYYY-MM-DD:Label` or `*:YYYY-MM-DD:Label`.
3. Run `python -m app.main` after the market has enough 5m candles. With real-time entitlements, current 5-minute aggregate buckets should report `Stock Data Freshness=LIVE`; stale labels now account for Polygon bucket-start timestamps.
4. Open the dashboard and review only the top candidates: `BULLISH_TOP_1` through `BULLISH_TOP_3` and `BEARISH_TOP_1` through `BEARISH_TOP_3`.
5. For each candidate, check `RS vs QQQ`, `RS vs SPY`, `Relative Volume`, `ATR %`, `Risk Reward`, `Option Quality Score`, `Option Liquidity Grade`, `Option Quote Freshness`, `Expiration Bucket`, and `Event Blocked`.
6. Before 9:30 ET, use `PREMARKET_WATCH` rows as a watchlist only. From 9:30-9:45 ET, use `OPENING_RANGE_CONFIRMATION` rows as candidates waiting for confirmation. Do not paper-enter until after 9:45 ET.
7. If `Action Status` is `REVIEW_TV_CHART`, `DELAYED_QUOTE`, `STALE_QUOTE`, or any option rejection code, do not treat the scanner as execution-ready. Confirm the live chart and broker option premium manually.
8. Paper trade first. For small real trades, use broker live bid/ask and limit orders only; avoid market orders, 0DTE, 1DTE, wide spreads, stale quotes, and event-risk windows.
9. After the session, review paper/real outcomes and telemetry before changing thresholds. Paper closes now preserve setup grade, RS, regime, sector, option-quality, blocker, and realized R context for model tuning.
10. Download/export scanner output, telemetry, paper trade state, and trade state from the dashboard sidebar before restarts or end-of-day review.

## Suggested Next Priorities

1. Add focused tests for option metrics, option hard gates, RS ranking, risk calculation, state transitions, and replay outcomes.
2. Re-enable and validate Polygon TTL caching if duplicate aggregate calls are still a problem.
3. Add official market breadth data if a reliable source/entitlement becomes available.
4. Consider moving telemetry from CSV to SQLite or another schema-aware store once sample size grows.

## Next Evolution Roadmap

1. Replay calibration
2. Expectancy engine
3. Win-rate analytics
4. ATR adaptive stops
5. Regime-aware position sizing
6. Historical backtesting framework
7. Live alerting
8. Web dashboard/API

## Current Strategic Direction

The project focus has shifted away from adding indicators and toward:

- Replay validation
- Expectancy analytics
- Calibration
- Measurable edge discovery
- Historical outcome analysis

## Quick Mental Model

The project currently behaves like a command-line trading copilot:

```text
WATCHLIST
  -> Polygon/mock aggregate candles
  -> 5m indicators
  -> 15m and 1h resampled indicators
  -> momentum scoring
  -> entry detection
  -> risk and trade management
  -> option chain ranking and quality/freshness gates
  -> projection and replay
  -> relative strength ranking and top candidate tagging
  -> Rich table, Excel report, telemetry CSV, optional OpenAI summary
```

## Runtime Data Flow

Polygon/mock candles
  -> indicator generation
  -> multi-timeframe scoring
  -> entry detection
  -> risk evaluation
  -> option ranking and option safety gates
  -> projection generation
  -> replay simulation
  -> RS ranking/top candidate tagging
  -> telemetry persistence
  -> analytics summary
  -> optional AI narration

## Current Major Limitations

- No real broker integration
- No true historical backtesting dataset
- Replay uses limited recent candle windows
- Real-time diagnostics must pass before using scanner output for manual real-trade decisions
- No asynchronous market stream processing

## Fresh Chat Bootstrap

When starting a fresh GPT session:

1. Upload this PROJECT_STATE.md file
2. Upload only the files relevant to the current task
3. Clearly state:
   - current objective
   - current blockers
   - whether mock mode is enabled
4. Avoid uploading the full workspace unless necessary

## Recent Major Changes

2026-06-30
- Added session-aware scanner action gating: premarket candidates surface as `PREMARKET_WATCH`, 9:30-9:45 ET candidates wait as `OPENING_RANGE_CONFIRMATION`, and `ENTER`/`ENTER_PAPER` is only allowed after 9:45 ET when all gates pass.
- Fixed stock aggregate freshness so Polygon/Massive aggregate bucket-start timestamps do not incorrectly trigger `STALE_STOCK_DATA` for current 5-minute candles.
- Added `market_session`, `raw_delay_minutes`, and `aggregate_interval_minutes` to stock data status calculations for clearer diagnostics.
- Added focused regression tests in `tests/test_market_session_decisions.py` covering premarket freshness, watch-only premarket labels, opening-range hold, and post-9:45 entry eligibility.

2026-06-15
- Added real-time data validation mode: stock aggregate freshness gates, option bid/ask and quote freshness gates, quote source/timeframe/timestamp fields, `ENTER_PAPER` status for real-time-ready paper candidates, and `tools/diag_realtime_entitlements.py`.
- Updated option contract selection preference to favor 14-30 DTE, keep 10-13 and 31-45 DTE as fallbacks, heavily penalize 2-6 DTE, de-prioritize 46+ DTE, and keep 0DTE/1DTE blocked.
- Fixed Streamlit key loading so secrets override stale/blank env values before scanner import; Polygon aggregate calls now read the API key dynamically instead of relying only on an import-time cached value.
- Added dashboard sidebar runtime key status for Polygon and app AI key presence without exposing key values.
- Disabled automatic scanner AI summaries unless `SCANNER_AI_SUMMARY_ENABLED=true`, preserving rules-only refreshes during market hours.
- Added gated on-demand dashboard AI explanations for top candidates only, with app-key support via `OPENAI_API_KEY_APP`, small-model configuration, max-token cap, and local cache.
- Set local `.env` to `ENABLE_AI_SUMMARY=false` and changed settings loading so `.env` overrides stale process variables.
- Added context-rich paper trade telemetry at close, including setup grade/score, planned RR, relative strength, market/sector regime, breadth, option quality/freshness, blockers, and realized outcome/R multiple.
- Added dashboard export/download buttons for scanner output, telemetry CSV, paper trade state JSON, and live trade state JSON.
- Added paper-close telemetry validation warnings for missing end-of-day review fields.
- Dashboard paper trades opened after this change store scanner context at entry. Older open paper trades can attach close-time scanner context when closed.
- Added paper-only automation controls in Streamlit: auto paper entries OFF by default, auto exits ON by default, max daily entries, setup/RR thresholds, direction filter, end-of-day close, and configurable profit R threshold.
- Auto paper minimum RR default lowered to 1.8 for sample collection, while keeping top-candidate, setup %, blocker, quote, quality, expiration, duplicate, active-trade, direction, and daily-limit gates intact.
- Auto paper entry gates enforce market hours, 9:45-15:30 ET entry window, top candidate status, setup/RR thresholds, allowed action status, event/regime/stale-data blocks, fresh option quote, option quality, spread, expiration bucket, duplicate prevention, active trade caps, per-direction cap, and daily limits.
- Auto paper entries allow missing option spread only when option quality is otherwise acceptable (`Option Quality Score >= 65`), because Polygon may omit bid/ask. Known spreads above 10% still block, and real-money execution remains manual with broker quote confirmation.
- Added `app/state/auto_paper_decision_log.json` and dashboard display to show each auto-entry decision as `SKIPPED`, `BLOCKED`, or `OPENED` with the exact gate reason.
- Paper Trade Setup now only shows current valid scanner candidates with entry buttons. Stale/blocked/history rows are shown in a read-only Last Seen Candidates section with no entry controls.
- Added `app/state/suggested_trade_state.json` and `app/state/suggested_trade_manager.py` to persist suggested call/put lifecycle across dashboard refreshes.
- Dashboard is split into New Calls / Puts, Still Valid Suggested Trades, Paper Trade Setup, Last Seen Candidates, Active Trades, Exit Now Alerts, Auto Paper Decision Log, and Telemetry.
- Dashboard refresh cadence is separated from full scanner cadence: dashboard can refresh every 1 minute while full scanner defaults to every 5 minutes.
- Scanner output now includes lifecycle placeholder columns: Suggestion Status, Suggestion First Seen, Suggestion Last Seen, Suggestion Age Minutes, Still Valid, Invalidation Reason, Exit Status, and Exit Reason Live.
- Auto paper exits can close on stop, target, live exit signal/reason, profit R threshold, or end-of-day close. Real trading remains manual only.
- Hardened JSON state writes for Streamlit Cloud/local filesystems by writing temp files in the target directory, creating parents, using atomic replace, and falling back to direct write if replace fails.
- Telemetry summaries now tolerate empty CSVs and schemas without projection-only columns such as `trade_grade`.
- Active Trades now calculates live R progress from entry/current/stop for paper trades and tracked trades, instead of showing stale stored zero progress.
- Fixed dashboard telemetry loading so an existing telemetry file cannot return `None` and crash the Telemetry section.
- Made telemetry CSV writing robust when `telemetry/trade_telemetry.csv` exists but is empty.
- Made Market Health and Active Trades dataframe display safer for Streamlit/Arrow mixed-type serialization and replaced deprecated `use_container_width` usage with `width="stretch"`.
- Dashboard Trade Opportunities and Paper Trade Setup now show recommended option contract details directly: option ticker, strike, expiration date, expiration bucket/risk, moneyness, mid price, spread %, quality grade, and quote freshness.
- Scanner output and dashboard now include alternate option contracts when available: Short DTE alternate from 2-13 DTE and Longer DTE alternate from 31-45 DTE, while preserving 14-30 DTE as the primary recommendation.

2026-06-13
- Expanded active watchlist to 16 liquid option names: QQQ, SPY, NVDA, AAPL, MSFT, AMZN, META, TSLA, AMD, AVGO, MU, PLTR, NFLX, CRWD, SMCI, SPCX.
- Added relative strength ranking fields: `Symbol Move %`, `RS vs QQQ`, `RS vs SPY`, `RS Rank Score`, `Premarket Gap %`, `Relative Volume`, `ATR %`, `Bullish Rank`, `Bearish Rank`, and `Top Candidate`.
- Added non-trade references SMH, SOXX, XLK, and VIX/VIXY proxy for regime, sector, breadth, and volatility context.
- Added explicit regime labels and setup-family regime blockers.
- Added watchlist breadth proxy, above VWAP %, above EMA20 %, and top 5 strongest/weakest leaderboards.
- Dashboard Trade Opportunities now sorts top candidates first and displays RS/ranking context.
- Scanner now writes stale/no-data/error rows to Excel so the dashboard remains readable even when Polygon returns stale weekend data.
- Fixed replay direction normalization so `HIGH CONVICTION BULLISH` and `HIGH CONVICTION BEARISH` replay as bullish/bearish directions.
- Added startup readiness checks for mock-mode safety, Polygon key presence, scanner output writability, 0DTE/1DTE disabled state, and max spread sanity.
- Repaired `tools/diag_fetch.py` so it fetches data before calling `compute_indicators()`.
- Dashboard Trade Opportunities now directly shows `Market Data Delay Minutes`, `Option Quote Freshness`, `Option Quote Age Minutes`, `Action Status`, `Blocked By`, `Event Blocked`, and `Regime Blocked`.
- Dashboard sidebar now supports auto refresh with 1, 5, and 15 minute intervals. Market-hours default is ON at 5 minutes; after-hours default is OFF. The scanner auto-runs only when the Excel output is missing or older than the selected interval.
- Added option quote freshness, quality score, liquidity grade, expiration bucket/risk, delayed/stale quote blocking, 0DTE/1DTE blocking, and configurable option gate thresholds.
- Added manual event blocker settings and event-risk report fields.
- Verified scanner run on 2026-06-13: all 15 symbols correctly reported `STALE DATA` / `AVOID` because Polygon returned 2026-06-12 data on Saturday.

2026-05-24
- Added replay engine
- Added telemetry analytics
- Added replay outcome tracking
- Added expectancy analytics foundation
- Added PROJECT_STATE governance

## Current Live Risks

The system should not yet be trusted for fully automated live trading because:

- replay calibration is immature
- expectancy engine is incomplete
- no broker integration safeguards exist
- no production backtesting validation exists
- Polygon data is delayed and must be confirmed against live broker/TradingView data before any real trade

## Production Readiness Checklist

Before live deployment:

- Disable mock market data
- Confirm delayed-data warnings and option quote gates are visible in dashboard/report
- Keep 0DTE/1DTE disabled unless deliberately testing them
- Add event dates before market open
- Validate Polygon cache behavior
- Add automated replay tests
- Add risk kill-switch
- Add config validation
- Validate expectancy over large sample size
- Add persistent DB telemetry storage