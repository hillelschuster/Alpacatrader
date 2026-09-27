# ATLAS E1 — exhaustion-score release rule (verdict)

Producer `factory/scripts/basket_atlas_e1.py` (sha256 `83e2e8a5b986e4a8a2f5ff150ae0fd74cf592d310da6ae839d146f5ed9c654d7`) →
`factory/artifacts/basket/phase2/ATLAS/E1/E1.json` (sha256 `90e922ad98a47248bc15fe79915b2266a18aafa4947879c6a17b0d0dca6a3b95`, 2945642 bytes),
`E1/selftest.json` (sha256 `e075a20f7fd78980be1d643c4edd6d1fa276128f764cbe2a7907d496d60bfb1f`, 17 unit/integration cases,
PASS) and this file. Panel `ATLAS/panel.parquet` sha `2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488` —
1900432 rows, 6160 members, 273
terminal-censored members. Imported code (shas in `E1.json:provenance`):
`basket_atlas_fall.py` `67a9b2712a527faf…`,
`basket_atlas_ledger.py` `7f8686350ebe3000…`.
Run status **PASS**, 36 cells (9 arms x 2 folds x 2 frictions), wall
922s. **Reproducible:** two independent full runs of this producer are identical in
every cell, arm, grid curve, niche table and attribution (0 differences excluding
timestamp/runtime/provenance keys).

## Part E1a — unconditional release test

* **PRIMARY target** `final_high_now(t)` = 1 iff the running high at *t* is the session's final
  running high on a tape complete to the close, else 0 — defined at every completed bar; verified
  identical to the panel's `final_high_flag` on all 1,860,700 rows where the panel defines it
  (0 mismatches). Training population: 1453995 complete & uncensored post-fill bars with a new
  high already seen and the label defined; decision population 1458789 such bars
  across 4794 of 5887 complete members. Model:
  ridge logistic, 14 fall state features + exact clock-minute fixed effects, pooled over both
  families, machinery imported from `basket_atlas_fall.py`. Out-of-block **within-clock AUC
  0.8367** (block1→block2) / **0.8175** (block2→block1).
* **ABLATION only** the fall event-hazard P(this new high is the last), trained on new-high bars with
  decisions restricted to new-high bars: within-clock AUC 0.6806 / 0.6595.
* **Folds** `block_of` (frozen; two blocks): A = block1 train → block2 eval, B = block2 → block1.
* **Cross-fitting, two layers.** Evaluation: every evaluated bar is scored by the model fitted on the
  other block. Selection: every threshold (the dollar grid and every clock-minute bank) comes from an
  **inner day-level cross-fit inside the train block**, and the grid objective replays the ledger on
  the train block only. Proven in the artifact: the inner score arrays are finite on 0 rows outside
  their train block and the replay frame is train-block-only.
