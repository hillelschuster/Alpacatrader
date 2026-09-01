# PAPER BOT SPEC — ML top-gainer admission v0 (smallest live reproduction)

Scope: what must run live to reproduce the frozen research result. No framework, no sizing
engine, no live-risk layer — the research itself is the risk model at this stage.

## The frozen stack (nothing tunable below is chosen live)

| component | frozen value | source |
|---|---|---|
| model | `factory/artifacts/ml/model_v1.pkl` (LightGBM depth-6, best_iter=57) | train May+Jun 2025 |
| theta_fixed | 0.00115 (90th pct of May-Jul scores) | live_admission.py |
| admission | score-rank ≤ 2 among the current minute's visible top-gainer list AND score ≥ theta_fixed | M3, causal by construction |
| optional gate | rvol>4 & vwap_dist>0.03 & tod<270 (composite) | 2025 OOS, partially validated, confirmed on 2026-01..03 (eval_2026.py) |
| sequencing | S4: unit1 at first qualifying minute; unit2 at first later qualifying minute with score≥0.00340 and tod ≥ unit1+5m; unit3 same with tod ≥ unit2+20m | sequencing.py |
| hold | 60 minutes from fill; no late-day entries when entry+60m crosses 16:00 ET (entry cutoff 14:59) | label construction |
| cost model | 20–40bps RT budget | OOS: composite survives 40bps |

## Decision-time feature inventory (30 inputs — all computable from 1m bars)

Per candidate ticker at minute t (ET session grid):
1. `pct_gain_grid` — gain vs prior session close (scanner has it)
2. `rank` — position in current top-20 list (scanner has it)
3. `n_hod_breaks` — count of new intraday highs so far (running state)
4. `dip_5m`, `dip_depth_5m` — 5-min grid: low of last 5 real bars vs prior HOD (state)
5. `trap_reclaim` — H009: low pierced prior-HOD then close reclaimed (state)
6. `vwap_dist`, `above_vwap` — cumulative VWAP from 09:30 (state)
7. `dist_open`, `open_gap` — daily open; prior close (daily reference table)
8. `dist_hod`, `range_pos` — running HOD / day range (state)
9. `log_close`, `log_dollar_volume` — price + cum dollar volume (state)
10. `dv_5m_rate`, `dv_accel` — 5m dv / pace; accel vs prior 5m (state)
11. `rvol` — dv_5m_rate ÷ 20-session same-minute-of-day mean dv (needs a small per-ticker per-minute baseline table, rolling 20 sessions; store per (tod_min) mean dv per ticker, refresh daily)
12. `excess_gain` — gain minus next-best gainer's gain (needs full visible list — scanner)
13. `market_ret_5m` — equal-weight 5m return of the candidate universe (scanner-wide)
14. `tod_min`, `dow` — clock
15. `ret_1m..ret_30m` — time-based close returns
16. `realized_vol_15m` — std of 1m returns, 15m
17. `efficiency_30m` — |net move| / path length, 30m
18. `n_up_bars_15` — up-bar count, 15m

State needed: per-ticker intraday series (HOD, cum dv, VWAP, 15/30m windows), a 20-day dv
baseline table keyed by (ticker, tod_min), daily prev-close/open table, the live top-20 list.
No order-book data, no bid/ask, no fundamentals.

## What live reproduction deliberately changes vs research (disclosed)

- Research "events" = certified top-20/minute with n_bars≥30 full-day gate. Live scanner
  membership will differ slightly (different universe, latency) → M3's vis_rank is defined
  against whatever list is visible; expect modest membership noise, edge should be
  insensitive to list composition within ±2 ranks (research shows rank 1 vs 2 similar).
- Fills: research assumes next-1m-close fill. Paper bot records the SAME construction:
  entry price = close of the bar after the admission minute; exit = close 60 bars later.
- Label-complete subset selection (null t1 labels) disappears live — accept full-population
  expectancy (research full-set numbers are lower than label-complete: use full set).

## Paper ledger (the only deliverable of v0)

Per minute: visible list size, per-candidate score, admission decision (Y/N + why).
Per trade: ticker, entry minute, fill price, units (S4), add minutes/scores, exit price,
60m return net of assumed 20bps, then post-hoc true fill comparison. Daily: ledger rollup.
Success gate for forward paper: pooled net ≥ +30bps/trade over ≥ 20 trading days at
composite-gated flow (~80/day in OOS → expect 10–40/day live after scanner differences),
and month sign consistent with eval_2026.py monthly pattern.

## Explicitly out of scope for v0

Position sizing beyond equal units, portfolio concurrency caps, risk stops (research has
no stop simulation — 60m hold only), short legs, multiday holds, live model retraining.
