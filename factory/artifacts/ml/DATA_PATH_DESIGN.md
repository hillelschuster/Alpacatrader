# DATA_PATH_DESIGN — market data / universe / scanner for the v0 paper bot

Companion to CANONICAL_STRATEGY.md (frozen spec) + BOT_DATA_MAP.md. Written after the
feed fingerprint measurement (`feed_fingerprint.log`) and verified Alpaca/IBKR docs.
Strategy is frozen — this doc only decides how to feed it faithfully.

## 1. Feed verdict: SIP is MANDATORY for v0

**Measured ground truth**: research/training data (HuggingFace mito0o852/OHLCV-1m) is
consolidated SIP-grade — NVDA 176.8M, SPY 70.3M, TSLA 89.1M shares on 2026-01-06
(full consolidated volume), 960 bars/session, 15.6–16.4k tickers/day incl. OTC
(`feed_fingerprint.log`). The model's features live on the consolidated scale.

**Alpaca plans (verified)**: free Basic = IEX only; Algo Trader Plus $99/mo = SIP
(REST + websocket).

Quantified distortion if live ran IEX against a SIP-trained model:

| signal | research scale | IEX distortion | verdict |
|---|---|---|---|
| rvol (17.6% importance; gates rvol>4, rvol>8) | consolidated cumdv / consolidated baseline | IEX share is ~2–2.5% average but **varies per ticker and per day** (retail-heavy names 5–10%, others <1%) → ratio unstable even with a self-consistent IEX baseline; a 2× share swing = 2× rvol error, firing gates on wrong minutes | FATAL |
| cum_dv ≥ $5M day gate | consolidated dollars | IEX $5M ≈ $200–250M consolidated → gate semantics wrong by ~50× | FATAL |
| vwap_dist > 0.03 composite gate | consolidated VWAP | IEX VWAP deviates from consolidated; sign flips near threshold | MATERIAL |
| top-20 rank cross-section | every listed symbol with a bar | symbols with no IEX prints in a minute are **invisible** (stale last-trade) → missed top-20 members, wrong gains | FATAL (discovery layer) |
| market_ret_5m | whole-universe median | smaller, IEX-biased universe | MATERIAL |

**Cheaper alternatives checked**: Polygon real-time websocket requires a paid tier
(exact prices not verified this session) and brings a second vendor with no paper
execution attached. Alpaca $99/mo is the cheapest verified SIP-grade source and the
paper broker is the same account.
**Verdict: Alpaca Algo Trader Plus ($99/mo), feed=sip everywhere, from day one.**
The old `IEX_SCALE=40` hack in src/market_data.py is deleted — it was always wrong
(scalar for a stochastic share) and becomes moot.

## 2. Universe and onboarding

- **Seed** (day one): `data/universe_tags.parquet` eligible rows = 6,898 listed EQUITY
  tickers (yfinance exchange NYQ/NMS/ASE + quoteType EQUITY — exactly the research
  population). Load at boot; ~6.9k symbols.
- **Weekly drift check** (Sunday): pull Alpaca `GET /v2/assets?status=active`
  (class=us_equity, exchange ∈ NYSE/NASDAQ/AMEX, tradable) → diff against onboarded set;
  new symbols queued as PENDING.
- **ETF/OTC exclusion, fail-closed**: a symbol is RANKABLE only while its tag says
  listed-EQUITY. New/unknown symbols stay PENDING (not rankable) until verified —
  mirrors the research fail-closed (`fill_null(False)`) and the measured 1/1029 impact
  of never-cached listed tickers. Verification for a handful of new symbols per week:
  yfinance quoteType==EQUITY (cheap at that volume; the 6.9k bulk is already done).
- **prev_close**: Alpaca snapshot `previousDailyBar.close` = raw prior-session close —
  matches research construction (raw close of immediately-prior session from the same
  data pipeline, no corporate-action adjustment). **gap==1 rule**: require
  previousDailyBar timestamp == prior trading session (Alpaca calendar); else skip
  ticker for the day (first session after halt/gap excluded — research-faithful).

## 3. Per-minute acquisition: SIP websocket `bars:["*"]` (primary)

Verified: the v2 stream subscription supports the wildcard all-symbols subscription on
the bars channel; minute bars are emitted right after each minute mark
(fields: symbol, o/h/l/c, v, **vw**, n, t); an `updatedBars` channel emits late-trade
updates after half-minute marks.

**Design (matches research construction exactly — no snapshot approximation needed):**

