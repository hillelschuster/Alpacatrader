# Alpacatrader — Reference Findings & Data Notes

Last updated: 2026-06-06

Reference-only notes about data providers, API limitations, and prior research.
The rebuild source of truth is `docs/SPEC.md`. If this file conflicts with the spec,
the spec wins.

---

## 1. Alpaca Paper Trading Account — Verified

**Status: ✅ Connected and working.**

| Detail | Value |
|--------|-------|
| Account type | Paper trading |
| API endpoint | `https://paper-api.alpaca.markets` |
| Account status | ACTIVE |
| Equity | $97,235 |
| Buying power | $365,482 |
| Currency | USD |
| Credentials | `.env` → `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` |
| Provider | `AlpacaPaperProvider` (`src/providers/alpaca.py:267`) — `paper=True`, `url_override=self.BROKER_URL` |

The keys in `.env` route to paper trading, not live. The bot has a triple-safety gate 
for live trading: `trading.live_trading_confirmed` must be `"yes_i_accept_the_risks"`.

---

## 2. Alpaca Free IEX Data — Verified

**Status: ✅ Data confirmed working. Feed parameter NOT wired.**

### Verification Results

| Test | Result |
|------|--------|
| IEX 1-min bars for AAPL (June 5, 2026) | ✅ 31 bars returned (`feed='iex'`) |
| IEX 1-min bars for AAPL/TSLA/NVDA (today) | ✅ Connected (market closed, no bars) |
| SIP data (default, no feed param) | ❌ `403: subscription does not permit querying recent SIP data` |

### The Problem

The free Alpaca plan includes **IEX data only** (not SIP/consolidated). The bot's 
Alpaca providers (`AlpacaPaperProvider`, `AlpacaDataProvider`) do NOT pass a `feed` 
parameter to `StockBarsRequest` or `StockLatestQuoteRequest`. They use the default 
(SIP), which returns 403 on free accounts.

### What IEX Is (and Isn't)

IEX is ONE US stock exchange. It captures approximately **2-5% of total US market 
volume**. Per Alpaca's own example: AAPL had ~923K IEX volume vs ~51.8M consolidated 
(SIP) volume on the same day.

**IEX price levels are generally accurate** (arbitrage across exchanges keeps them 
aligned with the consolidated market). **IEX volume is systematically wrong** — it 
represents only a fraction of true market activity.

---

## 3. IEX Impact on Bot Components

### Impact Matrix

| Component | Data Source | IEX Severity | Why |
|-----------|------------|:----------:|-----|
| Premarket gap detection | yfinance `prepost=True` | **NONE** | yfinance uses consolidated data |
| Scanner (top gainers) | Finviz scraper + yfinance | **NONE** | Consolidated/aggregated data |
| Float, sector, market cap | yfinance `.info` | **NONE** | Consolidated |
| RVOL (relative volume) | yfinance `averageVolume` | **NONE** | Consolidated avg volume |
| Premarket high/low | yfinance `prepost=True` | **NONE** | Consolidated |
| 1-min bars (pattern detection) | Alpaca `get_bars("1Min")` | **HIGH** | IEX-only bars: <5% of true volume |
| 5-min bars (exit checks) | Alpaca `get_bars("5Min")` | **HIGH** | Same — VWAP/EMA/ATR from IEX bars |
| VWAP computation | `indicators.py` — on bars | **HIGH** | Cumulative (price×volume)/volume — wrong with IEX volume |
| EMA 9/20/200 | `indicators.py` — on bar closes | **HIGH** | IEX close ≠ consolidated close |
| ATR (stop/entry distances) | `indicators.py` — on bar ranges | **HIGH** | IEX ranges differ from consolidated |
| Current price quote | Alpaca `get_quote()` | **MEDIUM** | Price levels generally correct (arbitrage), bid/ask may differ |
| Position current_price | Alpaca `get_positions()` | **MEDIUM** | Same as quotes |
| Spread widening detection | Alpaca `get_quote()` | **MEDIUM** | May show wider spreads than consolidated |
| Exit monitoring (all rules) | 5-min bars + quote | **HIGH** | Depends on VWAP, EMA, RVOL — all degraded |
| Order execution | Alpaca paper trading API | **NONE** | Paper trading simulates fills independently |

### What Works for Testing

