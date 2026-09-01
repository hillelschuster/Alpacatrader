# STRATEGY v1 — Top-Gainer Continuation (composite + E6 exposure)

One-page trading description distilled from H001–H010, ML v1 (frozen May–Jul 2025),
OOS Aug–Dec 2025, and the pre-registered 2026-01..03 frozen passes (eval_2026.py,
exposure_design.py). Everything below is causal-by-construction or frozen pre-2026.

## 1. Universe
Live top-gainers list, refreshed every minute during RTH: top-20 US equities by intraday
% gain vs prior close (pct_gain ≥ ~3% effective; scanner = existing Alpacatrader scanner
top-20/minute). Only names with a real 1m bar this minute are candidates.

## 2. Model & score
`factory/artifacts/ml/model_v1.pkl` (LightGBM depth-6, best_iter=57, trained May+Jun 2025
only) scores each candidate on the 30-feature decision-time inventory (exact definitions
in PAPER_BOT_SPEC.md — rvol = CUM $vol ÷ 20-session bucket-interpolated expected cum $vol;
market_ret_5m = cross-sectional median 5m return of the whole clean universe; excess_gain
= pct_gain − 100·market_ret_5m).

## 3. Admission (per minute, strictly causal)
A candidate is ADMITTED iff:
- score ≥ theta_fixed = 0.00115, AND
- score-rank ≤ 2 among the current minute's visible admitted-score candidates (M3), AND
- stream gate: rvol > 4 AND vwap_dist > 0.03 AND tod_min < 270 (composite).

## 4. State (the economic mechanism)
Extraordinary participation (rvol>8 ≈ top ~17% of tape) + price extended above session
VWAP + model-confirmed path quality. VWAP extension is safe only with volume behind it
(rvol>8 & vwap_dist>8% ≈ +200bps; same extension without volume ≈ −60bps). Monotone in
rvol and vwap_dist inside the gate — no knife-edge, broad region (all KMeans clusters
+55..+183bps). Toxic overlays (avoid entries): realized_vol_15m>2%, ret_15m>3% pops,
gap>30%. Economic pocket: close ≤ $20 & cum_dv $5–100M (+129.5bps 2026 first look);
>$20 names lose at all cost levels — size them down or skip.

## 5. Entry (E6 exposure structure)
- FIRST position: at the first admitted minute of a ticker-episode where rvol > 8.
- Fill model: close of the next 1m bar after the admission minute (t+1 close).
- Entry cutoff: tod_min ≤ 328 (entry + 60m fits inside RTH; = 14:58 ET latest entry).

## 6. Scaling (how persistence becomes exposure)
After each 60-minute hold completes, RE-ENTER at the next admitted minute with rvol > 8
(unlimited cycles while the state re-qualifies; ~1.6 entries/episode in practice).
1 position per ticker at a time. No score-threshold ladder (S4 did not transfer: +77→+18).
Do NOT try to time within an episode — first-entry-only is below stream average in 2026.

## 7. Exit
FIXED 60-minute hold from fill. Never exit on state death — continuation persists past
model-state lapse (state-death exit: +5.4bps vs +54.6 2025; +1.0 vs +34.4 2026).
If capital-bound, 30m hold is the documented alternative (1.23 vs 0.91 bps per
capital-minute in 2025) — not deployed in v1.

## 8. Per-name & portfolio exposure
- 1 unit = $10k (v0); 1 concurrent position per ticker.
- Portfolio concurrency cap 10 names (2026 sim: ~4 concurrent on average, zero skipped).
- Single-name concentration: no name > ~1% of its own day's dollar volume — at $10k units
  this only binds on <$1M/day names; cum_dv pocket floor ($5M) handles most of it.
- 20bps RT planning cost; break-even floor 40bps (E6 2026 = +14.4bps @40).

## 9. Economics (research numbers, t+1 fills, 20bps RT)
| metric | 2025 (Aug–Dec) | 2026 (Jan–Mar) |
|---|---|---|
| E6 net/unit | +66.4bps | +34.4bps |
| E6 net/episode | +113.9bps | +54.1bps |
| entries/day | 6.2 | 5.4 |
| months ≥ 0 | 5/5 | 3/3 |
| win rate | 0.548 | 0.498 |
| $/day @ cap10×$10k | +$415 | +$194 |
Underlying event stream (before E6 thinning): +91bps/event 2025, +87bps 2026 @20bps.
Compounding shape: January-weighted (Jan 2026 +78bps/unit), positive every month tested.

## 10. Forward validation (what the paper bot records)
Per minute: visible list, per-candidate score, admission Y/N + reason.
Per trade: ticker, entry minute, cycle #, minutes-since-first-qual, rvol at entry, score,
fill, units, exit fill, net 60m return @20bps, true-fill comparison post-hoc.
Daily rollup. Success gate: pooled net ≥ +30bps/trade over ≥ 20 trading days at E6 flow
(2–10/day live expected), monthly sign consistent with the research pattern.
Known live frictions (disclosed): scanner membership noise (edge insensitive to ±2 ranks,
25% list drops), rvol baseline needs 20 sessions warm-up, label-complete-vs-full-population
gap (research full-set numbers are the live-relevant ones).
