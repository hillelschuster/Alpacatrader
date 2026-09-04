# CANONICAL RESEARCH STATE — dominant-leader program (2026-09-04)

Inheritance contract: a new session reading THIS file + SOUL.md + factory/STATE.md tail
has everything needed. Detail lives in researches/07-dominant-leader-program.md.

## Thesis
Extreme market attention creates a distinct stock population whose price-action/flow
structures may carry different expectancy than ordinary stocks. Selection first, extraction
second. Profitability decides; no frozen strategy exists.

## CORRECTED facts (ET-anchored, causal harness; old UTC-clock numbers are VOID)
- Raw top-gainer watchlists ≈ 0 pooled (48d: top3 +17 / top5 +46bps, meds neg, hit ~.45).
- Selected-vs-rejected = coin flip (20-24/48d). $-vol-only selection dead.
- First-hour leader drift: directional only (144/28/102/34bps over 4 ET-checked days).
- Eventual-#1 minutes positive / tail ~0-or-negative: SUPPORTED directional, hindsight only.
- Rank-#1 chasing loses (only large-n month-stratified causal result). Rotation tops.
- Blocks heterogeneous (-507..+831); NO seasonal form (old summer-cold/fall-hot was clock artifact).
- Gap-fade monotonic (FILTRIX n=66,906); 40%+ unstable; 10-20% pocket = prior only.
- MFE always present (top5 P(MFE>100bps)=1.00, mean 2,193bps vs +46 fwd ≈48×).
- Emerging-(a) (out-top10@14:45 → top-5@15:30): additive lottery (22% non-overlap) but
  taxed -151bp/day union-wide → capped satellite only. #1 outside top-10@14:45 42% of days.
- RAPP rule: halt holes are part of momentum (12 bars by 10:00 ET, +49% day). Gates must
  tolerate halts (min_bars=10).

## NOT demonstrated (hypotheses, month-blocked proof required)
Separation hit-rates, hot-tape gates (n10/share move nothing pooled), health filters
(~none pooled), static float tiers, RVOL 2x/5x as alpha cutoffs, PM-high distance (~0),
scarcity (~0), pattern edges on own panel (public evidence ~0 unconditional).

## Targets (adopted)
Primary: mean MFE with MAE/drawdown gate (only stable rule-ranker, ρ=0.90 halves).
Co-target: FP-per-captured-opportunity (pooled ≥1.2 FP/hit even lenient).
STOP ranking by mean/median terminal fwd, hit-rate, sel-rej gaps (ρ≤0.33, sign-unstable).
Binding constraint: per-name {fwd,mfe,mae,obp} logging (landed in stagea --out).

## Active data
- data/clean_ohlcv_2025-*.parquet (session; 2025 files start 13:30 UTC in DST — use ET clock)
- data/backfill/clean_ohlcv_2026-*.parquet (has some premarket) + premarket_ohlcv_2025-MM (NEW,
  repaired+validated 325MB, assert maxUTC<870)
- data/forward/YYYY-MM-DD/ (live observer JSONL; Friday 2026-09-04 captured)
- Forward full-market replay = next-day historical backfill. NEVER run backfill_alpaca for
  the in-progress month (partial final poisons resume-skip).

## Active code
- factory/scripts/stagea_eval.py + replay_watchlist.py (ET clocks, causal gating, 1-bar lag)
- factory/scripts/forward_observe.py (ORDER-FREE; widened 50 snapshots; gain_open_anchored
  = parity field with harness open-anchored gain vs vendor prior-close gain)
- factory/scripts/backfill_premarket_2025.py (Int32-cast lesson: polars dt.hour is Int8)
- certify_month.py canonical for ranks (rank_day/extract_events deprecated headers).

## Known traps (do not re-learn)
UTC-vs-ET DST shift (EDT dates lost open hour); full-day candidate peek; start-stamp 1-bar
lookahead; i8 overflow in polars time math; missing tzdata on Windows (pure-UTC arithmetic);
duplicate background PIDs (verify via tasklist + log coherence); /tmp reaped aggressively
(write outputs to data/_scratch/); 2026-04 clean file absent locally (use backfill).

## Open questions (in order)
1. MFE-before-drawdown curves month-blocked; FP-penalized recall gate for Stage B.
2. Premarket-hypothesis retests on repaired 2025 (echo ratio, gap×catalyst, PM range×dir).
3. Friday+ forward sessions scored through harness.
4. Same-pattern expectancy inside selected vs rejected (E1 probe design proven, needs scale).
5. Day-state that marks hot tape (continuous score; binary gates failed pooled).
6. Hindsight-leader panel effect re-verified under ET clocks.
