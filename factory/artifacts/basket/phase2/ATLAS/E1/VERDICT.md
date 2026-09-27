# ATLAS E1a — exhaustion-score release rule (verdict)

Producer `factory/scripts/basket_atlas_e1.py` (sha256 `f8d9388f060d6a25765fcf5bf80b3e690982885a2680e48679aa4967c3041256`) →
`factory/artifacts/basket/phase2/ATLAS/E1/E1.json` (sha256 `810fe1f26f8289cb0240a027197fb748fa7ee3ac7b4eed58808ab0e0e3f18324`, 2872291 bytes),
`E1/selftest.json` (sha256 `af259287ed38654544732557c27ce1ac7c580568875def6250c5d274934a0300`, 15 unit/integration cases, PASS)
and this file. Panel `ATLAS/panel.parquet` sha `2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488` — 1900432 rows,
6160 members, 273 terminal-censored members.
Imported code (shas in `E1.json:provenance`): `basket_atlas_fall.py`
`67a9b2712a527faf…`, `basket_atlas_ledger.py`
`7f8686350ebe3000…`.
Run status **PASS**, 36 cells (9 arms x 2 folds x 2 frictions), wall 910s.
**Reproducible:** two independent full runs of this producer are identical in every cell, arm, grid
curve and attribution (0 differences excluding timestamp/runtime/provenance keys).

## What was tested

* **PRIMARY target** `final_high_now(t)` = 1 iff the running high at *t* is the session's final
  running high on a tape complete to the close, else 0 — defined at every completed bar; verified
  identical to the panel's `final_high_flag` on all 1,860,700 rows where the panel defines it
  (0 mismatches). Training population: 1453995 complete & uncensored post-fill bars with a new
  high already seen and the label defined. Decision population (where the rule may release):
  1458789 such bars across 4794 of 5887 complete
  members. Model: ridge logistic, 14 fall state features + exact clock-minute fixed effects, pooled
  over both families, machinery imported from `basket_atlas_fall.py`. Out-of-block **within-clock AUC
  0.8367** (block1→block2) / **0.8175** (block2→block1).
* **ABLATION only** the fall event-hazard P(this new high is the last), trained on new-high bars with
  decisions restricted to new-high bars: within-clock AUC 0.6806 / 0.6595.
* **Folds** `block_of` (frozen; exactly two blocks exist): A = block1 train → block2 eval,
  B = block2 train → block1 eval.
* **Cross-fitting, two layers.** Evaluation: every evaluated bar is scored by the model fitted on the
  other block. Selection: every threshold — the primary dollar grid and every clock-minute bank — is
  built from an **inner day-level cross-fit inside the train block** (the block's sorted days alternate
  between two halves, each half scored by the model fitted on the other half), and the grid's training
  objective is evaluated by replaying the ledger on the train block only. The artifact proves the
  selection never reads the evaluation block: the inner score arrays are finite on 0 rows outside
  their train block, and the replay frame is train-block-only (`run.checks.selection_never_reads_the_eval_block`).
