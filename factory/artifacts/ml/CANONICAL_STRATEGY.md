# CANONICAL STRATEGY — implementation reference (v1, frozen)

> **CORRECTION (2026-09-02, rvol corruption — see RVOL_CORRUPTION_REPORT.md):** the rvol
> column in every research feature parquet was corrupted (unsorted cum-sum). All numbers
> depending on rvol/model scores were re-derived on corrected data: canonical model is
> now **model_v2.pkl** (best_iter=56, same config/training months) with **theta 0.00098**
> (dev-only recal, May–Jul p90). Corrected results: composite event-stream +86.1bps @20
> (2025-11+12 val); E6 2025 **+45.0**bps/unit (was +66.4); E6 2026 **−62.8**bps/unit
> (was +34.4) — **the 2026 GO is REVERSED**. Final live verdict awaits the pre-registered
> fresh-window eval 2026-04..08 (FROZEN_V2.md, eval_frozen.py, run BEFORE any peek).
> Stale v1 numbers below are retained and marked [CORRECTED].

Single source of truth for building the paper bot. Everything here is either (a) frozen
research code (`factory/scripts/*`), (b) measured fact (log cited), or (c) a live
approximation explicitly labeled APPROX. No strategy redesign in this document.

Companions: STRATEGY_v1.md (evidence + economics), PAPER_BOT_SPEC.md (feature inventory),
BOT_DATA_MAP.md (data/API/state mechanics), PRE_REG_2026.md + PRE_REG_EXPOSURE.md (gates).

---

## A. VALIDATED CORE (the alpha — do not modify)

Every element below survived: OOS Aug–Dec 2025 → pre-registered frozen 2026-01..03 pass
(all 3 gates) → pre-registered E6 exposure test on 2026 (all 3 gates). [CORRECTED:
E6 2026 fails on corrected data (−62.8bps/unit); the pre-registered 2026-04..08
fresh-window eval (FROZEN_V2.md) now arbitrates the live verdict.] Historical (polluted)
results: event-stream +91/+87bps @20bps (2025/2026); E6 +66.4/+34.4bps per unit;
survives 40bps RT; reviewer-audited (no BLOCKER/MATERIAL).

1. **Model**: `factory/artifacts/ml/model_v2.pkl` [CORRECTED: was model_v1.pkl;
   v2 retrained on corrected rvol, same config, best_iter=56] (LightGBM depth-6,
   trained May+Jun 2025 on 30 features — order in `train_ml.py:FEATURES`, identical in
   all scorers, verified). Never retrain live.
2. **Admission (M3)**: score ≥ theta_fixed **0.00098** [CORRECTED: was 0.00115 on
   polluted rvol] AND score-rank ≤ 2 among the
   current minute's scored candidates.
3. **Stream gate**: rvol > 4 AND vwap_dist > 0.03 AND tod_min < 270 (= admission before
   **14:00 ET**). Entry minutes additionally tod_min ≤ 328 (= 14:58 ET).
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

**Measured resolution (entry_gain_stats.log, residual_gate_stats.log)**:
- At E6 entry minutes the whole-day condition is already satisfied — pct_gain ≥ 8% at
  entry: **98.5% (2025) / 99.5% (2026)** (median entry gain +19–21%); cum_dv ≥ $5M at
  entry: **100.0%** (p1 = $5.0M).
- The day-level conditions are causally implied by the gates — NO live proxy filter for
  the 8% condition. Adding a gain floor would be a new filter (silently changes the
  trade set) — not added.
- Rank population: research ranks among gap==1, close≥$2, vol≥100/bar, listed-equity
  rows (universe_tags cache adds only sub-8% names). Recomputing every entry's rank at
  its entry minute over the full listed population: **identical rank 99.9% (1029/1071),
  100.0% still top-20** (1 differing case, a 4.9% gainer, stays top-20). The research
  population is reproduced by: listed equities only (NYSE/NASDAQ/AMEX, quoteType EQUITY,
  ETFs and OTC EXCLUDED), close ≥ $2, bar volume ≥ 100, gap==1, dense-desc rank by
  pct_gain vs prev_close, top 20.
- n_bars ≥ 30 (research full-day screen): **18.4% of E6 entries had <30 real bars at
  their entry minute and those trades averaged +47.0bps** (vs +57.3 for ≥30) — a live
  "30 bars so far" rule would tax real edge. NO minimum-bar condition is applied live;
  bars_so_far is recorded in the ledger only.

**Live scanner rules (resolved)**: build the top-20 locally from live data over the
onboarded listed-equity universe — do NOT use Alpaca's "top gainers" endpoint (different
definition) and do NOT use a Finviz gainers list as the rank population (that ranks the
list, not the market). prev_close = adjusted close of the immediately-prior session.
gap==1: skip a ticker's first session after a trading gap. Tie-break identical ranks
deterministically (by symbol). ETF/OTC exclusion via universe onboarding check (once,
cached; unknown ⇒ excluded, mirrors research fail-closed).

