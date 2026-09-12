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
