# PRE-REG-TOXICITY-02 — Does the range5 conditioner improve the deployable (pf2) population?

Frozen 2026-09-13, measurement only. H025 and the live bot untouched.

## Purpose
TOXICITY-01/A1 left one surviving causal conditioner: **range5** (trailing
5-min high-low range / close at the anchor). This study asks the only question
that matters for deployment: does it improve the **pf2 subset** (the population
H025's economics actually lean on), and what does a practical cut cost in
frequency?

## Population & features
Same pooled fills as TOXICITY-01 (1,392 = 541 OOS 2024-01..2025-02 + 851
backbone 2021-02..2023-12), features from `lb18_toxicity.parquet`.
- Baseline: `prior_flush >= 2` (pf2), all of it.
- Cut: `range5 >= median(range5)` computed on **all pooled fills** (median from
  the full sample, not from pf2 — no in-sample tuning). Call it HIGH; the
  complement is LOW.
- Descriptive only: pf2 range5 quartiles.

## Metrics (pooled and per era)
n, mean, median, months+, worst month, share of pf2 fills retained.

## Frozen gate for a filter candidate
HIGH-on-pf2 must satisfy all of:
1. mean >= pf2 baseline mean + 0.30pp;
2. months+ >= pf2 baseline months+ − 1;
3. retained fills >= 50% of pf2 baseline;
4. day-clustered bootstrap (2,000 draws, seed 20260913) of the difference
   (HIGH mean − LOW mean) has 95% CI lower bound > 0.
Else NEGATIVE. No adoption either way; a pass still requires its own forward
test before any live use.

## Known limits (frozen with the design)
- Features come from the research tape; the live bot would compute range5 from
  IEX 1-min bars (sparser, 24.6% median session-minute coverage). The live
  proxy is NOT validated here — it is part of what a forward test must prove.
- Sample is heavily overlapping with H025's own fills; this is a filter on the
  same population, not new alpha. Seen data; not an alpha claim.

Artifacts: `factory/artifacts/lb18_toxicity_pf2.json` from
`factory/scripts/toxicity_state.py --pf2`.
