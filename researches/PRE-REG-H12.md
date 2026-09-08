# PRE-REG-H12 — Hot-Window Pattern Mining (top-5 causal leaderboard, raw 1-min features)

Registered: 2026-09-08 (before any H12 model run). Status: ACTIVE experiment.

## Question
Among the ACTUAL top-5 gainers visible at each moment of the hot window
(9:45–10:30 ET), does recent raw 1-min OHLCV action discriminate a tradeable
short-term opportunity (first-touch +400bps before −200bps within 30 min,
next-bar-open entry)? NOT EOD prediction. NOT hindsight eventual runners.

## Population / sampling (causal, scanner semantics)
- Snapshots: t ∈ {585, 590, …, 630} ET (9:45–10:30, every 5 min), per trading day.
- Eligibility (harness `snapshot()` contract, unchanged): ≥10 session bars by t
  (bars et ≤ t−1), first session open $1–50, prev_close exists, not split-suspect
  (po/prev_close outside [0.5, 2.0] excluded).
- Leaderboard: rank eligible by scanner gain = pc/prev_close − 1 (close at t−1
  vs prior session close). Take top-5. Open-anchored variant parked (only if
  primary fails AND a distinct a-priori reason emerges — not as rescue).
- Sample = (date, t, ticker). Multiple snapshots of same ticker-day allowed
  (features/time differ); economic check also reported deduped per ticker-day
  (earliest top-decile snapshot) as diagnostic.

## Features (~22, raw only, from bars et ∈ [570, t−1])
n bars, price level (log pc), gain vs open, gain vs prev close, gap,
run-up so far (ph/po−1), pullback from high (pc/ph−1), dist from VWAP,
ret last 1/5/15/30 bars, avg bar range last 10, up-bar frac last 10,
consecutive up bars, last-bar volume / session avg volume, recent-10-bar
per-bar volume vs session avg, log session dollar volume, MFE/MAE so far
(vs po), t (minutes since midnight ET). No indicators, no engineered stacks.
Missing early-window values stay NaN (LightGBM handles natively).

## Targets (entry = open of bar et = t, next-bar-open fill, start-stamped)
Window = bars et ∈ [t, t+30]. Require ≥10 bars in window AND bar et=t exists,
else drop sample.
- T1 (primary, binary): 1 iff high ≥ entry×1.04 occurs strictly before
  low ≤ entry×0.98 within window. Same-bar both-touch → 0 (adverse-first
  conservative). Never-touched → 0.
- T2 (economic): (last close in window / entry − 1) × 10000, bps.
- Store mfe30/mae30 (window) for opportunity diagnostics.

## Model
LightGBM binary classifier on T1, FIXED hyperparams, no tuning (tuning = rescue
theater): num_leaves=31, n_estimators=300, lr=0.05, min_child_samples=50,
feature_fraction=0.9, bagging 0.8, seed=7. Dev eval = leave-one-month-out
(LOMO) over dev pool: train 9 months, score held-out month, pooled OOS scores
→ AUC + score-decile table of T2.

## Splits
- Dev: 2025-03..2025-12 (10 months).
- Collision: 2026-01..2026-03 (3 months, unseen for this lane). Model trained
  on ALL dev months only.
- 2026-04..08: optional extension ONLY if collision gate passes (burns the
  months sealed for P2 — ask user first).

## Gates (pre-registered)
- DEV GATE (to open collision): pooled LOMO AUC ≥ 0.55 AND pooled OOS top-decile
  mean T2 net of 100bps friction ≥ +30bps.
- COLLISION GATE (H12 survives): AUC ≥ 0.53 AND top-decile net ≥ +30bps pooled
  AND top-decile net > 0 in ≥2 of 3 months.
- Friction: 100bps (current doctrine).
- Any gate fails → lane DEAD, recorded honestly in HYPOTHESES.md/STATE.md.
  No reformulation on the same data under the H12 banner ("new formulation +
  more power only" doctrine).

## Machinery constraints
Samples cached incrementally per day (data/cache_h12/, resumable), producer
script = factory/scripts/hot_window_ml.py committed. Artifacts:
factory/artifacts/hot_window_ml_H12_*.json. No sophistication manufacture.
