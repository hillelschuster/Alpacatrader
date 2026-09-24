# F3 structural map → fixed release-module surface

**Status: descriptive prerequisite complete; module surface retained; no strategy promotion.**

## Structural-map prerequisite [RUN]

`structural_map/` contains causal own-path observations for canonical A_pm/T570 top-2 filled
positions over all 1,066 permitted development dates: block1 2021-02–2023-12 (734 days / 35
months), block2 2025-02–2026-05 (332 days / 16 months). The run config, hashes, monthly parquet
parts, merged daily parquet, relationship tables and provenance are retained there. The fixed
relationship vocabularies are entry-relative depth, below-entry duration, reclaim status,
peak-relative drawdown, and MFE surrendered; the relationship tables keep the complete
registered category cross-products, including empty cells.

At a row for completed bar `t`, entry-relative close drawdown, consecutive closes below entry,
running high through `t`, close drawdown from that running high, MFE through `t`, MFE surrendered,
bars since that high, and failed reclaim are state only. Recovery latency is completed bars
since the low in the current below-entry episode; `recovery_bars_from_episode_low` is null until
entry reclaim is observed. No future high/peak enters state. Future high/low/close outcomes use
bars with `et > t` only and are measured from the decision close; a bar's high at `t` is never a
future outcome. A row with no later bar has unavailable/null outcomes, not a fabricated value.

These are descriptive associations, not fitted triggers, thresholds, gates, composites, cell
selection, action effects, profitability, or confirmation. No finding is used to narrow the
registered surface.

## Existing fixed module surface [RUN]

The original 20-cell A_pm/N2 surface is retained without rerun: R0 control (100/150 bps), six
R2 cells (`L={10,15}` × `w={3,5,10}` × 100/150 bps), and three R3 cells (`g={40,50,60}` ×
100/150 bps). Audit confirmed each cell has exactly 1,066 canonical daily rows, all 51
canonical monthly daily parts, complete configured grid/provenance against the F3 run hashes,
and no temporary outputs. The existing simulator and cell outputs were not edited.

The map is a prerequisite in sequence, not an outcome-fitting dataset for these cells. The
module grid and A_pm/N2 path were already fixed by PRE-REG-BASKET-02 and the existing F3
producer; the structural map does not create or select any additional release setting. Module
results remain the separate descriptive fixed finite test documented in `README.md` and
`surface.json`, not evidence of profitability or authorization to promote a strategy.

Block-level first-touch/action chronology is not retained in the native F3 ticket rows, so the
existing module report leaves block false-release rates unavailable/null; the map does not
manufacture a release chronology or substitute an outcome-derived proxy.

## Boundary and verification

Only canonical development anatomy, bars and session calendar were accessed. The producer
refuses dates outside the two allowed spans and verifies the exact 1,066-day / 734+332 block
counts. Sealed 2024 / 2025-01 and reserved 2026-06–08 were not read. Structural output and the
retained module surface do not authorize selection, live use, or an out-of-sample claim.
