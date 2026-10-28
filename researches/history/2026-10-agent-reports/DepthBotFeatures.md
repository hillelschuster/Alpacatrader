# Depth bot feature feasibility / parity report

## Verdict

Bit parity passed on all three replay days. Model SHA-256 was verified as `c5493c6c043a22e544ca98aa4fb5620292d4a1aeb370f63f6492907334f0bf96`. `uv run --no-sync pytest tests/test_depth_bot_features.py -q`: 4 passed (three historical replays plus the chosen-arm trade fixture). `uv run --no-sync ruff check factory/scripts/depth_bot_features.py tests/test_depth_bot_features.py`: clean.

| Day | Panel feature rows | Liquidity-qualified scored rows | `pred_60 >= .030` signals | Admission/rank mismatches | Feature mismatches | Prediction/signal mismatches |
|---|---:|---:|---:|---:|---:|---:|
| 2023-01-19 | 826 | 549 | 4 | 0 | 0 / 26 columns | 0 / 0 |
| 2023-05-15 | 843 | 508 | 0 | 0 | 0 / 26 columns | 0 / 0 |
| 2025-02-04 | 1,067 | 757 | 1 | 0 | 0 / 26 columns | 0 / 0 |
| **Total** | **2,736** | **1,814** | **5** | **0** | **0 / 26 columns** | **0 / 0** |

The 2023-01-19 replay includes a trade under `thr300bps|p50` (the validation replay trade artifact contains one row for that arm). No 2024, 2025-01, or 2026-06..08 data was used.

## Parity details / root causes addressed

- Admission and rank snapshots match the panel for the panel-represented admitted universe on each snapshot. Snapshot ranking uses each symbol's latest close at/before `T-1`, including stale closes; the `et >= T-2` freshness rule gates admission, not ranking. Filtering stale bars before ranking had caused false top-10 substitutions and extra admissions. The implementation retains stale rank candidates and applies freshness only to admission.
- The panel's admission score is consumed from candidate snapshot JSON, serialized to six decimal places. Storing full precision for `admit_gain` caused per-row feature mismatches; the incremental module now applies the same six-decimal serialization before admission and feature construction. Ranking order remains based on full-precision score, then ticker ascending.
- All other path features call the panel's own `symbol_rows`; peer columns call `day_context`; model input uses `FEATURES_ALL`, `feature_matrix`, and `causal_liquidity`. Exact row keys, all 26 feature values (absolute tolerance `1e-9`), all qualified predictions (tolerance `1e-9`), and signal sets were asserted.

## Not reproducible without live prerequisites

The tested algorithm needs the complete eligible universe's consolidated SIP bars at each of the six checkpoints; a watchlist-only or exchange-partial feed cannot yield the same top-10 ranks, admission set, peer context, or consolidated dollar-volume gate. Live parity therefore depends on full-universe SIP availability and complete minute bars, not merely this module. This deliverable does not implement market-data acquisition, quote processing, orders, or the rest of the bot; those remain outside this feasibility-gate scope. No network calls, orders, or secrets were used.

## Files

- `factory/scripts/depth_bot_features.py`: incremental bars, six-checkpoint rank/admission state, causal panel feature construction, model verification/scoring, and threshold signals.
- `tests/test_depth_bot_features.py`: minute-by-minute raw-bar replay against frozen panel and depth-state artifacts for the three days above.
