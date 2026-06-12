# Alpacatrader Rebuild Spec — Attention-First Top-Gainer Bot

Last updated: 2026-06-12 — §22.17 post-audit remediation implemented. 771 tests pass, zero warnings.

This is the project source of truth for the rebuild. The existing source code is
legacy reference only until it is rebuilt against this spec.

---

## 1. Purpose

### 1.1 What The Bot Is Trying To Do

Alpacatrader is a top-gainer momentum execution machine.

It tries to find stocks that the market is focused on right now, wait for a
defined-risk entry, enter small, add only if the trade proves itself, scale out
quickly, and exit when risk is no longer controlled.

Core thesis:

```text
Top gainer + attention + definable risk = potential trade.
```

The bot should ask:

```text
This stock is moving. How can I participate with controlled risk?
```

Not:

```text
How can I reject this stock?
```

### 1.2 What The Bot Is Not Trying To Do

The bot is not:

- a swing-trade thesis engine,
- a fundamental analyst,
- a company-quality judge,
- a news-prediction system,
- a risk committee,
- a 5-Pillar scorecard with new names,
- an AI approval system,
- a scanner that deletes risky-looking runners before price action is checked.

It trades momentum. It does not decide whether a company is good.

### 1.3 DSY Lesson

DSY is the permanent regression example.

The old bot saw DSY and rejected it because it was Chinese, had no news, started
with light volume, and looked speculative. Those facts were not the reason to
skip. They were the reason to size smaller, demand definable risk, and exit fast
if wrong.

The correct behavior is:

```text
DSY is running.
Chinese/no-news is a theme today.
Spread is acceptable.
Volume is now enough.
First pullback formed.
Risk is 7 cents.
Starter size is 50 shares.
Stop is under pullback.
If it pops, sell partial.
If it fails, exit.
If it reclaims HOD, add only if risk stays controlled.
If spread explodes, flatten.
```

If a DSY-like candidate is rejected only because it is Chinese, has no news, has
no catalyst label, is low float, or looks speculative, the rebuild failed.

---

## 2. Non-Negotiable Rules

1. Rank attention before applying soft warnings.
2. Hard rejects must be mechanical and execution-related.
3. Old alpha-killing filters become annotations, not automatic no-trade reasons.
4. Every entry must define entry price, stop price, risk per share, size, and
   invalidation.
5. Starter size is the default for every new symbol.
6. Never average down.
7. Add only when the starter is already right and protection can be improved.
8. Scale out fast enough that winners pay for their volatility.
9. Exits run before new entries and emergency exits run before normal exits.
10. Monitoring open positions has priority over scanning.
11. A symbol with an open/pending/exiting position is locked from new entries.
12. Broker position truth wins over local state after restart.
13. No live trading until paper logs prove the system and a live data path exists.

---

## 3. Strategy Flow

```text
1. Scan top gainers.

2. Enrich candidates with current quote, recent bars, float/sector/country if available.

3. Score attention:
   - premarket/intraday gain,
   - current volume/RVOL/dollar volume,
   - HOD behavior and acceleration,
   - simple theme/former-runner bonuses.

4. Process top-attention candidates only.

5. Run catastrophic hard checks:
   - broker-tradeable,
   - market/session allows entries,
   - fresh quote and valid bid/ask,
   - spread and liquidity allow exit,
   - not currently halted,
   - stop can be defined,
   - account risk budget exists,
   - symbol is not locked.

6. Classify move state:
   - early,
   - active,
   - extended/parabolic,
   - backside/fading,
   - halt-risk.

7. Choose mode:
   - watch,
   - starter entry,
   - add on confirmation,
   - scalp only,
   - avoid new longs.

8. Enter only on supported price-action setups:
   - first pullback,
   - micro pullback,
   - HOD reclaim,
   - consolidation breakout,
   - VWAP reclaim/bounce,
   - scalp reclaim.

9. Size from risk per share:
   - starter risk first,
   - attention and soft warnings adjust size,
   - account limits cap size,
   - zero-size means no entry.

10. Manage position:
    - stop immediately,
    - add only if right,
    - partial quickly,
    - protect runner,
    - flatten when risk cannot be measured.
```

---

## 4. Terms And Minimum Tradeability

### 4.1 Attention

Attention means the market is focused on the stock now.

Common attention signals:

- large premarket gap or intraday percent gain,
- rapid recent price change,
- unusual current volume or RVOL,
- repeated HOD pushes,
- scanner persistence,
- theme participation,
- former-runner status.

Attention is not approval. It decides what the bot watches first.

### 4.2 Tradeable

Tradeable does not mean safe.

Tradeable means the bot has the minimum information and market access needed to
attempt a controlled trade:

- broker marks the symbol tradable,
- market/session allows a new long entry,
- current price is available,
- fresh bid/ask quote exists,
- spread can be calculated and is not in the hard-reject zone,
- recent volume/dollar volume is enough to enter and exit,
- a logical stop can be placed,
- account/symbol risk limits allow another trade,
- the symbol is not locked by existing position or pending orders.

### 4.3 Definable Risk

Risk is definable when the bot can answer:

```text
Where exactly am I wrong?
How many cents per share am I risking?
How many shares can I take without exceeding max loss?
Can I exit if wrong?
```

If the answer is unclear, no entry.

### 4.4 Data Confidence

Data confidence is separate from attention.

`data_confidence` is a 0.0-1.0 value on every enriched candidate:

- `1.0`: quote fresh, bars fresh enough, scanner timestamp known, key fields present.
- `0.7`: scanner delayed or non-critical enrichment fields missing.
- `0.5`: several non-critical fields missing or bars stale, but quote is fresh.
- `<0.5`: watch only unless setup is exceptionally clear and size is tiny.

Missing float, market cap, sector, premarket high/low, or news does not hard-reject.
Missing fresh quote, bid/ask, current price, or stop definition can hard-reject.

Default calculation:

```text
start at 1.0
-0.10 if scanner timestamp is unknown
-0.20 if scanner data is older than 20 minutes
-0.20 if recent bars are stale or missing
-0.05 for each missing optional metadata field: float, market_cap, sector, industry, country
-0.05 for each missing optional premarket field: premarket_high, premarket_low, premarket_gap_pct
floor at 0.3 if execution-critical quote/price fields are present
```

If execution-critical quote, bid/ask, price, or stop data is missing, hard-filter
rules apply before confidence is used.

---

## 5. Discovery And Data Reality

### 5.1 Required Scanner Behavior

The scanner asks:

```text
What is the market paying attention to right now?
```

Not:

```text
Which stocks already satisfy my checklist?
```

The scanner ranks and reports. It must not delete candidates for Chinese ADR,
no news, no catalyst, low float, biotech, speculative theme, or parabolic price
action.

### 5.2 Free-Tier Scanner Stack

Free data cannot produce true real-time full-market top gainers. The free stack
is for paper-mode learning and delayed discovery.

1. **Finviz free screener — primary free scanner**
   - Top-gainer list is delayed, commonly 15-20 minutes.
   - Good enough to find sustained movers for paper logs.
   - Not good enough for live first-minute scanning.
   - Scraping can break when the site changes.

2. **Alpaca free IEX — quote/bar enrichment for known symbols**
   - Current quote/bar requests support a `feed` parameter.
   - IEX is available on free accounts; SIP/OTC require subscription for latest
     quotes/trades/snapshots.
   - IEX is one exchange and does not represent full-market volume.
   - Use it to validate price/spread on discovered symbols, not to compute a
     full-market scanner.

3. **yfinance — enrichment fallback only**
   - Not a primary scanner.
   - Unofficial/private Yahoo endpoints can fail or rate-limit.
   - Useful only for non-critical enrichment such as float, market cap, sector,
     country, and daily history when available.
   - If it fails, log and continue with lower confidence.

4. **Manual watchlist — emergency only**
   - Allowed only when dynamic scanners fail.
   - Must be labeled `source = manual_emergency_watchlist`.
   - Manual names are not true top gainers unless current quote/volume confirms
     attention.

### 5.3 Paid Data Path

The strategy should not need a rewrite when better data is added. The scanner
and data provider must be swappable.

Realistic paid upgrades:

- Alpaca Algo Trader Plus: SIP quotes/bars/snapshots/screener in the same broker
  ecosystem.
- Polygon/Massive Developer or higher: real-time market data and gainers/losers.
- Trade Ideas / Benzinga / Finviz Elite: scanner or news/catalyst feeds.

Paid data is needed for live-quality first-minute discovery, accurate full-market
small-cap volume, and robust real-time scanner behavior.

### 5.4 Candidate Output

Scanner/enrichment should produce a candidate with these fields when available:

```python
Candidate:
    symbol
    price
    percent_gain
    premarket_gap_pct
    current_volume
    relative_volume
    dollar_volume
    previous_close
    day_high
    day_low
    premarket_high
    premarket_low
    float_shares
    market_cap
    sector
    industry
    country
    exchange
    source
    source_timestamp
    quote_timestamp
    bar_timestamp
    data_confidence
```

Field availability is uneven:

| Field group | Free reliability | Rule |
|---|---:|---|
| symbol / delayed gain / delayed volume | medium | usable for attention, log source age |
| current quote / bid / ask | high via Alpaca IEX for known symbols | required for entry |
| full-market real-time volume | low on free tier | do not pretend IEX volume is consolidated |
| float / sector / country | medium-low | soft context only |
| premarket levels | medium | useful if present, not required |

### 5.5 Candidate Enrichment

For each top scanner candidate, fetch only what is needed:

1. fresh quote: bid, ask, last/mark, timestamp,
2. recent 1-minute bars for setup detection,
3. recent 5-minute bars only if needed for state/exit context,
4. daily context: prior close, prior high/low, recent top-gainer history if available,
5. optional metadata: float, market cap, sector, industry, country.

Enrichment failures reduce confidence unless they remove execution-critical data.

### 5.6 Scanner Gap

Free scanner discovery has a chicken-and-egg problem:

```text
Finviz delayed scanner discovers symbol 15-20 minutes late
→ Alpaca IEX gives fresh quote for that now-known symbol
→ quote is fresh but discovery is stale
```

Paper mode must log both ages:

- scanner age,
- quote age.

A fresh quote does not make stale discovery real-time.

---

## 6. Attention Ranking

### 6.1 Score Shape

Use a simple 0-100 score.

V1 weighted factors:

```text
price_attention:       40  # premarket gap or intraday percent gain
volume_attention:      35  # RVOL, current volume, dollar volume
hod_acceleration:      25  # HOD proximity/reclaims, 1/3/5m price ROC if available
```

V1 bonuses, capped at 100 total:

```text
theme_active:          +10
former_runner:          +5
repeated_scanner_seen:   +5
```

Do not add more factors until paper logs prove the need.

### 6.2 Default Normalization

These defaults are intentionally simple and paper-tunable:

```text
price_attention = min(40, max(premarket_gap_pct, percent_gain, 0) / 50 * 40)

volume_attention =
  min(20, RVOL * 5) if RVOL known, plus
  min(15, trailing_5m_dollar_volume / min_dollar_volume * 15)

hod_acceleration =
  15 if price is within 1% of HOD, 8 if within 3%, else 0, plus
  min(10, max(roc_1m_pct, roc_3m_pct, roc_5m_pct, 0) / 5 * 10)
```

If a factor is unavailable, do not fabricate it. Redistribute its weight across
available factors and reduce `data_confidence`.

Redistribution algorithm:

```text
available_weight = sum(weights for factors with usable data)
raw_score = sum(points earned by factors with usable data)
base_attention_score = raw_score * (100 / available_weight)
attention_score = min(100, base_attention_score + bonuses)
```

If `available_weight < 50`, do not enter from the score alone. Candidate may be
watched only until more data is available.

### 6.3 Theme Detection

Theme detection must stay simple in v1.

A theme is active when either:

- at least 3 of the top 10 attention candidates share country, sector, or
  industry, or
- one clear leader is the #1 attention candidate and at least 2 related names
  start moving after it.

"Start moving" means a related symbol appears in the top-10 attention list or
gains at least 5% within 30 minutes after the leader appears.

Examples:

- Chinese no-news runners,
- low-float biotech approvals,
- quantum names,
- crypto sympathy,
- EV sympathy.

Theme is an attention driver, not a quality claim. It may increase attention and
also create risk warnings.

### 6.4 Former Runner

Defaults, configurable later:

A symbol is a former runner if the bot previously observed one of these within
30 trading days:

- intraday gain of 50% or more,
- close-to-high move of 25% or more with strong volume,
- previous trade on the symbol made 2R or more,
- symbol was top-3 attention on a prior session.

The former-runner store is empty on first run. That is expected.

