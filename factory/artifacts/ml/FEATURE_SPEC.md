# ML Phase 1 — Feature & Label Spec (v1, for audit)

## Population / unit
- Rows = certified top-gainer events from `certification_2025-MM/events_topN.parquet` (May/June/July now; Aug–Dec after certification).
- Event = minute-bar where ticker is top-20 by pct_gain (rank over gap==1, universe-eligible, split-guard-excluded universe) AND dv≥$5M cum, n_bars≥30, exchange NYSE/NASDAQ/AMEX equity.
- Statistical unit for honesty: (ticker, et_date) episode. 383,237 events / 3,104 episodes / 1,015 tickers / 62 sessions (May–Jul). All splits chronological by day; never row-level.

## Splits
- Train: 2025-05 + 2025-06 (+2025-04 once certified)
- Validation/dev: 2025-07 (BURNED — influenced H006/H009 conclusions; used for early stopping + threshold choice only)
- Final OOS (untouched, certified tonight from fresh downloads): 2025-08, 09, 10, 11, 12
- Labels never cross sessions (fwd ≤60m, events after 15:00 have null 60m) → month boundaries need no purge; within-episode overlap handled by day-level grouping.

## Labels (from events parquet, computed by certify pipeline vs clean bars, exact t+h joins)
- fwd_ret_1/3/5/15/30/60m (raw close-to-close)
- mfe_60m / mae_60m
- derived: net_long_60m = fwd_ret_60m − 0.002 (20bps RT); net_long_15m/30m; net_short_60m = −fwd_ret_60m − 0.002; net @40bps variants
- primary modelling target v1: net_long_60m (continuous regression). Secondary: net_long_30m. Classification mirror: net_long_60m > 0.

## Features (all decision-time-safe, computed from clean bars ≤ t of the same session, or prior sessions)
Top-gainer state:
1. pct_gain (from prev_close, %)
2. rank (1–20)
3. gain_vel_5m = pct_gain − pct_gain@t−5m; gain_vel_15m; gain_accel = vel_5m − vel_15m (approx rank velocity via gain velocity; true cross-sectional rank history deferred)
4. rank_at_t_minus_5m / rank_at_t_minus_15m if cheap via per-day recompute (optional v1.1)

Path/trajectory:
5. ret_1m, ret_3m, ret_5m, ret_10m, ret_15m, ret_30m (close/close−1)
6. realized_vol_15m = std(1m rets, past 15m)
7. efficiency_30m = |sum 1m rets| / sum |1m rets| over past 30m (Kaufman-style)
8. range_pos = (close − session_low) / (session_high − session_low)
9. n_up_bars_15 / 15

Structure/HOD/VWAP:
10. dist_hod = close/rth_hod − 1
11. time_since_hod_min (et − hod_time)
12. n_hod_breaks (count of new session highs so far)
13. dip_depth_30m = min(low past 30m)/rth_hod − 1 (H009 trap depth proxy)
14. reclaim_flag = close > rth_hod@t−30m after dipping below it (H009 state at t; exact H009 defn in experiment_h009.py — reuse faithfully)
15. vwap_dist = close/vwap_cum − 1; above_vwap (0/1)
16. dist_open = close/open − 1; open_gap = open/prev_close − 1

Volume/participation:
17. log_dollar_volume (cum $vol)
18. dv_5m_rate = $vol(t−5m..t) / 5; dv_accel = dv_5m_rate / ($vol rate over prior 30m)
19. RVOL = cum$vol(t) / E[cum$vol(t)] (H008 baseline: trailing 20 sessions of ticker's own history, hourly bucket-end curve interpolated; code from experiment_h008.py — with 2025-03+04 downloads, May/June baselines complete)
20. vol_trend_15m: rolling 5m $vol rate slope

Context:
21. log_close (price level)
22. tod_min (minutes since 09:30)
23. dow (0–4)
24. market_ret_5m = cross-sectional median close(t)/close(t−5m)−1 over all RTH clean tickers that minute (breadth proxy, idiosyncratic vs market push)
25. market_ret_5m_rank_gap = pct_gain − market_ret_5m×100 (idiosyncratic excess)

Leakage guards: no feature uses data > t; rank/labels from certify pipeline are t-safe by construction (fwd_* excluded from features); hod_time/rth_hod are cum-max ≤ t; market_ret uses closes ≤ t only. MFE/MAE are labels only, never features.

## Models v1
- LightGBM regression on net_long_60m (and net_long_30m), small configs (depth 4–6, ≤600 trees, lr 0.05, min_data 200–500, early stop on July)
- Logistic/ElasticNet baseline on standardized features
- Evaluation: score decile → mean net return @20/40bps, wr, n (all events) + episode-deduped "one trade per ticker-day" (max score per episode) table; monotonicity of decile curve; daily PnL series, total PnL, per-day, max drawdown, monthly stability, long vs short
