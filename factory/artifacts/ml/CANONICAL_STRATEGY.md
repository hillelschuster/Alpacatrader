# CANONICAL STRATEGY — implementation reference (v1, frozen)

Single source of truth for building the paper bot. Everything here is either (a) frozen
research code (`factory/scripts/*`), (b) measured fact (log cited), or (c) a live
approximation explicitly labeled APPROX. No strategy redesign in this document.

Companions: STRATEGY_v1.md (evidence + economics), PAPER_BOT_SPEC.md (feature inventory),
BOT_DATA_MAP.md (data/API/state mechanics), PRE_REG_2026.md + PRE_REG_EXPOSURE.md (gates).

---

## A. VALIDATED CORE (the alpha — do not modify)

Every element below survived: OOS Aug–Dec 2025 → pre-registered frozen 2026-01..03 pass
(all 3 gates) → pre-registered E6 exposure test on 2026 (all 3 gates). Results:
event-stream +91/+87bps @20bps (2025/2026); E6 +66.4/+34.4bps per unit, 5/5 and 3/3
months ≥0; survives 40bps RT; reviewer-audited (no BLOCKER/MATERIAL).

1. **Model**: `factory/artifacts/ml/model_v1.pkl` (LightGBM depth-6, best_iter=57,
   trained May+Jun 2025 on 30 features — order in `train_ml.py:FEATURES`, identical in
   all scorers, verified). Never retrain live.
2. **Admission (M3)**: score ≥ theta_fixed **0.00115** AND score-rank ≤ 2 among the
   current minute's scored candidates.
3. **Stream gate**: rvol > 4 AND vwap_dist > 0.03 AND tod_min < 270. Entry minutes
   additionally tod_min ≤ 328.
4. **Exposure (E6)**: first entry at the first admitted minute with **rvol > 8**;
   after each 60m hold, re-enter at the next admitted minute with rvol > 8; ~1.6
   entries/episode; 1 position per ticker at a time.
5. **Exit**: FIXED 60-minute hold (fill = close of bar t+1, exit = close of bar t+61).
   Never exit on state death — measured to destroy the edge in both years.
6. **Cost budget**: 20bps RT planning, 40bps break-even floor.

## B. NON-CORE OBSERVATIONS (documented, NOT deployed — do not silently promote)

- 30m hold alternative (better bps/capital-minute, fewer bps/trade) — not validated as a
  full structure; keep 60m.
- Economic pocket close≤$20 & cum_dv $5–100M (+129.5bps 2026 first look) — post-peek
  2025 selection, secondary read only; NOT a gate. Do not filter by price.
- Toxic states (rvol_15m>2%, ret_15m>3% pops, gap>30%) — archetype observations, not gates.
- vwap_dist>12% = "extension safe only with volume" mechanism confirmation — not a cell.
- S4 score-ladder scale-ins — did NOT transfer (2026 +18bps); superseded by E6.
- H009 trap/reclaim, above_vwap, dip_5m — 0.0 model importance; inert features.

## C. UNIVERSE — research vs live (the resolved question)

Research ticker-day universe (`certify_month.py`): per-minute dense rank of
`pct_gain = close/prev_corrected_session_close − 1` among tickers with a bar that minute;
`is_topN = rank ≤ 20`; day-level conditions: gap==1, day-max-close ≥ prev_close,
**day-max-gain ≥ 8%** (whole-day, unknowable live), day dollar_volume ≥ $5M,
n_bars ≥ 30, exchange ∈ {NYSE,NASDAQ,AMEX}, EQUITY, not split-suspect.

**Measured resolution (entry_gain_stats.log)**: at E6 entry minutes the whole-day
condition is already satisfied — pct_gain ≥ 8% at entry: **98.5% (2025) / 99.5% (2026)**
(median entry gain +19–21%); cum_dv ≥ $5M at entry: **100.0%** (p1 = $5.0M).
**Conclusion: NO live proxy filter for the 8% condition — the causal gates imply it.**
Adding a gain floor would be a new filter (silently changes the trade set) — not added.
Live-only causal readings of the day gates (near-free, disclosed): require cum_dv ≥ $5M
at entry and ≥ 30 real bars so far today at entry.

**Live scanner rules (from oracle mapping)**: recompute rank LOCALLY from bars — do NOT
use Alpaca's "top gainers" endpoint (different definition). Rank dense-descending among
all RTH equities with a bar this minute; take top-20. prev_close = adjusted close of the
immediately-prior session. gap==1: skip a ticker's first session after a trading gap.
Tie-break identical ranks deterministically (by symbol).

## D. EXACT LIVE RULES (per-minute loop)

At each RTH bar close t (tod_min = minutes since 09:30 bar):

1. Build top-20 candidate list (rule C). Plus: keep bars for open/recent positions.
2. For each candidate compute the 30 features exactly as `build_features.py`
   (state machine per BOT_DATA_MAP §4: no-bar minute ⇒ volume 0, close forward-filled,
   real-bar-only stats for r1/realized_vol/dip lows). `rvol` = cum_dv ÷ 20-session
   bucket-end-interpolated expected cum_dv (hourly buckets 10:00..16:00, shift-1
   baseline, self-consistent with the live data feed — see F). Null at 09:30.
3. Score with model_v1.pkl (exact FEATURES order; log_dollar_volume = ln(1+cum_dv)).
4. Admit iff: score ≥ 0.00115 AND score-rank ≤ 2 (within this minute's scored
   candidates, tie-break by symbol) AND rvol > 4 AND vwap_dist > 0.03 AND tod_min < 270.