### 6.5 Float Rotation

Float rotation is an edge signal, not a filter.

If float is known:

```text
rotation = cumulative_session_volume / float_shares
```

Session volume means volume from 9:30 ET market open, or from the earliest
available quote/bar time if market-open data is unavailable.

Default interpretation:

- `<30%`: attention may still be building,
- `30%-100%`: active zone,
- `100%-200%`: watch for exhaustion,
- `>200%`: reduce size and take faster partials.

If float is unknown, log `float_unknown` and use raw volume/dollar volume.

### 6.6 Multi-Day Context

The move classifier should know whether the stock is a fresh first-day runner or
a multi-day extended runner.

Inputs when available:

- prior day high/low/close,
- whether the symbol was a prior top gainer,
- cumulative volume/float across the move,
- prior session close-to-high behavior.

Default effect:

- first-day breakout: normal attention and starter rules,
- second-day continuation: normal starter but faster review for exhaustion,
- third+ day runner: reduced size, faster partials, no blind chase.

---

## 7. Hard Filters

Hard filters are the only automatic blocks. Every hard skip must be logged with
a mechanical reason.

### 7.1 Broker / Market Structure

Hard reject new entries if:

- broker marks symbol not tradable,
- broker rejects opening orders for the symbol,
- symbol is OTC/Pink/unsupported by the broker setup,
- symbol is currently halted/suspended,
- market is closed for new entries,
- current time is after the configured new-entry cutoff.

Default time gates:

- 9:30-9:35 ET: watch only, no new entries.
- 11:30-14:00 ET lunch: soft size reduction, not a blackout.
- after 15:30 ET: no new entries; continue managing open positions.
- 15:55 ET: flatten any intraday positions unless explicitly changed later.

### 7.2 Execution Data

Hard reject new entries if:

- no current price,
- no bid/ask when needed,
- bid <= 0 or ask <= 0,
- bid >= ask,
- quote age > `max_quote_age_seconds` (default 15s),
- spread cannot be calculated.

Quote age tiers:

| Quote age | New entry behavior |
|---:|---|
| `<=5s` | normal |
| `>5s` and `<=15s` | stale-warning size reduction |
| `>15s` | hard reject |

### 7.3 Liquidity / Spread

Default spread tiers:

```text
spread <= 1.0%: normal
spread 1.0%-3.0%: caution, smaller size
spread 3.0%-5.0%: tiny starter or scalp only
spread > 5.0%: hard reject
```

Hard reject if:

- current volume is effectively zero,
- trailing 5-minute dollar volume is below `min_dollar_volume`, unless only
  scanner-level delayed volume exists and the candidate is watch-only,
- proposed order would be too much of observed liquidity,
- spread > hard threshold.

Default `min_dollar_volume` means trailing 5-minute dollar volume. If only
scanner data is available, log that it is delayed and do not enter until quote
and recent bars confirm liquidity.

### 7.4 Risk Definition

Hard reject if:

- no logical stop exists,
- `risk_per_share <= 0`,
- `floor(risk_amount / risk_per_share) < 1`,
- stop is too tight to matter: `risk_per_share <= 1.5 * (spread + slippage_estimate)`,
- stop distance exceeds setup max stop width,
- loss cannot be bounded.

Default paper `slippage_estimate = $0.01` unless configured.

### 7.5 Account And Symbol Risk

Hard reject if:

- kill switch active,
- daily max loss hit,
- per-symbol loss cap hit,
- max open positions hit,
- max total open risk hit,
- theme concentration limit blocks the entry,
- existing position or pending entry already exists for the symbol.

---

## 8. Soft Annotations

Soft annotations affect size, mode, entry requirement, or exit speed. They do not
hard-reject by themselves.

Soft multipliers multiply together after attention sizing. Floor total soft
multiplier at `0.25x` unless account risk requires zero.

| Signal | Default treatment |
|---|---|
| Chinese ADR | theme/risk annotation; never hard reject |
| No news / no catalyst | no penalty if attention >=70; `0.75x` size if attention <70 |
| Speculative / weird industry | annotate; require normal hard checks only |
| Parabolic / extended | `0.5x`; no chase; require pullback/reclaim; faster partial |
| Below VWAP | context only; possible backside warning or VWAP reclaim setup |
| Below EMA | trend warning; not a ban |
| Lunch | `0.5x`; require cleaner setup; not blackout |
| Outside focus price range | attention still logged; `0.5x` size unless risk proves otherwise |
| Price < $2 | `0.5x`; require liquidity/spread to prove exitability |
| Low float | squeeze potential plus size cap, usually `0.5x` if very low |
| Unknown float | `0.5x`; do not reject if liquidity is obvious |
| Biotech | volatility warning; confirmed catalyst may be positive |
| High insider ownership | data warning; verify impossible values; not a ban |
| Reverse split / recent IPO | `0.5x`; dilution/history warning |
| Halt history today | tiny starter or scalp only; no market order in wide spread |
| Float rotation >200% | `0.5x`; fast partials; watch for backside |
| Stale quote 5-15s | `0.5x`; entry allowed only if all other checks are clean |
| Weak liquidity but above hard minimum | `0.5x`; faster exit if spread widens |

Former runner is a positive attention modifier, not a warning.

---

## 9. Move States And Modes

### 9.1 Classifier Inputs

The move classifier receives:

- enriched candidate,
- current quote,
- last 20 1-minute bars when available,
- last 12 5-minute bars when available,
- VWAP, 9EMA, ATR14 or 10-bar average range,
- HOD/LOD and HOD break history,
- prior state for the symbol,
- halt count today,
- multi-day context when available.

If ATR14 is unavailable, use average 1-minute bar range over the available recent
bars. State evidence must be logged.

### 9.2 State Priority

Classify in this order:

1. halt-risk,
2. backside/fading,
3. extended/parabolic,
4. active,
5. early.

Higher-priority states win when rules conflict.

Evaluate states in priority order and stop at the first match. Do not continue
into lower-priority states after a higher-priority state is detected.

### 9.3 State Definitions

Defaults are starting points for paper logs, not sacred values.

#### Halt-Risk

Detected when any is true:

- halted earlier today,
- spread > 3% and candles are vertical,
- price moved >=10% in <=5 minutes on high volatility,
- quote instability or rapid spread jumps,
- no simple pullback after a vertical run: no 2-bar pullback retracing at least
  `1 * avg_range` with declining volume.

Mode: avoid chase; tiny/scalp only after clean pullback and acceptable spread.
Emergency exits run first.

#### Backside / Fading

Detected when any is true:

- at least 2 lower highs over the last 20 bars and failed HOD reclaim,
- below VWAP for 5 consecutive bars and at least one failed VWAP reclaim,
- volume fading while bounces fail,
- spread widening while price cannot reclaim.

Mode: avoid new longs. Only VWAP reclaim may reconsider the symbol.

Use 1-minute bars for HOD reclaim failures. Use 5-minute bars for VWAP/lower-high
structure when 5-minute bars are available; otherwise use 1-minute bars and log
the reduced confidence.

#### Extended / Parabolic

Detected when any is true:

- distance from nearest logical stop > setup max stop width,
- price is >15% above last clean pullback low,
- recent candle range >2x average range for multiple candles,
- move is vertical and pullback has not formed.

Mode: no chase; starter only; first pullback/HOD reclaim/scalp only; faster
partials; no full-size initial entry.

#### Active

Detected when most are true:

- repeated HOD behavior,
- higher-low structure,
- pullbacks bought,
- strong volume/RVOL,
- spread manageable,
- not backside, not halt-risk, not too extended from stop.

Mode: supported entry setups allowed; add only after confirmation.

#### Early

Detected when:

- recently appeared on scanner or premarket watch,
- attention is building,
- volume is improving,
- no clear backside behavior,
- a first pullback may still form.

Mode: watch closely; allow starter only if a tight, clear setup appears.

### 9.4 Entry Permission Matrix

| State | First pullback | Micro pullback | HOD reclaim | Consolidation breakout | VWAP reclaim/bounce | Scalp reclaim |
|---|---:|---:|---:|---:|---:|---:|
| early | yes | no | no | no | yes if clean | no |
| active | yes | yes | yes | yes | yes | no |
| extended | yes, starter only | no | yes, starter only | no | yes, starter only | yes |
| backside | no | no | no | no | yes only | no |
| halt-risk | no chase | no | no chase | no | no unless very clean | yes, tiny only |

Only one entry signal per symbol per cycle is allowed.

Mode mapping:

| State | Default mode |
|---|---|
| early | `watch`; `starter_entry` only if first-pullback/VWAP setup is valid |
| active | `starter_entry`; `add_on_confirmation` only for existing winners |
| extended | `scalp_only` or `starter_entry_tiny`; never full-size initial entry |
| backside | `avoid_new_longs`; only reconsider on clean VWAP reclaim |
| halt-risk | `avoid_new_longs`; `scalp_only` only if scalp criteria pass |

Setup priority when multiple fire:

1. first pullback,
2. HOD reclaim,
3. consolidation breakout,
4. micro pullback,
5. VWAP reclaim/bounce,
6. scalp reclaim.

If equal priority, choose the setup with tighter valid risk per share.

---

## 10. Entry Model

### 10.1 Universal Entry Requirements

Every entry signal must include:

- `entry_setup` enum,
- entry price,
- stop price,
- risk per share,
- target or first scale-out area,
- proposed size,
- invalidation condition,
- state and state evidence,
- quote age and spread at decision time.

If any required field is missing, no entry.

### 10.2 Shared Mechanical Terms

Use these defaults unless paper logs justify changes:

- `tick`: minimum practical price increment, default `$0.01`.
- `avg_range`: ATR14 if available, else average range of recent 1-minute bars.
- `strong move`: price advanced at least `3 * avg_range` within 20 bars or at
  least 5% from a recent base, with volume >1.5x prior average.
- `logical level`: VWAP, 9EMA, prior intraday support, prior HOD, premarket high,
  prior day high/low, or round/half-dollar level.
- `controlled selling`: pullback volume <=70% of surge volume and no candle closes
  far below the logical level.

### 10.3 Setup Stop Width Defaults

Hard reject a setup if the stop is outside its max width.

| Setup | Typical stop | Max stop |
|---|---:|---:|
| first pullback | 1.5%-4.0% | 5.0% |
| micro pullback | 0.8%-2.0% | 3.0% |
| HOD reclaim | 1.0%-3.0% | 4.0% |
| consolidation breakout | 0.8%-2.5% | 3.5% |
| VWAP reclaim/bounce | 1.0%-3.0% | 4.0% |
| scalp reclaim | 0.3%-1.0% | 2.0% |

### 10.4 First Pullback

Primary setup.

Mechanical definition:

1. Strong move exists.
2. This is the first observed pullback since the symbol appeared on scanner and
   then made a new session high. A pullback is valid only if it satisfies the
   criteria below.
3. Pullback retraces at least 20% of the prior up-leg or at least `1 * avg_range`.
4. Pullback spans 2-8 bars.
5. Pullback shows controlled selling.
6. Pullback low holds a logical level.
7. Reclaim candle closes green above the prior candle high with volume >1.2x
   pullback average.

Entry: reclaim candle close plus one tick or break of pullback high.

Stop: pullback low minus one tick.

Invalidation: price trades below pullback low before entry fills, or reclaim
candle fails on the next bar.

### 10.5 Micro Pullback

For active squeezes only.

Mechanical definition:

1. State is active.
2. Price advanced at least `1.5 * avg_range` over the last 3-5 bars.
3. 1-3 red/doji candles pause after the surge peak.
4. Dip candles have lower volume than the surge average.
5. Dip does not break VWAP or the nearest logical support.
6. Green reclaim candle closes above the surge peak with volume >=1.5x dip
   average.

Entry: reclaim candle close plus one tick.

Stop: lowest dip low minus one tick.

Invalidation: dip low breaks, spread enters hard zone, or quote becomes stale.

### 10.6 HOD Reclaim

For continuation after a pause or failed first attempt.

Mechanical definition:

1. Prior HOD is established.
2. Price pulls back and holds above a logical stop level.
3. Price closes back above prior HOD.
4. Reclaim candle volume is at least the average of the prior 10 bars or >1.2x
   pullback average.

Entry: above reclaim trigger.

Stop: below reclaim candle low or recent higher low.

Invalidation: next bar closes back below reclaimed HOD.

### 10.7 Consolidation Breakout

For tight high consolidation.

Mechanical definition:

1. Range lasts 5-20 bars.
2. Range high-low <=2% of price.
3. Range is within 3% of HOD.
4. Volume is not dead: recent volume >=50% of prior 10-bar average.
5. Breakout candle closes above range high.

