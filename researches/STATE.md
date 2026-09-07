# STATE — current truth (snapshot 2026-09-08). Full chronicle: factory/STATE.md (append-only log).
# Hypothesis ranking + falsifiers: researches/HYPOTHESES.md (living).
# History before 2026-09-04: researches/CANONICAL_STATE.md (superseded snapshot, kept for trust map).

## Where we are
Phase: post-collision triage. No live hypothesis currently holds dev+collision support.
The runner-machine phenomenology is real and well-measured (see below), but every
tradable formulation of it so far died out-of-sample. The binding constraint is
STATISTICAL POWER (n per month ~20-30 gate names; tail-driven means) — dev-month
positives have replicated 0-for-4 on unseen months.

## What is solidly established (multiple provenances, harness-verified)
- Phenomenon: ~1.2-1.5 runner days (+60%+ open->close, $1-50) per day, all 9 months
  scanned; zero dead months. Frequency is regime-stable.
- Runner shape: median open->high 322min; half the move completes by ~11:25 only 15%
  of the time; afternoon contributes median 55% of the move. Halts fingerprint the
  process (84% of runners halt; dose-response 0 halt -> +83% mean gain, 11+ -> +177%).
- Thrust alone is DEAD (+0.3% remainder, n=5,521). Rank/health/E1/E2/E3/Ridge/DTW/
  snapshot selection: all failed conditionality at scale. Retired, thesis-level.
- Buying halt reopens is DEAD causally (next-bar-open D30 -0.5% to -1.5%). The
  halt-gap money (morning qualifying events: mean +6.3%, median +4.4%, n=68) accrues
  only to the already-long.
- MFE-before-drawdown is the most stable measured property of top-gainer cohorts
  (mb100 positive in 9/9 month blocks, 31/33 days) — an opportunity map, NOT an
  entry signal (H11 was its capture attempt and failed).
- Money-location map (measured on the GATE cohort — 15-30% by 10:30 + >=1 halt,
  n=180 dev name-days; not general-population): morning halt-gaps (if holding),
  10:30-11:00 inflow wave (+1.2%), afternoon 14:00-16:00 wave (+2.0%). Midday is
  dead capital. The +2.39% afternoon rental did NOT survive collision months
  (2025-04 -6.27%, 2026-01 -3.51% net; pooled -2.13% at 100bps).

## Collisions: 0-for-4 (the meta-finding that shapes everything)
E1xhealth (146d) -> failed. Ridge (one formulation) -> failed twice (Aug, Oct). DTW
medoid October -> failed. H11 afternoon rental -> failed (2026-09-08). Pattern:
month-blocked dev positives with small per-month n are found easily and do not
replicate. SCOPE: this covers small-n selection/timing rules; the ML fresh-window
failure had high n (5/5 months negative) — that implicates regime/decay. Power is
necessary, not sufficient. Consequence: no selection or timing rule gets believed
again without (a) pooled n across MANY months before testing, or (b)
forward-observer accumulation as primary evidence.
Full retire list with causes: H8 gate (descriptive only; capture failed via H11);
H9 reopen participation (anchor illusion; causal entry negative); H10a hold-through-
halt (UNTESTED — open family, not promising); H10b/H11 afternoon rental (collision-
failed); plus historical: E1/E2/E3, gain-rank, health, Ridge, DTW, 10:00 snapshots.

## Current strategy for the next phase (decision 2026-09-08)
1. STOP inventing single-month-sliced selection rules. Statistical power first:
   any new test must pool >= 6 months of name-days in dev AND pre-register >= 2
   unseen collision months.
2. Forward observer = the honest evidence engine. It runs daily, order-free, and
   accumulates live n (no backtest overfit). Priority: keep it running, score every
   session through score_forward_day.py, build the live record.
3. Phenomenology remains the idea mine (money-location map above), but extraction
   formulations must be power-aware from inception.
4. Open (untested, not promising): hold-through-halt capture; turnover/float clock
  (needs PIT float data we lack); failed-move fade (conditions on late info);
  transitions/handoffs (needs event logging wiring).

## Open questions (unresolved; do not cite either side as settled)
- Rank-1 chasing conflict: Cameron-lane "causal minute-rank-1 negative every month
  both years" vs verify_core 11-point monthly h60 DOWNGRADED to mixed (-579..+411).
  Different horizons/universes; never re-opened head-to-head.
- FP-penalized recall gate for Stage B (MFE-before-DD target adopted).
- Harness-path rerun of MFE magnitudes (lane-only provenance currently).
- 2026-04..08 clean-file provenance (duplicates in data/; backfill clean absent).
- H10a hold-through-halt: the one open capture family (untested, power-caveated).

## Critical session-semantics (never re-learn these)
- et_minute() in replay_watchlist.py; session ET [570, 960); snapshot 10:00 uses
  et<=599, targets et>=600. PM data only via return_pm=True (separate frame).
- Month->path: >=2026-03 loads data/backfill/, else data/. 2026-04 clean file absent
  locally. 2026 premarket coverage absent; repaired premarket = 2025 only.
- MFE/MAE conventions: outcome_MB() low-based, entry-bar-included, same-bar
  drawdown-first (conservative). Never mix with older looser MFE numbers.
- Halt-gap proxy = >=5min hole in RTH minute bars. Trustworthy only for fast names
  + long holes; flat names print ~1M spurious holes (measured, excluded).
- Friction: use 100bps round-trip minimum for these names; adversary case says
  50-150bps realistic. 20bps assumptions are void.

## Live evidence sources
- factory/artifacts/*.json — every number above has a committed artifact.
- data/forward/2026-09-04..06/ — live observer days (09-05/06 are WEEKENDS —
  exclude non-trading days in analysis; 09-04 has the 16% phantom/warrant issue).
- H11 collision artifact: factory/artifacts/h11_collision_results.json (n=112).

## Provenance doctrine (adopted 2026-09-08 after audit)
- Every artifact's producer script must be committed under factory/scripts/.
- Every cohort scan must report distinct days covered (the 281-vs-245 gate-cohort
  confusion was a day-coverage gap, not a population difference).
- Friction is annotated per claim; cross-friction comparisons are void (20bps ML-era
  numbers are not comparable to 100bps H-era numbers).