| Component | Rationale |
|-----------|----------|
| Order flow lifecycle | Entry → bracket → scale-out → exit. Paper API executes the same regardless of feed. |
| State machine transitions | PREMARKET → REGULAR → CLOSED. Deterministic from NY time. |
| StateDB persistence | Position lifecycle, restart reconciliation. Independent of data feed. |
| Pattern detection algorithm | The code paths process whatever bars they're given. Logic is testable. |
| Error handling | Network failures, API rate limits, retry logic. |
| Config validation | Pydantic validators, `validate_config_warnings()`. |
| Risk sizing math | Pure computation, validated by inputs. |
| Journal logging | Records what happened regardless of data quality. |

### What Would Produce Wrong Signals

1. **Missed breakout**: Stock spikes to 10M consolidated volume, IEX shows 30K → bot misses the entry
2. **Wrong VWAP exit**: True VWAP $10.50, IEX VWAP $10.35 → bot exits too late or too early
3. **False RVOL exit**: Consolidated RVOL 5x, IEX RVOL 0.15x → bot thinks volume dried up
4. **Wrong EMA levels**: IEX close ≠ consolidated close → EMA 9/20/200 at wrong levels
5. **ATR-based stops wrong**: IEX range differs → stop distances too tight or too loose
6. **Spread-widening false alarm**: IEX shows wider spread than consolidated market

---

## 4. Oracle's Recommendation: Option C (Hybrid) — SUPERSEDED

