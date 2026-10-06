# H025 qualification: causal historical research

Actual legitimate available calendar: **1046 days**, not an assumed 1066 or 1047. 533 original development days; later blocks are already-seen DEVELOPMENT/temporal replication, not new OOS.

## Evidence boundary

Original top-three ranked inputs remain uncertified (raw close/split/reference quality). Historical split flags are sensitivity labels, not a corrected full-universe board. Filtering flagged fills is post-hoc sensitivity only and does not re-rank. Source correction must precede new rank-based economic claims. Frozen published OOS pass remains unchanged; 2024, Jan/Feb2025 and Jun/Aug2026 outcomes are excluded from this study.

All predictors are observable at the anchor: gain, 15-row clock-grid close thrust, cummax-CLOSE pullback, legacy episode count, clock, stored rank1–3, trailing5 clock-grid CLOSE range, trailing5 HL range, new-bar-only trailing5-minute dollar volume, current-completed-bar/20-new-bar volx, 20-clock-minute print density, last-new-bar age, elapsed time since prior episode start, elapsed time since already-observed top3 appearance. No fc or future MFE qualifies orders. Legacy prior_flush is zero on stale rows by original build semantics; this is preserved, not silently repaired.

## Whole-policy prior-flush curves

| minimum prior_flush | fills | mean net100 | total net/day | $500 integer-order net/day | 2022 total net/day |
|---|---:|---:|---:|---:|---:|
| 0 | 1580 | 0.0934% | 0.1411% | $1.341 | 0.2571% |
| 1 | 1451 | 0.4035% | 0.5597% | $2.903 | 0.7234% |
| 2 | 1265 | 0.6560% | 0.7933% | $4.049 | 0.4558% |
| 3 | 1066 | 0.6526% | 0.6650% | $3.405 | 0.3800% |

Legacy ALL-order lifecycle followed by postfill pf2 subset: n=1072, mean=1.1338%, total/day=1.1620%. This is not the pf2 PRE-order policy above.

## Mechanistic neighborhoods (not best-cell selection)

Every sweep and joint neighborhood is in response_curves.csv and qualification_curves.json, including null/negative cells, month/year, frequency, active days, tail, name-minute occupancy, 100/150/200bps, $250/$500/$1000 integer-order dollars and causal-pf2 cohort retention. Net/day includes all calendar days, including zero-fill days. Independent order budgets imply no aggregate capital cap; summed returns are not compounded account returns. Cost is additive on allocated entry notional, matching legacy 100bps convention.

Range5 is a mandatory 2022 inversion check, not a universally robust predictor:

| range5 lower cut | pooled mean | pooled total/day | 2021 total/day | 2022 mean | 2022 total/day | 2023 total/day |
|---|---:|---:|---:|---:|---:|---:|
| 1% | 0.6406% | 0.7735% | 0.3247% | 0.7730% | 0.4558% | 0.5236% |
| 2% | 0.6182% | 0.7430% | 0.1968% | 0.8531% | 0.4996% | 0.5422% |
| 3% | 0.6132% | 0.7340% | 0.3216% | 0.7948% | 0.4655% | 0.5017% |
| 5% | 0.6324% | 0.7346% | 0.3879% | 0.8792% | 0.4939% | 0.4525% |
| 10% | 0.5967% | 0.5328% | -0.1837% | 1.0219% | 0.4275% | 0.1705% |

## Candidate conditions (research-only, no freeze)

- **history_range**: 0/9 cells improve pooled total/day while not reducing 2021/2022/2023 total/day relative to causalpf2. Conditions: none; retain negative/inversion evidence rather than choosing highest per-fill EV.
- **clock_thrust**: 0/9 cells improve pooled total/day while not reducing 2021/2022/2023 total/day relative to causalpf2. Conditions: none; retain negative/inversion evidence rather than choosing highest per-fill EV.
- **activity_liquidity**: 2/6 cells improve pooled total/day while not reducing 2021/2022/2023 total/day relative to causalpf2. Conditions: activity_liquidity_n0.5_d100000.0, activity_liquidity_n0.5_d1000000.0.
- **counter_activity**: 2/13 cells improve pooled total/day while not reducing 2021/2022/2023 total/day relative to causalpf2. Conditions: carry_activity_0.25, carry_activity_0.5.

This cross-year comparison is descriptive, not a preregistered adoption gate; no family is certified by passing it. Original development versus later seen-data replication is reported separately. Pessimistic/optimistic intrabar modes are lifecycle chronology SCENARIOS, not guaranteed aggregate P&L bounds because exit timing changes later rearm/fill cohorts. No exchange queue probabilities or broker order calls.

## Reproduce / resume

Run factory/scripts/h025_research_qualification.py with --data-root explicitly bound to the external data root and --mode native or --mode curves, then --mode report. Bulk per-day outputs live under data/h025_research/qualification; each completed day is atomic and resumable with producer/feature/core and source content identities. --limit is for targeted research smoke only. No builds, gates, linters, formatters or tests run.
