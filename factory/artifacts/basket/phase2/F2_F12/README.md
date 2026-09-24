# F2/F12 — causal golden-window panel

**[RUN] Descriptive development-state read only.** Producer: `factory/scripts/basket_f2_f12.py`;
tests: `tests/test_basket_f2_f12.py`. Frozen scope: `researches/PRE-REG-BASKET-02.md` §3.2;
timing/input contract: `factory/BASKET-SIM-CONTRACT.md`.

## Frozen scope and artifacts

- Inputs: canonical anatomy, minute bars, and phase-2 session calendar only. `coverage.json`
  records SHA-256 manifests for all 1,066 anatomy/bar files and the calendar/contract.
  Simulator guards reject sealed 2024/2025-01 and reserved 2026-06–08 dates.
- Days: 1,066 total; dual blocks are 734 days (2021-02–2023-12) and 332 days
  (2025-02–2026-05).
- Families: `A_pm`, `A_pm31`, `A_open`, `B585`, `B600`; checkpoints exactly
  `{580,585,590,600,615}`. Post-fill eligibility yields 21 family/checkpoint dimensions;
  exact row counts are in `coverage.json`.
- Every panel row uses anatomy fill, checked against the fill-minute open. State uses
  completed bars `et <= checkpoint`; forward targets use bars `et > checkpoint` through
  canonical session end. NHBA scans chronologically from the session-running checkpoint
  high; a 10% adverse crossing wins an ambiguous same-bar tie.
- `state_panel.parquet`: 182,094 unique observations × 51 columns. `surface.json`: full
  feature-bucket outcome surface per family/checkpoint/block and remaining-outcome cohort
  summaries. Quantile edges are pooled across blocks per family/checkpoint, then held fixed
  for both blocks. `excluded.jsonl`: 3,142 name-family-day exclusions (3,117 blocked,
  25 no-fill). `shards/month=YYYY-MM/` contains 51 resumable monthly panels and completion
  markers. No temporary files remain.

## Dual-block base rates [RUN]

Future touch probabilities are relative to checkpoint close, not executable P&L. Rows
overlap names across entry families and must not be treated as independent.

| Block | ET | n | P(+30%) | P(+50%) | P(+100%) | Mean remaining MFE | P(NHBA) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 580 | 18,526 | 9.71% | 4.08% | 1.18% | 12.61% | 41.12% |
| 1 | 585 | 23,744 | 8.56% | 3.92% | 1.09% | 12.12% | 41.93% |
| 1 | 590 | 23,415 | 8.16% | 3.81% | 1.04% | 11.78% | 37.38% |
| 1 | 600 | 28,749 | 7.82% | 3.53% | 0.94% | 11.35% | 36.35% |
| 1 | 615 | 28,110 | 6.89% | 3.15% | 0.75% | 10.59% | 29.81% |
| 2 | 580 | 8,720 | 13.68% | 6.63% | 2.12% | 16.89% | 35.72% |
| 2 | 585 | 11,400 | 12.67% | 6.36% | 2.24% | 16.54% | 38.24% |
| 2 | 590 | 11,421 | 12.21% | 5.98% | 1.63% | 15.77% | 32.88% |
| 2 | 600 | 14,195 | 11.64% | 5.94% | 1.66% | 15.26% | 33.75% |
| 2 | 615 | 13,814 | 10.53% | 5.30% | 1.48% | 14.26% | 27.67% |

Conditional bucket spreads show descriptive state separation in several fields. For
example, block-2 B600 P(+50%) spans 2.02%–13.90% across current-rank buckets at ET 615;
block-1 B600 spans 1.02%–10.88% across `ret_from_prevclose` buckets at ET 600. Rank change,
relative strength, MFE-so-far, and short velocity also vary across cells. These are
univariate development associations, not independent confirmation, causal effect estimates,
or a selected threshold/gate. No strategy is selected or promoted.

## Limits

No trade actions, friction, or strategy P&L are computed. Outcomes are same-session
descriptive paths, not profitability evidence. Spread state is null because quote data is
outside the permitted canonical inputs; no raw SIP/quote layer was read. Quantile buckets
are descriptive, multiple comparisons are unadjusted, and results do not establish
generalization beyond the two development blocks.
