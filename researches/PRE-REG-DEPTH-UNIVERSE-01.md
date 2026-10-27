# PRE-REG-DEPTH-UNIVERSE-01 - widen the scored universe for the frozen depth rule

Registered: 2026-10-27, BEFORE any widened-universe state, signal or outcome is computed.
Scope evidence: the 2026-10-27 universe-expansion scoping (panel rank cap K_TOP=10 in
sip_candidates.py:54 with the rank-11 sentinel in alpha_open_panel.py:139 / depth_bot_features.py:96;
the 5% admission score gate never binds; ranks 11..~15 already have bars via the margin band;
ranks ~16..50 need bars + NBBO quotes).

## Fixed (identical to PRE-REG-DEPTH-01)

Model (h60 head sha c5493c6c..., no refit), threshold 0.030, entry/size/exit/book/cost rules,
liquidity gate (log_cum_dv >= log1p(1e6) & bars15 >= 12), $1 price floor, snapshot clocks
(575,585,600,630,660,720), 5-minute lattice, latency 250 ms. Only the rank cutoff changes.

## The single variable

Rank cutoff K in {10 (control = PRE-REG-DEPTH-01), 20, 30} on the same open-anchored gain
ranking (gain = px_T / o570 - 1) and the same 0.05 admission score gate.

Encodings / arms:
- PRIMARY (registered) = ADDITIVE arm, encoding A: rows of the original top-10 universe are
  bit-identical to K=10 (their peer features are NOT recomputed); each extra name (rank 11..K)
  is scored with rank_snapshot = min(rank, 11) (in the training support) and peer features
  computed over the ORIGINAL top-10 watchlist (the same context the existing rows see).
- DIAGNOSTIC only: POOLED arm (peer features recomputed over the widened watchlist) and
  encoding B (rank_snapshot = min(rank, K+1)). Reported, never selected.

## Selection (2023 validation only; late 2025-02..2026-05 is descriptive)

Adopt K > 10 only if, on validation at 25 bps, the PRIMARY arm satisfies ALL of:
(a) $/session >= the K=10 control's +1.3635;
(b) $/session at 50 bps > 0;
(c) the EXTRA-name known fills (rank 11..K) have mean net at 25 bps > 0 and number >= 10;
(d) POOLED and ADDITIVE arms agree in sign of the K-vs-10 increment.
If several K pass, adopt the smaller K. If none pass, K stays 10 and widening is CLOSED for
this model. An adopted K becomes PRE-REG-DEPTH-02 and inherits PRE-REG-DEPTH-01's pristine
2024 test and paper plan; it does not replace the frozen rule before that test.

## Data guard

Only 2023-01..2023-12 and 2025-02..2026-05 may be acquired or read. 2024-*, 2025-01-* and
2026-06..08 stay sealed (code-guarded). Acquisition follows the staged plan: stage 0 = free
re-rank from the stored full-market RTH universe table; stage 1 = minute bars for the extra
names; stage 2 = NBBO quotes for extra (day, ticker) pairs in the K window.

## Report

Per K and arm: signals, funded, known fills, UNKNOWN share, $/session and $/calendar day at
0/25/50/100 bps, median fill, top-5 share, months positive, and the extra-name contribution
split out - for validation and late.
