# PAPER BOT SPEC — ML top-gainer admission v0 (smallest live reproduction)

Scope: what must run live to reproduce the frozen research result. No framework, no sizing
engine, no live-risk layer — the research itself is the risk model at this stage.

## The frozen stack (nothing tunable below is chosen live)

| component | frozen value | source |
|---|---|---|
| model | `factory/artifacts/ml/model_v1.pkl` (LightGBM depth-6, best_iter=57) | train May+Jun 2025 |
| theta_fixed | 0.00115 (90th pct of May-Jul scores) | live_admission.py |
| admission | score-rank ≤ 2 among the current minute's visible top-gainer list AND score ≥ theta_fixed (M3) | live_admission.py |
| stream gate | rvol>4 & vwap_dist>0.03 & tod_min<270 (composite; entry minutes ≤ 328) | eval_2026.py (2025→2026 transferred) |
| exposure | E6: first entry at first qualified minute with rvol>8; re-enter after each 60m hold at next qualified minute with rvol>8; 1 position per ticker at a time; unlimited re-entry while re-qualifying | exposure_design.py (PRE_REG_EXPOSURE.md: 2026 +34.4bps/unit, 3/3 months ≥0) |
| exit | FIXED 60m hold from fill — do NOT exit on state death (X_death +5.4bps vs +54.6 in 2025; +1.0 vs +34.4 in 2026: continuation persists past model-state lapse) | exposure_design.py |
| cost model | 20bps RT planning number; 40bps break-even floor | OOS/2026 evals |

Superseded (documented, not deployed): S4 score-threshold scale-ins (+77bps/unit 2025 →
+18bps 2026, did not transfer); E1 first-entry-only (weaker than re-entry structures in
both years); state-death exits (destroy edge); 15/30m holds (inferior unless capital-bound).

## Decision-time feature inventory (30 inputs — all computable from 1m bars)

Per candidate ticker at minute t (ET session grid, tod_min = minutes since 09:30):

1. `pct_gain_grid` — close/prev_session_close − 1, in percent
2. `rank` — position in current minute's top-20 certified list
3. `n_hod_breaks` — running count of minutes making a new intraday high (from 09:30)
4. `dip_5m`, `dip_depth_5m` — low of last 5 REAL bars (no forward-fill) vs max(prior-HOD·0.997, VWAP); depth vs prior HOD, clipped to [−1, 0]
5. `trap_reclaim` — dip_5m AND close back above prior HOD (H009 feature)
6. `vwap_dist` — close/cumulative-VWAP − 1, VWAP = cum(dv_bar)/cum(volume) from session start (09:30); `above_vwap` = close > vwap
7. `dist_open` — close/session_open − 1; `open_gap` — session_open/prev_close − 1
8. `dist_hod` — close/running-HOD − 1; `range_pos` — (close − day-low)/(day-high − day-low), real-bar extremes
9. `log_close` — ln(1 + close); `log_dollar_volume` — ln(1 + cum dollar volume)
10. `dv_5m_rate` — cum-dv(t) − cum-dv(t−5), per minute; `dv_accel` — dv_5m_rate(t) ÷ mean(dv over minutes t−35..t−6), null when prior window is 0
11. **`rvol` — cum dollar volume at t ÷ 20-session expected cum dollar volume at t.** The
    expectation is built from the last 20 sessions' cum-dv at bucket ends
    [10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 16:00], rolling mean shifted one session
    (no same-day leakage), then LINEARLY INTERPOLATED between bucket ends to minute t.
    rvol is null at 09:30 (zero expectation). This is a CUMULATIVE participation ratio,
    not a 5-minute rate ratio. (Spec v0 was wrong here; matches build_features.rvol_attach.)
12. `market_ret_5m` — cross-sectional MEDIAN 5m return over the ENTIRE clean universe
    (all tickers with bars that day), not the top-gainer list. (Spec v0 was wrong here.)
13. `excess_gain` — pct_gain_grid − 100·market_ret_5m (gain minus the universe-median 5m
    move, cumulative-vs-close convention). (Spec v0 was wrong here.)
14. `tod_min`, `dow` — clock
15. `ret_1m..ret_30m` — close/Close(t−k) − 1 on the forward-filled grid
16. `realized_vol_15m` — rolling std of 1m real-bar returns, 15 bars, min 10
17. `efficiency_30m` — |Σ r1| / Σ|r1| over 30 bars, min 15
18. `n_up_bars_15` — count of r1>0 over last 15 real bars, min 10

State needed: per-ticker intraday series (HOD, cum dv, VWAP, 5/15/30m windows), a
20-session bucket-end cum-dv baseline table keyed by (ticker, bucket_end), daily
prev-close/session-open table, the live top-20 list. No order book, no fundamentals.

## What live reproduction deliberately changes vs research (disclosed)

- Research "events" = certified top-20/minute with n_bars≥30 full-day gate. Live scanner
  membership will differ slightly → M3's vis_rank is defined against whatever list is
  visible; edge is insensitive to list composition within ±2 ranks and to 25% random
  list drops (research-verified).
- Fills: research assumes next-1m-close fill. Paper bot records the SAME construction:
  entry price = close of the bar after the admission minute; exit = close 60 bars later.
- Label-complete subset selection (null t1 labels) disappears live — accept full-population
  expectancy (research full-set numbers are lower than label-complete).
- Live rvol needs 20 sessions of the ticker's own history before it is trustworthy; until
  the baseline table warms up, rvol>8 gating will under-admit (disclosed, not fixable in v0).

## Paper ledger (the only deliverable of v0)

Per minute: visible list size, per-candidate score, admission decision (Y/N + why).
Per trade: ticker, entry minute, cycle number, minutes-since-first-qual, rvol at entry,
score, fill price, units (v0 = 1), exit price, 60m return net of assumed 20bps, post-hoc
true-fill comparison. Daily: ledger rollup.
Success gate for forward paper: pooled net ≥ +30bps/trade over ≥ 20 trading days at
E6 flow (~5.4 composite re-entries/day in 2026 research; expect 2–10/day live), and
month sign consistent with research monthly pattern.

## Economics (research numbers, t1 fills, 20bps RT)

- Composite stream: 2025 +91bps/event @20 (83/day); 2026 +87bps (70/day), +62bps @40.
- E6 episodes: 2026 +34.4bps/unit, +54.1bps/episode, 5.4 entries/day, wr 0.498.
- Concurrency sim (cap 10 names × $10k units): mean +$194/day over 57 trade-days
  (p10 −$1,262 / p90 +$1,400); capital committed averages ≈4 positions ≈ $40k; zero
  skipped entries in 2026. Not capacity-bound at small size; binding constraint is
  per-name spread on <$5 names (survives 100bps RT) — cap single-name units accordingly.
- Monthly shape: January-dominated (+78bps/unit Jan 2026), positive all 3 months.

## Explicitly out of scope for v0

Position sizing beyond equal units, portfolio concurrency optimization, risk stops
(research has no stop simulation — 60m hold only), short legs, multiday holds, live
model retraining.
