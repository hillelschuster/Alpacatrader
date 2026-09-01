# BOT_DATA_MAP — data / API / state inventory for the v0 paper bot

Companion to STRATEGY_v1.md + PAPER_BOT_SPEC.md. Source of feature semantics:
`factory/scripts/build_features.py` (authoritative), `src/market_data.py` (existing feed
code, inspected read-only). No strategy logic here — inputs, state, execution, ledger only.

## 0. What exists in src/ (v0.5.0, reusable as-is)

- `src/market_data.py`: Alpaca `StockHistoricalDataClient`, 1m bars via `StockBarsRequest`,
  `feed=DataFeed.IEX`, 60-bar lookback, VWAP/day-high from bar list, `IEX_SCALE` volume
  correction, `build_market_snapshot(candidate)`.
- `src/scanner/`: Finviz gainers scrape (`enrichment.scrape_finviz_gainers`),
  attention scoring, freshness, former-runner store.
- `src/paper_execution.py`, `src/trade_ledger.py`, `src/_atomic.py`: paper order flow,
  JSONL ledger, atomic writes.
- The v0 bot composes these; nothing under src/ is modified by the factory.

## 1. Per-minute live inputs (RTH loop, aligned to bar close)

Loop: wake at each minute mark +5s (e.g., 10:31:05 for the 10:30 bar), 390 cycles/day.

| input | call | cadence | payload |
|---|---|---|---|
| candidate set | Finviz gainers scrape (existing) + intraday pct-gain recompute from bars | 1/min | ~50–200 rows HTML |
| 1m bars, candidates | `StockBarsRequest(symbols=<cand∪open-pos>, timeframe=1Min, start=t−95min, end=t, feed=IEX)` | 1/min | ~30–60 symbols × ≤95 bars ≈ 150–350 KB JSON |
| daily context | `StockBarsRequest(timeframe=1Day, lookback=25 sessions)` per NEW candidate only | on first appearance | ~1.5 KB/symbol/session |

Per candidate the minute loop must yield, at minute t (ET, tod_min = minutes since 09:30):
prev_close, session_open, today's bar list (real bars only flagged), cum dollar volume.
The 30 model features are computed from state (§4), never re-fetched.

Feed caveat (disclose in ledger): research parquet is SIP-grade full-volume 1m data;
live Alpaca IEX undercounts volume — existing `IEX_SCALE` partially corrects 5m dollar
volume. Therefore the rvol baseline (§2) MUST be built from the same live IEX stream
(self-consistent ratio), never seeded from research parquet values.

## 2. rvol baseline table (20-session bucket-end cum-dollar-volume)

Exact research construction to reproduce (`build_features.build_baselines` + `rvol_attach`):

1. Bucket ends `B = [10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 16:00]` ET (tod 600,660,720,780,840,900,960; session grid starts tod 570).
2. For each (ticker, session): `cumdv_b = Σ(close·volume) up to bucket end b` from 1m bars — 7 values/session.
3. Baseline for bucket i: rolling mean of the last 20 sessions' `cumdv_b_i`, shifted 1 session (today's own value never enters today's expectation).
4. Live expectation at minute t: linear interpolation between the surrounding bucket ends — `exp(t) = e_{i-1} + (e_i − e_{i-1}) · (t − b_{i-1})/(b_i − b_{i-1})`; before 10:00 interpolate from 0 at 09:30 to e_0 at 10:00.
5. `rvol(t) = cumdv(t) / exp(t)`; null at 09:30 (exp=0).

Fetch/warm-up: first time a ticker is admitted to the watchlist, pull 20 sessions of 1m
bars (`TimeFrameUnit.Min`, chunked ≤10 days/request → 2–3 calls), compute the 7×N cumdv
history. Thereafter, at each day's close (16:05, one pass) append today's 7 bucket-end
values, drop the 21st-oldest session. Ticker never seen before ⇒ rvol null ⇒ E6 entry
gate cannot fire (fails closed — correct, disclosed).

Cache shape: `dict[ticker] -> deque[maxlen=20] of tuple[7 float cumdv]` + date-stamp;
persist JSONL daily (`journal/rvol_baseline.jsonl` via `_atomic`). Size: ~500 tracked
names × 20 × 7 floats ≈ <1 MB. Refresh: post-close append only; no intraday writes.

## 3. market_ret_5m live proxy (approximation, disclosed)

Research: cross-sectional **median 5m return over the entire clean universe** — measured
2026-01: 25.1M rows, **15,745 tickers**, ~33 bars/ticker-day median (price≥$2, ≥100
vol/bar, RTH). Live equivalent fetch is impractical.

