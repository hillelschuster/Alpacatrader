# Adaptive depth plateau grid

## Method and interpretation

Reused the adaptive-depth scorer, causal quote gate, $1,000 ticket, h60 scheduled exit, and 15-minute cooldown. Thresholds .020/.025/.030/.035/.040/.050 × participation .25/.50/.75/1.00 × arrival 250ms/1s × 3/5 slots. Validation (2023) is the selection set; 2025-02 through 2026-05 is the previously explored late block, not a pristine holdout. P&L is known-fill contribution; unknown executions are excluded. Heatmaps show $/session at 25 bps; full cost rungs and $/calendar day for all 96 cells are in results.json. Fill metrics and positive months are at 25 bps.

## Validation heatmaps ($/session @25 bps)

### Validation: arrival 250 ms · 3 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | -8.84 | -10.20 | -12.46 | -13.78 |
| 0.025 | +0.36 | +2.76 | +3.27 | +3.25 |
| 0.030 | +0.80 | +1.36 | +1.06 | +1.27 |
| 0.035 | +0.36 | +0.23 | +0.00 | -0.24 |
| 0.040 | -1.10 | -1.68 | -1.66 | -1.81 |
| 0.050 | -0.84 | -1.41 | -1.57 | -1.70 |

### Validation: arrival 250 ms · 5 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | -8.84 | -10.20 | -12.46 | -13.78 |
| 0.025 | +0.36 | +2.76 | +3.27 | +3.25 |
| 0.030 | +0.80 | +1.36 | +1.06 | +1.27 |
| 0.035 | +0.36 | +0.23 | +0.00 | -0.24 |
| 0.040 | -1.10 | -1.68 | -1.66 | -1.81 |
| 0.050 | -0.84 | -1.41 | -1.57 | -1.70 |

### Validation: arrival 1000 ms · 3 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | -6.61 | -8.46 | -10.80 | -11.84 |
| 0.025 | -0.53 | +1.36 | +0.94 | +1.12 |
| 0.030 | +1.39 | +2.26 | +2.30 | +2.64 |
| 0.035 | +0.07 | -0.01 | -0.37 | -0.71 |
| 0.040 | -1.19 | -1.85 | -1.86 | -1.95 |
| 0.050 | -0.86 | -1.45 | -1.60 | -1.74 |

### Validation: arrival 1000 ms · 5 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | -6.61 | -8.46 | -10.80 | -11.84 |
| 0.025 | -0.53 | +1.36 | +0.94 | +1.12 |
| 0.030 | +1.39 | +2.26 | +2.30 | +2.64 |
| 0.035 | +0.07 | -0.01 | -0.37 | -0.71 |
| 0.040 | -1.19 | -1.85 | -1.86 | -1.95 |
| 0.050 | -0.86 | -1.45 | -1.60 | -1.74 |

## Late heatmaps ($/session @25 bps)

### Late: arrival 250 ms · 3 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | +3.72 | +7.35 | +8.00 | +8.94 |
| 0.025 | +2.05 | +5.04 | +4.73 | +5.84 |
| 0.030 | +2.52 | +5.95 | +6.85 | +8.59 |
| 0.035 | +2.03 | +3.72 | +3.21 | +4.15 |
| 0.040 | +0.63 | +1.52 | +2.46 | +2.75 |
| 0.050 | -0.02 | +0.61 | +0.66 | +0.45 |

### Late: arrival 250 ms · 5 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | +3.72 | +7.35 | +8.00 | +8.94 |
| 0.025 | +2.05 | +5.04 | +4.73 | +5.84 |
| 0.030 | +2.52 | +5.95 | +6.85 | +8.59 |
| 0.035 | +2.03 | +3.72 | +3.21 | +4.15 |
| 0.040 | +0.63 | +1.52 | +2.46 | +2.75 |
| 0.050 | -0.02 | +0.61 | +0.66 | +0.45 |

### Late: arrival 1000 ms · 3 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | +3.49 | +5.28 | +10.18 | +12.62 |
| 0.025 | +3.57 | +4.84 | +9.13 | +11.32 |
| 0.030 | +2.75 | +5.59 | +9.89 | +12.38 |
| 0.035 | +1.64 | +4.35 | +8.14 | +10.33 |
| 0.040 | +1.12 | +2.37 | +3.90 | +4.37 |
| 0.050 | -0.02 | +0.61 | +1.33 | +1.27 |

### Late: arrival 1000 ms · 5 slots

