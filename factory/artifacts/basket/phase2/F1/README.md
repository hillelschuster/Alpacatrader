# F1 — entry family and basket breadth

**Evidence:** [RUN] 120 frozen cells, each on the same 1,066 permitted development days; see `surface.json` and each cell's `config.json`, `metrics.json`, and `run_summary.json`. Grid: A_pm, A_pm31, A_open, B585, B600 × N={2,3,4} × hold/R1(-8,-10,-15) × {100,150} bps. Parameters, seed 20260922, contract hash/version, and daily artifacts were checked against the frozen family specification and simulator contract. No cells were rerun.

## Descriptive read

Across the entire surface, mean basket-day return ranges from **-4.60% to -1.99% at 100 bps** and **-5.07% to -2.44% at 150 bps**. The pre-registered dual blocks are reported without selecting cells: in **2021-02–2023-12 (35 months)**, per-cell block means range from **-4.90% to -2.16% at 100 bps** and **-5.37% to -2.59% at 150 bps**; in **2025-02–2026-05 (16 months)**, ranges are **-5.29% to -0.94% at 100 bps** and **-5.73% to -1.42% at 150 bps**. These are descriptive ranges across tested cells, not inferential comparisons.

F1 measures entry timing and equal-capital breadth using primitive hold/stop exits. The full net surface is negative at both required frictions; this does not establish incremental tail capture versus failed-ticket cost or justify a cell. Entry/breadth and exit choices are not promoted as modules for a combined architecture. No out-of-sample or selection claim is made. These development results are **not** evidence of profitability, a frozen/selected strategy, or live readiness.

## Validation and limits

The existing `canary_recovery/canary_report.json` records all seven mandatory simulator canaries passing; canary 7 covers 1,066 days, 10,528 entries, and cash/deployed-capital invariants. Every cell has the five required outputs, unique daily rows for the exact permitted development-date set, and metrics consistent with its summary and daily returns. Sealed 2024/2025-01 and reserved 2026-06..08 were not read. Full surface: `surface.json`; family grid: `researches/PRE-REG-BASKET-02.md` §3.1; mechanics: `factory/BASKET-SIM-CONTRACT.md`.
