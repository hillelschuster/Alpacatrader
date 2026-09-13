# PRE-REG-PERSIST-01 — H034 probe 2: cross-day survivor persistence

Frozen 2026-09-13. Measurement only. H025 and the live bot are untouched; no
trading rule is adopted. This is a post-hoc split of an already-seen fill set,
so any positive is a CANDIDATE requiring a new pre-reg + forward test.

## Motivation
The 2021–2023 replication (H035/EXP-75) showed the in-session `prior_flush>=2`
survivor conditioning is the load-bearing element of H025, while the broad rule
does not travel. H034 holds that this is a NAME-level latent survivor state.
Probe 1 (post-recovery drift) was negative. Probe 2 asks the other half: does
the survivor state persist ACROSS days, and does yesterday's status condition
today's frozen fills?

## Definitions (frozen)
- Survivor day: a (date,ticker) present in the causal top-3 tape with
  `n_events >= 2` vacuum starts that session (census event definition, new bars,
  `low <= 0.9*running session max close`).
- `prev_survivor` for a fill: the ticker's survivor flag on the PREVIOUS trading
  day in the tape's own date sequence. Causal (known before today's fill).
  `-1` = ticker absent from that previous day's tape.
- Fills: `lb18_backbone_fills.parquet` (2021-02..2023-12, n=851) joined with
  `lb18_oos_oos.parquet` (2024-01..2025-02, n=541), deduped on
  (date,ticker,tf). `ret` is net of the standard 100bps friction.

## Frozen outputs
1. Persistence table over all consecutive-day (date,ticker) pairs: base rate,
   P(survivor | prev survivor), P(survivor | prev non-survivor), lift, by era
   (2021–2023 vs 2024–2026 where the tape allows).
2. Fill EV splits: all-fills and pf2, each by prev_survivor in {1, 0, absent},
   with n, mean, median, months+, worst month, and era means.

## Frozen gate (candidate)
For the pf2 fill split with `prev_survivor=1`:
- n >= 200, AND
- mean >= pf2 baseline mean + 0.30pp (baseline = all pf2 fills), AND
- months+ >= 60%, AND
- mean > 0 in BOTH era blocks (2021–2023 and 2024–2025).
All four required. Otherwise NEGATIVE. Persistence lift is reported
descriptively, not gated.

## Explicit non-claims
Not a resurrection of H005 (rank persistence) — this is cross-day VACUUM
survivorship, not rank. No H025 modification; a positive becomes a candidate
selection gate needing its own pre-reg + forward validation.