| Threshold | p25 | p50 | p75 | p100 |
|---:|---:|---:|---:|---:|
| 0.020 | +3.49 | +5.28 | +10.18 | +12.62 |
| 0.025 | +3.57 | +4.84 | +9.13 | +11.32 |
| 0.030 | +2.75 | +5.59 | +9.89 | +12.38 |
| 0.035 | +1.64 | +4.35 | +8.14 | +10.33 |
| 0.040 | +1.12 | +2.37 | +3.90 | +4.37 |
| 0.050 | -0.02 | +0.61 | +1.33 | +1.27 |

## Choices

Validation-best maximizes validation $/calendar day at 25 bps. Robust maximizes the minimum validation 25 bps $/calendar day over immediate one-coordinate neighbours. The baseline is the tested thr.030/p.50/250ms/3-slot arm.

| Choice | Cell | Val $/session | Late $/session | Val $/calendar day | Late $/calendar day |
|---|---|---:|---:|---:|---:|
| Baseline | `thr300bps|p50|a250|s3` | +1.36 | +5.95 | +0.94 | +4.11 |

### Cost ladder and fill diagnostics: Baseline `thr300bps|p50|a250|s3`

Values are $/session ($/calendar day) at 0/25/50/100 bps; remaining columns are 25 bps.

| Block | 0 bps | 25 bps | 50 bps | 100 bps | Fills | Median fill | Top-5 share | Positive months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Validation | +1.73 (+1.20) | +1.36 (+0.94) | +1.00 (+0.69) | +0.27 (+0.18) | 53 | -5.49 | 465.3% | 4/12 |
| Late | +6.60 (+4.56) | +5.95 (+4.11) | +5.30 (+3.66) | +3.99 (+2.75) | 122 | -9.21 | 124.6% | 10/16 |
| Validation best | `thr250bps|p75|a250|s3` | +3.27 | +4.73 | +2.26 | +3.27 |

### Cost ladder and fill diagnostics: Validation best `thr250bps|p75|a250|s3`

Values are $/session ($/calendar day) at 0/25/50/100 bps; remaining columns are 25 bps.

| Block | 0 bps | 25 bps | 50 bps | 100 bps | Fills | Median fill | Top-5 share | Positive months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Validation | +3.89 (+2.69) | +3.27 (+2.26) | +2.65 (+1.84) | +1.42 (+0.98) | 79 | -15.70 | 240.0% | 5/12 |
| Late | +5.91 (+4.08) | +4.73 (+3.27) | +3.56 (+2.46) | +1.20 (+0.83) | 196 | -11.12 | 185.1% | 7/16 |
| Robust max-min | `thr300bps|p25|a250|s5` | +0.80 | +2.52 | +0.55 | +1.74 |

### Cost ladder and fill diagnostics: Robust max-min `thr300bps|p25|a250|s5`

Values are $/session ($/calendar day) at 0/25/50/100 bps; remaining columns are 25 bps.

| Block | 0 bps | 25 bps | 50 bps | 100 bps | Fills | Median fill | Top-5 share | Positive months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Validation | +1.04 (+0.72) | +0.80 (+0.55) | +0.55 (+0.38) | +0.06 (+0.04) | 53 | -2.75 | 525.4% | 4/12 |
| Late | +3.01 (+2.07) | +2.52 (+1.74) | +2.03 (+1.40) | +1.05 (+0.73) | 124 | -4.67 | 217.3% | 9/16 |

## Plateau verdict

The centre thr.030/p.50/250ms/3-slot has six immediate one-coordinate neighbours (threshold/participation adjacent, or alternate latency/slots):
- `thr250bps|p50|a250|s3`: +2.76 $/session @25
- `thr300bps|p25|a250|s3`: +0.80 $/session @25
- `thr300bps|p50|a1000|s3`: +2.26 $/session @25
- `thr300bps|p50|a250|s5`: +1.36 $/session @25
- `thr300bps|p75|a250|s3`: +1.06 $/session @25
- `thr350bps|p50|a250|s3`: +0.23 $/session @25

Verdict: not a convincing plateau / fragile edge. The centre has modest positive validation mean but few positive months and a negative median fill; top-five concentration exceeds 100% because aggregate net P&L is small relative to a few wins and offsetting losses. The validation-best cell is selected from 96 variants and therefore is not evidence of a non-overfit live edge; late was already inspected. The robust cell is the defined max-min-neighbour selection, not proof of durability. No live-order recommendation.

## Artifacts

- Producer: `factory/scripts/alpha_depth_plateau.py`
- Results/all 96 cells: `/home/hillel/alpha-data/open-search-v1/depth_plateau/results.json`
- Incremental scored states and per-day replay/trade artifacts: `/home/hillel/alpha-data/open-search-v1/depth_plateau/