# OOS Protocol — Frozen BEFORE any Aug–Dec evaluation (2026-09-01)

Dev facts (July, burned): D10 all-events net +8.4bps @20bps RT (+6.2bps t+1-entry),
0 @40bps; episode-equal wr gradient monotone 0.18→0.49; IC +0.047; D1 = −2.8% (avoid/short).
n=1,285 episodes; CI straddles 0. July proves RANKING, not absolute edge.

## Frozen setup
- Model: LightGBM depth-6 config in train_ml.py, trained 2025-05+06, early-stop on July dev
  (best_iter=57). Threshold: day-rank top-10% (score rank within et_date ≥ 0.9·n).
- Baseline model: ElasticNet (alpha=0.001, l1_ratio=0.2) same splits — must show same wr gradient.
- OOS months: 2025-08, 09, 10, 11, 12. ONE evaluation pass per model. No re-tuning after first peek.
  If any parameter is touched after the peek, 2025-11+12 are re-frozen as a second OOS.

## GO — standalone strategy is alive (proceed to Phase 6 sizing/archetypes)
All of, across the 5 OOS months (full population, all-events table, @20bps):
1. D10 net ≥ +3bps/month average AND D10 ≥ 0 in ≥4/5 months.
2. D10 t+1m-entry net ≥ 0/month average (executable entry).
3. wr gradient monotone (Spearman wr vs decile ≥ +0.8) in ≥4/5 months.
4. No single month with D10 net < −20bps.
5. ElasticNet shows the same D10≥D1 ordering (signal is not a tree artifact).

## PARTIAL — selection factor only (fold into existing strategies, no standalone)
Ranking alive (items 3+5 hold, IC>0 in ≥4/5 months) but absolute net fails (items 1/2/4).
Use: gate H008-style volume-matched entries / suppress D1–D5 entries; do not trade alone.

## NO-GO — kill ML v1
D10 ≤ 0 or non-monotone in ≥3/5 months. Record and stop; no rescue tuning on Aug–Dec.

## Secondary reads (context only, not gates)
- Short side: D1 short is an upper bound (borrow unrealistic on small caps).
- Capacity: ~58 episodes/day ⇒ D10 covers most; 1-trade-per-episode top-score variant tracked.
- Costs: @40bps reported for sensitivity; GO requires @20bps only (top-gainer liquid names).

Decision recorded in train_report json + STATE.md after the single pass.