Entry: range high break plus one tick.

Stop: consolidation low minus one tick.

Invalidation: breakout closes back inside range immediately.

### 10.8 VWAP Reclaim / Bounce

VWAP is a context level, not a gate.

Valid scenarios:

1. Price above VWAP pulls back to VWAP and bounces.
2. Price below VWAP reclaims VWAP with volume.
3. Price below VWAP forms first pullback at a higher-timeframe level; VWAP is
   overhead context, not an automatic backside label.

Mechanical definition:

- reclaim/bounce candle closes above VWAP,
- stop can be placed below VWAP or reclaim low,
- volume is not dead,
- spread is acceptable.

Entry: reclaim confirmation.

Stop: below failed reclaim/bounce low.

Invalidation: next bar closes back below VWAP and cannot reclaim.

### 10.9 Scalp Reclaim

Scalp mode exists only to participate in extended/halt-risk moves with tiny,
fast risk. It is not a normal trade.

Allowed only when:

- state is extended or halt-risk,
- spread <=3%,
- quote age <=5s,
- a 1-2 candle micro dip forms,
- immediate reclaim occurs,
- stop width <=1% of price or <=`1 * avg_range`, whichever is smaller.

Entry: reclaim break.

Stop: micro dip low.

Exit: sell 50%-100% at +0.5R or first stall/red candle, whichever comes first.

Adds: never.

### 10.10 Entry Invalidators

Any of these cancels an entry before fill:

- quote becomes stale,
- spread moves into hard reject zone,
- price trades through planned stop before fill,
- setup level fails,
- symbol becomes locked,
- account risk changes so size becomes invalid,
- broker rejects protection order.

---

## 11. Sizing And Adds

### 11.1 Starter Size

Every new symbol starts with starter size.

Default formula:

```text
max_trade_risk = equity * max_trade_risk_pct
starter_risk = equity * starter_risk_pct
```

`starter_risk_pct` is configured directly. Recommended default is 25%-33% of
`max_trade_risk_pct`.

Shares:

```text
shares = floor(adjusted_starter_risk / risk_per_share)
```

If shares < 1, no entry.

### 11.2 Attention Size Modifier

Attention adjusts starter risk. Low attention is not a reject; it reduces size
and entry aggressiveness.

| Attention score | Risk multiplier | Behavior |
|---:|---:|---|
| 85-100 | 1.0x | full starter; adds allowed after confirmation |
| 70-84 | 0.75x | reduced starter; stronger add confirmation |
| 50-69 | 0.50x | tiny starter; no adds unless paper logs later prove otherwise |
| <50 | 0.25x | watch/scalp only; no normal entry |

### 11.3 Soft And Confidence Multipliers

Adjusted starter risk:

```text
adjusted_starter_risk =
  starter_risk
  * attention_multiplier
  * soft_multiplier
  * data_confidence
```

Floor soft multiplier at 0.25x. Do not floor account-level risk checks.

### 11.4 Add Size

A starter can become larger only through adds on confirmation. The initial entry
is never full size by default.

Valid add requires all:

- current price >= starter entry + `0.5R`,
- price makes new session high, breaks consolidation, or forms a higher low,
- stop can be raised to breakeven or a new higher low,
- spread <= caution threshold,
- quote age <=5s,
- no halt in last 5 minutes,
- at least 60 seconds since the last add,
- total combined risk remains <= `max_trade_risk_pct`,
- total open risk remains <= `max_open_risk_pct`.

Add risk:

```text
add_risk = min(starter_risk, remaining_trade_risk_budget)
```

Adding is a mutation of the existing position, not a new candidate flow. The
combined position must be re-protected atomically.

### 11.5 Never Add

Never add if any are true:

- current price <= average entry,
- starter is losing,
- trade is below original entry,
- reclaim failed,
- spread widened by >50% since entry or is above caution threshold,
- quote age >5s,
- stop cannot be raised,
- position is unprotected,
- after first scale-out unless explicitly enabled later,
- because the first entry is losing.

### 11.6 Risk Limits

Required risk calculations:

```text
open_risk_per_position = max(0, avg_entry - stop_price) * shares
total_open_risk = sum(open_risk_per_position for all open/running positions)
per_symbol_daily_loss = realized_symbol_pnl_today + min(0, unrealized_symbol_pnl)
daily_pnl = realized_pnl_today + unrealized_pnl_all_open_positions
```

Required limits:

- max trade risk,
- max per-symbol daily loss,
- max daily loss,
- max open positions,
- max total open risk,
- theme concentration.

If per-symbol daily loss breaches the cap, flatten that symbol and ban re-entry
for the session. If daily loss breaches the cap, flatten all positions and turn
on the kill switch.

### 11.7 Theme Concentration

Default: only one active position per theme.

If a new candidate shares a theme with an open position:

- keep watching it,
- do not enter while the existing themed position is open,
- allow entry only after the existing position is closed or reduced to protected
  runner mode.

Also stagger new entries by at least 30 seconds.

---

## 12. Exit Model

Exits matter more than filters.

### 12.1 Exit Priority

Position monitoring runs before scanning. Emergency checks run first.

```text
1. emergency exit
2. daily loss / per-symbol loss cap
3. hard stop / setup invalidation
4. missing protection / unprotected state
5. scale-out trigger
6. failed reclaim / failed pullback
7. VWAP loss without reclaim
8. spread expansion
9. volume/liquidity disappearance
10. time-based exit
11. trailing runner exit
```

### 12.2 Hard Stop And Invalidation

Hard stop triggers when price trades at or below the stop price.

Invalidation triggers before hard stop when the setup fails:

- pullback entry: price trades below pullback low,
- HOD reclaim: next bar closes below reclaimed HOD,
- VWAP reclaim: next bar closes below VWAP and cannot reclaim,
- consolidation breakout: price closes back inside range immediately,
- scalp: first stall/red candle after entry if target not hit.

### 12.3 Fast Scale-Out

Normal starter trade:

- at +1R: sell 33%-50%,
- extension bar while profitable: sell 25%-50%,
- spread enters caution zone while profitable: sell 25%,
- after first partial, move remaining stop to breakeven or better.

Extension bar default: 1-minute or 5-minute candle range >1.5x the 10-bar average
range.

Extended/parabolic state:

- first partial at +0.5R,
- sell at least 50%,
- no full-size hold through a vertical move.

Scalp mode:

- sell 50%-100% at +0.5R or first stall/red candle,
- no runner unless already risk-free and explicitly logged.

### 12.4 Runner

Runner is allowed only after partial profit.

Runner rules:

- remaining shares have breakeven-or-better stop,
- if price reaches +1.5R, trail under the higher of the recent 3-bar low or entry +0.5R,
- trail only moves up,
- exit runner on trail hit, failed continuation, 2 consecutive red 5-minute bars,
  or end-of-session time exit,
- no overnight hold in v1.

The recent 3-bar low means the lowest low of the last three 5-minute bars. If
5-minute bars are unavailable, use the last three 1-minute bars and log reduced
confidence.

### 12.5 Emergency Exit Conditions

Flatten immediately, or mark unprotected and escalate as specified, when:

| Condition | Default trigger | Action |
|---|---|---|
| Spread explosion | spread >5% or >3x entry spread | flatten if quote is valid |
| Quote unreliable | quote age >60s, bid/ask invalid, or crossed | block entries; flatten losing positions; mark profitable positions unprotected if risk cannot be measured |
| Stop missing | broker has position but stop/OCO missing and replacement fails | flatten if quote live; otherwise mark UNPROTECTED and retry |
| Halt resume adverse gap | resume price against position by >5% or >2R | flatten |
| Per-symbol cap | cap breached | flatten symbol and ban re-entry |
| Daily cap | cap breached | flatten all and kill switch |
| Broker mismatch | broker qty and local qty disagree materially | broker truth wins; reconcile; flatten if uncontrolled |
| Multiple halts | 3+ halts same symbol same session | flatten and ban symbol |
| Broker unreachable | >120s while in position | do not assume flat; mark UNPROTECTED; poll every 10s |

### 12.6 Order Handling During Exits

Before any partial or full exit:

1. cancel or adjust conflicting open orders for that symbol,
2. submit the exit order,
3. move symbol state to `SCALING_OUT` or `EXITING`,
4. do not submit another exit for the same symbol until the order resolves,
5. re-place protection for remaining shares after partial fill.

---

## 13. State Model For Multi-Position Trading

The bot may trade multiple symbols, but only if state is explicit. The state
model is necessary simplicity, not fancy architecture.

### 13.1 Candidate Lifecycle

Pre-entry lifecycle:

```text
DISCOVERED
ATTENTION_RANKED
ENRICHED
HARD_CHECKED
WATCHING
STARTER_READY
```

Once an order is submitted, the position state machine owns the symbol.

### 13.2 Position States

```text
NONE
  No position and no pending orders.

PENDING_ENTRY
  Entry order submitted but not fully resolved. Symbol is locked.

OPEN
  Filled and protected by stop/OCO. Normal monitor state.

ADDING
  Add order pending. Original position remains protected. Symbol locked.

SCALING_OUT
  Partial exit pending. No duplicate exits or adds.

RUNNER
  Partial profit taken. Remaining shares protected breakeven or better.

EXITING
  Full exit order pending. No further exit logic until resolved.

UNPROTECTED
  Position exists but protection is missing or uncertain. Emergency state.

CLOSED
  Terminal state for the session unless re-entry is explicitly allowed and risk permits.

ERROR
  Broker/local state cannot be reconciled automatically. Alert and attempt flatten.
```

### 13.3 Symbol Lock

A symbol is locked when any are true:

- position state is not `NONE` or terminal `CLOSED`,
- pending buy order exists at broker,
- entry decision was submitted this cycle and has not resolved,
- exit is in progress,
- symbol hit per-symbol daily loss cap.

Locked symbols are excluded from new entry scanning. They are processed only by
the position monitor.

### 13.4 Pending Orders

Track pending orders by symbol:

```text
symbol
order_id
order_type: entry | stop | target | add | scale_out | exit | oco
side
qty
status
submitted_at
linked_position_id
```

This prevents duplicate entries, double-sells, and missing-protection errors.

### 13.5 Account Risk State

Track at runtime:

```text
daily_realized_pnl
daily_unrealized_pnl
total_open_risk
open_position_count
per_symbol_daily_loss
theme_exposure
kill_switch_active
daily_loss_breached
```

All entry gates use this state before any order submission.

---

## 14. Processing Model And Logs

### 14.1 Main Loop

Use separate monitor and scan cadence.

```text
on startup:
  load config
  connect broker/data
  load local state
  reconcile broker positions and open orders

loop every monitor_interval_seconds:
  update clock/session

  for each open/locked position:
    fetch fresh quote if possible
    reconcile pending orders/fills
    run emergency exits
    run loss caps
    run stop/invalidation checks
    run scale-out/trailing/time exits
    log decisions

  for each watched candidate:
    refresh quote/bars if within watch window
    check for entry triggers at watch cadence
    submit only if hard checks, state, setup, sizing, and lock checks pass

  if entries allowed and scan_interval_seconds elapsed:
    scan top gainers
    enrich candidates
    score attention
    rank candidates
    process top_attention_to_process candidates

  sleep until next monitor tick
```

Position monitoring must not wait on scanner requests.

### 14.2 Active Candidate Monitoring

Do not scan and forget.

Watched candidates remain active for a configurable watch window. During that
window, the bot refreshes quote/bars and checks for pullback/reclaim/breakout
triggers more frequently than the full scanner loop.

Default:

- scanner interval: 30 seconds in paper mode if source supports it; otherwise as
  low as the provider permits without scraping abuse,
- monitor interval: 10-30 seconds,
- watched candidate entry check: same as monitor interval,
- watch expiry: 30 minutes unless attention persists.

### 14.3 Order Serialization

All order submission goes through one execution gateway.

Rules:

- one symbol can have only one order-state transition at a time,
- entries are blocked while any exit/scale/add is pending for that symbol,
- broker order IDs are persisted before the next loop,
- broker truth wins during reconciliation.

### 14.4 Decision Records

Every candidate and position action must write JSONL.

Minimum fields:

```json
{
  "symbol": "DSY",
  "timestamp": "...",
  "source": "finviz",
  "source_timestamp": "...",
  "scanner_age_seconds": 900,
  "quote_age_seconds": 2,
  "attention_score": 91,
  "attention_drivers": ["top_gainer", "theme_chinese_no_news", "hod_reclaim"],
  "data_confidence": 0.7,
  "hard_blocks": [],
  "soft_warnings": ["chinese_adr", "no_news"],
  "state": "active",
  "state_evidence": ["higher_lows=2", "near_hod=true", "spread=0.8%"],
  "mode": "watch_for_first_pullback",
  "entry_setup": null,
  "entry": {
    "price": null,
    "stop": null,
    "risk_per_share": null,
    "shares": null,
    "risk_amount": null
  },
  "exit": {
    "reason": null,
    "pnl": null,
    "pnl_r": null,
    "remaining_shares": null
  },
  "decision": "watch",
  "reason": "no defined-risk pullback yet"
}
```

