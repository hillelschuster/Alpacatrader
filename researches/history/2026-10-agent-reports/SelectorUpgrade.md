# h60 selector upgrade: adaptive-depth execution

## Verdict
**KEEP CURRENT HEAD; neither tested alternative is a replacement.** The compact candidate's positive validation result is based on only eight known fills and fails the requested parameter plateau test (adjacent threshold 0.015 is negative and threshold 0.030 has no fills). The monotone-constrained candidate produces no validation fills at the selected cell. Current-head executed-return deciles are not monotonic. The incumbent itself has strong 2023 threshold fragility, despite positive result at the 0.030 selection point; therefore “keep” means no evidence-backed replacement, not validation as robust/live-ready.

## Fit and method
Reviewed `alpha_open_learned.py`: fit-only window is 2021-05..2022-12, training rows `t%15==0`, filled-proxy entries, known h60 gross label, causal-liquidity qualified; clip target to [-0.5, 2.0] for fitting only. Model has 26 features (22 panel + 4 same-session peer context), no ticker/date IDs; incumbent fixed LGBMRegressor uses 15 leaves, 300 min data/leaf, learning rate .03, 200 estimators, 2 threads, seed 20261008, deterministic/row-wise. Fit rebuilt on 102,265 eligible rows, observed dates 2021-05-03..2022-12-30. No 2023 refit. Compact = 7 leaves, 600 min data/leaf, 150 estimators; monotone-compact adds `+1` constraint to all 26 features (deliberately broad/simple stress test). Same depth-lane ask/bid quote execution, book, and threshold/participation grid applied to all.

Validation uses 2023 only; threshold/participation selection is the producer's validation objective, known actual-touch contribution $/panel session at 25bps. Late (2025-02..2026-05, previously explored) is reported after selection only. Protected windows were not accessed. Main comparison is for the validation-selected arm per head; rates below are known contribution per panel session (250 validation / 332 late). Cost rungs 0/25/50/100bps are additional costs on observed ask/bid, and unknown executions are excluded from known numerator.

## Selected view results
| Head | Selected arm | Val 0/25/50/100 bps $/session | Val fills / unknown | Late 0/25/50/100 bps $/session | Late fills / unknown |
|---|---|---:|---:|---:|---:|
| Current | 0.030 / p50 | +1.729 / +1.364 / +0.998 / +0.267 | 53 / 8 | +6.602 / +5.949 / +5.296 / +3.991 | 122 / 20 |
| Compact | 0.020 / p25 | +0.675 / +0.634 / +0.592 / +0.510 | 8 / 0 | +1.625 / +1.548 / +1.471 / +1.316 | 22 / 2 |
| Monotone compact | 0.015 / p10 (tie/default) | 0 / 0 / 0 / 0 | 0 / 1 | -0.148 / -0.158 / -0.168 / -0.188 | 6 / 0 |

25bps selected-arm diagnostics:
| Head | Block | $/calendar day (span) | Median known fill | Top-5 share of known net | Positive months |
|---|---|---:|---:|---:|---:|
| Current | Val | +0.944 | -$5.49 | 465% | 4/12 |
| Current | Late | +4.106 | -$9.21 | 125% | 10/16 |
| Compact | Val | +0.439 | -$8.04 | 250% | 3/6 traded months |
| Compact | Late | +1.068 | -$12.53 | 220% | 6/12 traded months |
| Monotone compact | Val | $0.000 | n/a | n/a | 0/0 |
| Monotone compact | Late | -$0.109 | -$9.11 | 32% | 2/5 traded months |

Calendar-day denominators: validation 361; late 481. Top-5 shares can exceed 100% when other fills offset the top winners; compact estimates are very small-sample and not comparable as robust expectancy. Threshold neighbor check (selected p cell at 25bps): current 0.015/p50 -$5.22/session, 0.020/p50 -$10.20, 0.030/p50 +$1.36; compact 0.015/p25 -$0.66, 0.020/p25 +$0.63, 0.030/p25 $0; monotone 0.015/p10 $0, 0.020/p10 $0, 0.030/p10 $0. No positive neighboring-threshold plateau across any selector.

## Incumbent calibration
Panel-proxy deciles are equally sized within each block and cover all qualified states with known gross_60; table is mean `gross_60` (fractional return), decile 0 = lowest score. **This is model-label calibration, not quote execution.**

| Decile | Val proxy | Late proxy |
|---:|---:|---:|
| 0 | -1.507% | -1.070% |
| 1 | -0.549% | -0.019% |
| 2 | -0.268% | +0.106% |
| 3 | +0.000% | +0.093% |
| 4 | +0.078% | +0.184% |
| 5 | +0.012% | +0.101% |
| 6 | +0.054% | +0.180% |
| 7 | +0.152% | +0.072% |
| 8 | +0.159% | +0.095% |
| 9 | +0.016% | +0.249% |

Late top decile is the largest proxy bucket, but neither block is monotonically increasing. The very high score top decile is particularly weak versus deciles 7-8 in validation.

Actual executed trade-return buckets for audited incumbent `thr300bps|p50` (known conditional fills only for mean return/net; includes all funded trade rows in bucket counts, UNKNOWN shown separately; actual ask-entry/bid-exit after 25bps residual):

| Block | Pred bucket | Trades / known / unknown | Mean known return | Mean net $/fill |
|---|---|---:|---:|---:|
| Val | Q1 low | 16 / 14 / 2 | -0.073% | +$5.43 |
| Val | Q2 | 15 / 11 / 4 | +6.730% | +$13.09 |
| Val | Q3 | 15 / 14 / 0 | +1.326% | +$30.37 |
| Val | Q4 high | 16 / 14 / 2 | -3.902% | -$21.74 |
| Late | Q1 low | 36 / 30 / 6 | +2.795% | +$8.44 |
| Late | Q2 | 35 / 28 / 7 | +3.881% | +$15.86 |
| Late | Q3 | 35 / 30 / 5 | +8.389% | +$45.21 |
| Late | Q4 high | 36 / 34 / 2 | -0.367% | -$2.30 |

Executed returns rise through Q3 then fall in the highest prediction bucket in both blocks; current head is **not reliably calibrated at its top end**. Per-fill means are conditional on known fills; UNKNOWN is not silently valued as cash.

## Files and reproducibility
- Producer: `factory/scripts/alpha_selector_upgrade.py`
- Full machine-readable results: `~/alpha-data/open-search-v1/selector_upgrade/results.json`
- Candidate model bundles: `~/alpha-data/open-search-v1/selector_upgrade/{current,compact,monotone_compact}.joblib`
- Candidate per-day state/replay parts: `~/alpha-data/open-search-v1/selector_upgrade/<candidate>/states/` and `replay/{validation,confirmation}/`
- Existing incumbent executed calibration source: `~/alpha-data/open-search-v1/adaptive_depth_sizing/replay/<block>/*.trades.parquet`

The script's 25/50/100/0 values, all nine grid cells, and full replay statistics are in results.json. It now supports `--calibration-only` to add the incumbent's executed-return buckets to a previously generated result file. No live/paper orders placed. Checks were not run.
