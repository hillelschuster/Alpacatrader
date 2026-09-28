# ATLAS PHASE 2 — CLOSURE (2026-09-28)

Worktree/branch: `basket-phase2-f1`. Evidence: `factory/artifacts/basket/phase2/ATLAS/`
(panel v2, ledger v2, window v2, fall v2, pairs v2, E1/, E3/) with independent debug passes on
every artifact that carried a verdict. This file states what is closed, what stands, and what is
not yet priced.

## E1 — management by exhaustion score: CLOSED (no increment)

* Every-bar score `P(running high at t is the session's final high)` — causal, cross-fitted, AUC
  0.82-0.84 within clock — releases profitably vs holding in both folds.
* It does **not** beat the trivial `giveback:10` ruler on dollars in either fold (primary and all
  three clock-quantile rulers, sleeve and dedup views); the event-hazard ablation family is killed
  in fold B by the pre-registered giant-tail clause.
* The apparent conditional niche (top predicted-dispersion tercile) was a **look-ahead artifact**:
  the member-median conditioning statistic read post-release bars; with a decision-time statistic
  the niche is negative in 16/16 arm x fold cells. The calm-tercile box is perturb-stable but the
  arm nets <= 0 inside it and it fails the tail clause. Nothing promoted; no rescue slicing.
* Debug fixes that made this trustworthy: inner day-split cross-fit for every train-side threshold
  bank (a real leak, ~19% of the headline), decision-time-only conditioning guards, ledger
  reconciliation counters published per cell, byte-reproducible runs.

## E3 — sub-minute information: CLOSED (null)

* Freeze: 20,937 windows over 1,066 days, five case sets, census byte-stable across three runs at
  two worker counts; every gate and coverage join reproduced from raw by an independent debugger.
* Stage-B semantics corrected before measuring: decision instant = **close of the anchor bar**
  (the frozen first-print rule would have admitted 190,695 post-decision prints over 39.4% of
  windows), gap-safe execution mapping through the panel's own bar list, session containment by
  construction; CS-3 marked label-conditioned, CS-5 split by hole length.
* I-EV verdict: no feature family is positive out-of-block in both blocks for CS-1..CS-4; CS-5
  survives only the weak kill rule (F1 arrival, F3 occupancy, F6 signed flow) and every delta is
  <= 0.0168 (CS-5 <= 0.0055), three orders inside the pre-registered 0.054 null band. Zero
  promotions. The null is not a coverage artifact (all-columns-present 0.74-1.00; every window has
  causal prints).
* L-EV (latency cost): **blocked by precondition** — there is no signal whose latency could
  matter. Contract frozen in `E3/latency.json` for the day a positive I-EV family exists.

## What actually stands after Phase 2

1. **The only validated executable mechanism remains the H025 flush rule** (separate workstream,
   live paper, judged against A3b — untouched by this phase).
2. **Inside ATLAS, the strongest management policy tested is the trivial peak-relative ruler**
   (`giveback:10`): positive vs holding, and nothing learned beats it. That is a ruler, not a new
   edge; it is evidence about where EV is not.
3. Predictability without monetization: state predicts forward dispersion (AUC 0.67-0.74) and
   exhaustion (0.82-0.84) out-of-block, yet neither converts into dollars beyond the trivial ruler.
4. No state, timing, selection, pattern or sub-minute formulation tested to date adds executable
   dollars; negative evidence applies to exactly those formulations.

## Next, without compromising the doctrine

* Any new formulation (not a re-slice of a closed one) must arrive pre-registered with unseen
  confirmatory months; dev-only positives are 0-for-4 and stay untrusted.
* The two blocked lanes (dispersion sizing; latency EV) reopen only if a positive core appears.
* The live paper path and its accumulation remain the only running experiment with real money
  implications.
