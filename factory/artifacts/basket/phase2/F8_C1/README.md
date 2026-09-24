# F8 — unconditional joint basket economics

**Evidence: [RUN]** Full unconditional analysis of all 120 validated frozen F1 cells, each retaining the same 1,066 development dates. All result ranges below are across the complete tested surface; no cell is selected or promoted.

## Definitions

- A sleeve-day is one independent F1 run/day with fixed `C0=1`; tickets are the actual filled concurrent positions in that run, at the run's actual 100/150-bps friction. Cash/blocked/missing slots stay cash; no replacement or leverage is inferred.
- `pnl_c0` is the F1 end-of-day basket return in C0 units (sum of ticket `net`; it reconciles exactly to F1 `daily.r_day`). Tickets with `open_end=true` have no realized exit: their `net` is the contract's end-of-day mark. `n_closed`/`n_closed_profitable` count actual exits, while `n_open_end` and `n_eod_positive` expose still-open marks. `all_profitable`/`ge2_profitable` therefore mean EOD net-positive among filled tickets, not all tickets realized an exit profit. Reach H means actual post-fill `mfe_raw >= H%`; empty days remain in all-day denominators and are not called all-profitable.
- Pairwise correlation pools all unordered pairs of concurrent member `net_return` observations within sleeve-days. F1 tickets have no rank field, so no rank-pair estimate is invented. Member MFE and net-return distributions are ticket-level; sleeve EOD distribution is the C0 `pnl_c0` distribution.
- Golden-window conditional results remain **pending F2/F12** state-panel artifacts. This report is unconditional only.

## Full-surface probability ranges

Ranges are minimum–maximum probabilities on the 0–1 scale across all 120 F1 cells, not confidence intervals.

| Outcome | Pooled | Block 1 (2021-02–2023-12) | Block 2 (2025-02–2026-05) |
|---|---:|---:|---:|
| All filled positions EOD net-positive (realized + marked) | 0.0028 to 0.1520 | 0.0027 to 0.1635 | 0.0000 to 0.1506 |
| At least 2 positions EOD net-positive (realized + marked) | 0.0366 to 0.3837 | 0.0381 to 0.3610 | 0.0331 to 0.4518 |
| All filled positions reach +5% MFE | 0.2045 to 0.5056 | 0.1703 to 0.4891 | 0.2289 to 0.5783 |
| All filled positions reach +10% MFE | 0.0488 to 0.2974 | 0.0463 to 0.2657 | 0.0392 to 0.3675 |
| At least 2 reach +20% MFE | 0.0713 to 0.3246 | 0.0504 to 0.2793 | 0.0994 to 0.4247 |
| At least 2 reach +30% MFE | 0.0281 to 0.1623 | 0.0177 to 0.1281 | 0.0422 to 0.2380 |

Complete cell-by-cell pooled and dual-block metrics: `joint_surface.json`. Every per-cell/per-day joint count, C0 sleeve outcome, and filled-member MFE/return vectors: `joint_daily.parquet`. Input SHA-256 hashes and date-grid provenance for all five input files in each of the 120 cells: `provenance.json`.

## Interpretation and limits

F8 describes whether the concurrent filled members tended to finish EOD net-positive together, finish negative together, or produce multiple meaningful survivors under each already-run F1 implementation. Across cells, pooled all-filled-EOD-net-positive rates span 0.3–14.9% and at-least-two-+30%-touch rates span 1.0–15.8%; this breadth is fragile to the F1 implementation and does not support a surface-wide joint success claim. Overlapping block ranges do not establish per-cell stability. These descriptive ranges are not inferential comparisons: the cells share days and are not independent. A positive tail-touch rate does not establish executable profitability; the F1 net outputs and state-conditioned F2/F12 work must be read separately. No strategy, edge, or preferred cell is promoted. Sealed and reserved periods are absent because inputs are exclusively the frozen F1 development outputs.
