# PRE-REG-DAYTYPE-01 — Day/session context of the flush edge

Frozen 2026-09-13 (agent) under the user's standing goal: "only goal is
profitability, as realistically as possible, with top gainers." No computation
may run before this file is committed.

Status: seen-data measurement → a passing result may only produce a filter
CANDIDATE. No adoption without a new pre-reg + forward paper validation.

Motivation (user hypothesis): a day with one qualifying top gainer differs from
a day with several — attention composition varies. All prior probes averaged
over days; the flush edge's H2 flattening may be day-composition, not decay.

## Populations (both seen data for the rule)
- Primary: OOS fills `factory/artifacts/lb18_oos_oos.parquet` (n=541; pf2 381),
  2024-01..2025-02.
- Replication: dev fills `factory/artifacts/lb18_oos_dev.parquet` (n=937;
  pf2 658), 2025-03..2026-08.
Entry rule, exits, friction untouched; this study conditions/measures only.

## Frozen cuts — exactly five, no search, no threshold tuning
For each fill (date, ticker, tf), all features causal (known at tf):
1. `n_strict_names_sofar` — distinct tickers with >=1 strict-state minute
   (gain>=1.0, pullback>=-0.01, r15>=0.03 on lb top-3 rows joined to the path
   grid, engine semantics) strictly before tf. Bins: {0-1} vs {2+}.
2. `n_flush_names_sofar` — distinct tickers with >=1 flush start (new bar with
   low <= 0.9 * running session max close) strictly before tf.
   Bins: {0-1} vs {2+}.
3. `rank` — {1} vs {2-3}.
4. `prior_stop_today` — any earlier fill the same date with exit_t < tf and
   ret < -0.05. {no} vs {yes}.
5. `session` — tf < 12:00 ET {AM} vs >= 12:00 {PM}.

## Metrics per bin
n, mean and median net ret (100bps already applied), win rate, months positive,
worst month, worst-5 fills' share of total; the same restricted to pf2.

## Frozen gate — MATERIAL FILTER CANDIDATE (all five required, on OOS)
(a) worst bin mean <= population mean - 0.30pp with n_bin >= 50;
(b) excluding that bin lifts the pooled mean by >= +0.20pp;
(c) the bin's mean is below population in >= 2 of 3 chronological OOS blocks;
(d) excluding it retains >= 60% of fills;
(e) same direction on dev (bin mean < dev population mean).
Multiplicity: 5 cuts are declared here; report Bonferroni 0.05/5 as context;
the gate above is the decision rule. No other cuts may be computed.

A passing candidate is NOT adopted: it requires a new pre-reg + a forward paper
test. A failing study closes the day-context axis at this granularity.

## Artifacts
`factory/artifacts/lb18_daytype.json` + `.parquet` (per-fill features, bins,
population flag) from `factory/scripts/lb18_daytype.py`. Ledgers appended.

---

## Amendment A1 — robustness of the H029 candidate (frozen before running)

The base study's n_strict_names_sofar cut passed the gate on OOS but does not
replicate within rank1 on dev. Before any forward claim, run exactly these six
robustness checks (no new cuts, no adoption):

- R1 leave-one-month-out: sign of (mean 0-1 minus mean 2+) with each OOS month
  dropped in turn. Pass if the sign holds in >=12/14 folds (pooled; rank1
  reported alongside).
- R2 day-clustered bootstrap: resample OOS dates with replacement (5000 draws);
  95% CI of the difference, pooled and rank1. Pass if the CI excludes 0 in both.
- R3 secondary population: the A3b deployable-hybrid fills from
  `lb18_iex_hybrid.parquet`; direction check only (busy < quiet).
- R4 alternative breadth definition: count of tickers with `gain_c >= 1.0` at
  tf-1 on the path grid; bins {0-1} vs {2+}; direction check on OOS and dev.
- R5 concentration: report the top-5 (date,ticker) contributors to the 2+
  bucket's negative sum; recompute the difference excluding all fills of the
  top-3 names by absolute contribution. Pass if the sign survives.
- R6 permutation placebo: 5000 within-population label shuffles preserving bin
  sizes; report the share with difference >= observed (context only, no gate).

Verdict rule: H029 stays CANDIDATE only if R1, R2 and R5 pass and R4 agrees in
direction on OOS; otherwise downgrade to "OOS-only, not robust". Forward paper
remains the only adoption path.