* **PRIMARY arm** one global threshold learned on the TRAIN block from ledger dollars (grid over
  quantiles 0.50…0.99 of the train-side score distribution, objective = train net @100 bps after
  removing the five largest-|day net| days, ties toward fewer releases), then frozen and applied to
  the evaluation block unchanged. Fold A chose q0.50
  (thr 0.843427, train net-after-top5 73.17, optimum on the grid's lower bound);
  fold B chose q0.58 (thr 0.906524, train net-after-top5 19.85).
* **SECONDARY rulers** clock-minute quantile arms q70/q80/q90 of the same score. **CONTROLS**
  `hold_flat` (the executable forced flat) and `giveback:10` (the panel's declared ruler).
* **Accounting** entirely `basket_atlas_ledger.py` (next-open execution, forced-flat precedence,
  censored members partitioned as unresolved, sleeve and independent-path views, day-clustered 95%
  CIs); every cell publishes the ledger's reconciliation counters;
  `run.checks.reconciliation_clean_every_cell` = True.
* **Kill criterion (pre-registered)** after removing the five largest-|day net| days: dead if net ≤
  hold **or** the destroyed giant tail (MFE from the release ≥ +300% and close above the release) per
  avoided dollar is not below the binding non-score control (`giveback:10`, same surviving days), in
  either fold.

### E1a results @100 bps (150 bps is the same picture at the higher friction scale)

| arm | fold | bps | releases | avoided $ | destroyed $ | net $ | net $ after top-5 | giant300/avoided | control ratio | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ablation_event_hazard_quantile_q70 | A | 100 | 1463 | 169.56 | 118.22 | 51.34 | 71.51 | 0.00933 | 0.01709 | survives |
| ablation_event_hazard_quantile_q70 | A | 150 | 1463 | 168.71 | 117.63 | 51.08 | 71.15 | 0.00933 | 0.01709 | survives |
| ablation_event_hazard_quantile_q70 | B | 100 | 2829 | 277.45 | 184.04 | 93.41 | 119.02 | 0.03739 | 0.03483 | KILLED |
| ablation_event_hazard_quantile_q70 | B | 150 | 2829 | 276.06 | 183.12 | 92.94 | 118.42 | 0.03739 | 0.03483 | KILLED |
| ablation_event_hazard_quantile_q80 | A | 100 | 1260 | 152.26 | 104.02 | 48.24 | 67.93 | 0.01038 | 0.01709 | survives |
| ablation_event_hazard_quantile_q80 | A | 150 | 1260 | 151.50 | 103.50 | 48.00 | 67.59 | 0.01038 | 0.01709 | survives |
| ablation_event_hazard_quantile_q80 | B | 100 | 2234 | 232.56 | 152.47 | 80.09 | 104.61 | 0.04278 | 0.03483 | KILLED |
| ablation_event_hazard_quantile_q80 | B | 150 | 2234 | 231.40 | 151.71 | 79.69 | 104.09 | 0.04278 | 0.03483 | KILLED |
| ablation_event_hazard_quantile_q90 | A | 100 | 905 | 123.27 | 81.30 | 41.97 | 55.77 | 0.01310 | 0.03582 | survives |
| ablation_event_hazard_quantile_q90 | A | 150 | 905 | 122.65 | 80.90 | 41.76 | 55.49 | 0.01310 | 0.03582 | survives |
| ablation_event_hazard_quantile_q90 | B | 100 | 1292 | 151.75 | 95.49 | 56.26 | 78.75 | 0.01892 | 0.01614 | KILLED |
| ablation_event_hazard_quantile_q90 | B | 150 | 1292 | 150.99 | 95.01 | 55.98 | 78.36 | 0.01892 | 0.01614 | KILLED |
| dollar_threshold | A | 100 | 1493 | 99.04 | 89.87 | 9.17 | 17.05 | 0.02954 | 0.03652 | survives |
| dollar_threshold | A | 150 | 1493 | 98.54 | 89.42 | 9.12 | 16.96 | 0.02954 | 0.03652 | survives |
| dollar_threshold | B | 100 | 2997 | 131.36 | 101.67 | 29.69 | 43.77 | 0.00000 | 0.02487 | survives |
| dollar_threshold | B | 150 | 2997 | 130.71 | 101.16 | 29.55 | 43.55 | 0.00000 | 0.02487 | survives |
| giveback:10 | A | 100 | 1631 | 162.49 | 126.18 | 36.32 | - | - | - | control |
| giveback:10 | A | 150 | 1631 | 161.68 | 125.55 | 36.14 | - | - | - | control |
| giveback:10 | B | 100 | 3315 | 282.37 | 194.70 | 87.68 | - | - | - | control |
| giveback:10 | B | 150 | 3315 | 280.96 | 193.72 | 87.24 | - | - | - | control |
| hold_flat | A | 100 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| hold_flat | A | 150 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| hold_flat | B | 100 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| hold_flat | B | 150 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| quantile_q70 | A | 100 | 1010 | 110.31 | 81.70 | 28.60 | 32.98 | 0.02620 | 0.06060 | survives |
| quantile_q70 | A | 150 | 1010 | 109.76 | 81.29 | 28.46 | 32.82 | 0.02620 | 0.06060 | survives |
| quantile_q70 | B | 100 | 1825 | 164.32 | 113.06 | 51.27 | 66.73 | 0.00199 | 0.02653 | survives |
| quantile_q70 | B | 150 | 1825 | 163.50 | 112.49 | 51.01 | 66.40 | 0.00199 | 0.02653 | survives |
| quantile_q80 | A | 100 | 831 | 92.43 | 65.18 | 27.25 | 25.63 | 0.03227 | 0.08035 | survives |
| quantile_q80 | A | 150 | 831 | 91.97 | 64.85 | 27.12 | 25.50 | 0.03227 | 0.08035 | survives |
| quantile_q80 | B | 100 | 1288 | 119.65 | 80.20 | 39.45 | 55.74 | 0.00288 | 0.02653 | survives |
| quantile_q80 | B | 150 | 1288 | 119.05 | 79.80 | 39.25 | 55.46 | 0.00288 | 0.02653 | survives |
| quantile_q90 | A | 100 | 593 | 69.22 | 44.30 | 24.92 | 22.81 | 0.01875 | 0.07047 | survives |
| quantile_q90 | A | 150 | 593 | 68.87 | 44.08 | 24.80 | 22.70 | 0.01875 | 0.07047 | survives |
| quantile_q90 | B | 100 | 683 | 65.34 | 37.46 | 27.89 | 34.11 | 0.00000 | 0.06438 | survives |
| quantile_q90 | B | 150 | 683 | 65.02 | 37.27 | 27.75 | 33.94 | 0.00000 | 0.06438 | survives |

### E1a verdicts

| arm | role | verdict | evidence |
|---|---|---|---|
| `dollar_threshold` | primary | SURVIVES | net after top-5 17.05 (A) / 43.77 (B); CI [-0.008682, 0.018682] / [0.001301, 0.013352] |
| `quantile_q70` | secondary_ruler | SURVIVES | net after top-5 32.98 (A) / 66.73 (B); CI [0.003648, 0.027545] / [0.005769, 0.019529] |
| `quantile_q80` | secondary_ruler | SURVIVES | net after top-5 25.63 (A) / 55.74 (B); CI [0.004277, 0.025441] / [0.003411, 0.016057] |
| `quantile_q90` | secondary_ruler | SURVIVES | net after top-5 22.81 (A) / 34.11 (B); CI [0.004877, 0.022298] / [0.002858, 0.010903] |
| `ablation_event_hazard_quantile_q70` | ablation | **KILLED (fold B, both frictions)** | tail clause in fold B: giant300/avoided 0.03739 >= control 0.03483 (the delta clause passed in those cells) |
| `ablation_event_hazard_quantile_q80` | ablation | **KILLED (fold B, both frictions)** | tail clause in fold B: giant300/avoided 0.04278 >= control 0.03483 (the delta clause passed in those cells) |
| `ablation_event_hazard_quantile_q90` | ablation | **KILLED (fold B, both frictions)** | tail clause in fold B: giant300/avoided 0.01892 >= control 0.01614 (the delta clause passed in those cells) |
| `hold_flat` | control | CONTROL | zero by construction (the executable forced flat) |
| `giveback:10` | control | CONTROL | net 36.32 (A) / 87.68 (B); dedup-view net 32.29 / 75.87; duplicate-priority swing 9.03 / 11.71 |

### E1a readings

1. **Every score arm beats holding** in both folds after top-5-day removal; the quantile arms'
   day-clustered mean-delta CIs exclude zero (q70 A [0.003648, 0.027545], B [0.005769, 0.019529]).
   The primary arm is weakest: fold A CI [-0.008682, 0.018682] includes 0.
2. **Unconditionally, no score arm beats the `giveback:10` ruler on dollars.** Fold A: ablation_event_hazard_quantile_q70, ablation_event_hazard_quantile_q80, ablation_event_hazard_quantile_q90
   beat it on the same surviving days and dollar_threshold, quantile_q70, quantile_q80, quantile_q90 do not (primary 17.05 vs 44.36;
   q70 32.98 vs 40.59; q80 25.63 vs 35.19; q90 22.81 vs 33.69).
   Fold B: ablation_event_hazard_quantile_q70 beat it; dollar_threshold, quantile_q70, quantile_q80, quantile_q90, ablation_event_hazard_quantile_q80, ablation_event_hazard_quantile_q90 do not (primary 43.77 vs 105.23;
   q70 66.73 vs 105.98). Dedup-view nets confirm the ordering (ruler 32.29 (A) /
   75.87 (B) vs primary 5.89 / 24.95); the declared duplicate rule is worth 9.03 to the
   ruler's sleeve total in fold A.
3. **The tail clause kills the ablation family**: ablation_event_hazard_quantile_q70, ablation_event_hazard_quantile_q80, ablation_event_hazard_quantile_q90 die in fold B
   (giant300 per avoided dollar above the control's on the same days); surviving: dollar_threshold, quantile_q70, quantile_q80, quantile_q90.
   The delta clause never fires (every arm is positive after top-5-day removal).
4. **The train-learned threshold does not transfer cleanly**: fold A's grid objective
   (73.17) exceeds its evaluation net (9.17) and the optimum sits on the grid's lower bound.
5. The every-bar level target is far more predictable than the event hazard
   (0.837/0.818 vs 0.681/0.660 within clock) but the extra
   discrimination does not become dollars — the level label is largely the clock restated.

## Protocol ambiguities resolved (recorded in `protocol.ambiguity_resolutions`)

1. **Fold letters** — "blocks 0-1 train → 2-3 eval" maps onto the two blocks `block_of` produces
   (A = block1 → block2, B = block2 → block1), verified equal to the panel's block column.
2. **Decision population** — complete & uncensored bars past the fill with a new high already seen;
   the score itself is emitted for every complete bar.
3. **Threshold pools** — the train block's decision population (ablation: its own new-high decision
   population), scored by the inner day-level cross-fit; minutes with <30 train rows fall back to the
   pooled train quantile.
4. **Censoring** — never a decision gate or selection input; censored members cannot carry a label,
   so they are excluded from training and the ledger partitions them as unresolved.
5. **"avoided"/"destroyed"** — the ledger's definitions verbatim; the brief's giant definition
   (MFE ≥ +300% and close above the release) is reported beside the ledger's own giant sets and is the
   one used for the tail ratio. Both views appear in every cell.
6. **`hold_flat`'s 0/0 tail ratio** — reported as null; the binding tail control is `giveback:10`.
7. **Top-5-day removal** — the five days with the largest |day net| dropped from the arm and from the
   control on the same surviving days.
8. **Families** — one pooled cross-sectional model; per-family out-of-block AUCs are in the artifact.


## Part E1b — conditional check: does any score arm beat the ruler inside a predicted-dispersion niche?

**Question.** does any every-bar score arm beat giveback:10 inside a niche defined by CAUSALLY PREDICTED forward dispersion, even though it loses unconditionally?

**Splitter (frozen before any outcome is read).** ridge least squares (fall.multi_effect_ridge_linear) on log1p(fwd_range_30) with the 14 fall state features + exact clock-minute fixed effects, fitted pooled over both families; fitted on
fwd_range_30 (a label-side target) and scored on causal state only. Train side:
inner day-level cross-fit inside the train block (the B1 split discipline): each day half is scored by the model fitted on the other half, so the tercile cuts see no evaluation row. Eval side: predicted by the model fitted on the whole train block. Splitter quality on the
evaluation block, measured after the cuts were frozen: Spearman
A: 0.601496, B: 0.574636; R2 on
log1p A: 0.101623, B: 0.141366.

**The niche.** A member's FIRST state bar is systematically a high-predicted-dispersion moment (the
first new high past the fill) and putting the niche there collapsed ~97% of members into the top
tercile, so the niche is the MEDIAN of the member's state-bar terciles under the same frozen cuts —
arm-independent, causal, invariant to the monotone score transform, and it partitions the same judged
members for every arm, so the terciles decompose the unconditional increment. The cuts stay the frozen
train-side terciles; the eval rows' terciles come from the train-block dispersion model.

| fold | train block | cuts | eval row terciles | eval members per tercile | eval members without an assigned niche |
|---|---|---|---|---|---|
| A | block1 | [0.515469, 0.523272] | [111151, 132822, 199430] | [339, 644, 531] | 428 |
| B | block2 | [0.51947, 0.529892] | [429460, 338537, 247389] | [1465, 1498, 317] | 938 |

**Increments @100 bps** (sleeve / dedup): arm net minus `giveback:10` net on the arm's own surviving
days, with the control restricted to the same members. A positive dedup increment in the same tercile
in BOTH folds is what the pre-registered closure rule looks for.

| arm | tercile | n members A / B | increment sleeve A / B | increment dedup A / B | arm net A / B | control net A / B | giant300/avoided (arm) A / B | positive in both folds |
|---|---|---|---|---|---|---|---|---|
| `dollar_threshold` | 0 | 335 / 1458 | -0.484015 / -30.625625 | **-0.484015 / -30.590512** | -2.262039 / 3.503255 | -1.778024 / 34.12888 | 0.0 / 0.0 | - |
| `dollar_threshold` | 1 | 637 / 1490 | 1.958133 / -3.741062 | **1.752169 / -2.886222** | -9.293994 / 9.141394 | -11.252127 / 12.882455 | 0.0 / 0.0 | - |
| `dollar_threshold` | 2 | 516 / 308 | 8.633367 / 34.18351 | **8.796307 / 32.983028** | 28.603815 / 31.123227 | 19.970448 / -3.060283 | 0.038591 / 0.0 | YES |
| `quantile_q70` | 0 | 335 / 1460 | 6.114415 / 8.308517 | **6.114415 / 8.202558** | 4.336391 / 42.577713 | -1.778024 / 34.269196 | 0.0 / 0.0 | YES |
| `quantile_q70` | 1 | 637 / 1489 | 6.308143 / -4.096766 | **5.875569 / -3.100413** | -4.176605 / 9.751039 | -10.484748 / 13.847805 | 0.0 / 0.0 | - |
| `quantile_q70` | 2 | 517 / 309 | 17.389715 / 17.660187 | **17.381033 / 15.950525** | 32.824503 / 14.400894 | 15.434788 / -3.259293 | 0.036108 / 0.007259 | YES |
| `quantile_q80` | 0 | 334 / 1460 | 5.015479 / -4.3941 | **5.015479 / -4.452957** | 3.269431 / 29.875095 | -1.746048 / 34.269196 | 0.0 / 0.0 | - |
| `quantile_q80` | 1 | 639 / 1489 | 10.444276 / -3.394494 | **10.009248 / -1.7836** | -1.033497 / 10.453311 | -11.477773 / 13.847805 | 0.0 / 0.0 | - |
| `quantile_q80` | 2 | 517 / 309 | 12.51077 / 18.672507 | **13.568862 / 17.481404** | 23.395653 / 15.413215 | 10.884883 / -3.259293 | 0.042459 / 0.00974 | YES |
| `quantile_q90` | 0 | 333 / 1459 | 1.945966 / -20.823387 | **1.945966 / -20.846446** | 0.430699 / 13.414812 | -1.515268 / 34.238199 | 0.0 / 0.0 | - |
| `quantile_q90` | 1 | 639 / 1492 | 11.27389 / -5.027355 | **10.839986 / -2.623927** | -0.404568 / 11.18646 | -11.678459 / 16.213815 | 0.0 / 0.0 | - |
| `quantile_q90` | 2 | 518 / 306 | 12.913231 / 23.578175 | **13.427264 / 21.439316** | 22.785294 / 9.510646 | 9.872063 / -14.067529 | 0.023405 / 0.0 | YES |
| `ablation_event_hazard_quantile_q70` | 0 | 335 / 1457 | 8.599972 / 25.959857 | **8.599972 / 25.648499** | 6.825973 / 60.209877 | -1.773999 / 34.250021 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q70` | 1 | 630 / 1492 | 13.196392 / -4.980337 | **13.25741 / -4.724913** | 5.054126 / 11.233478 | -8.142266 / 16.213815 | 0.0 / 0.0 | - |
| `ablation_event_hazard_quantile_q70` | 2 | 522 / 309 | -6.670573 / -7.991634 | **-5.843712 / -7.748389** | 20.262805 / -7.93183 | 26.933377 / 0.059804 | 0.018975 / 0.221159 | - |
| `ablation_event_hazard_quantile_q80` | 0 | 335 / 1457 | 8.489292 / 10.559273 | **8.489292 / 10.330496** | 6.715293 / 44.809294 | -1.773999 / 34.250021 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q80` | 1 | 630 / 1492 | 13.327656 / 3.864221 | **13.543833 / 5.423399** | 5.18539 / 20.078037 | -8.142266 / 16.213815 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q80` | 2 | 522 / 309 | -3.275886 / -0.716986 | **-2.476792 / -0.009382** | 23.657491 / -0.657182 | 26.933377 / 0.059804 | 0.019655 / 0.201197 | - |
| `ablation_event_hazard_quantile_q90` | 0 | 335 / 1459 | 5.463086 / -14.294563 | **5.463086 / -14.343712** | 3.758868 / 19.834317 | -1.704218 / 34.12888 | 0.0 / 0.0 | - |
| `ablation_event_hazard_quantile_q90` | 1 | 631 / 1490 | 13.32187 / 2.806326 | **13.376662 / 2.199792** | 5.079059 / 17.400582 | -8.24281 / 14.594256 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q90` | 2 | 521 / 309 | 2.531063 / 17.760336 | **4.682265 / 16.096842** | 24.742996 / 18.748859 | 22.211933 / 0.988523 | 0.02188 / 0.059073 | YES |

**Tercile 2 (highest predicted dispersion), dedup increments of the every-bar arms:**
`dollar_threshold` 8.796307 (A) / 32.983028 (B); `quantile_q70` 17.381033 (A) / 15.950525 (B); `quantile_q80` 13.568862 (A) / 17.481404 (B); `quantile_q90` 13.427264 (A) / 21.439316 (B).
**Their giant300 dollars per avoided dollar vs the control on those same days:**
`dollar_threshold` arm 0.038591 vs control 0.068384 (A), arm 0.0 vs control 0.132427 (B); `quantile_q70` arm 0.036108 vs control 0.113579 (A), arm 0.007259 vs control 0.140802 (B); `quantile_q80` arm 0.042459 vs control 0.152122 (A), arm 0.00974 vs control 0.140802 (B); `quantile_q90` arm 0.023405 vs control 0.132911 (A), arm 0.0 vs control 0.348436 (B).

**Pre-registered closure verdict.** Rule: E1 (score-based release) is economically closed unless some tercile shows a positive dedup increment for a score arm in BOTH folds
Terciles positive (dedup) in both folds at 100 bps: ablation_event_hazard_quantile_q70 tercile 0, ablation_event_hazard_quantile_q80 tercile 0, ablation_event_hazard_quantile_q80 tercile 1, ablation_event_hazard_quantile_q90 tercile 1, ablation_event_hazard_quantile_q90 tercile 2, dollar_threshold tercile 2, quantile_q70 tercile 0, quantile_q70 tercile 2, quantile_q80 tercile 2, quantile_q90 tercile 2.
Arms holding such a tercile: ablation_event_hazard_quantile_q70, ablation_event_hazard_quantile_q80, ablation_event_hazard_quantile_q90, dollar_threshold, quantile_q70, quantile_q80, quantile_q90.
Restricted to the every-bar arms (primary + the three clock-quantile rulers):
dollar_threshold, quantile_q70, quantile_q80, quantile_q90.

**E1 is NOT closed by this test**: the rule asks for one tercile with a positive dedup increment in both folds for at
least one score arm at 100 bps; the pairs above meet it, so the rule does NOT close E1. Restricted to the every-bar arms:
NOT closed either. At 150 bps the same
pairs are positive (ablation_event_hazard_quantile_q70 tercile 0, ablation_event_hazard_quantile_q80 tercile 0, ablation_event_hazard_quantile_q80 tercile 1, ablation_event_hazard_quantile_q90 tercile 1, ablation_event_hazard_quantile_q90 tercile 2, dollar_threshold tercile 2, quantile_q70 tercile 0, quantile_q70 tercile 2, quantile_q80 tercile 2, quantile_q90 tercile 2), consistent with 100 bps.
