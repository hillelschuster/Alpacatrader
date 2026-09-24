# Swarm reports — 2026-09-24 (C1 cycle)

Full, unedited reports from the eight parallel read-only agents launched for the
interpretation reset. They are the source material for `researches/SWARM-SYNTHESIS-20260924.md`
(section 2 merges them; sections 3 and 4 are the ranked moves and the preserved disagreements).

Each JSON file is the agent's complete payload: `summary`, `architecture`, `files` (every path it
inspected, with a description) and `report` (the full text, including the evidence it gathered,
the bugs it found, its disagreements with the parent reading, and the smallest decisive study it
proposes). Agent-authored: treat claims as leads, not verified results; the ones that mattered
were re-checked by the parent and the outcome is recorded in the synthesis.

| report | one-line content | where its findings landed |
|---|---|---|
| `TouchHarvestChallenger.json` | Harvest/"sell into strength" critique: exposure decomposition invalidated by phantom carry exposure, gain fragility (month-blocked CI95 [−20.3, +183.9] bps/day), exposure removal is not a mechanism in this engine, execution-convention framing error | synthesis §2.1, §2.3, §2.7 (its proposed placebo, run) |
| `PathStateThinker.json` | Causal state-vector inventory with provenance and gaps; `retained = close/running_peak − 1` is the coordinate monotone in forward value in both blocks | §2.2 |
| `HoldConvexityChallenger.json` | The conditional-hold case: mixture confirmed, late-touch mean is a right-tail lottery, the only executable arm is the unconditional +30 exit | §2.4 |
| `RankMigrationAnalyst.json` | Rank/relative-strength state exists but was never tested as a decision; `rank_change` is not an adjacent migration, `rel_strength` is a return residual, `basket_score_disp` empty | §2.5, §2.8 |
| `FailureCollapseAnatomist.json` | No pre-standard-stop marker has a block-stable error-cost advantage; structural map is path-row weighted; named counterexamples (VS, AVTX, EYEN, HCAI); the ticket-level study that would settle it | §2.6 |
| `ThesisReconstructor.json` | Lineage and claim→evidence table; twelve rulers that drifted into apparent constants; the load-bearing claims never tested in their stated form; T10 contract/implementation mismatch | §2.9 |
| `GiantRunnerAnatomist.json` | The extreme tail is peak/late-continuation, not touch: +30 eliminates 100% of every MFE≥100% band; named giant anatomies; no single halt signature; T11 non-deduplication | §2.12 |
| `MultiSurvivorAnalyst.json` | Multi-survivor real at +5/+10% and rare at +30/+100%; rank-1 advantage is hindsight; handoffs unmeasured; F8 built from pre-C1 inputs (fixed, `F8_C1` regenerated) | §2.13, §3 M0 |

Artifacts cited by the reports are committed under `factory/artifacts/basket/phase2/`
(`DIAGNOSTICS_20260924/`, `PLACEBO_DIAG/`, `F8_C1/`, `HARVEST_DIAG/`, `F1_C1/`, `F3_C1/`) with their
producers in `factory/scripts/`. Engine and validator defects found by the swarm and fixed:
`basket_sim.py` (rule parameters now bind the run fingerprint) and `basket_f8_joint.py`
(contract-version guard; `F8_C1` regenerated from `F1_C1`).
