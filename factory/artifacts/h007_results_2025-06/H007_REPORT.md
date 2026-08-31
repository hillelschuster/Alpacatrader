# H007 — Precursor contraction before breakout vs uninterrupted grind (2025-06 certified)

**Verdict: REJECTED.** Consolidation-before-breakout does **not** beat uninterrupted grind after 20bps RT.
The control (non-contraction / wide range) is positive at 30m & 60m; contraction is negative at every
horizon. On the June-25+ OOS slice, control's edge collapses to ~0 and contraction is negative.

## Method
- Segment A (hypothesis): signal bar clears the prior **15-min rolling high** (`close > prev_cons_high`)
  and the trailing 15-min range `prev_range_pct < 1%` → `contraction`.
- Segment B (control): same breakout signal but `prev_range_pct >= 1.5%` → `non_contraction` (grind).
- Min `pct_gain >= 5%`, RTH 09:30–15:30, 5-min dedup per (ticker, day, group). No lookahead:
  `prev_cons_high` / `prev_range_pct` are the 15-min window shifted **1 bar**, both observable at t-1.
- Fwd returns via exact timestamp join to clean OHLCV close at +5/15/30/60m. MFE/MAE = next 60m high/low vs sig close.

## Primary — 20bps roundtrip (`--cost-bps 10`, cost = 0.002)

| Segment | n | h30m avg | h30m wr | h30m exp_net | h60m avg | h60m wr | h60m exp_net |
|---|---|---|---|---|---|---|---|
| **contraction** | 16,676 | +0.001% | 0.469 | **-0.199%** | +0.021% | 0.477 | **-0.179%** |
| **non_contraction** (control) | 24,820 | +0.252% | 0.499 | **+0.052%** | +0.441% | 0.500 | **+0.241%** |

- Contraction beats control at **no** horizon. Control's h60 exp_net +0.241% vs contraction **-0.179%** → spread **-0.420pp**.
- h5m: contraction -0.200%, control -0.154% (both negative). h15m: contraction -0.194%, control **-0.028%**.

## Sensitivity — 40bps roundtrip (`--cost-bps 20`, cost = 0.004)

| Segment | h30m exp_net | h60m exp_net |
|---|---|---|
| contraction | -0.399% | -0.379% |
| non_contraction | -0.148% | **+0.041%** |

Control stays barely positive at 60m; contraction firmly negative. Sensitive to cost, but direction holds.

## Segment breakdown (contraction, 20bps)
- **By time bucket**: all four buckets negative at 60m (09:30-10:00 -0.249%, 10:00-12:00 -0.154%,
  12:00-14:00 -0.194%, 14:00-15:30 -0.177%). No time-of-day rescue.
- **By gain_bin**: 60m exp_net <10%: -0.183%, 10-20%: -0.156%, 20%+: -0.171%. No gain-tier rescue.

## OOS status (June 25+; degenerate)
Only 4 sessions OOS (06/25, 06/26, 06/27, 06/30): train n=34,028 / **test n=7,468**. Test is a 4-session
sliver, not a valid OOS generalization sample. Report honestly — no fabrication of out-of-sample edge.

| Segment | train h60 exp_net (n) | test h60 exp_net (n) |
|---|---|---|
| contraction | -0.198% (9,283) | -0.075% (1,668) |
| non_contraction | +0.297% (10,691) | -0.008% (2,426) |

Control's +0.30% train edge → **-0.01%** test. The 20bps edge does not survive the tiny OOS slice.

## Notes
- First session 2025-06-02 dropped (no prior_close in June-only clean) — same as H009/H006 behavior.
- H007 derives `pct_gain` from clean OHLCV itself and does **not** join the certified events file; this is
  the established H009 pattern. Signal = 15-min <1% contraction (the "<1% for 15m" arm of the
  hypothesis). The rolling-ATR arm ("<40% of rolling ATR") is not separately implemented.
- Files: `results_c10.parquet` (2.1M), `results_c20.parquet` (2.1M), `*_summary.json`, `run_*.log`.
