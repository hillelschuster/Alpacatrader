# RVOL CORRUPTION FINDING + CORRECTED VERDICT (2026-09-02)

## The bug

`build_features.rvol_attach()` computed the rvol **numerator** with
`(close*volume).cum_sum().over(ticker, et_date)` on **unsorted** rows. Every other
helper in the file sorts by `timestamp` first; `rvol_attach` was the only exception.
The clean parquets are stored with **shuffled** row order (verified: ATEC 2025-08-01
stored order is not timestamp-monotone), so the "cumulative" dollar volume was a
**stored-row-order partial sum** — effectively a random fraction of day volume.
ATEC rel-2: numerator 140.3M vs true 5.86M (24×).

Evidence: `tests/test_feature_parity.py` (live engine vs research replay) — 29/30
feature columns matched exactly, rvol did not; my live engine's value matched the
*intended* math (sorted cumsum) exactly.

Fix: one line — `month_clean.sort("timestamp")` before the cumsum
(factory/scripts/build_features.py, committed with this report).

## Scope

- ALL 11 feature parquets (2025-05 … 2026-03) had polluted rvol.
- Only the `rvol` column affected. All other features sort before windowing; verified
  unchanged (feature-parity test compares all 30; 29 matched pre-fix, cum_dv matched).
- Affected downstream: model training (rvol is 17.6% gain importance), M3 theta stream,
  composite gate (rvol>4), E6 gate (rvol>8), the 2026 frozen verdict, exposure design.

## Rebuild (v2, same configs, same protocol)

- 11 feature months rebuilt. rvol distribution now sane: p50=4.17, p99=122 (was
  p50≈254, max≈45,000).
- **model_v2.pkl** retrained: May+Jun train / Jul dev, same LightGBM config,
  best_iter=56 (v1: 57). OOS Aug–Dec D10 t1-entry all net20: −13/−5/−17/−14/−15 bps —
  same negative sign as v1 (edge was never in the raw stream).
- Composite nested selection: **same gates selected** (rvol>4 & vwap_dist>0.03 &
  tod<270). FROZEN Nov+Dec: **+86.1bps @20bps** (v1 polluted: +102.6bps).
- Dev-recalibrated thresholds (May–Jul p90/p97 of v2 scores): **theta=0.00098,
  theta_hi=0.00259** (v1: 0.00115/0.00340).
- Exposure E6_rvol8 (THETA=0.00098):
  - **2025: +45.0bps/unit, wr 0.528, 2.6/day** (v1 polluted: +66.4)
  - **2026 FROZEN: −62.8bps/unit, wr 0.447, 2.6/day** (v1 polluted: +34.4)
  - Economics 2026 @cap10/$10k: mean/day **−$168** (v1: +$194)
- eval_2026 with v2 + theta=0.00098: all cuts negative
  (M3 −25.0, M3×composite −32.5, pocket −46.1bps @20bps).

## Verdict (corrected)

1. **The 2026 frozen verdict does NOT survive the rvol fix.** E6 on 2026 = −62.8bps/unit.
   The prior +34.4bps/unit was an artifact of corrupted rvol leaking noise into gates.
   n=123 entries, 3 months — small sample, but the sign flip is unambiguous across
   every structure variant (E1/E2/E3/E5/E6 all negative in 2026).
2. **2025 (derivation year) remains positive but weaker**: E6 +45bps/unit, composite
   FROZEN +86bps. Selection-year numbers are in-sample by construction.
3. The theta/model pipeline itself is sound; the data-layer bug is fixed at the source
   and the parity test now guards it.
4. **Paper bot: still build it** — but its purpose changes from "rehearse +34bps" to
   "measure the corrected strategy live". There is currently **no validated positive
   2026-period edge to deploy**. The honest read: composite+86bps (2025 val) is the only
   surviving signal, and it was selected on 2025.

## Files

- Fixed: factory/scripts/build_features.py (sort before cumsum)
- Rebuilt: data/ml_features/features_2025-05…2026-03 (data/ untracked)
- New: factory/artifacts/ml/model_v2.pkl, train_report_v2.json,
  feature_importance_v2.json, train_v2.log, composite_v2.log,
  exposure_2025_v2.log, exposure_2025_v2theta.log,
  exposure_2026_frozen_v2.log, exposure_2026_frozen_v2theta.log, eval_2026_v2.log
- Test: tests/test_feature_parity.py (live engine == research replay, incl. E6 entries)
- Script env-overrides added: exposure_design.py --model/--env THETA;
  eval_2026.py MODEL_PATH env.