Logs must make it obvious whether a symbol was skipped mechanically, watched,
entered, scaled, exited, or missed.

---

## 15. Operational Reliability

### 15.1 Scanner Failure

If primary scanner fails:

1. log scanner failure,
2. try fallback scanner,
3. if fallback fails, do not invent candidates,
4. keep monitoring open positions,
5. write `scanner_unavailable` decision record.

yfinance enrichment failure is not scanner failure. Log it, reduce confidence,
and continue without that field.

### 15.2 Data Staleness

| Data | Fresh threshold | Stale behavior |
|---|---:|---|
| scanner | source-specific | can create watch candidate; log scanner age |
| quote | <=5s full, <=15s stale | >15s blocks entry |
| quote in open position | <=15s normal | >60s triggers emergency/unprotected rules |
| 1-min bars | <=5m for entries | stale bars cannot create entry by themselves |
| 5-min bars | <=10m for context | stale bars reduce confidence |

Stale data during an open position means monitor at emergency cadence (default
10s). If risk cannot be measured, flatten when quote/broker state allows.

### 15.3 Broker / API Failure

Before entry:

- no broker response means no entry.

While in position:

- retry failed calls up to 3 times with backoff,
- do not assume flat,
- broker unreachable >120s marks affected positions `UNPROTECTED`,
- poll every 10s until restored,
- broker reachable but protection placement fails: retry once, then flatten if
  risk is uncontrolled.

### 15.4 Restart Recovery

On startup:

1. load local state,
2. fetch broker positions,
3. fetch open orders,
4. broker position truth wins,
5. verify protection for every broker position,
6. re-protect or flatten unprotected positions,
7. cancel stale orders with no matching position.

Required reconciliation cases:

| Broker state | Local state | Action |
|---|---|---|
| position exists | none | insert local OPEN, place protection; if fails mark UNPROTECTED |
| position exists, qty matches | OPEN/RUNNER | verify stop/OCO exists; replace if missing |
| broker qty < local qty | OPEN/RUNNER | assume scale-out/fill happened; update local qty; re-protect |
| broker qty > local qty | OPEN/RUNNER | update local qty; re-protect; log warning |
| no broker position | local open/pending | broker truth wins; close local stale record |
| pending orders, no position | any | cancel stale orders |
| irreconcilable mismatch | any | mark ERROR, alert/log critical, attempt flatten |

---

## 16. Rebuild Architecture

Keep modules boring and small. Do not build a framework.

Recommended v1 module map:

```text
src/
  app.py                # main loop, clock/session helpers
  models.py             # Candidate, AttentionScore, MoveState, PositionState, EntrySignal, ExitDecision
  config.py             # load/validate config
  scanner.py            # Finviz/free scanners + paid adapter interface + attention ranking
  data.py               # Alpaca/yfinance enrichment, quote/bar freshness, feed selection
  hard_filters.py       # catastrophic checks only
  move_classifier.py    # early/active/extended/backside/halt-risk
  entries.py            # all supported entry setup detectors
  risk.py               # starter/add sizing and account/symbol risk limits
  execution.py          # broker orders, OCO/stop placement, re-protection
  exits.py              # emergency-first exit decisions
  reconciliation.py     # startup/broker/local state reconciliation
  persistence.py        # position state, pending orders, JSONL logs, daily stats

tests/
  test_attention.py
  test_hard_filters.py
  test_move_classifier.py
  test_entries.py
  test_risk.py
  test_exits.py
  test_state_machine.py
  test_reconciliation.py
  test_pipeline.py
```

Do not split scanners, entries, risk, or execution into directory trees until
paper logs prove the files are too large.

---

## 17. Configuration Shape

Keep config small. Defaults are paper-mode starting points.

```yaml
mode: paper

scanner:
  max_candidates: 30
  top_attention_to_process: 10
  scan_interval_seconds: 30
  watch_expiry_minutes: 30
  # Observation range only. Never discard solely because price is outside it.
  # Outside-range candidates are still ranked/logged and treated as soft warnings.
  focus_price_min: 1.0
  focus_price_max: 50.0

data:
  quote_feed: iex
  bar_feed: iex
  max_quote_age_seconds: 15
  fresh_quote_seconds: 5

attention:
  price_attention_weight: 40
  volume_attention_weight: 35
  hod_acceleration_weight: 25
  theme_bonus: 10
  former_runner_bonus: 5
  repeated_scanner_seen_bonus: 5

tradeability:
  max_spread_pct_normal: 1.0
  max_spread_pct_caution: 3.0
  max_spread_pct_hard: 5.0
  min_dollar_volume_5m: 100000
  estimated_slippage: 0.01

risk:
  starter_risk_pct: 0.0025
  max_trade_risk_pct: 0.01
  max_symbol_loss_pct: 0.01
  max_daily_loss_pct: 0.03
  max_positions: 3
  max_open_risk_pct: 0.03
  max_positions_per_theme: 1
  min_seconds_between_entries: 30

execution:
  paper_only: true
  allow_live: false
  use_limit_entries: true
  emergency_market_exit: true
  entry_order_timeout_seconds: 60
  exit_order_timeout_seconds: 30
  monitor_interval_seconds: 10
  no_entry_before: "09:35"
  no_new_entries_after: "15:30"
  flatten_time: "15:55"

logging:
  decision_log: data/decisions.jsonl
  trade_log: data/trades.jsonl
```

Do not expose every tiny threshold before paper logs. Constants can remain in
code with names and tests.

---

## 18. Implementation Plan

### Phase 0 — Freeze Legacy (Pre-Purge)

Goal:

- stop treating legacy code/docs as source of truth,
- keep legacy code only as temporary reference (during rebuild phases only),
- legacy code may remain in `src/` during rebuild for reference,
- but final bot acceptance requires removal of all legacy source files or
  relocation to a non-importable archive directory entirely outside `src/`,
- do not implement runtime changes until this spec is accepted.

### Phase 1 — Models, Config, Logs

Build:

- Candidate,
- AttentionScore,
- HardFilterResult,
- MoveState,
- EntrySignal,
- PositionState,
- PendingOrder,
- AccountRiskState,
- ExitDecision,
- DecisionRecord.

Acceptance:

- pure unit tests,
- no broker calls,
- JSONL records are queryable.

**Implementation notes (2026-06-11):**  Created `src/models/schemas.py` (broker-agnostic Pydantic v2 models: Candidate, AttentionScore, HardFilterResult, MoveState/PositionState enums, EntrySignal with risk validators, PositionStateModel, PendingOrder, AccountRiskState, ExitDecision, DecisionRecord with JSONL serialisation).  Added `Phase1Settings` to `config/settings.py` and `phase1:` defaults in `config/default_config.yaml`.  Built `src/journal/decision_logger.py` (JSONL write/read/filter).  Updated dependencies for yfinance import compatibility (websockets>=15.0, protobuf>=5.29).  Tests: `test_phase1_schemas.py` (model validation, risk matching tolerance, frozen immutability), `test_phase1_logger.py` (roundtrip, filtering, queryability).  431 tests passed.  No legacy code or main.py touched.

### Phase 2 — Scanner, Enrichment, Attention Report

Build:

- free scanner adapter,
- enrichment with quote/bar age,
- attention scoring,
- watchlist and attention report.

No entries yet.

Acceptance:

- top gainers ranked,
- scanner age and quote age logged,
- old hard rejects appear only as soft annotations,
- DSY-like stock is watched, not deleted.

**Implementation notes (2026-06-11):**  Created `src/scanner/scanner.py` (Finviz → Candidate adapter wrapping existing `scrape_finviz_gainers()`, manual-watchlist fallback); `src/scanner/confidence.py` (data-confidence calculator per §4.4 — scanner staleness, bar staleness, missing metadata penalties, 0.3 floor with critical data); `src/scanner/attention.py` (three-factor attention scoring per §6.1-6.2 with weight redistribution when factors unavailable, theme detection per §6.3 via country/sector/industry clustering, FormerRunnerStore stub, float-rotation calculation, `map_soft_warnings()` per §8 mapping 30+ soft annotations, `soft_warning_multiplier()` floored at 0.25x, batch `score_candidates()`).  No hard filters — scanner returns every symbol.  Tests: 145 tests across 3 files with DSY regression verifying attention is high, soft warnings only, no hard blocks.  576 total.

### Phase 3 — Hard Filters

Build catastrophic checks only.

Acceptance:

- Chinese/no-news/parabolic are not hard blocks,
- halted/no quote/wide spread/no stop are hard blocks,
- every skip has a mechanical reason.

**Implementation notes (2026-06-11):**  Created `src/hard_filters.py` (pure-function catastrophic checks only per §7).  Five check categories: `check_market_structure()` (tradable, halted, OTC, market closed, cutoff, watch-only window), `check_execution_data()` (price, bid/ask, crossed market, quote staleness), `check_liquidity_spread()` (spread tier, zero volume, dollar volume, scanner-only bypass), `check_risk_definition()` (logical stop, risk-per-share, risk-amount floor, stop-too-tight-for-spread, max stop width), `check_account_risk()` (kill switch, daily loss, max positions, symbol lock, theme concentration).  Utility classifiers: `quote_age_tier()` / `spread_tier()` returning "normal"/"stale_warning"/"hard_reject".  Time-gate helpers (`is_watch_only_window`, `is_lunch_window`, `is_past_entry_cutoff`, `is_flatten_time`).  Orchestrator: `run_hard_filters()` aggregates all checks.  9 negative tests verify Chinese/no-news/parabolic/low-float/biotech/below-VWAP/lunch/speculative are never in hard blocks.  DSY passes with valid data.  81 tests.  657 total.

### Phase 4 — Move Classifier

Build early/active/extended/backside/halt-risk classifier.

Acceptance:

- classifier returns state, mode, and evidence,
- no entry logic yet,
- logs are understandable.

**Implementation notes (2026-06-11):**  Created `src/move_classifier.py` (five-state detector per §9).  Priority-ordered evaluation: halt-risk → backside → extended → active → early (first match wins).  Each state detector returns (detected, evidence_list).  Halt-risk: halt count, spread+vertical, >10% in 5m, quote instability, vertical no pullback.  Backside: lower highs + failed HOD, 5+ bars below VWAP + failed reclaim, volume fading + bounces failing, spread widening no reclaim.  Extended: stop distance > max, >15% above pullback low, candle range >2x avg, vertical no pullback.  Active: ≥3 of 5 core signals (HOD repeated, higher lows, pullbacks bought, strong volume, manageable spread, stop in range).  Returns (MoveState, ModeType, evidence).  Entry-permission matrix per §9.4 via `setup_allowed()`.  Mode mapping: early→WATCH, active→STARTER_ENTRY, extended→SCALP_ONLY, backside→AVOID_NEW_LONGS, halt→AVOID_NEW_LONGS.  67 tests covering all states, priority order, mode mapping, evidence, and permission matrix.  724 total.

### Phase 5 — Entry Engine

Build supported setups:

- first pullback,
- micro pullback,
- HOD reclaim,
- consolidation breakout,
- VWAP reclaim/bounce,
- scalp reclaim.

Acceptance:

- each signal has entry, stop, risk/share, invalidation,
- no signal if risk cannot be defined,
- permission matrix enforced.

**Implementation notes (2026-06-11):**  Created `src/entries.py` (six setup detectors + orchestrator per §10).  `Bar` dataclass (OHLCV with is_green/is_red/range properties).  Shared helpers: `avg_bar_range()`, `_is_strong_move()` (3·avg_range or 5% with volume confirmation), `_is_controlled_selling()` (≤70% surge volume).  Six detectors: `detect_first_pullback()` (surge→pullback 2-8 bars→controlled selling→green reclaim above prior high, vol >1.2x pb avg, stop at pullback low), `detect_micro_pullback()` (active state, 1.5·avg_range advance, 1-3 red dips with lower vol, green reclaim above surge peak, vol ≥1.5x dip avg), `detect_hod_reclaim()` (prior HOD→pullback→close above HOD with vol check), `detect_consolidation_breakout()` (5-20 bar range ≤2%, near HOD, vol alive, breakout candle above range high), `detect_vwap_reclaim()` (close above VWAP after being below/near, vol not dead, spread ≤5%), `detect_scalp_reclaim()` (extended/halt-risk only, spread ≤3%, quote ≤5s, 1-2 red dips, stop ≤1% price or 1·avg_range, 0.5R target).  `_build_signal()` enforces risk definability and setup-specific max stop widths.  `find_entry()` evaluates in priority order (first_pullback→hod_reclaim→consolidation→micro_pullback→vwap→scalp), respects allowed-setups gating.  Sizing uses placeholders (1 share) replaced by Phase 6.  48 tests.  772 total.

