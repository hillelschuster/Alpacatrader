# F9 — fixed-total-gross weighting / sizing

**Evidence: [RUN]** `surface.json` contains the complete Cartesian coverage of the 120 completed F1 cells (5 entry families × N={2,3,4} × 4 exits × {100,150} bps) and all five F9 schemes: 600 rows. Every computed row uses 1,066 permitted development days: block 1 has 734 days (2021-02–2023-12), block 2 has 332 days (2025-02–2026-05). No F1 cell was rerun. The F1 ticket `net_return`, entry anatomy ranks/scores, and daily files are the only numerical inputs. Source contract: `researches/PRE-REG-BASKET-02.md` §§1, 3.8, 5; `factory/BASKET-SIM-CONTRACT.md` §§5, 8–10.

## Fixed-gross definitions

- `equal`: 1/N of C0 per selected rank, the F1 baseline.
- `rank_linear`: raw weight N−rank+1, normalized across all N selected slots.
- `score_gap_mild`: raw weight `1 + 0.25 × (sel − min(sel)) / (max(sel) − min(sel))`, normalized across the selected N; tied scores reduce to equal. This makes “mild” explicit and bounded (largest raw allocation premium 25% before normalization); it is descriptive, not an optimized parameter.
- `golden_gate_1p5x` and `survival_state_1p5x`: frozen multiplier magnitude represented, but results are `not_computable_preregistered_gate_or_timing_missing`. PRE-REG-BASKET-02 §3.8 does not specify which gate/state predicate or checkpoint establishes a pass. The F2/F12 output defining that predicate is not present here. Applying an invented cutoff or using later state to retrospectively allocate entry capital would add a degree of freedom/look-ahead, so neither scheme is assigned an outcome.

Weights sum to C0=1 over the originally selected N slots. Blocked/unfilled slots stay cash; weights are not redistributed among filled names. Since each ticket’s net return is proportional to its F1 unit notional, the reweighted daily return is the sum of ticket net returns times the new C0 weights. This is an initial-allocation reweighting of fixed F1 paths, not a new event-driven simulation. Gate/survival checkpoint reallocations cannot be reconstructed from these F1 outputs without a frozen causal predicate and execution path.

## Descriptive read (computed rows only)

Every computed mean is negative in both dual blocks. Across the 120 cells, equal mean ranges are −5.07% to −1.99% over all days, −5.52% to −2.00% in block 1, and −6.08% to −1.57% in block 2. Rank-linear means range −5.33% to −2.48% full, −5.60% to −2.31% block 1, and −6.85% to −2.34% block 2. Mild score-gap means range −5.15% to −2.09% full, −5.55% to −2.06% block 1, and −6.34% to −1.71% block 2.

Versus equal, rank-linear improves the full-period cell mean in 6/120 cells (14/120 block 1; 10/120 block 2); its average full-period difference is −0.280 percentage points. Mild score-gap improves 8/120 full (16/120 block 1; 12/120 block 2); its average difference is −0.063 points. These paired comparisons are descriptive across the frozen F1 grid, not inferential. Results show no broad robust uplift; the small favorable subsets are fragile and not strategy selections. Weighting cannot cure the negative underlying F1 net surface.

## Limits and reproduction

This is not a profitability, deployment, or OOS claim. It does not promote any sizing scheme. The gate/survival outcomes remain uncomputed rather than guessed; completing those rows requires the already-frozen causal gate/state predicate and compatible checkpoint execution evidence, not tuning on these results. Sealed/reserved dates were not accessed. Equal-row means were checked against the corresponding F1 metrics and match for all 120 cells.

Run from the repository root:

```bash
/home/hillel/projects/Alpacatrader/.venv/bin/python factory/scripts/f9_weighting.py
/home/hillel/projects/Alpacatrader/.venv/bin/python -m pytest -q tests/test_f9_weighting.py
```
