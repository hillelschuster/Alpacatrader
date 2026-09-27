# ATLAS E1a — exhaustion-score release rule (verdict)

Producer `factory/scripts/basket_atlas_e1.py` (sha256 `d1b47b82607adc2281fb204d059620f46c75108786ab7824d0bda122c416b3f2`) →
`factory/artifacts/basket/phase2/ATLAS/E1/E1.json` (sha256 `667f8cc369d8993544ebd377edcb313fec2b63b79fb53d5b7bff00e5f655406d`, 2824152 bytes) and
`E1/selftest.json` (14 unit/integration cases, all PASS; `.venv/bin/python factory/scripts/basket_atlas_e1.py --selftest`).
Panel `ATLAS/panel.parquet` sha `2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488` — 1900432 rows, 6160 members,
1458789 state bars, 42298 new-high bars.
Imported code: `basket_atlas_fall.py` sha `b944d210cea7b396…`,
`basket_atlas_ledger.py` sha `7f8686350ebe3000…`.
Run status **PASS**, 36 cells, wall 928s.
**Reproducible:** two independent full runs are identical across every cell, arm, grid curve and
attribution (0 differences excluding timestamp/runtime/provenance keys).

## What was tested

* **PRIMARY target** `final_high_now(t)` = 1 iff the running high at *t* is the session's final running
  high on a complete tape, else 0 — defined at every completed bar; verified identical to the panel's
  `final_high_flag` on all 1,860,700 rows where the panel defines it (0 mismatches). Trained on
  complete & uncensored post-fill bars where a new high has already occurred
  (1458789 state bars, 4794 of 5887 complete members); ridge logistic, 14 fall state
  features + exact clock-minute fixed effects, pooled over both families, machinery imported from
  `basket_atlas_fall.py`. Out-of-block **within-clock AUC 0.8367** (block1→block2) /
  **0.8175** (block2→block1).
* **ABLATION only** the fall event-hazard P(this new high is the last), trained on new-high bars with
  decisions on new-high bars: within-clock AUC 0.6806 / 0.6595 (the Phase-1 published range).
* **Folds** `block_of` (frozen; exactly two blocks exist): A = block1 train → block2 eval,
  B = block2 train → block1 eval. Every scored bar and every threshold comes from the model fitted on
  the other block; nothing is refit on an evaluation block.