### Phase 6 — Risk, Sizing, Adds

Build:

- starter sizing,
- attention/soft/confidence multipliers,
- add sizing,
- account/symbol/theme limits,
- no-averaging-down enforcement.

Acceptance:

- no order can exceed risk,
- adds only happen after confirmation,
- losing positions cannot be added to.

**Implementation notes (2026-06-11):**  Created `src/sizing.py` (pure-math risk module per §11).  Note: placed in `src/sizing.py` instead of `src/risk.py` because legacy `src/risk/` directory already exists.  Sizing: `max_trade_risk()`, `starter_risk_amount()`, `attention_multiplier()` (4 tiers: 85+→1.0x, 70-84→0.75x, 50-69→0.50x, <50→0.25x), `adjusted_starter_risk()` (starter × attention × soft × confidence, soft floored at 0.25), `calculate_shares()` (floor division, returns 0 if <1), `entry_sizing()` (end-to-end).  Add logic: `can_add()` (9 conditions per §11.4 — 0.5R above entry, price confirmation, stop raisable, spread/quote/halt/timing checks), `never_add()` (10 blocking conditions per §11.5 — below avg entry, below original entry, spread widened >50%, stop unraisable, unprotected, after scale-out), `add_risk_amount()` (capped at remaining budget).  Risk calculations: `open_risk_per_position()`, `total_open_risk()`, `per_symbol_daily_loss()` (realized + min(0, unrealized)), `daily_pnl()`.  Limits: `check_risk_limits()` (kill switch, max positions, open risk, daily loss, per-symbol loss, theme concentration, entry staggering), `can_open_new_position()` (high-level gate).  No-averaging-down explicitly enforced via `never_add()`.  77 tests.  849 total.

### Phase 7 — Paper Execution And State Machine

Build:

- execution gateway,
- pending order tracking,
- stop/OCO placement,
- position persistence,
- re-protection,
- restart reconciliation.

Paper only.

Acceptance:

- tiny starter can submit in paper,
- stop exists immediately,
- duplicate entries are impossible,
- reconciliation detects unprotected positions.

**Implementation notes (2026-06-11):**  Created two modules.  `src/state_machine.py`: `_VALID_TRANSITIONS` dict enforcing legal state changes (10 states, ~30 valid edges), `transition_position()` with optional force flag, `is_symbol_locked()` / `is_symbol_locked_for_entries()` per §13.3 (locked when state not NONE/CLOSED, pending buy exists, exit in progress, or daily loss capped), candidate lifecycle helper (`candidate_has_reached()`).  `PositionStore`: in-memory dict with JSON persistence, methods for upsert/get/remove/all_open/locked_symbols/open_position_count/save_to_disk/load_from_disk.  `PendingOrderStore`: tracks unresolved orders by symbol, prevents duplicate entries and double-sells, methods for add/get/resolve/has_pending_buy.  `src/paper_execution.py`: `PaperExecutionGateway` — mockable paper-trading interface with `submit_entry()` (creates PENDING_ENTRY + PendingOrder, blocks duplicates), `confirm_fill()` (advances to OPEN), `place_stop()` / `protect_position()` (idempotent stop placement), `cancel_order()` / `cancel_stale_orders()`, `submit_exit()` / `confirm_exit_fill()` (closes position), `get_unprotected_positions()` (detects missing stops).  `reconcile_positions()` handles all 8 SPEC §15.4 restart cases (insert+protect, verify stop, partial fill update, missed fill update, close stale local, cancel stale orders, irreconcilable→ERROR).  71 tests.  920 total.

### Phase 8 — Exit Engine

Build:

- emergency-first exits,
- hard stop/invalidation,
- scale-out,
- failed reclaim/pullback exits,
- spread/liquidity exits,
- time exit,
- runner trail.

Acceptance:

- every open position is monitored,
- emergency exits run first,
- no unprotected position survives silently.

**Implementation notes (2026-06-11):**  Created `src/exits.py` (11-priority exit engine per §12).  Every position is evaluated every monitor cycle.  Priority: P1 emergency (spread explosion >5% or >3x entry, quote unreliable >60s + losing, unprotected + losing, 3+ halts, broker unreachable) → P2 loss caps (daily, per-symbol) → P3 hard stop (price ≤ stop) → P3b setup invalidation (HOD reclaim fails, VWAP reclaim fails, consolidation breakout fails, scalp first red candle) → P4 missing protection → P5 scale-out (1R normal sells 33%, extension bar >1.5x avg range sells 25%, spread caution sells 25%, extended state sells 50% at 0.5R, scalp sells 100% at 0.5R or first red) → P6 failed reclaim (2 consecutive red bars after >0.5R profit) → P7 VWAP loss (below VWAP while losing) → P8 spread expansion (>2x entry + >2%) → P9 volume disappearance → P10 time exit (flatten at 15:55 ET) → P11 runner trail (trail hit or 2 red bars).  Helpers: `calculate_pnl()`, `calculate_pnl_r()`.  Orchestrator: `check_exits()` evaluates all 11 checks in order, returns first triggered `ExitDecision`.  45 tests including priority verification (emergency before hard stop).  965 total.

### Phase 9 — Paper Trading Trial

Run multiple sessions with tiny size.

Track:

- top attention names,
- watched names,
- mechanical skips,
- entries,
- exits,
- missed entries,
- DSY-like candidates,
- false positives,
- slippage/spread/data-age issues.

Do not optimize until logs exist.

**Implementation notes (2026-06-11):**  Created `src/decision_pipeline.py` (integration layer wiring all Phase 1-8 modules).  Note: placed in `src/decision_pipeline.py` instead of `src/pipeline.py` because legacy `src/pipeline/` directory already exists.  `PipelineResult` mutable accumulator class (Context7-recommended pattern) holds per-candidate step outputs.  `run_pipeline()` executes the full chain for one candidate: data confidence → attention scoring → soft warnings → hard filters (with paper-mode bid/ask estimation) → move classification → entry detection → sizing → paper order submission → exit check → DecisionRecord → JSONL log.  `run_pipeline_batch()` processes multiple candidates sorted by attention.  Also improved `DecisionRecord.to_json_line()` per Context7 recommendation — now uses `model_dump_json()` (Pydantic v2 native) instead of `json.dumps(model_dump(mode="json"))`.  Integration tests verify: pipeline runs without crashes, high-attention candidates may enter, low-attention → watch, hard-filtered → skip, DSY survives pipeline (no qualitative hard blocks), duplicate entries blocked, exit check triggers on open positions, JSONL logging works end-to-end.  15 tests.  980 total.

### Phase 10 — Data Upgrade Decision

Before live trading, decide and verify the data path:

- Alpaca SIP,
- Polygon/Massive,
- Trade Ideas/Benzinga/Finviz Elite,
- or another real-time scanner.

Acceptance:

- real-time top-gainer discovery verified,
- quote/bar feed semantics known,
- live trading remains disabled until explicitly approved.

**Implementation notes (2026-06-11):**  Created `src/app.py` — main application loop per SPEC §14.1.  `TradingApp` class orchestrates the full trading cycle: scanner callback → enrichment callback → pipeline → execution → position monitoring.  Uses `signal.SIGINT`/`SIGTERM` flag-based graceful shutdown (Context7-confirmed pattern — no `KeyboardInterrupt` corruption risk).  `time.monotonic()` for cadence tracking with separate monitor (10 s) and scan (30 s) intervals.  All external data sources (scanner, enrichment) are injectable callbacks — fully testable without network.  `_monitor_positions()` checks exits for every open position each cycle.  `_scan_and_process()` skips locked symbols, runs pipeline for each candidate.  Phase 10 is the data-upgrade decision — per `docs/FINDINGS.md` §9, the recommended path is Alpaca Basic free tier (`feed=sip` bars + `feed=iex` quotes) for paper testing, Tradier Pro ($10/mo) for realistic simulation, Alpaca Algo Trader Plus ($99/mo) for production.  9 tests.  989 total.

---

## 19. Tests Required

### 19.1 Unit Tests

- attention ranks obvious top gainers above quiet names,
- premarket gap contributes to attention,
- missing optional data reduces confidence but does not hard-reject,
- Chinese/no-news/parabolic/lunch/low-float are soft annotations only,
- halted/no quote/wide spread/no stop hard-reject,
- spread and quote-age tiers behave mechanically,
- move classifier separates early/active/extended/backside/halt-risk,
- each entry setup emits entry, stop, risk/share, invalidation,
- entry permission matrix is enforced,
- sizing respects starter, soft multipliers, attention multipliers, and caps,
- add logic cannot average down,
- exits run emergency-first,
- position states prevent duplicate entries and double exits,
- restart reconciliation handles all table cases.

### 19.2 DSY Regression Test

Create a fake DSY-like candidate:

```text
Chinese
no news
top gainer
theme active
early light volume then squeeze
spread acceptable
volume acceptable
first pullback risk definable
```

Expected:

```text
attention high
hard_blocks = []
soft_warnings include chinese_adr/no_news/speculative
state = early or active depending on bars
mode = watch or starter_ready
entry allowed if quote/spread/volume/stop/account risk pass
not rejected because of Chinese/no-news/no-catalyst
```

This test stays forever.

### 19.3 Mock Market Data

Mock data must simulate:

- early mover,
- active squeeze,
- parabolic extension,
- backside fade,
- halt-risk spread expansion,
- stale quote,
- delayed scanner with fresh quote,
- scanner failure,
- yfinance enrichment failure,
- broker disconnect,
- restart with unprotected position,
- scale-out and runner state.

No network calls in unit tests.

### 19.4 Paper Validation

Paper logs must answer:

- Did the scanner see the runners?
- Were old hard rejects removed?
- Did entries have definable risk?
- Were partials fast enough?
- Did emergency exits run first?
- Did stale data cause missed or bad decisions?
- Would DSY-like names survive to watch/entry?

---

## 20. What Not To Build

Do not build:

- new Pillars,
- new quality tiers,
- AI approval gates,
- complex indicator stack,
- market-regime veto system,
- multi-agent debate,
- fancy dashboards,
- ML scoring,
- strategy factory,
- plugin framework,
- abstract pipeline framework,
- 20-factor attention model,
- automatic live trading.

Do not port these old concepts as hard filters:

- Chinese ADR reject,
- no-news reject,
- no-catalyst reject,
- parabolic reject,
- lunch blackout,
- below-VWAP reject,
- low-float reject,
- speculative-theme reject,
- biotech reject,
- insider-ownership reject.

If a component does not help find, enter, size, manage, or exit top gainers, do
not build it.

---

## 21. Acceptance Criteria

The rebuild is acceptable only when:

1. The bot ranks top gainers by attention before soft warnings are considered.
2. Chinese/no-news/parabolic/low-float names are not automatic rejects.
3. Every hard skip is mechanical and logged clearly.
4. Every soft warning changes size, mode, entry requirement, or exit speed.
5. Every entry has entry, stop, risk/share, size, and invalidation.
6. Starter size is the default for every new symbol.
7. Adds only happen after confirmation and never average down.
8. Fast partials and emergency-first exits are implemented.
9. Multiple symbols can be managed without duplicate entries or double exits.
10. Broker/API/data failures cannot silently leave positions unprotected.
11. Restart recovery reconciles broker truth and local state.
12. Decision logs explain watch, enter, skip, add, scale, exit, and missed setups.
13. Paper mode works before any live mode exists.
14. The DSY regression test passes.
15. The implementation is smaller and easier to reason about than the legacy bot.

---

## 22. Audit Findings And Required Remediation

### 22.1 Audit Verdict — 2026-06-11

An implementation audit was performed after Phase 10.

Test status at audit time:

```text
python3 -m pytest tests/ -q
989 passed, 2 warnings
```

Passing tests are not sufficient for acceptance. The audit found that the
isolated rebuild modules mostly preserve the attention-first philosophy, but the
runtime wiring is not yet acceptable for paper trading.

Current verdict:

```text
Philosophy in rebuild modules: mostly pass.
Runtime/paper readiness: fail.
No live or realistic paper trial until this section is fixed.
```

The rebuild remains governed by the same core thesis:

```text
Top gainer + attention + definable risk = potential trade.
```

