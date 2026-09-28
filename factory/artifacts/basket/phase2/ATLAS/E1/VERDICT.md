# ATLAS E1 — exhaustion-score release rule (verdict)

Producer `factory/scripts/basket_atlas_e1.py` (sha256 `1305c17149b7b6f075d650a5b0ed40041037eac20e85d9a3e21372f03be055b9`) →
`factory/artifacts/basket/phase2/ATLAS/E1/E1.json` (sha256 `d33cddb28f2a844abbe1f1e43bbf457ed9678cb07fa5daa669f78d6c045a6ac0`, 2952320 bytes),
`E1/selftest.json` (sha256 `0b782b2e5e11c9942a15531b067300685435230bf1fe2db7fb845e5ef02318f0`, 18 unit/integration cases,
PASS) and this file. Panel `ATLAS/panel.parquet` sha `2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488` —
1900432 rows, 6160 members, 273
terminal-censored members. Imported code (shas in `E1.json:provenance`):
`basket_atlas_fall.py` `67a9b2712a527faf…`,
`basket_atlas_ledger.py` `7f8686350ebe3000…`.
Run status **PASS**, 36 cells (9 arms x 2 folds x 2 frictions), wall
928s. **Reproducible:** two independent full runs of this producer are identical in
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
evaluation block, after the cuts were frozen: Spearman
A: 0.601496, B: 0.574636; R2 on
log1p A: 0.101623, B: 0.141366.

**The niche (corrected).** The conditioning statistic is the predicted-dispersion tercile **at the
arm's own decision bar** (its trigger bar) under the frozen train-side cuts — the last row the arm is
allowed to know about. Members the arm never released have no decision bar and are excluded from the
conditional table (counted per cell in the artifact). A future-perturbation guard runs on the real
data for every fold and arm — scrambling every tercile strictly AFTER the decision bar leaves the
statistic identical while perturbing the decision bar itself changes it — and
`run.checks.e1b_lookahead_guards_all_passed` = True.

> **Retraction.** An earlier revision of this block conditioned on the member-level MEDIAN
> predicted-dispersion tercile over the member's state bars. That statistic reads bars *after* the
> release decision (87-99% of its top-tercile members were top-tercile only because of post-decision
> bars), so its positive niche increments were a **look-ahead artifact**. The median statistic has
> been deleted from the code path, not kept under an invalid label; the numbers in this section are
> the corrected ones, and the corrected tercile-2 cells reproduce the debug pass's values exactly.

| fold | train block | cuts | eval row terciles | eval members with a decision bar / without | guard |
|---|---|---|---|---|---|
| A | block1 | [0.515469, 0.523272] | [111151, 132822, 199430] | 1082 / 333 | 7/7 arms pass |
| B | block2 | [0.51947, 0.529892] | [429460, 338537, 247389] | 1905 / 919 | 7/7 arms pass |

**Increments @100 bps** (sleeve / dedup): arm net minus `giveback:10` net on the arm's own surviving
days, with the control restricted to the same members.