* **PRIMARY arm** one global threshold learned on the TRAIN block from ledger dollars: grid over
  quantiles 0.50…0.99 of the train score distribution, objective = train net @100 bps after removing
  the five largest-|day net| days, ties broken toward fewer releases, then frozen and applied to the
  evaluation block unchanged. Fold A chose q0.50 (thr 0.809851, train net-after-top5 66.62,
  3254/4218 train members released; the optimum sits on the grid's lower bound).
  Fold B chose q0.53 (thr 0.898930, train net-after-top5 20.89, 1472/1942).
* **SECONDARY rulers** clock-minute quantile arms q70/q80/q90 of the same score (<30 train rows in a
  minute → the pooled train quantile).
* **CONTROLS** `hold_flat` (the executable forced flat) and `giveback:10` (the panel's declared ruler).
* **Accounting** entirely `basket_atlas_ledger.py`: decision on a completed bar, execution at the next
  printed bar's open, forced-flat precedence, censored members partitioned as unresolved (identical
  census in every arm and control), sleeve and independent-path (dedup) views, day-clustered 95% CIs
  (sandwich and 1000-rep bootstrap), frictions 100 and 150 bps.
* **Kill criterion (pre-registered)** after removing the five largest-|day net| days: dead if net ≤
  hold **or** the destroyed giant tail (MFE from the release ≥ +300% and close above the release) per
  avoided dollar is not below the binding non-score control (`giveback:10`, same surviving days), in
  either fold.

## Results @100 bps (150 bps is the same picture at the higher friction scale; full table in the artifact)

| arm | fold | bps | releases | avoided $ | destroyed $ | net $ | net $ after top-5 | giant300/avoided | control ratio | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| ablation_event_hazard_quantile_q70 | A | 100 | 1596 | 175.48 | 124.57 | 50.91 | 71.02 | 0.00902 | 0.01709 | survives |
| ablation_event_hazard_quantile_q70 | A | 150 | 1596 | 174.60 | 123.94 | 50.66 | 70.67 | 0.00902 | 0.01709 | survives |
| ablation_event_hazard_quantile_q70 | B | 100 | 2711 | 259.42 | 181.19 | 78.22 | 103.16 | 0.03834 | 0.03483 | KILLED |
| ablation_event_hazard_quantile_q70 | B | 150 | 2711 | 258.12 | 180.29 | 77.83 | 102.65 | 0.03834 | 0.03483 | KILLED |
| ablation_event_hazard_quantile_q80 | A | 100 | 1474 | 166.51 | 118.04 | 48.46 | 68.22 | 0.00950 | 0.01709 | survives |
| ablation_event_hazard_quantile_q80 | A | 150 | 1474 | 165.68 | 117.45 | 48.22 | 67.88 | 0.00950 | 0.01709 | survives |
| ablation_event_hazard_quantile_q80 | B | 100 | 2180 | 219.56 | 152.81 | 66.75 | 91.57 | 0.03226 | 0.03483 | survives |
| ablation_event_hazard_quantile_q80 | B | 150 | 2180 | 218.47 | 152.05 | 66.42 | 91.11 | 0.03226 | 0.03483 | survives |
| ablation_event_hazard_quantile_q90 | A | 100 | 1177 | 142.73 | 98.24 | 44.49 | 58.47 | 0.01130 | 0.03582 | survives |
| ablation_event_hazard_quantile_q90 | A | 150 | 1177 | 142.02 | 97.75 | 44.27 | 58.18 | 0.01130 | 0.03582 | survives |
| ablation_event_hazard_quantile_q90 | B | 100 | 1353 | 152.60 | 104.45 | 48.15 | 66.91 | 0.01904 | 0.04262 | survives |
| ablation_event_hazard_quantile_q90 | B | 150 | 1353 | 151.84 | 103.93 | 47.91 | 66.57 | 0.01904 | 0.04262 | survives |
| dollar_threshold | A | 100 | 1504 | 105.10 | 93.69 | 11.41 | 20.02 | 0.02781 | 0.03652 | survives |
| dollar_threshold | A | 150 | 1504 | 104.58 | 93.22 | 11.36 | 19.92 | 0.02781 | 0.03652 | survives |
| dollar_threshold | B | 100 | 3034 | 135.39 | 102.64 | 32.75 | 47.17 | 0.00000 | 0.02487 | survives |
| dollar_threshold | B | 150 | 3034 | 134.72 | 102.13 | 32.58 | 46.94 | 0.00000 | 0.02487 | survives |
| giveback:10 | A | 100 | 1631 | 162.49 | 126.18 | 36.32 | - | - | - | control |
| giveback:10 | A | 150 | 1631 | 161.68 | 125.55 | 36.14 | - | - | - | control |
| giveback:10 | B | 100 | 3315 | 282.37 | 194.70 | 87.68 | - | - | - | control |
| giveback:10 | B | 150 | 3315 | 280.96 | 193.72 | 87.24 | - | - | - | control |
| hold_flat | A | 100 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| hold_flat | A | 150 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| hold_flat | B | 100 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| hold_flat | B | 150 | 0 | 0.00 | 0.00 | 0.00 | - | - | - | control |
| quantile_q70 | A | 100 | 1075 | 119.63 | 86.90 | 32.73 | 46.92 | 0.02315 | 0.05885 | survives |
| quantile_q70 | A | 150 | 1075 | 119.04 | 86.46 | 32.57 | 46.69 | 0.02315 | 0.05885 | survives |
| quantile_q70 | B | 100 | 1638 | 138.89 | 97.41 | 41.48 | 57.61 | 0.00000 | 0.02653 | survives |
| quantile_q70 | B | 150 | 1638 | 138.20 | 96.92 | 41.28 | 57.32 | 0.00000 | 0.02653 | survives |
| quantile_q80 | A | 100 | 906 | 102.16 | 73.60 | 28.57 | 32.43 | 0.02833 | 0.06060 | survives |
| quantile_q80 | A | 150 | 906 | 101.66 | 73.23 | 28.42 | 32.27 | 0.02833 | 0.06060 | survives |
| quantile_q80 | B | 100 | 1125 | 96.56 | 64.87 | 31.69 | 51.30 | 0.00000 | 0.02630 | survives |
| quantile_q80 | B | 150 | 1125 | 96.07 | 64.54 | 31.53 | 51.05 | 0.00000 | 0.02630 | survives |
| quantile_q90 | A | 100 | 674 | 75.91 | 51.63 | 24.28 | 26.37 | 0.01656 | 0.06970 | survives |
| quantile_q90 | A | 150 | 674 | 75.53 | 51.37 | 24.16 | 26.24 | 0.01656 | 0.06970 | survives |
| quantile_q90 | B | 100 | 584 | 48.84 | 25.35 | 23.49 | 23.92 | 0.00000 | 0.07300 | survives |
| quantile_q90 | B | 150 | 584 | 48.60 | 25.23 | 23.37 | 23.80 | 0.00000 | 0.07300 | survives |

## Verdicts

| arm | role | verdict | evidence |
|---|---|---|---|
| `dollar_threshold` | primary | **SURVIVES** | net after top-5 20.02 (A) / 47.17 (B); giant300/avoided 0.02781 < control 0.03652 (A), 0.0 < 0.02487 (B). Fold-A mean-delta CI [-0.00772, 0.020168] includes 0 |
| `quantile_q70` | secondary | SURVIVES | net 32.73 (A) / 41.48 (B); CI [0.005164, 0.030534] / [0.003588, 0.016883] |
| `quantile_q80` | secondary | SURVIVES | net 28.57 (A) / 31.69 (B) |
| `quantile_q90` | secondary | SURVIVES | net 24.28 (A) / 23.49 (B) |
| `ablation_event_hazard_quantile_q70` | ablation | **KILLED (fold B, both frictions)** | tail clause: giant300/avoided 0.03834 ≥ control 0.02487 on the same surviving days |
| `ablation_event_hazard_quantile_q80` | ablation | SURVIVES | 0.03226 < 0.02487 in fold B (marginal) |
| `ablation_event_hazard_quantile_q90` | ablation | SURVIVES | 0.01904 < 0.02487 in fold B |
| `hold_flat` | control | CONTROL | zero by construction (the executable forced flat) |
| `giveback:10` | control | CONTROL | net 36.32 (A) / 87.68 (B), dedup net 32.29 / 75.87 |

## What the numbers say

1. **Every score arm beats holding** in both folds (positive net after top-5-day removal; the
   day-clustered CIs of the quantile arms exclude zero).
2. **No score arm beats the `giveback:10` ruler on dollars.** The ruler nets 36.32 (A) and
   87.68 (B) against the best score arm's 32.73 (A) and 41.48 (B); the primary arm nets
   11.41 and 32.75. The score arms do preserve the +300% tail better per avoided dollar
   (ratios 0.02781–0.02315 against the ruler's 0.03652 in fold A), but they avoid far fewer dollars.
3. **The tail clause kills exactly one arm**: the ablated event-hazard q70 in fold B — it buys its
   78.22 net with a giant tail per avoided dollar above the ruler's. The clause does its job on
   the object the correction removed from the primary arm.
4. **The train-learned threshold does not transfer cleanly.** Fold A's grid objective
   (66.62) is ~3x its evaluation net (11.41) and its mean-delta CI includes 0;
   the grid optimum also sits on the lower bound of the declared 0.50…0.99 range ("release earlier"
   keeps improving on train).
5. The every-bar level target is far more predictable than the event hazard (0.837/0.818 vs
   0.681/0.660 within clock), but the extra discrimination does not become dollars — the level label is
   largely the clock restated.

## Protocol ambiguities resolved (recorded in `protocol.ambiguity_resolutions`)

1. **Fold letters** — "blocks 0-1 train → 2-3 eval" maps onto the two blocks `block_of` produces:
   A = block1 → block2, B = block2 → block1 (verified equal to the panel's block column on every member).
2. **Decision population** — complete & uncensored bars past the fill with a new high already seen
   (the state the primary target is trained on); the score itself is emitted for every complete bar.
3. **Threshold pool** — the train block's decision population, cross-fitted; minutes with <30 train
   rows fall back to the pooled train quantile (fallback minutes counted and listed per fold).
4. **Censoring** — never a decision gate; censored members cannot carry a label, so they are excluded
   from training, and the ledger partitions them as unresolved (census asserted equal across arms and
   controls).
5. **"avoided"/"destroyed"** — the ledger's definitions verbatim; the brief's giant definition
   (MFE ≥ +300% and close above the release) is reported beside the ledger's own giant sets and is the
   one used for the tail ratio. Both views appear in every cell.
6. **`hold_flat`'s 0/0 tail ratio** — reported as null, never a fabricated 0; the binding control for
   the tail test is `giveback:10`.
7. **Top-5-day removal** — the five days with the largest |day net| dropped from the arm and from the
   control on the same surviving days.
8. **Families** — one pooled cross-sectional model (as "cross-sectionally fitted" implies); per-family
   out-of-block AUCs are in the artifact.