Per-symbol in-memory state (all ~6.9k onboarded symbols):
`deque(maxlen=35)` of real bars `(close, high, low, volume)` + scalars
`prev_close, session_open, cum_dv, vwap_num, vwap_den, hod, bucket_cumdv[7]`,
plus `last_bar_minute`. RAM: 7k × 35 × 4 floats + scalars ≈ **<100 MB**.

Per closed minute t (bar stamped [t−1, t), arriving ~t):
1. `has_bar=True` for symbols that emitted; `cum_dv += close·volume`;
   snapshot `bucket_cumdv` at bucket ends [10:00..16:00].
2. Grid row for every onboarded symbol: close carried forward if no bar
   (`has_bar=False`, volume 0, r1 null) — **identical to the research grid**.
3. `r1 = c/c_prev − 1` only on real bars.
4. **Rank**: pct_gain = c/prev_close − 1 for symbols with a real bar at t;
   filters close≥$2, volume≥100, gap==1, rankable-universe; dense-desc; top 20.
5. **market_ret_5m**: median of the 5m return (c/c.shift(5)) over **all** onboarded
   symbols with real bars this minute passing the same price/volume gates — same
   statistic as research (cross-sectional median of clean rows). Population differs:
   live 6.9k listed vs research ~15.7k incl. OTC — measured tolerance makes this a
   MINOR (research median p50 0.0000, p95 +0.13%; constant +1.5% bias theta-fails only
   3.8%); ledger records `market_ret_5m_source = "listed_universe_median_sip"`.
6. updatedBars for the last 2 minutes are folded in **before** scoring at t+5s; late
   updates after scoring mutate history but already-scored minutes are not rescored
   (research data is final-bars; residual = rare late-print differences, ledger-logged).
7. Score features for the top-20 (+ open positions) exactly per BOT_DATA_MAP §4;
   M3/composite/E6 per CANONICAL §D.

**rvol baseline (research-exact, bulk pre-warmed)**: per symbol, `deque(maxlen=20)`
sessions × 7 bucket-end cumdv values; expectation = shifted 20-session mean, linear
interpolation between bucket ends (BOT_DATA_MAP §2). **Boot-time bulk pre-warm of the
whole onboarded universe** from REST history (feed=sip, included in the plan):
multi-symbol `StockBarsRequest` in ~500-symbol chunks over a 20-session window —
~14–35 requests one-time (~10 MB of baselines), then only weekly for newly onboarded
symbols. Thereafter each day's 7 bucket-end values are appended at 16:05 from the ring
buffers (free — the websocket already carries them). **This removes the warm-up
under-admission approximation almost entirely**: composite (rvol>4) and E6 (rvol>8)
gates work from minute one. Baseline built from Alpaca SIP history + live SIP stream —
never seeded from the research parquet. A symbol with no history (fresh listing) has
rvol null ⇒ gates fail closed (research-faithful, rare).

**Reconnect / missed minutes**: on stream drop → reconnect with backoff; REST
`StockBarsRequest(feed=sip, TimeFrame.Minute)` backfill last ~100 min for all tracked
symbols (chunks of ~500 symbols, ~14 calls) + snapshot call for prev_close sanity.
Missed minutes become has_bar=False grid rows (research-consistent). Every minute the
bot logs `stream_ok`; a watchdog alerts if no bar-message traffic for >90s during RTH.

**Load estimate**: 6–10k bar messages/min (~1.5 MB/min JSON), ~400 msg/s peak at the
open; single process, ~5% of one core. Boot: snapshot call (1–2 requests, all symbols)
for prev_close + calendar call. REST budget: <20 calls/min steady state — far under
limits.

## 4. Remaining approximations vs research

1. ~~market_ret_5m proxy~~ — reduced to MINOR: same statistic, listed-universe median
   (6.9k) vs research whole-market median (15.7k incl. OTC); source recorded in ledger.
2. updatedBars/late-print timing vs final research bars — bounded to seconds; ledger
   logs `late_update_count` per minute.
3. rvol baseline for symbols with <20 sessions of history (fresh listings) — fails
   closed, rare. All other symbols pre-warmed from SIP history at boot.
4. split_suspect EOD exclusion not replicable live — trade through, ledger flag
   (CANONICAL §I #7), quantified in paper.
5. Feed label: `feed=sip` recorded in every ledger record.

## 5. Ledger fields added by this design

`feed: "sip"`, `market_ret_5m_source: "full_universe_median_sip"`, `late_update_count`,
`bars_so_far`, `split_suspect_eod`, `rvol_baseline_sessions` (warm-up completeness).

## Open blockers

- Alpaca account must have Algo Trader Plus active (data plan for stream + history;
  paper trading itself is free). Boot-time pre-warm needs it before first session.
- None otherwise.
