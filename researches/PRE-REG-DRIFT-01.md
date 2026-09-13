# PRE-REG-DRIFT-01 — H034 probe 1: forward drift after a recovered vacuum

Frozen 2026-09-13. Measurement only. **H025 untouched; the live bot untouched.**
No adoption path: a positive cell becomes a candidate requiring its own new
pre-reg + forward test.

## Motivation (why this is not a resurrection)
The 2021–2023 backbone replication (`EXP-75`/`H035`) showed the **pf>=2 survivor
conditioning is the load-bearing element**: the broad all-fills rule does not
travel across regimes (+0.14%/+0.34%/-0.56%), while pf2 reproduces
(+1.08%/+1.08%/+1.95%). The census (`EXP-72`) showed event-level prior_flush is
*inverse*, so "survivor" is a name-level state. H034 asks whether that state pays
in a *different* way than the frozen resting-bid catch.

This probe tests one specific manifestation: **after a top-3 gainer has suffered
a >=10% vacuum and recovered it (re-touched its pre-vacuum running-max close
within 30 min), does a plain hold earn positive forward drift?**

This is not H001 (first-pullback continuation), H005 (rank persistence), H006
(VWAP fade) or H022 (afternoon rental): it is conditioned on a validated event
(vacuum + recovery) and is a hold, not a bid. Killed families stay killed.

## Population and anchor (causal)
- Events: all vacuum events from `event_census` (E1 semantics: new bar with
  `low <= 0.9 * running session max close`), 2021-02..2026-08 (all 1,402
  usable days), restricted to `recovered_30m=True` with finite
  `t_recover_min`.
- `t_rec` = event minute + `t_recover_min` (the minute the pre-vacuum running
  max is re-touched). The recovery is only known once that bar completes, so
  **entry is at the first grid minute strictly after t_rec** (close), never at
  `t_rec` itself.
- Exit: grid close at the last minute <= entry_minute + H, H ∈ {15, 30, 60} min.
- Net: `exit/entry - 1 - 0.01` (100bps friction).

## Frozen control (paired, one only)
For the same (date, ticker, H): enter at the first minute strictly after the
**event minute t** (i.e., the same name-day path without requiring recovery),
same exit rule. Cell mean is compared against this control; the paired
difference in pp is the effect.

## Frozen cuts
`seq` ∈ {1, 2, 3+}; `rank` ∈ {1, 2, 3}; `session` ∈ {AM (<12:00 ET), PM};
`key` = rank==1 AND seq>=2; `all`. Each reported overall and split by period
block {2021-2023, 2024-2026}. No other cuts; no horizon/cut selection after
seeing results.

## Frozen gate (evaluated at H ∈ {30, 60})
A cell is **CANDIDATE** iff all of:
- n >= 300;
- mean net > 0;
- mean net − paired control >= +0.30pp;
- months+ >= 60% of months in the cell;
- mean net > 0 in **both** period blocks.
Otherwise **NEGATIVE**. Exactly one verdict per cell, reported verbatim.

## Artifacts
`factory/artifacts/lb18_drift.json` + `.parquet`, producer
`factory/scripts/drift_survivor.py`. Flags: measurement / seen-data /
not-an-alpha-claim. Ledgers appended after the run.
