# EV-01 — minute-horizon descriptor/outcome read (executed once)

Pre-registration: `researches/PRE-REG-TAPE-ATLAS-EV01.md` sha256 `ce61fcaf8ffcb575c3359b2a3801b227fc2e3af8cae9575fa118b5ae0d2e7d85` (FROZEN 2026-10-01).
Panel sha `2a021eda878cdbe53e41d4cc88e2d8ba3b14d9a3a819723d9f679c8aca7c3488`; descriptors sha `534fdb7afe8ecc50472e088f13ec457b1d5ba337ad101e231357c54e0effafe5`; outcomes sha `e7654b5194ae55670951af2b136f4c6c4f3d9664f4e4cb34e27316956c208c3f`.

## Question
Does the evidenced within-name structure separate forward member returns from the hold-flat baseline at minute horizons, net of 100 bps?

## Method
Unit = member (6,160). Descriptors (outcome-blind, published first): (a) balanced-view density mean over the 3 scales; (b) rare flag = density 0 in >=1 of five views (frozen rare_refs rule); (c) learned-view NN distance (frozen causal embeddings, masked squared-L2, same-tape exclusion, k=100, chunk 512).
Outcome: `Delta_member = k*(gross_rule - gross_hold)` per `exit_at_bar:N`, N in {1,5,30}, vs `hold_flat`, bps_total=100, k=0.99005, read through `basket_atlas_ledger.replay_member`. Censored members unresolved (excluded, counted); blocked fills = cash (no member row).
Stats: pooled block-tagged deciles (616 each, ties by (descriptor, member_id)); S1 Spearman; S2 top-minus-bottom decile mean Delta; month-blocked bootstrap 10,000 draws seed 20260922, percentile CIs 95% and 98.33% (Bonferroni 0.05/3).

## Numbers (point [98.33% CI])

### a balanced-density
| N | cell | n | S1 | S1 bonf CI | S2 | S2 bonf CI | cens |
|---|---|---|---|---|---|---|---|
| 1 | pooled | 5887 | -0.2336 | [-0.2697,-0.1969] | -8.91% | [-11.73%,-5.75%] | 4.43% |
| 1 | block1 | 4053 | -0.2376 | [-0.2832,-0.1890] | -8.15% | [-12.13%,-3.60%] | 3.91% |
| 1 | block2 | 1834 | -0.2313 | [-0.2875,-0.1692] | -11.02% | [-16.30%,-6.67%] | 5.56% |
| 5 | pooled | 5887 | -0.2483 | [-0.2849,-0.2116] | -9.74% | [-12.64%,-6.63%] | 4.43% |
| 5 | block1 | 4053 | -0.2635 | [-0.3086,-0.2173] | -9.75% | [-13.76%,-5.33%] | 3.91% |
| 5 | block2 | 1834 | -0.2257 | [-0.2841,-0.1641] | -10.86% | [-16.43%,-6.34%] | 5.56% |
| 30 | pooled | 5887 | -0.2248 | [-0.2617,-0.1893] | -8.98% | [-11.94%,-6.04%] | 4.43% |
| 30 | block1 | 4053 | -0.2525 | [-0.2924,-0.2146] | -10.05% | [-14.01%,-5.94%] | 3.91% |
| 30 | block2 | 1834 | -0.1756 | [-0.2354,-0.1130] | -8.31% | [-12.92%,-3.97%] | 5.56% |

### b rare-flag
| N | cell | n | S1 | S1 bonf CI | S2 | S2 bonf CI | cens |
|---|---|---|---|---|---|---|---|
| 1 | pooled | 5887 | -0.0038 | [-0.0404,+0.0305] | -1.79% | [-5.69%,+1.97%] | 4.43% |
| 1 | block1 | 4053 | -0.0247 | [-0.0733,+0.0224] | nan | [nan,nan] | 3.91% |
| 1 | block2 | 1834 | +0.0354 | [-0.0062,+0.0740] | nan | [nan,nan] | 5.56% |
| 5 | pooled | 5887 | +0.0067 | [-0.0298,+0.0421] | -0.86% | [-5.23%,+3.17%] | 4.43% |
| 5 | block1 | 4053 | -0.0103 | [-0.0585,+0.0369] | nan | [nan,nan] | 3.91% |
| 5 | block2 | 1834 | +0.0408 | [+0.0013,+0.0833] | nan | [nan,nan] | 5.56% |
| 30 | pooled | 5887 | +0.0460 | [+0.0079,+0.0835] | +0.33% | [-2.67%,+3.58%] | 4.43% |
| 30 | block1 | 4053 | +0.0410 | [-0.0086,+0.0909] | nan | [nan,nan] | 3.91% |
| 30 | block2 | 1834 | +0.0576 | [+0.0023,+0.1133] | nan | [nan,nan] | 5.56% |

