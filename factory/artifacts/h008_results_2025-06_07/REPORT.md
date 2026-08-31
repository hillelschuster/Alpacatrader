# H008 — RVOL (attention surprise) vs 20-day TOD baseline — 2025-06/07

**Hypothesis (ledger):** top gainers whose cumulative dollar volume exceeds time-of-day expected volume (RVOL vs trailing-20-session baseline > 1.5) outperform same-absolute-dollar-volume / low-RVOL peers over 15–60m. Fixes H003's flaw (per-day dollar tercile without TOD normalization).
**Data:** certified `events_topN.parquet` 2025-06 (120,054) + 2025-07 (139,400); baselines built from staged clean May+June+July (65M bars, RTH only). **May events excluded** — no April data locally, their trailing 20-session baseline would be truncated. 26,964 events (11%) dropped: ticker's own history < 20 prior sessions (IPOs/halts).
**RVOL(t)** = cum$vol(t) / E[cum$vol(t)], where E is the ticker's own trailing-20-session mean cum$vol curve at hourly bucket ends, linearly interpolated within the event's bucket.
**Cost:** roundtrip = cost_bps × 2 (primary 20bps RT, sensitivity 40bps RT). Long side (continuation).

## Unmatched segments (exp_net, 20bps RT)

| month | segment | n | 15m e | 60m e | 60m wr |
|---|---|---|---|---|---|
| 2025-06 | RVOL>1.5 | 66,752 | −0.192% | −0.171% | 0.480 |
| 2025-06 | RVOL<1.0 | 19,670 | −0.230% | −0.337% | 0.447 |
| 2025-07 | RVOL>1.5 | 77,789 | −0.193% | −0.136% | 0.499 |
| 2025-07 | RVOL<1.0 | 20,429 | −0.235% | −0.296% | 0.482 |

## Volume-matched cells (the hypothesis's actual claim) — 60m exp_net @20bps RT

| month | dv tercile | RVOL>1.5 | RVOL<1.0 | edge |
|---|---|---|---|---|
| 2025-06 | high | −0.183% (n=25,792) | −0.254% (n=2,246) | +7.1bps |
| 2025-06 | mid | −0.180% (n=22,114) | −0.384% (n=3,461) | +20.4bps |
| 2025-06 | low | −0.125% (n=11,857) | −0.338% (n=10,012) | +21.3bps |
| 2025-07 | high | −0.185% (n=28,701) | −0.420% (n=2,538) | +23.5bps |
| 2025-07 | mid | −0.146% (n=25,433) | −0.427% (n=3,493) | +28.1bps |
| 2025-07 | low | −0.021% (n=14,523) | −0.219% (n=10,101) | +19.8bps |

**6/6 matched cells positive, both months, edge +7..+28bps** (15m same direction; 40bps RT spreads widen identically). Effect largest in low/mid absolute-$-volume names — the attention-surprise mechanism works as hypothesized. Best absolute cell: July dv_low RVOL>1.5 −0.021% ≈ breakeven; everything else net-negative.

## Stack probe: faithful H009 trap-reclaim × RVOL>1.5 (intersection, n=1,838)

| month | cell | n | 60m e @20bps |
|---|---|---|---|
| 2025-06 | trap+RVOL>1.5 | 639 | **+0.074%** |
| 2025-06 | trap+RVOL≤1.5 | 196 | −0.530% |
| 2025-07 | trap+RVOL>1.5 | 821 | −0.261% |
| 2025-07 | trap+RVOL≤1.5 | 182 | −0.406% |

June stacked (+60bps relative, net-positive @60m @20bps), **July did not replicate** (−26bps relative direction flip; all negative @40bps). Small samples. Not a trade.

## Verdict

- **Relative effect REAL and PERSISTENT**: volume surprise (RVOL>1.5 vs ticker's own TOD baseline) beats same-absolute-volume low-RVOL peers by ~+20bps @60m in 6/6 matched cells across 2 independent months. This is the first hypothesis in the ledger with a cleanly replicated, mechanism-consistent relative edge.
- **Absolute edge NOT executable**: the certified top-gainer universe's long side loses ~20–30bps RT on average; RVOL>1.5 recovers ~20bps of that but lands at ≈0 to −0.2%. Same signature as H009 (trap-reclaim, ~+14–29bps relative).
- **Stacking does not survive July** (June-only artifact).
- Consistent with the H001–H005 synthesis: after 20bps RT, raw top-gainer continuation is net-negative; structure/reclam and volume surprise are genuine *selection* factors (~+20bps each), not standalone strategies. The profitable tail documented in H001–H005 synthesis (short-side/mean-reversion) remains the only net-positive family tested so far — and H006's fade-tail also failed July OOS.

## Files written

- `factory/artifacts/h008_results_2025-06_07/h008_results.parquet` (c10, 219,540 events with rvol/cumdv/fwd)
- `factory/artifacts/h008_results_2025-06_07/h008_results_c20.parquet` (40bps RT; fwd columns identical)
- `factory/artifacts/h008_results_2025-06_07/h008_summary.json` (c10; reconstructed from parquet — c20 run overwrote the live summary, known footgun) + `h008_summary_c20.json`
- `factory/scripts/experiment_h008.py`