## D. EXACT LIVE RULES (per-minute loop)

At each RTH bar close t (tod_min = minutes since 09:30 bar):

1. Build the top-20 candidate list over the onboarded listed-equity universe (rule C):
   each minute, gain vs prev_close for every onboarded symbol with activity (snapshot /
   batched bars — see BOT_DATA_MAP §1), dense-desc rank, top 20, ETFs/OTC/sub-$2
   excluded. Plus: keep bars for open/recent positions.
2. For each candidate compute the 30 features exactly as `build_features.py`
   (state machine per BOT_DATA_MAP §4: no-bar minute ⇒ volume 0, close forward-filled,
   real-bar-only stats for r1/realized_vol/dip lows). `rvol` = cum_dv ÷ 20-session
   bucket-end-interpolated expected cum_dv (hourly buckets 10:00..16:00, shift-1
   baseline, self-consistent with the live data feed — see F). Null at 09:30.
3. Score with model_v1.pkl (exact FEATURES order; log_dollar_volume = ln(1+cum_dv)).
4. Admit iff: score ≥ 0.00098 [CORRECTED] AND score-rank ≤ 2 (within this minute's scored
   candidates, tie-break by symbol) AND rvol > 4 AND vwap_dist > 0.03 AND tod_min < 270.
5. Exposure state machine per ticker:
   - idle → ENTER iff admitted AND rvol > 8 AND tod_min ≤ 328 AND cum_dv ≥ $5M
     AND no open position in ticker AND concurrency < cap. (No minimum-bar condition —
     measured to cost edge; bars_so_far recorded in ledger.)
   - in-position: exit unconditionally at close of bar (entry_fill_bar + 60).
   - flat after exit → re-ENTER iff an admitted minute with rvol > 8 occurs at
     tod_min ≥ exit_admission_minute + 61 (same cutoffs as above).
6. Concurrency cap: 10 simultaneous positions (research sim: ~4 avg, zero skipped).
7. `market_ret_5m` = median 5m return of a BROAD liquid basket (APPROX — research
   statistic is the whole-market median, measured p50 0.0000, p95 +0.13%; a gainers-list
   median would be off-scale; a constant worst-case +1.5% proxy bias moves scores ~5%
   and theta-fails ~3.8% of entries, vis_rank invariant). Record source in ledger.
   `excess_gain` = pct_gain − 100·market_ret_5m. Do NOT substitute a single index return.
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

Every minute during regular hours: rank all listed stocks (>$2, ETFs/OTC excluded) by
gain vs yesterday's close, take the top 20. For each, compute 30 intraday state features
from 1-minute bars, score with the frozen model. If a stock's score is in the minute's
top-2 and above the frozen threshold, and it's showing extreme volume (rvol>4) while
extended above session VWAP, before 14:00 — it qualifies. If its volume is extreme
(rvol>8), buy $10k at the next minute's close. Hold exactly 60 minutes and sell. If it
qualifies again later with rvol>8, buy again. At most 10 names at once, one position per
name. That's the whole system. [CORRECTED: historical edge +45bps/unit (E6, 2025,
corrected rvol); 2026-01..03 replayed −62.8bps/unit — verdict pending the
pre-registered 2026-04..08 fresh-window eval.]

## I. UNRESOLVED AMBIGUITIES (all disclosed; none block)

1. market_ret_5m broad-basket median proxy (importance 0.015, measured sensitivity:
   constant +1.5% bias ⇒ ~3.8% theta-fails, ranks invariant — recorded in ledger).
2. Live feed volume parity (SIP vs IEX) — mitigated by self-consistent baseline or SIP.
3. rvol baseline warm-up period (first ~4 weeks under-admit; fails closed, safe).
4. Scanner snapshot-vs-bar-close convention (ranked gain from latest price vs 1m bar
   close — small timing offset; rank-robust per scanner-robustness results).
5. Full-population vs label-complete expectancy (live trades all picks; research
   full-set numbers are the right expectation).
6. Early-session entries (26% before tod 45): no research time gate exists — verified
   once at warmup that live ret_15m bar history matches research construction.
7. split_suspect: research excludes ticker-days whose final close/prev_close is outside
   [0.5, 2.0] — unknowable live. ~40 excluded 8%-gainer days/month; E6 entry gains cap
   at ~67% (consistent with those days being fully excluded from research). Live cannot
   and should not replicate the exclusion; ledger records the EOD flag so paper results
   can quantify any asymmetry. No pre-filter.

## J. READY TO BUILD? **YES.**

Strategy: fully specified, causal, frozen, reviewer-audited (no blockers), 2026-validated.
Data map: complete (BOT_DATA_MAP.md). Ambiguities: disclosed with bounded impact.
Next action: implement the v0 paper bot per this document + BOT_DATA_MAP.md; paper gate
≥ +30bps/trade over ≥ 20 trading days (model convention) with positive actual-fill
trajectory. Live sizing only after the paper gate passes.