### c learned-NN
| N | cell | n | S1 | S1 bonf CI | S2 | S2 bonf CI | cens |
|---|---|---|---|---|---|---|---|
| 1 | pooled | 5887 | -0.0426 | [-0.0679,-0.0172] | +3.57% | [-1.89%,+10.68%] | 4.43% |
| 1 | block1 | 4053 | -0.0368 | [-0.0714,-0.0027] | +5.36% | [-0.89%,+14.36%] | 3.91% |
| 1 | block2 | 1834 | -0.0565 | [-0.0863,-0.0214] | -0.75% | [-10.11%,+10.69%] | 5.56% |
| 5 | pooled | 5887 | -0.0552 | [-0.0816,-0.0295] | +1.85% | [-3.84%,+9.07%] | 4.43% |
| 5 | block1 | 4053 | -0.0553 | [-0.0904,-0.0211] | +3.94% | [-2.89%,+13.13%] | 3.91% |
| 5 | block2 | 1834 | -0.0588 | [-0.0934,-0.0236] | -3.15% | [-12.22%,+7.93%] | 5.56% |
| 30 | pooled | 5887 | -0.0636 | [-0.0939,-0.0354] | -0.99% | [-6.26%,+5.22%] | 4.43% |
| 30 | block1 | 4053 | -0.0622 | [-0.1017,-0.0276] | +0.46% | [-5.88%,+8.51%] | 3.91% |
| 30 | block2 | 1834 | -0.0701 | [-0.1182,-0.0207] | -4.46% | [-12.97%,+5.36%] | 5.56% |

## Verdicts (K1-K7 verbatim; PASS = no kill fired)
| descriptor | N=1 | N=5 | N=30 |
|---|---|---|---|
| a balanced-density | PASS | PASS | PASS |
| b rare-flag | DISMISS (K1,K2,K4) | DISMISS (K1,K2,K4) | DISMISS (K4) |
| c learned-NN | DISMISS (K3) | PASS | DISMISS (K4) |

Kill detail per descriptor x horizon (verbatim booleans) is in `results.json` (`kill_decisions`) and `verdicts.txt`.

## Caveats
1. Pre-reg 4(b) states 'density==0 in all five views' but its FROZEN clause says 'no neighbour ... in ANY of the five views'. The all-five reading is empty by construction (normalized_shape has 0 unique objects), so the FROZEN/rare_refs reading (ANY) was used; computed set is identical to `rare_refs.parquet` (8,577 objects).
2. Pre-reg 4(c) defines the descriptor as 'smallest finite distance (equivalently nn_dist[:,0] / kth_dist)'. The explicit words were used: `nn_dist[:,0]` (smallest finite); `kth_dist` is the k-th value, a different statistic, and was not used.
3. K3 'monotone over top-5 vs bottom-5 deciles in the pooled sign's direction' is operationalised as sign(mean(deciles6-10) - mean(deciles1-5)) == sign(pooled S1); the stricter 10-decile monotonicity is also reported per block in `results.json`.
4. Descriptor (c) z-scores the embedding channels on the fit block (block1) exactly as `basket_tape_atlas_geometry.embedding_gram`; the geometry producer was not re-run.
5. Deciles are a single pooled cut (all 6,160) as FROZEN; per-block cuts are secondary only. For the binary `d_rare` the pooled cut is block-confounded (e.g. deciles 1-3 and 7-8 hold no block2 member, deciles 5 and 10 no block1 member), so each block has a decile with 0 resolved members: K6 voids K3 in both blocks and block-level S2 is undefined (empty top/bottom decile) - reported, not merged.
6. Verdict-application correction: the single read's first pass counted a K6-voided K3 as a kill; corrected (a voided block cannot fail K3). No statistic changed (verified field-by-field); `results_read_once.json` is the raw single-read output and `results.json` is the corrected one. Both are pinned in the manifest.
7. K3/K6 are applied per block; K4 uses the pooled cells; K2 uses the pooled Bonferroni interval; K5/K7 never fired (pooled n=5887>=500, blocks 4053/1834>=200; censored 3.91%/5.56%<10%).

## Limitations
Separation only: no entry/exit/sizing rule is declared. Minute horizons, no next-session/carry. Learned view has no frozen radius, so descriptor (c) supports only rank/decile separation, not a density threshold. Censored members (4.43%) and blocked slots (186 block1 / 49 block2, cash) are excluded from the ratio and reported separately.

## Execution integrity
Executed exactly once; descriptors published before any outcome column was read; every result including nulls published. `no_reread.json` is the no-reread statement. Output directory/names follow the task assignment (EV01/): pre-reg section 8.6's `member_deltas.parquet` / `tables.json` / `EV/v1/` correspond to `outcomes.parquet` / `results.json` here; `bootstrap_distributions.npz`, `coverage.json` and `manifest.json` keep their pre-registered names.