The remediation work must not reintroduce old filter-heavy behavior. Fix the
mechanics and runtime wiring. Do not add pillars, AI approval gates, quality
scores, market-regime vetoes, or new rejection frameworks.

### 22.2 Critical Finding — CLI Still Runs The Old Funnel

At audit time, `main.py` did not run the rebuild path. It still ran
`src.pipeline.v3_pipeline.RossCameronPipeline`, whose console banner and flow
referenced anti-patterns, regime, pillars, deep analysis, and quality score.

Observed evidence:

- `main.py` printed a flow containing `Anti-Patterns`, `Regime`, `5 Pillars`,
  `Deep Analysis`, and `Quality Score`.
- `_run_v3()` imported and instantiated `RossCameronPipeline` for paper/mock
  operation.
- `src/app.py` and `src/decision_pipeline.py` were not wired into the executable
  CLI path.

Required remediation:

1. The default non-legacy entrypoint must run the rebuild modules:
   - `src/app.py`,
   - `src/decision_pipeline.py`,
   - `src/scanner/scanner.py`,
   - `src/scanner/attention.py`,
   - `src/hard_filters.py`,
   - `src/move_classifier.py`,
   - `src/entries.py`,
   - `src/sizing.py`,
   - `src/paper_execution.py`,
   - `src/exits.py`.
2. There must be no default legacy path. The CLI must not offer a `--legacy`
   flag in its final form. The only way to run old code is by explicitly
   invoking files outside `src/` after they have been moved to a non-importable
   archive directory.
3. The default CLI (no mode flag, or any valid mode) must not run the old
   anti-pattern/regime/pillar/quality-score path.
4. Legacy code may remain during rebuild as archived reference only. It must
   be unreachable from any normal rebuild command. The final bot must have no
   importable legacy modules in the default Python path or runtime.
5. The startup banner/log summary must describe the rebuild pipeline:

   ```text
   Scan → Attention → Confidence → Soft Warnings → Mechanical Hard Filters
   → Move State → Entry Setup → Sizing → Paper Execution → Exits → DecisionRecord
   ```

6. `python main.py --mode mock --once` must exercise the rebuild mock path or
   the full rebuild pipeline. It must not exercise pillars, anti-patterns,
   regime veto, or quality scoring.
7. `python main.py --mode paper --once` must either:
   - run the rebuild pipeline (the final acceptance target), OR
   - print a clear temporary not-ready error with a reference to this audit
     section and exit.
   It must never fall back to legacy v3/Ross/pillar code.
8. Add tests proving the executable entrypoint imports/calls the rebuild app or
   rebuild pipeline, not `RossCameronPipeline`. Tests must cover both
   `--mode mock --once` and `--mode paper --once` paths.

Acceptance for this item:

```text
python main.py --mode mock --once
```

exercises the rebuild path (not pillars, anti-patterns, regime veto, or
quality scoring). No `--legacy` flag exists in the final CLI.
The bot is not considered finalized until `python main.py --mode paper --once`
also runs the rebuild path (not the temporary not-ready error).

### 22.3 Critical Finding — Entry Orchestrator Silently Skips Detectors

At audit time, `find_entry()` passed a superset of keyword arguments to every
setup detector and swallowed all exceptions. Several detectors did not accept
the extra keyword arguments. Because the orchestrator caught broad exceptions and
continued, valid setup detectors could silently fail.

Required remediation:

1. Remove blanket exception swallowing from `find_entry()`.
2. Dispatch to each setup detector with exactly the keyword arguments it accepts.
3. If a detector raises unexpectedly, log or surface the error in a test-visible
   way. Do not silently turn code errors into `no setup`.
4. Keep the priority order exactly as §9.4 specifies:
   1. first pullback,
   2. HOD reclaim,
   3. consolidation breakout,
   4. micro pullback,
   5. VWAP reclaim/bounce,
   6. scalp reclaim.
5. Add tests proving each of the six detectors can fire through `find_entry()`
   itself, not only through direct detector calls.
6. Add a regression test that intentionally creates a valid non-first-pullback
   setup and verifies `find_entry()` returns it.
7. Add a regression test that would fail if detector `TypeError`s are swallowed.

Acceptance for this item:

```text
find_entry(valid_hod_reclaim_bars) -> EntrySignal(entry_setup="hod_reclaim")
find_entry(valid_consolidation_bars) -> EntrySignal(entry_setup="consolidation_breakout")
find_entry(valid_micro_pullback_bars) -> EntrySignal(entry_setup="micro_pullback")
find_entry(valid_vwap_reclaim_bars) -> EntrySignal(entry_setup="vwap_reclaim")
find_entry(valid_scalp_reclaim_bars) -> EntrySignal(entry_setup="scalp_reclaim")
```

### 22.4 High Finding — Permission Matrix Not Enforced By Pipeline

At audit time, the pipeline checked only whether `first_pullback` was allowed in
the current state and then called `find_entry()` without passing the actual set
of allowed setups.

Required remediation:

1. The move-state permission matrix must be the single source of truth for which
   setup types are eligible.
2. The pipeline must build the allowed setup set for the current state and pass
   it to `find_entry()`.
3. The pipeline must not use a representative setup check.
4. `find_entry()` must never evaluate a setup outside the allowed set.
5. Add tests for every state:

   | State | Must allow | Must block |
   |---|---|---|
   | early | first pullback, clean VWAP reclaim | micro pullback, HOD reclaim, consolidation, scalp |
   | active | first pullback, micro pullback, HOD reclaim, consolidation, VWAP reclaim | scalp |
   | extended | first pullback, HOD reclaim, VWAP reclaim, scalp only/tiny | micro pullback, consolidation |
   | backside | VWAP reclaim only | all other setups |
   | halt-risk | scalp reclaim only | all normal setups |

Acceptance for this item:

```text
A valid setup outside the state permission matrix must return no entry.
A valid setup inside the state permission matrix must be considered.
```

### 22.5 High Finding — Sizing Not Applied To Submitted Orders

At audit time, `_build_signal()` used placeholder sizing of one share. The
pipeline calculated real size, but did not mutate or replace the signal before
submitting it to the paper execution gateway. The gateway used
`signal.proposed_shares`, so attempted paper entries submitted one share.

Required remediation:

1. Keep entry detection and sizing as separate concepts:
   - entry detection defines entry, stop, risk/share, target, invalidation,
   - sizing defines proposed shares and risk amount.
2. Before any order submission, create a sized signal or order request whose:
   - `proposed_shares` equals the sizing result,
   - `risk_amount` equals `shares * risk_per_share` or the intended capped risk,
   - zero shares means no order.
3. Do not submit any order using the placeholder one-share signal.
4. Add tests proving:
   - high-attention valid setup submits the calculated share count,
   - soft warnings reduce share count but do not force zero unless risk/account
     math requires it,
   - data confidence reduces share count,
   - zero-share sizing blocks entry with a clear reason.

Acceptance for this item:

```text
EntrySignal placeholder size is never used for order submission.
Paper order quantity equals calculated sizing result.
DecisionRecord entry_shares equals submitted order quantity.
```

### 22.6 High Finding — Protection Flow Creates Pending/Unprotected Zombie Positions

At audit time, the pipeline called `submit_entry()`, which created a
`PENDING_ENTRY` position, then immediately called `place_stop()`, which required
the position to be `OPEN`. The resulting `ValueError` was caught as a generic
symbol-lock/watch outcome. This could leave a pending entry with no protection
and a misleading decision record.

Required remediation:

1. Paper execution must model the actual lifecycle:

   ```text
   submit entry -> PENDING_ENTRY + pending entry order
   entry fill confirmed -> OPEN
   protection placed immediately after OPEN
   if protection fails -> UNPROTECTED or EXITING according to emergency rules
   ```

2. Do not place stop protection on a position that is still `PENDING_ENTRY`
   unless the execution model explicitly supports bracket/OCO submission at
   entry time.
3. If bracket/OCO-at-entry is implemented, model it explicitly as one atomic
   operation and test it explicitly.
4. Do not catch protection-placement failures and relabel them as ordinary
   `watch` decisions.
5. Add tests proving:
   - entry submission creates `PENDING_ENTRY`,
   - fill confirmation transitions to `OPEN`,
   - protection is placed after `OPEN`,
   - failure to protect creates `UNPROTECTED` or emergency flatten intent,
   - no pending entry is logged as a harmless watch.

Acceptance for this item:

```text
No active or pending position may exist silently without a protection state.
Decision logs must distinguish pending entry, entered/open, protected, unprotected, and failed protection.
```

### 22.7 High Finding — Runtime App Does Not Pass Live Market Data

At audit time, `src/app.py` did not forward bars, VWAP, EMA, HOD, quote age,
spread, RVOL, or dollar volume into the pipeline. It hardcoded
`bars_available=False`.

Required remediation:

1. Define a simple enrichment return shape for the rebuild app. It must include,
   when available:
   - enriched `Candidate`,
   - recent 1-minute bars,
   - optional 5-minute bars,
   - current bid/ask/price or enough data to compute spread,
   - quote timestamp / quote age,
   - VWAP,
   - EMA9,
   - day high / prior HOD,
   - RVOL,
   - trailing 5-minute dollar volume,
   - halt count if known.
2. `TradingApp._scan_and_process()` must pass this data into `run_pipeline()`.
3. The app must not fabricate fresh quotes. Missing critical quote data should
   flow into hard filters as mechanical blocks.
4. If a data provider cannot supply bars, the decision must be `watch` with a
   clear `no_bars_for_entry` or data-confidence reason, not a silent no-op.
5. Add tests using injectable enrichment callbacks that provide:
   - valid bars/quote -> entry path can proceed,
   - missing quote -> hard block,
   - stale quote -> hard block or soft warning depending age,
   - missing bars -> watch only.

Acceptance for this item:

```text
The app can produce a valid entry decision from injected scanner/enrichment data without network calls.
```

### 22.8 High Finding — Position Monitoring Uses Entry Price As Current Price

At audit time, `_monitor_positions()` created a candidate whose price was
`pos.average_entry`, not the current market price. Hard stops, scale-outs,
VWAP-loss exits, spread exits, and runner exits therefore could not be trusted.

Required remediation:

1. Position monitoring must fetch or receive a fresh quote/snapshot for each open
   or locked symbol.
2. Exit checks must receive the actual current price, quote age, spread, bars,
   and relevant setup context.
3. If quote fetching fails:
   - before entry: no entry,
   - while in position: apply §15.3 and §12.5 broker/data failure behavior.
4. Add tests proving:
   - price below stop triggers hard-stop exit in the app monitor path,
   - spread explosion triggers emergency before hard stop,
   - stale quote over 60s triggers emergency/unprotected handling,
   - profitable stale quote does not pretend the position is safe,
   - no exit is calculated from `average_entry` as a proxy.

Acceptance for this item:

```text
Monitor path hard stop test must fail if current_price is replaced with average_entry.
```

### 22.9 Medium Finding — Account Risk State Not Wired

At audit time, risk-limit helpers existed but the app and hard-filter path did
not maintain a full `AccountRiskState` or enforce max total open risk inside the
entry gate.

Required remediation:

1. Runtime must maintain or inject `AccountRiskState` with:
   - daily realized P&L,
   - daily unrealized P&L,
   - total open risk,
   - open position count,
   - per-symbol daily loss,
   - theme exposure,
   - kill switch state.
2. `max_open_risk_pct`, daily loss, per-symbol loss, max positions, and theme
   concentration must be enforced before order submission.
3. The hard-filter result or decision reason must log mechanical account-risk
   blocks clearly.
4. Add integration tests showing:
   - max open risk blocks a new entry,
   - daily loss cap blocks and turns on kill switch behavior,
   - per-symbol cap blocks re-entry,
   - theme concentration watches but does not delete the candidate.

Acceptance for this item:

```text
Account risk blocks are mechanical hard blocks, not qualitative stock judgments.
```

### 22.10 Medium Finding — Restart Reconciliation Not Wired Into App Startup

At audit time, `reconcile_positions()` existed and had tests, but the app did
not call it on startup.

Required remediation:

1. On app startup, load local state, fetch broker/paper positions and pending
   orders, and call reconciliation before scanning.
2. In pure paper/mock mode, provide injectable broker-position and pending-order
   snapshots for tests.
3. Reconciliation actions must be logged to DecisionRecord or a state log.
4. Unprotected broker positions after restart must be re-protected or flattened
   according to §15.4 and §12.5.
5. Add app-level tests proving the startup path handles all §15.4 cases, not
   only direct function tests.

Acceptance for this item:

```text
The app must not scan for new entries before restart reconciliation completes.
```

### 22.11 Low Finding — Decision Logs Omit Quote Age

