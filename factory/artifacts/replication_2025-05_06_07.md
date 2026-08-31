# May / June / July 2025 — H006 + faithful H009 after-cost comparison

Faithful H009 = trap reclaim closing above prior running RTH HOD (validated vs June report: n=34,916, edges +20.0/+28.6bps @30/60m).
All numbers net of roundtrip cost; exp in %, edge vs clean-control in bps.

## H006 — short fade when vwap_dist > 8% (exp_short_net %)

| month | RT | n(15m) | 15m | 30m | 60m | 12%+ n | 12%+ exp 15m | 12%+ avg 15m |
|---|---|---|---|---|---|---|---|---|
| 2025-05 | 20bps | 2236 | 0.007 | 0.014 | -0.052 | 517 | 0.128 | -0.328 |
| 2025-05 | 40bps | 2236 | -0.193 | -0.186 | -0.252 | 517 | -0.072 | -0.328 |
| 2025-06 | 20bps | 2098 | 0.493 | 0.581 | 0.544 | 247 | 3.085 | -3.285 |
| 2025-06 | 40bps | 2098 | 0.293 | 0.381 | 0.344 | 247 | 2.885 | -3.285 |
| 2025-07 | 20bps | 2581 | -0.255 | 0.071 | 0.33 | 1070 | -0.065 | -0.135 |
| 2025-07 | 40bps | 2581 | -0.455 | -0.129 | 0.13 | 1070 | -0.265 | -0.135 |

## H009 faithful subsets — long reclaim above prior HOD (exp_net %, edge vs clean bps)

loose = trap & close>prior_HOD (June-report convention); strict = trap & HOD-dip & close>prior_HOD (May-ledger convention).

| month | slice | RT | variant | n | 15m e | 15m edge | 30m e | 30m edge | 60m e | 60m edge | wr60 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2025-05 | full | 20bps | loose | 39836 | -0.133 | 7.5 | -0.069 | 11.4 | -0.01 | 15.1 | 0.482 |
| 2025-05 | full | 20bps | strict | 34923 | -0.12 | 8.8 | -0.05 | 13.4 | 0.054 | 21.5 | 0.494 |
| 2025-05 | full | 40bps | loose | 39836 | -0.333 | 7.5 | -0.269 | 11.4 | -0.21 | 15.1 | 0.482 |
| 2025-05 | full | 40bps | strict | 34923 | -0.32 | 8.8 | -0.25 | 13.4 | -0.146 | 21.5 | 0.494 |
| 2025-06 | full | 20bps | loose | 34916 | -0.1 | 7.9 | 0.033 | 20.0 | 0.194 | 28.6 | 0.515 |
| 2025-06 | full | 20bps | strict | 31281 | -0.104 | 7.5 | -0.009 | 15.9 | 0.158 | 25.0 | 0.513 |
| 2025-06 | full | 40bps | loose | 34916 | -0.3 | 7.9 | -0.167 | 20.0 | -0.006 | 28.6 | 0.515 |
| 2025-06 | full | 40bps | strict | 31281 | -0.304 | 7.5 | -0.209 | 15.9 | -0.042 | 25.0 | 0.513 |
| 2025-07 | full | 20bps | loose | 76232 | -0.094 | 4.4 | 0.01 | 13.8 | 0.103 | 20.4 | 0.503 |
| 2025-07 | full | 20bps | strict | 68398 | -0.101 | 3.7 | -0.007 | 12.1 | 0.088 | 18.9 | 0.502 |
| 2025-07 | full | 40bps | loose | 76232 | -0.294 | 4.4 | -0.19 | 13.8 | -0.097 | 20.4 | 0.503 |
| 2025-07 | full | 40bps | strict | 68398 | -0.301 | 3.7 | -0.207 | 12.1 | -0.112 | 18.9 | 0.502 |
| 2025-07 | July-only | 20bps | loose | 41316 | -0.089 | 1.3 | -0.009 | 8.5 | 0.028 | 13.7 | 0.494 |
| 2025-07 | July-only | 20bps | strict | 37117 | -0.098 | 0.4 | -0.005 | 8.8 | 0.031 | 14.0 | 0.494 |
| 2025-07 | July-only | 40bps | loose | 41316 | -0.289 | 1.3 | -0.209 | 8.5 | -0.172 | 13.7 | 0.494 |
| 2025-07 | July-only | 40bps | strict | 37117 | -0.298 | 0.4 | -0.205 | 8.8 | -0.169 | 14.0 | 0.494 |