| arm | tercile | n members A / B | increment sleeve A / B | increment dedup A / B | arm net A / B | control net A / B | giant300/avoided (arm) A / B | positive in both folds |
|---|---|---|---|---|---|---|---|---|
| `dollar_threshold` | 0 | 379 / 1603 | 9.311128 / 21.48488 | **9.311128 / 21.188703** | -0.676386 / -0.576727 | -9.987515 / -22.061607 | 0.0 / 0.0 | YES |
| `dollar_threshold` | 1 | 298 / 847 | 3.317388 / -15.078182 | **3.255449 / -14.912696** | -5.412006 / 9.323252 | -8.729394 / 24.401434 | 0.0 / 0.0 | - |
| `dollar_threshold` | 2 | 798 / 660 | -3.818433 / -19.060929 | **-3.498236 / -16.454273** | 23.136175 / 35.021351 | 26.954609 / 54.08228 | 0.033911 / 0.0 | - |
| `quantile_q70` | 0 | 15 / 111 | -0.586146 / -8.978341 | **-0.586146 / -8.978341** | -0.061335 / 0.472216 | 0.524811 / 9.450556 | 0.0 / 0.0 | - |
| `quantile_q70` | 1 | 44 / 175 | -1.748875 / -11.207998 | **-1.748875 / -10.960808** | -0.449916 / 1.748533 | 1.298958 / 12.956531 | 0.0 / 0.0 | - |
| `quantile_q70` | 2 | 934 / 1525 | -1.270642 / -18.012948 | **-1.021778 / -14.980943** | 33.495541 / 64.508898 | 34.766183 / 82.521846 | 0.026704 / 0.002101 | - |
| `quantile_q80` | 0 | 6 / 84 | -0.83575 / -10.170145 | **-0.83575 / -9.776792** | 0.149722 / 0.732452 | 0.985472 / 10.902597 | 0.0 / 0.0 | - |
| `quantile_q80` | 1 | 35 / 139 | -2.222376 / -13.535289 | **-2.222376 / -13.196228** | 0.303878 / -0.949706 | 2.526254 / 12.585583 | 0.0 / 0.0 | - |
| `quantile_q80` | 2 | 772 / 1056 | -17.718184 / -30.107354 | **-16.271326 / -26.129586** | 25.177988 / 55.958875 | 42.896172 / 86.066229 | 0.032843 / 0.003067 | - |
| `quantile_q90` | 0 | 4 / 49 | -0.752841 / -8.784943 | **-0.752841 / -8.784943** | -0.059665 / 0.20703 | 0.693176 / 8.991973 | None / 0.0 | - |
| `quantile_q90` | 1 | 19 / 76 | -2.324343 / -9.596337 | **-2.324343 / -9.596337** | -0.011413 / 0.994725 | 2.312931 / 10.591062 | 0.0 / 0.0 | - |
| `quantile_q90` | 2 | 556 / 550 | -35.448477 / -42.050476 | **-31.581266 / -38.051688** | 22.882502 / 32.910162 | 58.330979 / 74.960638 | 0.018982 / 0.0 | - |
| `ablation_event_hazard_quantile_q70` | 0 | 6 / 21 | 0.567454 / 1.00587 | **0.567454 / 1.00587** | 0.024194 / 0.006347 | -0.543261 / -0.999523 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q70` | 1 | 32 / 97 | 1.291034 / 2.117624 | **1.291034 / 2.130228** | -0.130614 / -1.018539 | -1.421648 / -3.136163 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q70` | 2 | 730 / 1745 | 27.899017 / 43.043435 | **27.785933 / 41.785024** | 34.893403 / 68.968531 | 6.994386 / 25.925096 | 0.017648 / 0.008235 | YES |
| `ablation_event_hazard_quantile_q80` | 0 | 5 / 23 | 0.472888 / 0.963227 | **0.472888 / 0.963227** | 0.024194 / 0.000494 | -0.448694 / -0.962733 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q80` | 1 | 30 / 98 | 1.299408 / 3.20307 | **1.299408 / 3.210758** | 0.156952 / -1.452293 | -1.142457 / -4.655362 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q80` | 2 | 704 / 1506 | 39.34497 / 59.408666 | **38.349238 / 56.386346** | 37.740486 / 70.95945 | -1.604483 / 11.550785 | 0.017539 / 0.020147 | YES |
| `ablation_event_hazard_quantile_q90` | 0 | 5 / 27 | 0.623451 / 3.433722 | **0.623451 / 3.433722** | 0.077095 / 0.043232 | -0.546355 / -3.39049 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q90` | 1 | 17 / 66 | 0.809557 / 7.298774 | **0.809557 / 7.291753** | 0.24267 / -0.256806 | -0.566887 / -7.55558 | 0.0 / 0.0 | YES |
| `ablation_event_hazard_quantile_q90` | 2 | 577 / 926 | 49.81004 / 60.407692 | **48.747079 / 55.653297** | 36.614088 / 57.823033 | -13.195952 / -2.584659 | 0.019139 / 0.01177 | YES |

**Tercile 2 (highest predicted dispersion), dedup increments of the every-bar arms** (sleeve in
brackets): `dollar_threshold` -3.498236 [-3.818433] (A, n 798) / -16.454273 [-19.060929] (B, n 660); `quantile_q70` -1.021778 [-1.270642] (A, n 934) / -14.980943 [-18.012948] (B, n 1525); `quantile_q80` -16.271326 [-17.718184] (A, n 772) / -26.129586 [-30.107354] (B, n 1056); `quantile_q90` -31.581266 [-35.448477] (A, n 556) / -38.051688 [-42.050476] (B, n 550). **All 16 every-bar tercile-2 cells are negative**
(4 arms x 2 folds x 2 frictions), so the violent-state
niche hypothesis is dead on the corrected statistic. The clock-quantile rulers q70/q80/q90 have
**no positive dedup increment in any tercile in either fold** (36 cells,
all negative). The primary arm's only positive conditional
cells are the calm tercile (t0): 9.311128 (A, n 379) / 21.188703 (B, n 1603); its
tercile-1 cells are 3.255449 / -14.912696 and its tercile-2 cells -3.498236 / -16.454273. The ablation family
(the event-hazard object on its own new-high support) is positive in all 18 (arm, fold, tercile) cells;
it is nevertheless dead: E1a's pre-registered tail clause kills all three ablation rulers in fold B,
and in this corrected niche its fold-B tercile-2 giant300 dollars per avoided dollar
(0.008235) is above the control's on the same days (0.006154).

**Closure.** Pre-registered rule: E1 (score-based release) is economically closed unless some tercile shows a positive dedup increment for a score arm in BOTH folds. Verdict on that rule: **the letter of the pre-registered rule is NOT satisfied**. Pairs positive in both folds =
ablation_event_hazard_quantile_q70 tercile 0, ablation_event_hazard_quantile_q70 tercile 1, ablation_event_hazard_quantile_q70 tercile 2, ablation_event_hazard_quantile_q80 tercile 0, ablation_event_hazard_quantile_q80 tercile 1, ablation_event_hazard_quantile_q80 tercile 2, ablation_event_hazard_quantile_q90 tercile 0, ablation_event_hazard_quantile_q90 tercile 1, ablation_event_hazard_quantile_q90 tercile 2, dollar_threshold tercile 0; restricted to the every-bar arms = dollar_threshold.
Read against the question that was asked, the answer is unambiguous: **E1 as a *violent-state* release
rule is economically closed** — no every-bar arm beats `giveback:10` in the high-dispersion tercile in
either fold at either friction, no score arm beats it unconditionally, and the earlier positive niche
is retracted as a look-ahead artifact. The cells that keep the *letter* of the rule open are the
primary arm's calm-tercile (t0) cells and the ablation family; neither survives E1a's tail clause
reading (the primary nets 17.05 after top-5-day removal in fold A with a CI that includes 0, the ablation
family is killed in fold B).
