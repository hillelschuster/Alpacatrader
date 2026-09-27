# ATLAS PHASE 1 — what the tape says (audited, 2026-09-27)

The first phase of the ATLAS program is complete: a verified minute-grain panel, a verified dollar
ledger, and three audited analyses of the top-gainer tape. Everything below carries an independent
audit trail; where a number was refuted in audit, the corrected value is the one reported.

## 1. Foundation (all independently audited PASS)

- **Panel v2** (`ATLAS/panel.parquet`, sha 2a021eda…): one row per member per completed minute,
  1,900,432 rows / 6,160 members (3,188 A_pm + 2,972 B600) / 1,066 days / 88 columns. 273 incomplete
  member-tapes (33,845 rows, 1.78%) are explicitly terminal-censored with 879,970 terminal-dependent
  cells nulled rather than fabricated; the executable hold value is `v_forced_flat` (session-end bar
  open) with `v_hold_flat` kept only as a labelled close reference; release rules never evaluate at
  `et >= session_end-1`; future/censor columns are registry-excluded from causal use; a checked-in
  verifier recomputes 254 boundary-stratified rows (17,272 comparisons, zero mismatches) and five
  corruption cases all fail correctly.
- **Ledger v2** (`basket_atlas_ledger.py`): per-member dollar attribution against the executable
  hold-to-flat, friction on both legs, giant-tail dollars destroyed versus failure tax avoided,
  censored members partitioned as unresolved (219 known rule exits reported separately), fail-closed
  leakage guard, and sleeve-versus-independent-path views (255 shared ticker-days; independent-path
  net ranges +108.16…+128.90 against a sleeve total of +123.99).

## 2. What the tape's personality actually is

**Shape (audited, `window_profile.json` / `fall.json`).** Two alignments are reported because the families
fill at different clocks (A_pm at ET 570, B600 at ET 600): comparing them by clock is a category error,
so the like-for-like comparison is by **tenure** (bars since entry).

| bars since entry | 0 | 1 | 30 | 60 | 120 | 180 |
|---|---|---|---|---|---|---|
| A_pm P(climb already over) | 0.2199 | 0.2705 | 0.6194 | 0.7071 | 0.7834 | 0.8329 |
| B600 P(climb already over) | 0.1490 | 0.1993 | 0.5149 | 0.6164 | 0.7249 | 0.7965 |

