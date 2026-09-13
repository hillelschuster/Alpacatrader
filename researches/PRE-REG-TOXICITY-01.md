# PRE-REG-TOXICITY-01 — Can causal pre-anchor state identify toxic flush fills?

Frozen 2026-09-13, measurement only. H025 and the live bot untouched. H037.

## Motivation (from PRE-REG-SIZE-01)
Fill-toxicity is strongly graded by **fill-minute** at-bid volume (quartiles
+2.68/+1.10/+2.13/−1.39%, breach 21.9% vs 45.3%), but that is only known after
the fill. If a **causal** state known at the anchor (bid-arm time) predicts the
toxic fills, those bids can be skipped — which would raise both the worst month
and the safely-sizeable notional. If nothing causal predicts toxicity, H037 is
closed.

## Population & data
All frozen-engine fills with an anchor in the causal top-3 tape:
`lb18_oos_oos.parquet` (541, 2024-01..2025-02) + `lb18_backbone_fills.parquet`
(851, 2021-02..2023-12), deduped on (date,ticker,tf). Path grid from
`data/leaderboard/path_{day}.parquet`. No API calls, no new fetch.

## Causal features (all from grid bars with `et <= t0`, the anchor minute)
- `vol5_ratio`: sum(volume) over the 5 grid minutes ending at t0, divided by the
  median of all such 5-minute sums so far that session (before t0).
- `range5`: (max(high) − min(low)) / close(t0) over the same 5 minutes.
- `pullback`: close(t0)/running-max(close ≤ t0) − 1 (the strict-state depth).
- `price_band`: c0 = close(t0): <$4 / $4–10 / >=$10 (SIZE-01 showed the toxicity
  gradient lives in cheap names).
- `rank`, `prior_flush`, `session` (AM/PM) — descriptors, not filters.

## Toxicity markers
- Primary: `ret` (net, frozen exit, 100 bps).
- Secondary: `fc < 0` (fill-bar close below bid = gap-through-like) and
  `ret <= −0.10` (stop-like).

## Frozen tests
For each feature (`vol5_ratio`, `range5`, `pullback`; quartiles via qcut with
duplicates dropped) and for `price_band`: per-bin n, mean ret, `fc<0` share,
`ret<=−0.10` share, months+; Spearman(feature, ret). Split by era
{2021–2023 backbone, 2024–2025 OOS}. **No other cuts, no interaction search.**

## Frozen gate ("licenses a filter candidate")
A feature passes iff: the worst bin mean <= best bin mean − 0.50pp AND both
extreme bins have n >= 80 AND the direction holds in BOTH eras AND removing the
worst bin retains >= 60% of fills with retained mean >= population mean.
Pass => a named filter candidate is recorded (still requires its own pre-reg +
forward test; nothing is adopted, nothing changes in H025). Fail => H037 closed
as a tested negative.

## Interpretation guard
This is a *filter* question, not a frequency expansion: a pass makes the
surviving population smaller and better, which matters only in combination with
the size flat zone. Any filter built here is a NEW formulation, explicitly not a
modification of the frozen rule. Seen data; not an alpha claim.

Artifacts: `factory/artifacts/lb18_toxicity.json` + `.parquet` from
`factory/scripts/toxicity_state.py`.

---

## Amendment A1 — robustness of the licensed candidates (frozen before running)

TOXICITY-01 licensed `vol5_ratio` and `range5` (the four-part gate passed; note
the surprising direction: HIGH pre-anchor activity fills better, LOW is the bad
bin). Before either counts as a real candidate, apply the H029 treatment — the
day-breadth candidate died exactly here. Frozen checks:
- **Day-clustered bootstrap** (2,000 draws, seed 20260913): resample days with
  replacement, recompute `best_bin_mean − worst_bin_mean` with the frozen
  pooled quartile labels; report the 2.5/97.5 percentiles.
- **Permutation** (2,000 shuffles of `ret` within the pooled sample):
  p = share(|diff_perm| >= |diff_obs|).
- **Confounds:** the same diff within rank strata {1,2,3,off} and within price
  bands {$4-10, >=$10}; strata with n<150 are reported as inconclusive.
- **SURVIVES** iff bootstrap 95% CI lower bound > 0 AND permutation p < 0.05 AND
  direction holds in both eras AND the confound rule (direction consistent in
  >=2 of 3 rank strata, and in both price bands). Otherwise RETIRED (OOS-only,
  not robust), exactly like H029.
No adoption either way; a surviving filter still needs its own forward test.
Artifact: `factory/artifacts/lb18_toxicity_robust.json`.
