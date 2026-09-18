# SIP certification -> Phase-1 regeneration decision note

Panel days certified: **20**. Method: `sip_certify.py` (SIP raw trades/quotes -> own bars per Alpaca's documented rules), compared event-by-event against the stored legacy anatomy. Pilot scope limits: candidate-union symbols only; stored prev_close reused; A_pm excluded; quoted spreads are market state, not assumed fills.

## A. Selection differences (membership / rank / decision prices)
- top-3 **set** changed on 0.1538 of 260 snapshots (LOWER BOUND: stored top-10 reranked by SIP only; full-universe SIP-bar discovery yields the actual number); order-only changes on 0.25
- rank-flip positions within stored top-10 (total): 763
- decision-price deltas (bps): median-of-medians {'n': 260, 'p50': 0.98, 'p90': 24.43, 'max': 106.0}, max 9766.681004
- fetched outsiders above the 10th stored member: 590

## B. Path differences (fills / MFE / MAE of stored members)
- member-days compared: 695
- |fill delta| bps: {'n': 695, 'p50': 0.0, 'p90': 76.62, 'max': 7444.13}; share >100bps: 0.0748
- MFE delta bps (SIP - stored): {'n': 695, 'p50': 0.0, 'p90': 95.56, 'max': 298713.59}; MAE delta bps: {'n': 695, 'p50': 0.0, 'p90': 16.51, 'max': 5204.08}

## C. First-passage differences
- bar-based order flips (legacy bars vs SIP-derived bars): 498; subminute ambiguity cells 58 (resolved by raw trades 58, by price-updating trades 58, unresolved 0)

## D. Execution truth at causal entry
- quoted spread at fill bps: {'n': 682, 'p50': 86.26, 'p90': 471.86, 'max': 6437.43}; buy-cross bps: {'n': 685, 'p50': 29.41, 'p90': 274.22, 'max': 120434.78}

## E. Extreme-tail integrity
- stored member-days with MFE >= +100%: 50; confirmed by SIP: 49; unconfirmed (stored-only): 1; SIP-only new: 0
- symbols with stored-high revision > 0.5%: 197
- largest revisions:
  - 2025-10-30 BNY: stored 10.26 -> SIP 108.78 (+960.23%)
  - 2022-03-10 BRP: stored 1092.71 -> SIP 26.35 (-97.59%)
  - 2021-10-25 BKKT: stored 31.57 -> SIP 13.86 (-56.10%)
  - 2025-03-24 GOLD: stored 19.065 -> SIP 28.8999 (+51.59%)
  - 2025-10-30 BQ: stored 56.1 -> SIP 72.7399 (+29.66%)
  - 2021-03-08 CNR: stored 14.29 -> SIP 11.45 (-19.87%)
  - 2021-10-25 MARK: stored 6.7 -> SIP 7.53 (+12.39%)
  - 2023-03-17 IVA: stored 4.77 -> SIP 5.35 (+12.16%)
  - 2021-03-08 MYT: stored 4.5 -> SIP 4.99 (+10.89%)
  - 2021-10-25 BE: stored 28.13 -> SIP 25.25 (-10.24%)
  - 2021-02-01 CDE: stored 12.58 -> SIP 13.75 (+9.30%)
  - 2021-02-01 QTNT: stored 5.5 -> SIP 6.0 (+9.09%)
  - 2025-11-25 MKZR: stored 4.47 -> SIP 4.86 (+8.72%)
  - 2023-12-15 CING: stored 2.31 -> SIP 2.5 (+8.23%)
  - 2025-11-25 WSHP: stored 225.0 -> SIP 242.92 (+7.96%)

## Per-day digest

| day | top3 set changes | rank flips | members | tail diffs |
|---|---|---|---|---|
| 2021-02-01 | 1 | 34 | 38 | 10 |
| 2021-02-02 | 2 | 34 | 39 | 6 |
| 2021-02-09 | 0 | 30 | 38 | 8 |
| 2021-03-08 | 2 | 37 | 38 | 14 |
| 2021-10-25 | 4 | 61 | 33 | 9 |
| 2022-02-17 | 1 | 57 | 27 | 5 |
| 2022-03-10 | 3 | 49 | 36 | 5 |
| 2023-03-16 | 2 | 45 | 28 | 14 |
| 2023-03-17 | 7 | 53 | 31 | 15 |
| 2023-08-30 | 5 | 66 | 27 | 9 |
| 2023-12-15 | 3 | 51 | 38 | 10 |
| 2023-12-28 | 3 | 41 | 37 | 14 |
| 2025-03-24 | 1 | 52 | 38 | 14 |
| 2025-09-09 | 1 | 51 | 34 | 14 |
| 2025-10-30 | 3 | 50 | 35 | 12 |
| 2025-11-25 | 2 | 52 | 36 | 11 |
| 2026-04-27 | 0 | 0 | 35 | 6 |
| 2026-04-29 | 0 | 0 | 35 | 6 |
| 2026-05-15 | 0 | 0 | 36 | 8 |
| 2026-05-29 | 0 | 0 | 36 | 7 |

## Decision framework (evidence -> scope)
- **remains valid**: all families ~zero — not the observed picture.
- **selective regeneration**: membership stable, but path/tail/order layers materially differ -> rebuild the anatomy path/barrier layers from SIP bars, keep selection where certified.
- **regenerate Phase-1 from SIP-derived bars**: membership or first-passage materially differs, or tail revisions are large -> the legacy substrate is not a sufficient measurement instrument.

Owner reads the numbers above; no thresholds are pre-claimed as verdicts.