Model importance (`model_v1.pkl`, gain share): **market_ret_5m = 0.52%** (rank 25 of 30;
top: ret_15m 22.5%, open_gap 6.9%). excess_gain (3.75%) inherits it.

Recommended proxy: **median 5m return over the visible scanner universe each minute**
(Finviz gainers list + all open/recent positions, typically 50–200 names). Same statistic
(median, 5m, cross-sectional), smaller breadth; ~0.5%-importance feature shifted by a
few dozen bps of breadth bias — materially irrelevant to score ranking but MUST be
recorded in the ledger (`market_ret_5m_source: "watchlist_median"`). Do not attempt
full-universe parity in v0.

## 4. Per-ticker in-memory state machine (updated per closed 1m bar)

State per ticker: `deques(maxlen=35)` of real-bar `(close, high, low, volume, dv_bar, r1)`,
`session_open`, `prev_close`, `hod_now`, `day_low/day_high (real bars)`, `n_hod_breaks`,
`cum_dv_now`, `cum_dv` snapshotted at each bucket end (for rvol), `last_qual_minute`,
`entry_state` (idle/cycle-k, hold-until minute).

Update on each closed bar at minute t:
- `cum_dv += close·volume` (0 if no bar); snapshot at bucket ends.
- `c = close` (forward-fill if no bar); `h = max(h, high)` forward-filled; `v = volume if bar else 0`.
- `r1 = c/c_prev − 1` **only on real bars** (null on fill rows — this is what keeps
  realized_vol_15m/dips/vol stats from seeing stale values).
- `hod_now = max(hod_now, high)` (real bar); `n_hod_breaks += (high > hod_before) & real`.
- Rolling features on the grid exactly as build_features: ret_k = c/c.shift(k) (fill rows
  included), realized_vol_15m = std(r1,15,min10), efficiency_30m = |Σr1|/Σ|r1| (30,min15),
  n_up_bars_15 = Σ(r1>0) (15,min10), lmin5 = min(low, 5 real bars), dip_level =
  max(hod_before·0.997, vwap), vwap = Σdv/Σv from session start, dv_5m_rate =
  (cum_dv(t)−cum_dv(t−5))/1, dv_accel = dv_5m_rate ÷ mean(dv over t−35..t−6) (null if 0),
  market_ret_5m = median 5m return of visible universe (§3), excess_gain =
  pct_gain − 100·market_ret_5m.
- No-bar minute: volume=0, dv contribution 0, low stays null, r1 null — matches research
  grid exactly (`has_bar` convention).

## 5. Execution representation (paper)

- Entry: strategy decides at minute t (admission), order submitted for t+1 open/first
  fill; **model fill** = close of the t+1 1m bar (research convention, label
  `fwd60_t1entry` = close(t+61)/close(t+1) − 1); **actual fill** = alpaca paper fill price
  + timestamp. Record both, plus slippage = actual − model.
- Exit: at t+61 decision minute, order at t+62; model exit = close(t+61) (the label's
  denominator/numerator convention: model 60m return uses close(t+1)→close(t+61));
  actual = paper fill. Record both.
- Entry cutoff: no entry when tod_min > 328 (14:58); no new cycle after it; force-flat
  not required for paper (hold completes by 16:00 only if entered ≤ 14:58).
- Rejections/partial fills logged verbatim; never overwrite model-convention numbers.

## 6. Ledger schema (JSONL, one file/day, atomic writes)

**Per-minute admission log** (every RTH minute):
`{ts, tod_min, list_size, market_ret_5m, candidates: [{ticker, pct_gain, score, vis_rank,
theta_pass, rvol, vwap_dist, gate_rvol4, gate_vwap3, gate_tod270, admitted, reason}]}`

**Per-trade record**:
`{ticker, et_date, entry_minute, cycle#, minutes_since_first_qual, rvol_at_entry, score_at_entry,
pct_gain_at_entry, model_fill, actual_fill, fill_slippage, units, hold_until_minute,
exit_minute, model_exit_price, actual_exit_price, exit_slippage,
model_net_60m (=fwd60_t1entry−0.002), actual_net_60m, rvol_at_exit, state_at_exit}`

**Daily rollup**:
`{date, n_minutes, n_admitted, n_entries, n_cycles_by_k, model_net_unit_bps (mean),
actual_net_unit_bps, slippage_stats, max_concurrent, skipped_by_cap, rvol_baseline_coverage,
open_positions_carryover}`

Paper success gate (from STRATEGY_v1): pooled net ≥ +30bps/trade over ≥ 20 trading days.
