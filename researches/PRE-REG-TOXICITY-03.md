# PRE-REG-TOXICITY-03 — `range5` live-feed deployability check

Frozen 2026-09-13, before running. Measurement only. H025 and the live bot are
untouched; no adoption.

## Question
`range5` (trailing 5-min (high−low)/close at the anchor `t0`) passed on the
research tape (EXP-80). Two deployability risks remain:
- **Q1 — feed proxy.** The frozen threshold 0.1321 is a full-tape median. The
  live bot sees IEX-only bars (sparse: median 24.6% of session minutes). Does
  `range5` recomputed **from the IEX tape** still separate the fills?
- **Q2 — time-of-day confound.** Is `range5` just an AM/PM proxy?

## Population
- Q1: frozen OOS fills 2024-01..2025-02 (n=541; pf2 n=381). The IEX tape exists
  only for 2024-01..2026-08, so the backbone years cannot be tested this way
  (stated limitation).
- Q2: pooled fills 2021-2026 (n=1,392).

## Frozen definitions
- Reference feature: `toxicity_state.feats` on `data/leaderboard` frames
  (identical code path; no re-derivation).
- IEX feature: the **same** `feats` function on `data/iex_tape/path_*.parquet`
  frames (same frozen schema).
- Threshold: `THR = 0.13214` (median `range5` of all 1,392 fills, as frozen in
  TOXICITY-02). Also report the IEX-distribution median as a secondary split.

## Q1 metrics (OOS)
- `coverage` = share of fills with a finite IEX `range5`.
- `agreement` = share of covered fills where the IEX hi/lo call equals the
  full-tape hi/lo call at `THR`.
- `gap_full` = mean(ret | full hi) − mean(ret | full lo); `gap_iex` = the same
  using IEX `range5`, both on the covered subset; pf2 primary.
- **Q1 PASS** iff coverage >= 0.70 AND sign(gap_iex) == sign(gap_full) AND
  gap_iex >= 0.50 * gap_full.

## Q2 metrics (pooled, full tape)
- `range5` mean/median by session; Spearman(`range5`, `t0`).
- Hi/lo EV split **within AM** and **within PM** at `THR`.
- **Q2 not confounded** iff hi > lo in both strata.

## Verdict
`DEPLOYABLE-PROXY` iff Q1 PASS and Q2 not confounded; `PROXY-WEAK` if Q1 passes
but Q2 is AM-only; `NOT-DEPLOYABLE-AS-IS` if Q1 fails. Any of these is
measurement; adoption still requires a separate pre-reg and the user's go.

Artifact: `factory/artifacts/lb18_range5_live.json` + `.parquet`.