5. Exposure state machine per ticker:
   - idle → ENTER iff admitted AND rvol > 8 AND tod_min ≤ 328 AND cum_dv ≥ $5M
     AND ≥30 real bars today AND no open position in ticker AND concurrency < cap.
   - in-position: exit unconditionally at close of bar (entry_fill_bar + 60).
   - flat after exit → re-ENTER iff an admitted minute with rvol > 8 occurs at
     tod_min ≥ exit_admission_minute + 61 (same cutoffs as above).
6. Concurrency cap: 10 simultaneous positions (research sim: ~4 avg, zero skipped).
7. `market_ret_5m` = median 5m return of the visible scanner universe (APPROX,
   importance 0.015 = rank 26/30; record source in ledger). `excess_gain` =
   pct_gain − 100·market_ret_5m. Do NOT substitute an index return.
8. Record everything (§F ledger). No other filters, no stops, no sizing logic.

## E. CLOCK ARITHMETIC (bar time)

Admission minute p → fill = close of bar p+1 → hold ends at close of bar p+61
(60 bars). E6 re-entry admission ≥ p_prev + 61 (fills never overlap). Entry cutoff:
no admission-for-entry with tod_min > 328 (14:58 bar; hold completes by 15:59).
09:30 bar = tod_min 0; 15:59 = 389. Position expiry uses real session minutes
(no overnight carry; a position always exits same-day by construction).

## F. DATA / STATE NEEDED (details in BOT_DATA_MAP.md)

- **1m bars** for candidates ∪ open positions, 95-bar lookback, every minute
  (Alpaca `StockBarsRequest`; existing `src/market_data.py` pattern).
- **Daily context** per new candidate: 25 sessions of daily bars + prior-session
  adjusted close (prev_close, session_open, gap==1 check).
- **rvol baseline cache**: per ticker, last 20 sessions' cum-dv at the 7 hourly bucket
  ends (deque of 7-float tuples; <1 MB); fetched on first appearance, appended
  post-close. Ticker without 20-session baseline ⇒ rvol null ⇒ cannot enter
  (fails closed; warms up over weeks).
- **In-memory per-ticker state**: deques(maxlen=35) real bars, session_open, prev_close,
  hod_now, n_hod_breaks, cum_dv + bucket-end snapshots, last_qual_minute, cycle state.
- **Ledger (JSONL, atomic)**: per-minute admission log (list, scores, vis_rank, gates,
  decisions+reasons); per-trade record (ticker, entry minute, cycle #, minutes-since-
  first-qual, rvol/score/pct_gain at entry, model fill, ACTUAL paper fill, slippage,
  exit both ways, model_net@20bps, actual_net); daily rollup (admitted, entries,
  cycles, net by model/actual, slippage stats, max concurrent, baseline coverage).
- **Feed parity (the one real approximation)**: research data is consolidated-volume
  (SIP-grade); Alpaca free feed is IEX (partial volume). Prefer **feed=SIP** for the
  paper bot. If IEX-only: rvol/gates must be computed self-consistently from the same
  IEX stream (baseline built from live data, never seeded from research parquet), and
  `feed` recorded in the ledger — disclosed, and the paper success gate arbitrates.

## G. EXECUTION REPRESENTATION (paper)

Entry order submitted at bar p+1 (first fill); exit order at bar p+61 close. Model
convention prices (close p+1 / close p+61) recorded alongside ACTUAL paper fills +
slippage; success gate evaluated on model convention AND actual fills. Rejections/
partials logged verbatim. v0: $10k units, 1 unit per entry, no pyramiding.

## H. PLAIN-ENGLISH FLOW

Every minute during regular hours: rank all traded stocks by gain vs yesterday's close,
take the top 20. For each, compute 30 intraday state features from 1-minute bars, score
with the frozen model. If a stock's score is in the minute's top-2 and above the frozen
threshold, and it's showing extreme volume (rvol>4) while extended above session VWAP,
before 11:00 — it qualifies. If its volume is extreme (rvol>8), buy $10k at the next
minute's close. Hold exactly 60 minutes and sell. If it qualifies again later with
rvol>8, buy again. At most 10 names at once, one position per name. That's the whole
system: roughly 5 trades/day, +34–66bps per trade after costs in frozen research,
positive in 8 of 8 tested months across two regimes.

## I. UNRESOLVED AMBIGUITIES (all disclosed; none block)

1. market_ret_5m watchlist-median proxy (importance 0.015 — bounded, recorded).
2. Live feed volume parity (SIP vs IEX) — mitigated by self-consistent baseline or SIP.
3. rvol baseline warm-up period (first ~4 weeks under-admit; fails closed, safe).
4. Scanner-membership noise vs research top-20 (research-verified insensitive to ±2
   ranks and 25% list drops).
5. Full-population vs label-complete expectancy (live trades all picks; research
   full-set numbers are the right expectation).
6. Early-session entries (26% before tod 45): no research time gate exists — verified
   once at warmup that live ret_15m bar history matches research construction.

## J. READY TO BUILD? **YES.**

Strategy: fully specified, causal, frozen, reviewer-audited (no blockers), 2026-validated.
Data map: complete (BOT_DATA_MAP.md). Ambiguities: disclosed with bounded impact.
Next action: implement the v0 paper bot per this document + BOT_DATA_MAP.md; paper gate
≥ +30bps/trade over ≥ 20 trading days (model convention) with positive actual-fill
trajectory. Live sizing only after the paper gate passes.
