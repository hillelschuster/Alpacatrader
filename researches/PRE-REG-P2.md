# PRE-REG-P2 — pooled money-map re-measure (FROZEN 2026-09-08)

STATUS: FROZEN. This file must never be edited. Any change requires a new dated
file (PRE-REG-P2b, ...) with reasons stated. The point is to fix the plan before
any P2 number exists.

## Why this exists (goal link)
H11 died because a capture rule was built on a 9-month map and collided on 4 fresh
months (pooled net -2.13%, 0-for-4 meta-finding). P2 re-measures WHERE runner money
accrues on maximum pooled evidence BEFORE any new capture rule is designed. No
rule, no selection, no tuning in P2 — description only. Profit relevance: we only
spend design effort on map components that survive pooling.

## Dev pool (descriptive — all 13 months WILL be read)
2025-03, 04, 05, 06, 07, 08, 09, 10, 11, 12 + 2026-01, 02, 03.
Clean session data verified readable for all (2026-04..08 excluded — see holdout).

## Holdout (SEALED — 5 months, reserved for future capture-rule collision)
2026-04, 05, 06, 07, 08. No bar reads for any formulation, descriptive, or
exploratory purpose until a FROZEN capture rule exists and names these months as
its collision set. Byte-level existence checks for ops are allowed; opening any
parquet for analysis is forbidden. Violation voids the holdout.
LINEAGE NOTE: these months were used by the ML lineage's fresh-window NO-GO test
(a different program: LightGBM/E6 exposure). For the runner/money-map lineage they
are unseen. A future P3 rule that reuses ML-lineage features must disclose that
overlap in its own pre-reg; the holdout is clean only for runner-lineage formulations.

## Cohort (frozen — identical to gate_decomp_2025 / h11_collision definitions)
Gate name-day: RTH open in $1-50; >=120 session bars; >=30 bars by 10:30 ET;
gain from session open to last bar <=10:30 in [15%, 30%); >=1 halt-gap (>=5min
RTH minute-bar hole) ending by 10:30. No other filters. No variants.

## Metrics (fixed list — gross, no fills, no friction; economics deferred to P3)
On the gate cohort, pooled + month-blocked (13 rows), with n and distinct days:
- M1: halt-gap A distribution (pre-halt close -> reopen open) for qualifying halt
  events (gain_cum >=30%, hole >=11min): mean/med/win/p90. Morning (<12:30) split.
- M2: causal post-reopen remainder D10/D30 from next-bar-open (expectation from dev:
  ~0/negative — confirm pooled; this is the anti-scalp check).
- M3: hourly accrual from 10:30 entry: cumulative by 11:00/12:00/13:00/14:00/15:00/
  16:00 + per-hour increments.
- M4: 13:30->close increment (the H11 leg), gross.
- M5: tail anatomy pooled: mean excl top-5/top-10, median, win rate, winners-mean
  vs losers-mean, monthly means.
- M6 (secondary, labeled): green-vs-red at 13:30 split of M4. Pre-existing dev
  split; reported for completeness, NOT a finding until holdout-confirmed.

## Decision gates (what follows from each outcome)
- Map replicates (same-sign afternoon wave + morning gap structure, similar
  magnitude order, pooled): P3 capture design may use the map as its idea mine;
  P3 collides ONLY on the sealed holdout (2026-04..08).
- Map vanishes pooled: retire the map; return to phenomenology; no capture
  design until a new replicated structure appears.
- Mixed (some components hold, some don't): P3 may use ONLY the replicated
  components, stated explicitly in its own pre-reg.

## Execution constraints
- One new script (moneymap_pooled.py or equivalent), committed + reviewed BEFORE
  running. Incremental per-day JSONL writes + resume (infra lesson). Producer
  committed per provenance doctrine; distinct days reported per cohort.
- Output: factory/artifacts/moneymap_pooled.json (committed) + STATE.md entry.
- This pre-reg covers description only. P3 needs its own pre-reg after P2 lands.