* **PRIMARY arm** one global threshold learned on the TRAIN block from ledger dollars: grid over
  quantiles 0.50…0.99 of the train-side (inner cross-fit) score distribution, objective = train net
  @100 bps after removing the five largest-|day net| days, ties broken toward fewer releases, then
  frozen and applied to the evaluation block unchanged. Fold A chose q0.50
  (thr 0.843427, train net-after-top5 73.17, 3228/4218 train members released;
  the optimum sits on the grid's lower bound). Fold B chose q0.58
  (thr 0.906524, train net-after-top5 19.85, 1441/1942).
* **SECONDARY rulers** clock-minute quantile arms q70/q80/q90 of the same score (<30 train rows in a
  minute → the pooled train quantile).
* **CONTROLS** `hold_flat` (the executable forced flat) and `giveback:10` (the panel's declared ruler).
* **Accounting** entirely `basket_atlas_ledger.py`: decision on a completed bar, execution at the next
  printed bar's open, forced-flat precedence, censored members partitioned as unresolved (identical
  census in every arm and control), sleeve and independent-path (dedup) views, day-clustered 95% CIs
  (sandwich and 1000-rep bootstrap), frictions 100 and 150 bps. Every cell publishes the ledger's own
  reconciliation counters (`fwd_mfe_*`, `forced_flat_*`, `level_ret_*`, `tail_class_*`,
  `giveback_locator_*`, `n_members_unscored_other`, censored observed-exit partition);
  `run.checks.reconciliation_clean_every_cell` = True.
* **Kill criterion (pre-registered)** after removing the five largest-|day net| days: dead if net ≤
  hold **or** the destroyed giant tail (MFE from the release ≥ +300% and close above the release) per
  avoided dollar is not below the binding non-score control (`giveback:10`, same surviving days), in
  either fold.

## Results @100 bps (150 bps is the same picture at the higher friction scale; full table in the artifact)

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

## Verdicts

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

## What the numbers say

1. **Every score arm beats holding** in both folds after top-5-day removal, and the quantile arms'
   day-clustered mean-delta CIs exclude zero (e.g. q70 A [0.003648, 0.027545], B [0.005769, 0.019529]).
   The primary arm is the weakest of them: fold A mean-delta CI [-0.008682, 0.018682] includes 0.
2. **On the same surviving days, which arms beat the `giveback:10` ruler's own net?**
   Fold A: ablation_event_hazard_quantile_q70, ablation_event_hazard_quantile_q80, ablation_event_hazard_quantile_q90 beat it; dollar_threshold, quantile_q70, quantile_q80, quantile_q90 do not
   (primary 17.05 vs ruler 44.36 on the same days; q70 32.98 vs 40.59;
   q80 25.63 vs 35.19; q90 22.81 vs 33.69). Fold B: ablation_event_hazard_quantile_q70 beat it;
   dollar_threshold, quantile_q70, quantile_q80, quantile_q90, ablation_event_hazard_quantile_q80, ablation_event_hazard_quantile_q90 do not (primary 43.77 vs ruler 105.23; q70 66.73 vs 105.98; q80 55.74 vs 105.98;
   q90 34.11 vs 97.84). The every-bar score arms do preserve the +300% tail better per avoided
   dollar than the ruler on the same days (primary 0.02954 vs 0.03652 in fold A), but they avoid far fewer dollars
   (fold A: ruler avoided 162.49 vs primary 99.04, q70 110.31). Dedup-view (independent-path) nets confirm the
   ordering: ruler 32.29 (A) / 75.87 (B) vs primary 5.89 / 24.95 and q70 25.07 / 44.11; the declared
   duplicate rule (keep A_pm first, then the lower entry_rank) is worth 9.03 to the ruler's sleeve total in fold A and 11.71 in fold B, so the
   ordering above is not an artifact of double-counted shared paths.
3. **The tail clause is what kills arms here**: the ablated event-hazard rulers
   (ablation_event_hazard_quantile_q70, ablation_event_hazard_quantile_q80, ablation_event_hazard_quantile_q90) die in fold B — they buy their dollars with a
   giant +300% tail per avoided dollar above the ruler's on the same surviving days
   (q70 0.03739, q80 0.04278, q90 0.01892 against the control's
   0.03483/0.03483/0.01614 on each arm's own surviving days). Surviving arms:
   dollar_threshold, quantile_q70, quantile_q80, quantile_q90. The delta clause never fires (every arm is positive after
   top-5-day removal).
4. **The train-learned threshold does not transfer cleanly.** Fold A's grid objective
   (73.17) exceeds its evaluation net (9.17), and the grid optimum sits on the
   lower bound of the declared 0.50…0.99 range ("release earlier" keeps improving on train).
5. The every-bar level target is far more predictable than the event hazard
   (0.837/0.818 vs 0.681/0.660 within clock) but the extra
   discrimination does not become dollars — the level label is largely the clock restated.

## Protocol ambiguities resolved (recorded in `protocol.ambiguity_resolutions`)

1. **Fold letters** — "blocks 0-1 train → 2-3 eval" maps onto the two blocks `block_of` produces:
   A = block1 → block2, B = block2 → block1 (verified equal to the panel's block column on every member).
2. **Decision population** — complete & uncensored bars past the fill with a new high already seen;
   the score itself is emitted for every complete bar.
3. **Threshold pools** — the train block's decision population (ablation: its own new-high decision
   population, i.e. without the extra label-availability filter), scored by the inner day-level
   cross-fit; minutes with <30 train rows fall back to the pooled train quantile (counted and listed).
4. **Censoring** — never a decision gate or a selection input; censored members cannot carry a label,
   so they are excluded from training, and the ledger partitions them as unresolved (identical census
   asserted across all arms and controls).
5. **"avoided"/"destroyed"** — the ledger's definitions verbatim; the brief's giant definition
   (MFE from the release ≥ +300% and close above the release) is reported beside the ledger's own
   giant sets and is the one used for the tail ratio. Both views appear in every cell.
6. **`hold_flat`'s 0/0 tail ratio** — reported as null, never a fabricated 0; the binding tail control
   is `giveback:10`.
7. **Top-5-day removal** — the five days with the largest |day net| dropped from the arm and from the
   control on the same surviving days.
8. **Families** — one pooled cross-sectional model (as "cross-sectionally fitted" implies); per-family
   out-of-block AUCs are in the artifact.