At audit time, `DecisionRecord` had `quote_age_seconds`, but
`PipelineResult.to_decision_record()` did not populate it.

Required remediation:

1. Store quote age in `PipelineResult`.
2. Populate `DecisionRecord.quote_age_seconds` for watch, skip, entry, and exit
   decisions.
3. Add tests proving quote age appears in JSONL output.

Acceptance for this item:

```text
Every decision involving quote-dependent logic logs quote_age_seconds when known.
```

### 22.12 Low Finding — `no_news` / `no_catalyst` Soft Warning Missing

At audit time, `map_soft_warnings()` did not emit `no_news` or `no_catalyst`,
and tests explicitly noted this as stubbed.

Required remediation:

1. If news/catalyst availability is known, missing news/catalyst must become a
   soft warning only.
2. Do not make no-news/no-catalyst a hard block.
3. Default treatment must follow §8:
   - no penalty when attention >= 70,
   - `0.75x` size when attention < 70,
   - never delete the candidate.
4. If news/catalyst data is unknown rather than known-missing, log
   `news_unknown` or lower data confidence rather than pretending `no_news` is
   known.
5. Add tests proving:
   - no-news high-attention candidate has no size penalty,
   - no-news lower-attention candidate gets 0.75x soft treatment,
   - no-news never appears in hard blocks,
   - DSY-like no-news candidate survives.

Acceptance for this item:

```text
No-news/no-catalyst can reduce aggressiveness but cannot eliminate a top gainer by itself.
```

### 22.13 Test Quality Remediation

The audit found that too many integration tests accepted broad outcomes such as:

```python
assert result.decision in ("watch", "enter", "skip")
```

This does not prove the intended behavior.

Required remediation:

1. Replace broad “does not crash” assertions with behavior-specific assertions.
2. Add tests that prove the full path, not only isolated helpers:
   - valid scanner + enrichment + bars -> attention -> entry -> sizing -> order,
   - entry fill -> protected open position,
   - open position -> hard stop exit,
   - emergency exit beats hard stop,
   - stale/missing data produces explicit watch/skip reasons,
   - DSY-like candidate survives to watch/entry,
   - old filters never appear as hard blocks.
3. Add negative tests for every old alpha-killing filter:
   - Chinese ADR,
   - no-news,
   - no-catalyst,
   - parabolic,
   - lunch,
   - below VWAP,
   - low float,
   - speculative theme,
   - biotech,
   - insider ownership.
4. Add tests proving network calls are not required in unit tests.
5. Keep tests small and mechanical. Do not build test frameworks.

### 22.14 Simplicity Remediation

The fix must simplify the runtime path rather than wrapping it in a framework.

Required remediation:

1. Remove or isolate the executable dependency on legacy anti-patterns, regime,
   pillars, agents, and quality scoring.
2. Keep the rebuild path boring and explicit.
3. Avoid adding strategy factories, plugin systems, or a generic pipeline
   framework.
4. If wide function signatures are causing wiring bugs, introduce at most a
   small number of plain data containers, such as:
   - `MarketSnapshot`,
   - `EnrichedCandidate`,
   - `PipelineContext`.
5. Do not abstract before the paper logs prove the need.

### 22.15 Post-Audit Acceptance Gate

The rebuild is not accepted for paper trial until all of the following are true:

1. The default CLI executes the rebuild path, not the old v3 pillar path.
2. `find_entry()` can return all six setup types through the orchestrator.
3. The state permission matrix is enforced in the pipeline.
4. Sizing result is the submitted paper order size.
5. Entry protection lifecycle cannot create silent unprotected/pending zombies.
6. App scanning passes real or injected bars, quote age, spread, and volume data
   to the pipeline.
7. App monitoring uses fresh current price, not average entry.
8. Account risk state is enforced before order submission.
9. Restart reconciliation runs before scanning.
10. DecisionRecord logs quote age when known.
11. No-news/no-catalyst are implemented as soft-only warnings when known.
12. Tests assert exact behavior for all critical paths.
13. `python3 -m pytest tests/ -q` passes.
14. No legacy qualitative filter appears as a hard block.
15. The implementation remains simpler than the old bot.
16. `python main.py --mode mock --once` exercises the rebuild path (no pillars,
    anti-patterns, regime veto, or quality scoring). No `--legacy` flag exists
    in the final CLI.
17. `python main.py --mode paper --once` runs the rebuild path (not legacy
    fallback, and not the temporary not-ready error permitted in §22.2 during
    intermediate remediation). The bot is not considered finalized until
    `--mode paper --once` succeeds through the rebuild path.
18. No importable legacy modules remain in the default runtime.
    `python -c "import src.pipeline.v3_pipeline"` (or any legacy module path)
    must raise `ModuleNotFoundError` when run from the project root.
19. `--mode live` is blocked with a clear error message. Only `mock` and `paper`
    are accepted modes. No code path attempts real broker trading.
20. Every `DecisionRecord` in JSONL output has all required fields populated:
    symbol, timestamp, source, source_timestamp, scanner_age_seconds,
    quote_age_seconds, attention_score, attention_drivers, data_confidence,
    hard_blocks, soft_warnings, state, state_evidence, mode, entry_setup,
    entry.{price,stop,risk_per_share,shares,risk_amount},
    exit.{reason,pnl,pnl_r,remaining_shares}, decision, reason. Fields whose
    value is null must still appear explicitly in the JSON.
21. `python3 -m pytest tests/ -q` passes with zero warnings. All
    PytestWarning, DeprecationWarning, and other diagnostic warnings must be
    fixed unless explicitly documented as unavoidable in a `conftest.py` filter
    or a pinned tracking issue with a clear rationale.
22. Legacy purge per §22.16 is complete: all old v3/Ross/pillar/regime/
    anti-pattern/AI/quality-score source files and tests have been removed or
    relocated to a non-importable archive directory entirely outside `src/`.

### 22.16 Final Legacy Purge And Platform Cleanup

Before the bot is considered finalized for paper trial, every vestige of legacy
code, configuration, tests, and documentation must be removed or quarantined so
that the rebuild path is the only path a developer or runtime can execute.

Required actions:

1. **Legacy source removal or quarantine.** Remove all legacy source files from
   the executable `src/` tree, or move them to a non-importable archive
   directory (e.g., `archive/src/` or a sibling directory with no `__init__.py`
   and no `PYTHONPATH` entry). At a minimum, these categories must be covered:

   - old v3 pipeline (`src/pipeline/v3_pipeline.py`, `src/pipeline/__init__.py`
     if it only serves legacy),
   - old RossCameronPipeline references,
   - pillar/anti-pattern code,
   - regime/market-regime code,
   - AI/agent approval code,
   - quality-score code,
   - any file still importing or depending on the above.

2. **Legacy test removal or rewrite.** Remove legacy tests that test the old
   funnel, pillars, regime, anti-patterns, AI, or quality score. Rewrite any
   legacy test that tests a concept now owned by the rebuild (e.g., attention,
   hard filters, entries) so it imports only rebuild modules.

3. **No `--legacy` flag.** The CLI must have no `--legacy` flag. Old code is
   not reachable from `main.py` by any flag or default. If legacy must be kept
   for archival purposes, it lives in a separate non-importable directory with
   its own runner.

4. **Remove stale config references.** The default config file and all config
   loading code must not reference legacy settings (e.g., pillar weights,
   quality thresholds, regime parameters, AI endpoints). Stale config keys
   must be removed.

5. **Remove stale dependency references.** `pyproject.toml`, `requirements.txt`,
   or `setup.cfg` must not list dependencies that exist only for legacy code.
   If a dependency is shared, it stays.

6. **Remove stale log references.** Decision log path, trade log path, and any
   other runtime paths must point to the rebuild log format. Old log formats
   are not written or expected by the runtime.

7. **Remove stale doc references.** Any documentation referencing old concepts
   (pillars, regime, anti-patterns, AI, quality score) as active runtime
   behavior must be updated or removed. The SPEC is the source of truth.

8. **Fix pytest warnings to zero.** Run `python3 -m pytest tests/ -q -W
   all::DeprecationWarning -W all::PendingDeprecationWarning` (or equivalent).
   All warnings must be fixed. If a third-party library produces an
   unavoidable warning, document it in `conftest.py` with a `pytest.mark.filter`
   or `warnings.filterwarnings` entry and a pinned tracking issue reference.

9. **Verify no importable legacy modules remain.** Create or extend a test that
   probes that legacy module paths raise `ModuleNotFoundError`:

   ```python
   # Example probe — exact paths depend on archive structure
   import pytest
   def test_no_legacy_pipeline():
       with pytest.raises(ModuleNotFoundError):
           import src.pipeline.v3_pipeline  # noqa: F401

   def test_no_legacy_risk():
       with pytest.raises(ModuleNotFoundError):
           import src.risk  # noqa: F401
   ```

   Every legacy module that was previously importable from `src/` must have a
   corresponding negative-import test.

10. **Acceptance command.** After purge:

    ```text
    python3 -m pytest tests/ -q -W error::DeprecationWarning
    ```

    must pass with zero failures and zero warnings. And:

    ```text
    python -c "import src.pipeline.v3_pipeline"
    ```

    must fail.

---

### 22.17 Post-Audit Remediation — 2026-06-12

A comprehensive implementation audit was performed on 2026-06-12 after all
§22.15 and §22.16 requirements were addressed. The audit assessed architecture
integrity, SPEC compliance, library usage, test coverage, dependency health,
and legacy residue.  771 tests existed at audit time.

The rebuild architecture is sound and 19 of 22 §22.15 items pass.  Three
items need targeted fixes, and several low-risk improvements were identified.
No new filters, pillars, frameworks, or rejection logic is required.

#### 22.17.1 Audit Verdict

```text
Architecture: PASS — clean module-per-concern, no frameworks, all injectable
SPEC compliance: 19/22 PASS, 3 NEEDS_WORK
Library usage: CLEAN — zero deprecated API patterns
Dependency health: CLEAN — 0 unused, 0 missing, 0 YAML mismatches
Legacy residue: TRACE — 1 orphaned utility file (src/utils.py, zero callers)
Test coverage: ~748 tests, zero network calls, no flaky assertions
```

The three NEEDS_WORK items and the top remediation priorities are detailed
below.  All findings are integration-wiring issues — the primitives exist and
are correctly implemented in isolation.

#### 22.17.2 Critical Finding — Exception Swallowing In Live Loops

Six locations use bare ``except Exception: continue`` with zero logging,
silently dropping failures during position monitoring, exit checks, scanning,
and pipeline execution:

- ``src/exits.py:492-493`` — exit orchestrator swallows all check-fn failures
- ``src/app.py:241-242`` — market-data fetch failure → snapshot = None silently
- ``src/app.py:275-276`` — pipeline error in position monitoring → continue
- ``src/app.py:282-283`` — scanner failure → silent return
- ``src/app.py:330-331`` — pipeline error in scan-and-process → continue
- ``src/scanner/enrichment.py:245-246`` — individual yfinance row failure → continue

Why this matters in a trading system:

- A broken exit check means no exit checks run for *all* positions indefinitely.
- A broken pipeline step becomes invisible — the loop continues without logging.
- ``KeyboardInterrupt`` or ``SystemExit`` caught inside the loop prevents graceful
  shutdown.
- You cannot distinguish transient errors (network blip) from systemic ones
  (broken logic, type mismatch).

Two locations already handle exceptions correctly:

- ``src/market_data.py:183-188`` — logs with ``logger.warning("…", exc)``
- ``src/decision_pipeline.py:345`` — catches ``ValueError`` specifically (symbol locked), not bare ``Exception``

**Required remediation:**

1. Replace every bare ``except Exception: continue`` with ``except Exception``
   plus a ``logger.exception("…")`` call that includes the symbol and operation
   name.  ``logger.exception()`` is preferred over ``logger.warning(exc)``
   because it includes the full traceback and variable inspection when
   ``backtrace=True`` and ``diagnose=True`` are set on the handler.
2. Do **not** catch ``KeyboardInterrupt`` or ``SystemExit`` — let them propagate
   for graceful shutdown.
3. In ``src/exits.py``, log the check label (``P1_emergency``, etc.) so a
   failing check is immediately identifiable.
4. In ``src/app.py``, log the symbol being processed when a pipeline step fails.
5. Add tests proving:
   - An exception in a single exit check does not block other exits.
   - An exception in pipeline processing does not crash the scan loop.
   - ``KeyboardInterrupt`` can stop the main loop.
6. Do **not** build an exception-handling framework or error-classification
   system.  Keep each handler simple, explicit, and local.

#### 22.17.3 Critical Finding — Paper Loop Mode Not Implemented