Per-clock values, for the families where the clock is meaningful: A_pm 0.2206 at 09:30, **0.6042 at
10:00**, 0.7363 at 11:00, 0.7939 at noon; B600 0.1451 at its own fill minute 10:00, **0.5951 at 11:00**,
0.7022 at noon. (An earlier draft of this file reported "B600 0.1490 → 0.5951 by 10:00", which is
impossible — B600's fill bar *is* 10:00; the 0.5951 is the 11:00 cell.)

A structural fact this exposes: **at equal tenure the 10:00 cohort is less likely to have peaked at every
horizon** (30 bars: 0.515 vs 0.619; 60: 0.616 vs 0.707). That is selection, not a clock effect — B600's
names were still leaders at 10:00, so persistence is part of what selected them. The entry clock is
therefore a *cohort-persistence* choice as much as a timing choice, and any management law must be
evaluated per family.

The peak-time distribution is produced by **arrival decaying ~30× through the session while the
termination hazard stays flat** — "the tape goes quiet", not "termination rates rise". The arrival ×
hazard telescoping reproduces the empirical peak pmf exactly (6/6 family×block cells, max minute
deviation 2.8e-17, exact by construction).

**What state predicts — and what it does not (audited, three-label comparison).**

| target | out-of-block within-clock AUC (identical rows) |
|---|---|
| forward **dispersion** (range-30 / mabs-30) | **0.714 / 0.699** (A_pm), **0.736 / 0.739** (B600) |
| **exhaustion** (a new high is the last) | 0.676 / 0.661 (A_pm), 0.699 / 0.701 (B600) |
| **direction** (sign of executable forward return, a non-max label) | **+0.07 / +0.04 over the clock alone** |

Three consequences:
1. **Dispersion is the strongest state target** and the exact clock explains much less of it
   (0.60–0.61), so the state carries genuine forward-activity information.
2. **Exhaustion is a separate, independent signal.** Inside forward-dispersion deciles the hazard
   keeps full strength (within-decile medians 0.6748/0.6926 and 0.6714/0.6987 against 0.6734/0.6525
   and 0.6935/0.6732 unconditioned). The hazard is not a volatility restatement.
3. **No directional separation was found with the current 1-minute state representation and tests.**
   (This is a statement about the representation and the tests, not a proof that direction is absent from
   the tape.) The honest two-sided test — matched
   middle-decile members, same day/minute/family/tenure, sides assigned blind — finds the largest
   absolute effect anywhere **0.054**, mostly *inverted*: the higher-range member is slightly *less*
   likely to hold the higher executable value later (0.446 A_pm / 0.453 B600). The earlier decile-split
   "separators" were an artifact of a label defined as a maximum over the future path (a max-order
   statistic is monotone in forward volatility), and the artifact now carries that prohibition
   explicitly: no policy, gate or directional rule may be derived from them.

Where the exhaustion content lives: `dist_from_running_high` and `mfe_surrendered_pos`, `up_close_streak`,
`bars_below_entry_episode`, `accel_1_5` — peak-relative decay variables. Under identified single-feature
models **15 of 16 carrier-cells are stronger in the wildest dispersion decile** (ratios 0.20–0.84), so
exhaustion reading is most informative exactly when the tape is violent. (An audit's apparent
attenuation was an artifact of unidentified 13-feature per-decile fits; the reconciliation is recorded
in the artifact.)

**Recovery (audited, `matched_pairs.json`).** After a matched down-moment: **~51.6% / 51.4% never close
back at the exit price** for the rest of the session (censoring-adjusted 49.6% / 50.5%; median 173 / 145
remaining bars). When recovery does happen it is fast (median **1 bar**) and shallow (median cost
−0.05%), but **25.7% / 44.0% make a new low within 30 bars afterwards** — a reclaim is frequently not a
bottom. The touch-based "reclaim" is degenerate by construction (100% at bar 1) and is quarantined.

**Window value asymmetry (audited).** The executable continuation value hovers ≈0 in *mean* for most of
the session (A_pm block1 crossing at 922, block2 643; B600 block2 940; B600 block1 never positive) while
the *median* dies far earlier — the average is carried by the right tail.

## 3. What this means for the harvest (the mechanic the thesis implies)

1. **Selection among look-alikes is not available at minute resolution.** The "which of these will run"
   question cannot be answered from minute bars; attempts produce volatility artifacts.
2. **Two channels are genuinely available**: *participation* (dispersion — how violently the tape will
   move) and *release* (exhaustion — whether the move is spent). Both are causal, out-of-block, and
   measurable at every completed minute.
3. **The mechanism to test next is therefore not a better entry signal but a better management law**:
   own the race, scale with dispersion, release on the exhaustion state rather than on a fixed giveback
   level, keep cash as a real action, and re-enter only on evidence of resurrection.
4. **Direction, if it exists anywhere, is below minute resolution.** That is now an evidence-based
   reason to use the local SIP trade prints (microsecond, already on disk) on a bounded set of matched
   divergence cases — not a general resolution increase.

## 4. Next experiments, in order

- **E1 — exhaustion-conditioned release through the ledger.** Score a release rule driven by the decay
  state (`dist_from_running_high` / `mfe_surrendered_pos` / `up_close_streak` / episode duration,
  conditioned on dispersion) against the fixed rulers (hold-to-flat, giveback:10, the +30 touch) on
  dollars: failure tax avoided versus giant-tail dollars destroyed, per block, sleeve and
  independent-path views. Question: can the state keep the ordinary-loss savings while destroying less
  tail than the rulers?
- **E2 — dispersion-conditioned participation.** Use the 0.70–0.74 dispersion channel for sizing/scale
  decisions (how much of the sleeve is exposed when) rather than uniform deployment; score with the
  ledger including capital-carry cost.
- **E3 — the print-level microscope** on matched divergence cases: seconds/microstructure around the
  match minute, to test whether a directional separator exists below minute bars. Bounded, targeted,
  and only on pairs the minute panel cannot separate.
- **E4 — resurrection/re-entry.** The recovery block says half of down-moments never recover and the
  ones that do are fast and fragile; test whether a fast, shallow reclaim followed by a new high (a
  causality-visible event) is a better re-entry trigger than time or level.

## 5. Discipline carried forward

Every headline number in this phase has an audit provenance; two of the three analyses required
corrections (a fabricated terminal value and a censoring leak in the panel; clustered means divided by
the wrong denominator, a fabricated envelope, and a saturated ruler in the window; a max-order-statistic
artifact, an overlapping "split", an EOD label, and a CI computed for a different estimator than its
point in the pairs). The rules that came out of it are in `researches/INTENT.md`: briefs carry labelled
priors not conclusions; beware max-order labels; discovery not prosecution; exact minute, not horizons;
no comfort-zone substitution.
