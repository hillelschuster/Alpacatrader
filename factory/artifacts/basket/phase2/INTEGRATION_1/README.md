# Phase-2 integration 1 — evidence to next causal EV tests

**Status: measurement integration / test queue, not a strategy promotion.** F1 is the shared
entry/primitive-exit source; F8, F9 and F10/F14 are read-only descriptions of its economics.
F2/F12 supplies state-conditioned *remaining outcome* associations, not an action effect.
Everything here uses the 1,066 permitted development days in blocks 2021-02–2023-12 (734)
and 2025-02–2026-05 (332). No cell, threshold, or strategy is selected.

## Accepted evidence

- **F1 baseline:** all 120 frozen cells and their daily outputs remain unchanged. Net basket-day
  means are negative at both 100 and 150 bps (overall −4.60% to −1.99% at 100 bps and
  −5.07% to −2.44% at 150 bps, across the surface); neither entry family nor N is a promoted
  choice. See `../F1/README.md` and `../F1/surface.json`.
- **F8 joint/remaining-tail opportunity:** full 120×1,066-day joint counts preserve cash slots,
  per-ticket post-fill `mfe_raw`, and C0 sleeve outcomes. Across cells, at-least-two +30% MFE
  touch probabilities range 0.68–12.67% in block 1 and 1.51–22.59% in block 2; all-filled
  EOD-positive probabilities are much lower (0–15.94% and 0–14.16%). This is a broad opportunity
  map, not executable capture. The `open_end` ticket's `net` is a mark, not a realized exit;
  F8 records closed and open-end counts separately, and `pnl_c0` reconciles to F1 `daily.r_day`.
- **F2/F12 remaining outcome:** at ET 600, every entry family has higher block-2 than block-1
  `rem_mfe` mean and +50/+100 touch probability (for example B600: mean rem MFE 11.80% / 15.29%,
  P(+50) 3.70% / 5.40%, P(+100) 0.97% / 1.64%). These are outcomes measured strictly after
  the checkpoint relative to checkpoint close; not executable P&L, causal treatment effects,
  or a registered gate. Use the full F2/F12 panel rather than selecting its best feature bucket.
- **F9 sizing:** the complete 120-cell comparison shows rank-linear and mild score-gap initial
  reweighting have negative means in both blocks and no broad improvement over equal weighting.
  Golden-gate and survival-state multipliers remain non-computable: no declared simple gate and
  compatible checkpoint execution path are supplied. Do not backfill either with a threshold.
- **F10/F14:** all 1,066 days × 120 cells are mapped against fixed 10:00 labels and dual blocks.
  Those labels use only `et < 600` bars and canonical snapshots. A-family F1 entries occur before
  10:00, so their full-day outcome association with a 10:00 label is retrospective and cannot
  justify entry economics. At most it motivates a morning-continuation question with strictly
  post-decision outcomes. The selected-name B/600 trend is not a broad-market index proxy.

## Causal timing contract for the queue

For a decision at checkpoint `t`, consume only bars completed by `t` (`et < t` under the
minute-stamp convention), act at the first eligible later bar open, and score only outcomes
after the action. Keep anatomy fills and blocked-slot cash, no replacement/leverage, fixed C0,
100 bps base and 150 bps adversity, and both development blocks. Separate realized, marked,
and tail-touch quantities. Pre-10:00 A-family entry comparisons cannot condition on 10:00 state.
F2/F12's current feature surfaces do not declare a gate: no F9 gate/survival multiplier may be
computed until that predicate, decision time, execution and falsifier are registered.

## Ranked next registered tests (hypotheses, not claims)

