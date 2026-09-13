# PRE-REG-ROBUST-RANGE5-01 — stability stress-test of the range5 conditioner

Frozen 2026-09-13. Measurement only; **no adoption, no bot change, H025 untouched.**
Purpose: before anyone considers arming on `range5`, stress it for overfit along
the axes that can still be tested on existing data. This is a *stability* pass,
not a new significance hunt.

## Population and primary subset
Pooled fills as in TOXICITY-01/02: 541 frozen OOS (2024-01..2025-02) + 851
backbone (2021-02..2023-12) = 1,392, deduped on (date,ticker,tf). Primary subset
**pf2 (prior_flush >= 2)**, since that is the deployable population. `ret` is net
of 100bps as recorded in the fill artifacts. `range_win` = (max high - min low) /
close at t0 over the trailing `win` minutes ending at t0 (>= 3 bars required).

## Frozen tests (all five must pass for STABLE)
- **R1 per-year:** with the pooled median threshold (0.13214), the pf2 hi-lo gap
  must be positive in >= 4 of the 5 years {2021, 2022, 2023, 2024, 2025}, with
  n_hi >= 30 and n_lo >= 30 in each counted year.
- **R2 window-length sensitivity:** recompute with win in {3, 10, 20}; the pf2
  hi-lo gap must be positive for >= 2 of the 3 alternative windows (the effect
  should be a volatility state, not a magic 5).
- **R3 month-blocked LOMO:** for each calendar month, set the threshold to the
  median of the *other* months' fills, apply to that month, and compute its
  hi/lo split. Aggregate: >= 55% of months with n_hi,n_lo >= 5 must have a
  positive gap. Also report the pooled LOMO gap with a day-clustered bootstrap.
- **R4 half-year sign stability:** calendar halves 2021H1..2026H1; the pooled
  pf2 gap must be positive in >= 70% of halves with n >= 20.
- **R5 threshold profile:** gaps at quantiles {0.33, 0.50, 0.67, 0.75}; at least
  3 of 4 positive AND no sign flip between adjacent quantiles (monotone-ish), so
  the result is not a knife-edge at one cutoff.

## Frozen verdict
- **STABLE** iff R1-R5 all pass.
- **FRAGILE** iff any of R1, R3, R4 fails (time-stability failures).
- **MIXED** otherwise (e.g. only R2 or R5 fails); the failing test is named.
No threshold re-selection beyond the frozen quantiles; no new features; no
per-subgroup fishing beyond the pre-declared cuts. Selection risk is
acknowledged: these are further looks at seen data, so a STABLE verdict raises
confidence but cannot establish the edge — that still requires forward fills.

## Artifacts
`factory/artifacts/lb18_range5_robust.json` + `.parquet` (per-fill ranges for
each window); producer `factory/scripts/range5_robust.py`.