``main.py:97-99`` prints ``"Paper loop mode is not yet implemented"`` and
returns.  ``--mode paper --loop`` exits immediately without any execution.
This blocks the primary purpose of paper mode: multi-cycle trading trials
(SPEC Phase 9).

Required remediation:

1. Wire ``TradingApp.run()`` (``src/app.py:195-222``) into the paper-mode CLI
   path.  The app already supports injectable ``scanner_fn``, ``enrichment_fn``,
   ``market_data_fn``, and ``broker_snapshot_fn`` — only the CLI needs to wire
   them.
2. Paper loop must:
   - Start the scanner on a 30-second cadence.
   - Monitor open positions on a 10-second cadence.
   - Respect entry cutoff (default 15:30 ET) and flatten time (15:55 ET).
   - Support graceful shutdown via SIGINT/SIGTERM (already implemented in app).
3. Add a CLI test proving ``--mode paper --loop`` runs at least one full
   scanner cycle and one monitor cycle without crashing.
4. Do **not** add a separate loop implementation.  The single ``TradingApp.run()``
   loop must serve both paper and future live modes.

#### 22.17.4 High Finding — Protection Lifecycle Incomplete

The pipeline calls ``submit_entry()`` (creates PENDING_ENTRY state and a
PendingOrder) but never calls ``confirm_fill()`` → ``protect_position()``.
Paper-mode positions stay `PENDING_ENTRY` forever.  Exit checks in
``_monitor_positions()`` only target `OPEN` and `UNPROTECTED` states
(``decision_pipeline.py:370``), so PENDING_ENTRY positions are invisible to
exit logic.

The primitives all exist in ``PaperExecutionGateway``:

- ``submit_entry()`` → creates PENDING_ENTRY + PendingOrder
- ``confirm_fill()`` → advances to OPEN
- ``protect_position()`` → places stop-loss (idempotent)

**Required remediation:**

1. After ``submit_entry()`` succeeds in the pipeline, call ``confirm_fill()``
   to simulate fill in paper mode.  In mock mode this is a no-op because the
   execution gateway's positions are never marked filled.
2. After ``confirm_fill()``, call ``protect_position()`` with the signal's
   stop price.  If protection fails, call ``mark_unprotected()`` to make the
   failure explicit.
3. The order of operations must be:

   ```text
   submit_entry(sized_signal)
   → PENDING_ENTRY + pending entry order
   → confirm_fill(order_id)
   → OPEN
   → protect_position(symbol, stop_price, shares)
   → OPEN (protected)  OR  UNPROTECTED (if stop placement failed)
   ```

4. ``DecisionRecord.entry_shares`` must reflect the actual submitted quantity.
5. Add tests proving:
   - A successful entry transitions PENDING_ENTRY → OPEN → protected.
   - A stop-placement failure transitions OPEN → UNPROTECTED (not silently unprotected).
   - The exit monitor can see and exit OPEN and UNPROTECTED positions.
   - The exit monitor does NOT exit PENDING_ENTRY positions (those have not filled).
6. Do **not** add bracket/OCO-at-entry in this phase.  Keep the lifecycle simple:
   submit, fill, protect.

#### 22.17.5 Medium Finding — Unused Imports And Dead Code

Identified during audit:

- ``src/paper_execution.py:18`` — ``DecisionRecord`` imported but never referenced.
- ``src/app.py:336-338`` — ``_shutdown()`` assigns ``record = self._logger`` and discards it.
- ``src/move_classifier.py:377`` — ``state_mode()`` never called outside its module.
- ``src/move_classifier.py:367`` — ``setup_allowed()`` only used in tests.
- ``src/decision_pipeline.py:190`` — ``bars_available`` parameter always overridden at line 240.
- ``src/utils.py`` — entire file orphaned (``utc_now()``, ``to_ny_time()``, ``elapsed_market_pct()``) with zero callers.

**Required remediation:**

1. Remove unused imports, parameters, and functions.
2. Remove ``src/utils.py`` entirely.  It contains no code used by any rebuild module.
3. Do not remove ``setup_allowed()`` if existing tests use it — keep as
   a test-support function, but add a comment noting its purpose.
4. Do not remove ``state_mode()`` if it serves as a public API for potential
   external callers — but if internal-only, fold into ``classify_move_state()``
   return type.

#### 22.17.6 Low Finding — Configuration Gaps

- ``.env.example`` is missing the ``ALPACATRADER_CONFIG`` variable (used by
  ``config/settings.py:43`` to load a custom config path).
- ``requirements.txt`` is missing ``ruff>=0.5`` (exists in ``pyproject.toml``
  dev deps but not in requirements.txt for dev-setup parity).
- ``README.md:152`` documents ``src/app.py`` as "Loop controller (scan →
  pipeline → sleep)" but paper loop mode is not yet implemented.  This is
  aspirational documentation.

**Required remediation:**

1. Add ``# ALPACATRADER_CONFIG=config/custom.yaml`` to ``.env.example`` (commented-out).
2. Add ``ruff>=0.5`` to ``requirements.txt`` for dev-tool parity.
3. Update README.md to note that loop mode is in progress.

#### 22.17.7 Low Finding — Test Gaps

The audit identified specific missing tests (none blocking, but worth adding
for long-term confidence):

1. **DSY entry integration test.**  SPEC §1.3 and §19.2 describe exact DSY
   entry behavior (first pullback, 7-cent risk, 50 shares).  No test feeds
   DSY-characteristic bars through ``find_entry()``.
2. **Three invalidation exit types.**  ``check_invalidation()`` in
   ``src/exits.py`` handles 5 setup types, but only HOD-reclaim and VWAP-reclaim
   invalidations are tested.  First-pullback, consolidation-breakout, and
   scalp invalidations need tests.
3. **Scanner failure recovery.**  SPEC §15.1 requires fallback scanner or
   ``scanner_unavailable`` decision record when primary scanner fails.
   No test for this path.
4. **No ``tests/conftest.py``.**  Shared fixtures (``candidate``,
   ``fake_entry_signal``, ``paper_gateway``) are duplicated across
   ``test_phase9_pipeline.py`` and ``test_phase10_app.py``.

**Required remediation:**

1. Add ``TestDSYEntry`` class in ``tests/test_phase5_entries.py`` with
   DSY-characteristic bars and verify ``find_entry()`` returns a first-pullback
   signal with reasonable risk.
2. Add 3 invalidation tests covering all setup types in
   ``tests/test_phase8_exits.py``.
3. Add a scanner-failure test in ``tests/test_phase10_app.py`` proving the
   app logs a warning and continues monitoring positions.
4. Create ``tests/conftest.py`` with shared fixtures.

#### 22.17.8 Acceptance Gate For §22.17

The following must be true before the bot is re-accepted for paper trial:

1. Every bare ``except Exception: continue`` is replaced with
   ``logger.exception()`` plus ``continue`` across all 6 locations.
2. ``KeyboardInterrupt`` and ``SystemExit`` are not caught by any exception
   handler — they propagate for graceful shutdown.
3. Paper entries progress through the full lifecycle:
   PENDING_ENTRY → OPEN → protected (or UNPROTECTED).
4. ``--mode paper --loop`` starts the ``TradingApp`` loop and runs at least
   one scanner cycle and one monitor cycle.
5. Unused imports, parameters, functions, and ``src/utils.py`` are removed.
6. ``.env.example`` documents ``ALPACATRADER_CONFIG``.
7. ``requirements.txt`` includes ``ruff`` for dev parity.
8. ``python3 -m pytest tests/ -q -W error`` passes with zero warnings.
9. ``python main.py --mode mock --once`` and ``python main.py --mode paper --once``
   exit cleanly with rebuild-only output.
10. All new code follows the attention-first philosophy: no new filters, no
    pillars, no AI gates, no quality scores, no rejection frameworks.  Every
    change makes the bot better at finding, entering, sizing, managing, and
    exiting top gainers with controlled risk.

#### 22.17.9 Implementation Plan

```text
Phase A — Exceptions: Replace bare except:continue with logger.exception() in
  exits.py, app.py, scanner/enrichment.py.  Add loguru handler with
  backtrace=True and diagnose=True.  (touches 3 files, ~6 lines changed each)

Phase B — Protection lifecycle: Add confirm_fill() + protect_position() after
  submit_entry() in decision_pipeline.py.  Update tests proving the lifecycle.
  (touches 2 files, ~15 new lines)

Phase C — Paper loop: Wire TradingApp into main.py paper-mode path.
  Inject scanner_fn, market_data_fn, enrichment_fn from existing imports.
  (touches 1 file, ~10 new lines)

Phase D — Cleanup: Remove orphaned src/utils.py, unused imports in
  paper_execution.py, dead code in app.py _shutdown, move_classifier dead
  functions, decision_pipeline.py unused parameter.  Fix .env.example and
  requirements.txt.
  (touches 6 files, ~5 deletions each)

Phase E — Tests: Add DSY entry test, 3 invalidation exit tests, scanner
  failure test.  Create conftest.py with shared fixtures.
  (touches 4 files, ~30 new tests)

Phase F — Validation: Full pytest run with -W error, mock CLI smoke, paper
  CLI smoke, negative import tests confirmed.
```

**Do not introduce:** new frameworks, exception-classification systems,
error-wrapping layers, abstract error handlers, or any code that makes the bot
reject top gainers instead of checking how to trade them.  Every change must
make the bot simpler, safer, and more debuggable — not fancier.

---

## 23. Final Rule

Keep the system rational.

Simplicity does not mean sloppy. It means every rule must help the bot do one of
five things:

1. find top-gainer attention,
2. enter with definable risk,
3. size the risk,
4. manage the position,
5. exit before damage becomes uncontrolled.

The goal is not to look sophisticated.

The goal is to exploit top-gainer momentum with controlled risk.

---

## 24. Post-Audit Remediation Tracking — 2026-06-12 Re-Verification

Implementation checklist. Checked = done.

### CRITICAL Fixes
- [x] **C1**: `decision_pipeline.py:347` — Replace `except Exception: pass` with `loguru.logger.exception()` for confirm_fill failure
- [x] **C2**: `decision_pipeline.py:366` — Replace `except Exception: pass` with `loguru.logger.exception()` for mark_unprotected failure
- [x] **C3**: `app.py:_monitor_positions()` — Capture `run_pipeline()` return, wire `submit_exit()`/`confirm_exit_fill()` for exit decisions
- [x] **C4**: `app.py:_monitor_positions()` — Pass `daily_loss_breached=self._risk_state.daily_loss_breached` to `run_pipeline()`

### HIGH Fixes
- [x] **H1**: `app.py:_reconcile_on_startup()` — Call `protect_position()` for "insert_protect" reconciliation actions
- [x] **H2**: `config/settings.py` + `config/default_config.yaml` — Remove ~25 dead Phase1Settings keys (never read)
- [x] **H3**: `src/sizing.py` — Remove 10 dead functions; keep only `starter_risk_amount`, `attention_multiplier`, `adjusted_starter_risk`, `calculate_shares`, `entry_sizing`
- [x] **H4**: `tests/test_phase6_risk.py` — Rewrote to only test kept sizing functions (21 tests pass)

### MEDIUM Fixes
- [x] **M1**: `src/entries.py:find_entry()` — Per-detector `try/except` with `loguru.logger.exception()`, continues to next detector
- [x] **M2**: `src/journal/decision_logger.py` — JSONL rotation at 10 MB, keeps 5 backups
- [x] **M3**: `src/models/schemas.py:74-83` — Removed dead `DecisionType` enum
- [x] **M4**: `src/models/__init__.py` — Removed `DecisionType` export, then deleted file entirely
- [x] **M5**: `tests/test_phase1_schemas.py` — Removed `DecisionType` tests
- [x] **M6**: `src/paper_execution.py` — DecisionRecord was not imported (no-op)

### LOW Fixes
- [x] **L1**: `src/sizing.py:12` — Removed unused `import math`
- [x] **L2**: `src/market_data.py` + `src/market_data_sim.py` — Extracted shared `_compute_ema()` to market_data.py; sim imports it
- [x] **L3**: `src/paper_execution.py:389,401` — Fixed reconciliation reason-string bug (old_shares captured before mutation)
- [x] **L4**: Deleted `src/models/__init__.py`, `src/journal/__init__.py`, `src/scanner/__init__.py` (kept `src/__init__.py` for `import src.app`)
- [x] **L5**: Deleted all `__pycache__/` directories
- [x] **L6**: Merged into L4

### VALIDATION
- [x] `python3 -m pytest tests/ -q -W error` — **714 passed, zero warnings***
- [x] `python main.py --mode mock --once` — exits cleanly with rebuild-only output