1. **F3 — survival/deterioration release; highest priority.** F1 loses money broadly while
   F8 shows some members reach meaningful MFE before ending negative, and F2/F12 shows measurable
   remaining outcomes at 09:40–10:15 in both blocks. Register a small structural map first:
   entry-relative and peak-relative deterioration (depth, duration, recovery/reclaim, % MFE
   surrendered, time since high), then only PRE-REG-02 R2(L∈{10,15}, w∈{3,5,10}) and R3(g∈{40,50,60})
   modules. **Falsifier:** neither release path improves net C0 EV / failed-ticket loss without
   materially sacrificing raw-MFE≥50/100 captured contribution in both blocks at 100 bps, with
   150 bps as adversity. Do not select a descriptive bucket as a new threshold.
2. **F5 — golden-window/new-high continuation add.** The F2/F12 ET-600 remaining-tail means and
   +50/+100 touch rates rise in block 2 for all five entry families; this warrants testing whether
   a causal strengthening event can earn *incremental* notional, not assuming that it can.
   Register one declared own-state trigger from §3.5 (new-high continuation, recovery-after-breach,
   or a separately frozen ≤3-condition golden gate); permit only +25/+50/+100% original unit
   notional, total adds capped at +100%, at 09:45/10:00 where eligible. **Falsifier:** added
   capital's incremental net EV is non-positive after friction, or positive only in one block, or
   funded adds breach cash/cap constraints. Measure remaining MFE at add time against the added
   tranche's next-bar-open net outcome. No retrospective 10:00 condition for pre-10 A entries.
3. **F4 — scale-out only after F3 identifies a deterioration event worth testing.** Use only an
   F3 release vocabulary or a separately declared golden-window deterioration gate, sizes
   25/33/50% and the frozen staged patterns. Compare per-ticket reduction with registered basket
   de-risking (K∈{2,3}, x∈{25%,50%}); measure each tranche's EV and false-release/tail-retention
   cost. **Falsifier:** no staged pattern improves net basket EV in both blocks without a worse
   tail-retention/captured-net tradeoff than full release. Do not trigger on F8 marginal cell rates.
4. **F6 — reserve deployment, conditional on an established causal state/action.** Test p∈{0.67,
   0.5,0.33} entry deployment with remaining cash, deploy once at checkpoint 585 or 600 by
   predeclared equal-across-survivors rule (cash remains a valid control). **Falsifier:** reserve
   deployment's incremental net C0 EV is non-positive after friction or depends on one block;
   report undeployed cash and missed tail exposure. Since no F2/F12 gate is frozen, do not test
   golden-gate survivors yet.
5. **F7 — released-capital recycling, conditional on F3/F4 exits freeing capital before a
   later eligible opportunity.** Compare 0/50/100% recycle fractions under the frozen equal
   surviving-member policy, with cash control, no leverage, and next-bar execution. **Falsifier:**
   recycled tranche's incremental net C0 EV is non-positive in either block or violates available
   cash / gross≤C0. Do not presume a release creates usable same-day capital or rank-1 allocation.

These are next registered questions only. Each requires its own frozen test declaration, full
surface, causal input/execution audit, and both blocks. No result here establishes positive EV,
OOS validity, deployment readiness, or a live strategy.

## Exact limitations / rejected interpretations

- F1's negative primitive baseline limits—not disproves—the value of later causal release,
  continuation, or capital actions; those are not simulated by F8/F9/F10.
- F8 reach/MFE is observed post-fill path opportunity. It is neither a realizable take-profit nor
  evidence a later add can capture it. Its EOD-positive ticket measure includes marked open ends;
  it must not be called realized all-winners.
- F9 cannot compute later-checkpoint or gate-conditioned multipliers with current F2/F12: the
  state panel is a feature/outcome surface, not a declared simple gate or executable path.
- F10/F14 contains no broad-market/SPY/IWM or point-in-time market-cap feature; fixed categories
  may be imbalanced. As-of-10:00 labels cannot causally explain A-family pre-10:00 entries.
- Shared days/cells and overlapping names make cross-cell and F2/F12 row comparisons dependent;
  descriptive ranges are not confidence intervals or independent replications. Only permitted
  development evidence was read; sealed/reserved periods remain untouched.