**Note (2026-06-06):** This section has been superseded by **[Section 9 — Consolidated Market Data Verdict](#9-consolidated-market-data-verdict)**. The oracle's original yfinance+Alpaca IEX hybrid recommendation was made before the Alpaca delayed SIP approach was discovered. Alpaca delayed SIP is strictly better — same SDK, official consolidated volume, no yfinance uncertainty. Retained here for historical context only.

### Problem Statement
IEX data is insufficient for volume-dependent indicators (VWAP, EMA, RVOL, ATR) 
but acceptable for current price quotes. We need consolidated data for indicators 
while staying on the free tier.

### The Hybrid Approach

| Data Need | Source | Rationale |
|-----------|--------|-----------|
| Bars for VWAP/EMA/ATR/RVOL | **yfinance** `history(interval="1m"/"5m")` | yfinance provides consolidated (SIP) data. Not real-time (~15-min delay on free tier), but indicator levels change slowly — delayed data is sufficient for reference levels. |
| Current price (entry/exit) | **Alpaca IEX** `get_quote(feed='iex')` | IEX quotes are ~real-time. Price levels are generally correct due to cross-exchange arbitrage. |
| Scanner, gap, RVOL, premarket | Already yfinance/Finviz | No change needed — already using consolidated data. |

### Why This Works

For a momentum day-trading bot:
- **Price levels** are the critical data point. IEX prices are kept in line with 
  consolidated by arbitrage across exchanges.
- **Volume is what's missing** from IEX. yfinance provides consolidated volume for 
  indicator computation.
- **Indicator levels are slow-moving** — VWAP and 200 EMA don't change dramatically 
  in 15 minutes. yfinance's delayed data is sufficient for computing reference levels.
- Current price quotes from IEX are real-time enough for entry/exit execution.

### Code Changes Required (~40 lines)

| File | Change | Effort |
|------|--------|--------|
| `config/settings.py` | Add `feed: str = "iex"` to `DataSettings` | 3 lines |
| `src/providers/alpaca.py` | Import `DataFeed` enum | 1 line |
| `src/providers/alpaca.py` | Add `feed` param to `AlpacaPaperProvider.__init__` and `AlpacaDataProvider.__init__` | 6 lines |
| `src/providers/alpaca.py` | Pass `feed=self._feed` to `StockBarsRequest` (2 places) | 2 lines |
| `src/providers/alpaca.py` | Pass `feed=self._feed` to `StockLatestQuoteRequest` (2 places) | 2 lines |
| `src/pipeline/v3_pipeline.py` | Add `_fetch_yfinance_bars()` method | ~20 lines |
| `src/pipeline/v3_pipeline.py` | Modify `_fetch_bars_for_pattern()` — prefer yfinance, fallback to Alpaca IEX | ~5 lines |
| `src/pipeline/v3_pipeline.py` | Modify `_fetch_5min_bars()` — prefer yfinance, fallback to Alpaca IEX | ~5 lines |

### What Could Go Wrong

| Risk | Mitigation |
|------|-----------|
| yfinance rate limiting | Alpaca IEX fallback ensures we always have SOME data |
| yfinance data unavailable (pre-market) | Fallback to Alpaca IEX bars |
| yfinance 15-min delay gives stale prices | Only used for indicator levels, not entry timing |
| IEX quote differs from consolidated price | For highly liquid stocks, difference is small. For illiquid runners, could matter. |

---

## 5. LLM-Based News Sentiment — Research Summary

### Recommendation
**Stay on DeepSeek V4 Flash** (already configured via litellm). Self-scrape news 
headlines from Finviz/Yahoo Finance (don't use LLM web search).

### System Prompt Design
- Explicit -1.0 to +1.0 scoring criteria with thresholds
- Catalyst type enum (earnings_beat, fda_approval, analyst_upgrade, etc.)
- JSON schema embedded in prompt with `response_format: {"type": "json_object"}`
- Temperature = 0.0 for maximum determinism
- Confidence threshold: ignore results with confidence < 0.3
- Total prompt: ~1,100 input + ~200 output tokens per symbol

### Cost Estimate
| Model | Daily (200 calls) | Monthly (22 days) |
|-------|-------------------|-------------------|
| DeepSeek V4 Flash | $0.042 | $0.92 |
| Gemini 2.5 Flash-Lite | $0.038 | $0.84 |

**Cost is negligible (<$1/month) at realistic usage.**

### Architecture
- `CatalystAnalystAgent.analyze_sentiment()` — new method in `src/agents/catalyst.py`
- `BaseAgent._call_llm()` — add `response_format` + per-call `temperature` params
- `_enrich_with_catalyst()` in `v3_pipeline.py` — wire as ADVISORY signal (non-blocking)
- Per-symbol cache (same symbol not queried twice in one trading day)
- Total implementation: ~5-6 hours, ~200 lines across 4 files

### Implementation Priority
**Do now (parallel to paper trading).** Cost is free, adds logging signal without 
changing behavior. Advisory only — never blocks entries.

---

## 6. Loguru %s Format Bug — Root Cause & Fix

### Root Cause
The codebase migrated from stdlib `logging` to loguru, but `reconciliation.py` 
retained 6 `%s`-style printf format strings. Loguru does NOT support `%s` — it 
uses `str.format()`-style `{}` exclusively. All 6 calls were logging literal `%s` 
strings with substitution args silently dropped.

### Fix Applied
All 6 calls in `src/pipeline/reconciliation.py` changed from `%s` → `{}`. 
Context7 verified: loguru docs explicitly state "exclusively supports curly-brace 
style formatting."

### Status
✅ Fixed. No other `%s`-style log calls remain in `src/`.

---

## 7. Deprecation Warnings — datetime.utcnow()

### Root Cause
Python 3.12 deprecated `datetime.utcnow()`. The codebase had 3 occurrences producing 
40 test warnings.

### Fix Applied
All 3 replaced with `datetime.now(timezone.utc)`:
- `src/session.py:45` — `field(default_factory=lambda: datetime.now(timezone.utc))`
- `src/session.py:114` — `datetime.now(timezone.utc)`
- `src/premarket.py:31` — `field(default_factory=lambda: datetime.now(timezone.utc))`

### Status
✅ Fixed. Zero DeprecationWarnings in test suite. `src/utils.py` `utc_now()` and 
`src/models/thesis.py` `_utcnow` were already correct.

---

## 8. Config Validation — Guardrails Added

### What Was Added
- **20 FATAL validators** on pydantic Settings model (`@field_validator` decorators)
- Fire at `Settings.load()` before any trading begins
- **25 WARNING checks** in `validate_config_warnings()` called from pipeline `__init__`

### Key Guards
| Field | Guard |
|-------|-------|
| `risk_per_trade_pct` | Must be ≤ 5% (FATAL) |
| `max_position_size_pct` | Must be ≤ 100% (FATAL) |
| `poll_interval_seconds` | Must be ≥ 10s (FATAL) |
| `max_spread_pct` | Must be ≤ 5% (FATAL) |
| `trailing_stop_distance_pct` | Warns if < 0.5% or > 10% |
| `addon_size_pct` | Must be ≤ 100% (FATAL) |

### Status
✅ Implemented. Invalid config raises `ValidationError` with clear message at startup.

---

## 9. Consolidated Market Data Verdict

*Synthesized from: API smoke test, `deep-research-report.md`, `Trading Bot Data Solution Research.docx`, and multi-subagent research across Alpaca, Polygon/Massive, Tradier, IBKR, Tiingo, Twelve Data, Finnhub, FMP, Alpha Vantage, Marketstack, Schwab, and yfinance.*

### 9.1 API Smoke Test — Hard Numbers

| Test | Result |
|------|--------|
| SIP 1-min bars (June 5, AAPL) | ✅ 391 bars, **62,371,523** total volume |
| IEX 1-min bars (same window) | ✅ 390 bars, **2,183,944** total volume |
| IEX as % of SIP | **3.5%** — confirms Alpaca's documented 2-5% figure |
| SIP bars with 0-min delay (past trading day) | ✅ Works — no 15-min restriction for historical days |
| SIP latest quote (free plan) | ❌ 403 — requires paid subscription |
| IEX latest quote (free plan) | ✅ Works |

**Critical unknown**: Whether SIP bars work during LIVE market hours with minimal delay (e.g., `end=now-1min`). Smoke test was on a Saturday (market closed). Both deep research reports state the free plan requires `end <= now - 15 minutes` for SIP bars during live hours per Alpaca's FAQ. **This must be verified on a Monday during market hours.**

### 9.2 Three-Phase Architecture

| Phase | Architecture | Monthly Cost | What You Get |
|-------|-------------|:---:|------|
| **Phase 1: Free paper testing** | Alpaca Basic: `feed=sip` historical bars + `feed=iex` current quotes | **$0** | Consolidated bars (15+ min delayed), accurate VWAP/EMA/ATR/RVOL, IEX quotes for execution |
| **Phase 2: Realistic paper simulation** | **Tradier Pro** ($10/mo) for real-time consolidated SIP streaming + Alpaca paper execution | **$10** | Real-time consolidated bars/quotes, Alpaca paper trading for fills. Best price/value ratio for live-like testing. |
| **Phase 3: Production live** | Alpaca Algo Trader Plus ($99/mo) — real-time SIP everywhere | **$99** | Zero migration, same SDK, real-time bars/quotes/streaming, 10K calls/min |

### 9.3 Every Provider Evaluated

#### ✅ Recommended

| Provider | Best For | Monthly Cost | Key Strength |
|----------|----------|:---:|------|
| **Alpaca Basic + delayed SIP** | Free paper testing | $0 | Official API, 100% consolidated volume for historical bars, same SDK |
| **Tradier Pro** | Low-cost realistic simulation | $10 | Real-time consolidated SIP streaming + REST, unlimited WebSocket symbols |
| **Alpaca Algo Trader Plus** | Production live trading | $99 | Same broker/SDK, real-time SIP everywhere, zero code migration, 10K req/min |

#### ❌ Rejected — Clear Reasons

| Provider | Monthly Cost | Reason for Rejection |
|----------|:---:|------|
| **yfinance** | $0 | Not a trading API. "Research and educational purposes only." No freshness/consolidation guarantees. Yahoo ToS disclaims accuracy. Rate limits unpredictable. |
| **Alpaca Basic IEX-only** | $0 | 3.5% of true volume → VWAP/RVOL/ATR systematically wrong for momentum strategy |
| **Tiingo** | $30 | Intraday volume is IEX-only. Same fundamental problem as Alpaca IEX. |
| **Marketstack** | varies | Sources from Tiingo → inherits IEX limitation. No WebSocket. |
| **Twelve Data (free/intro)** | $29 | ~5% of US volume on basic tier. Full coverage requires add-on licensing. No WebSocket below $99/mo. |
| **Alpha Vantage** | free/$50+ | 25 requests/day free. Not suitable for intraday bot loop. |
| **Finnhub** | free/$90+ | Unclear US intraday consolidated-volume provenance. Source-transparency rejection. |
| **Financial Modeling Prep** | $19+ | Unclear volume source for intraday. Source-transparency rejection. |
| **EODHD** | varies | Site states data is "not necessarily real-time nor accurate" — not fit for trading. |
| **Polygon/Massive (Starter $29)** | $29 | 15-min delayed at this tier. Real-time requires Advanced at $199/mo — pricier than Alpaca for worse integration. |
| **Polygon/Massive (Advanced $199)** | $199 | Excellent data-only provider, but $199 for real-time vs Alpaca's $99 all-in-one. Better as a second choice after Alpaca validation. |
| **IBKR** | ~$4.50 data | Requires TWS/IB Gateway. Default free feed is non-consolidated (Cboe One + IEX). Full NBBO needs bundle subscriptions. Heavy integration. |
| **Schwab** | $0 | No paper trading API. OAuth complexity. Rejected for this project's paper-testing workflow. |
| **QuantConnect/LEAN** | varies | Full framework rewrite — not a provider swap. |

### 9.4 Key Distinction: Bars vs Quotes

**This is the most important architectural insight:**

- **Bar data** (OHLCV for VWAP, EMA, RVOL, ATR, pattern detection): MUST have consolidated volume. IEX bars (3.5% of volume) produce systematically wrong indicators. On free plan, use Alpaca historical SIP bars (15-min delayed, but volume-accurate).
- **Quote data** (current price for exit timing, spread checks, position monitoring): IEX is acceptable. Price levels are accurate across exchanges due to arbitrage. The volume is wrong, but the price is right.

**Do NOT mix delayed SIP indicators with live IEX execution triggers in the same simulation loop.** The deep research report explicitly warns: this creates time-inconsistent signals and false confidence.

### 9.5 Recommended Way Forward

**Phase 1 (NOW — free, ~15 lines):**
Add `bar_feed`/`quote_feed` to `DataSettings` and wire through providers. Default: `bar_feed=sip`, `quote_feed=iex`. This gives consolidated bars for indicators and IEX quotes for execution — all on the free plan.

```python
# config/settings.py — DataSettings
bar_feed: str = "sip"       # "sip", "iex" — bar data feed
quote_feed: str = "iex"     # "iex" — current quote (sip requires paid plan)
```

```python
# src/providers/alpaca.py — add to both __init__
def __init__(self, bar_feed: DataFeed = DataFeed.SIP,
             quote_feed: DataFeed = DataFeed.IEX) -> None:
    self._bar_feed = bar_feed
    self._quote_feed = quote_feed
```

Then pass `feed=self._bar_feed` to `StockBarsRequest` and `feed=self._quote_feed` to `StockLatestQuoteRequest`. Wire from `main.py` via `_feed_map`.

**Phase 2 (when ready for realistic testing — $10/mo):**
Open a Tradier Pro account. Stream real-time consolidated SIP via Tradier API. Route to Alpaca paper trading for fills. Requires a new `TradierDataProvider` or adapter — ~200 lines, a few hours.

**Phase 3 (when ready for live — $99/mo):**
Upgrade Alpaca to Algo Trader Plus. Change `DATA_QUOTE_FEED=sip` in `.env`. Zero code changes needed — the same `bar_feed`/`quote_feed` architecture handles it.

### 9.6 What NOT to Do

- ❌ Don't use yfinance for core trading signals — no guarantees, no auditable feed semantics
- ❌ Don't run IEX-only for volume-dependent indicators — 3.5% volume = systematically wrong
- ❌ Don't mix IEX quotes with SIP indicators without logging the discrepancy
- ❌ Don't implement the yfinance hybrid (Option C from oracle) — Alpaca delayed SIP is strictly better
- ❌ Don't migrate to IBKR just for data — integration complexity > value for this project
- ❌ Don't pay for Polygon/Massive unless you specifically want data-broker separation

### 9.7 Pending Verification

| Item | Status |
|------|--------|
| Alpaca paper account connected ($97k equity) | ✅ Verified |
| IEX data working (`feed=iex`) | ✅ Verified |
| SIP historical bars working on free plan | ✅ Verified |
| SIP bars during LIVE market hours with minimal delay | ⚠️ **UNTESTED** — must verify on a trading day (Monday-Friday, 9:30 AM - 4:00 PM ET). Both deep research reports and Alpaca's FAQ state the free plan requires `end <= now - 15 min` for SIP bars during live hours. |
| Tradier Pro $10/mo real-time SIP claim | ⚠️ **UNTESTED** — needs account opening and API verification |
| `delayed_sip` feed on latest endpoints | ⚠️ **UNTESTED** — Alpaca docs mention it but smoke test not run |

---

## Related Documents

| Document | Purpose |
|----------|---------|
| `SPEC.md` | Authoritative rebuild specification |
| `FINDINGS.md` | This file — reference research and data notes |
| `AGENTS.md` | Agent workflow rules (Context7 requirement) |
