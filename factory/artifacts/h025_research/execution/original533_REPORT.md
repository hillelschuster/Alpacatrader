# H025 execution decomposition

Computed 533 legitimate original533 days (2021-02-01 .. 2023-03-14).

Original qualification unchanged. All results are development/temporal replication, not new OOS.

## Full lifecycle, 100bps

| Policy / chronology | fills | mean net/fill | return units/day | stop-gap units lost |
|---|---:|---:|---:|---:|
| all_orders_legacy | 593 | 0.002970 | 0.003304 | 3.891435 |
| pf2_POSTHOC_legacy | 405 | 0.012279 | 0.009330 | 0.839920 |
| all_orders_pessimistic | 593 | -0.023863 | -0.026549 | 4.418645 |
| pf2_POSTHOC_pessimistic | 405 | -0.013217 | -0.010043 | 1.149781 |
| all_orders_optimistic | 593 | 0.004750 | 0.005285 | 3.891435 |
| pf2_POSTHOC_optimistic | 405 | 0.014364 | 0.010914 | 0.839920 |
| pf2_preorder_legacy | 463 | 0.004916 | 0.004271 | 2.582739 |
| pf2_preorder_pessimistic | 463 | -0.019666 | -0.017083 | 2.892600 |
| pf2_preorder_optimistic | 463 | 0.006740 | 0.005855 | 2.582739 |

## Interpretation and evidence boundaries

- Original H025 name qualification held fixed; raw/reference source not certified.
- Legacy unchanged. Pessimistic/optimistic are OHLC chronology scenarios, not exact broker fill bounds.
- Paired fill-fixed sensitivities distinct from full-lifecycle counterfactual rearm/refresh.
- Minute volume / local ALL-print volume are support proxies, never empirical queue probabilities.
- Qbuy/Qsell, service fraction and latency unknown. Frontier values are assumed scenarios.
- Historical quotes normalize round lots x100; an as-of quote is not full depth or queue ahead.
- No protected OOS prices read; NET micro only original533. Old stripped caches cannot establish condition/exchange eligibility.
- Integer qty=floor(ORDER notional/B), costs charged on deployed notional; idle order cash earns zero. Actual budget policy rejects qty0 BEFORE placement; replays when high-price states can change pending refresh.
- Sum-return units/day is fixed order-notional turnover PnL, NOT account return or compounded return.

See summary JSON for the actual $250/$500/$1000 integer-order PnL frontier at 100/150/200bps, monthly zero-day-inclusive economics, occupancy, tails, missing windows and local NET whole-window source discrepancies.

Paper journal alignment: served 085b200 on Sep15/16 is descriptive source provenance only. Journal write is not broker filled_at; this producer reads no broker records, sends no orders and changes no flags.

Resume signature: 1869b23b2c670808e59c7a6976d17e3457a091f6083c3a998410c94daf152dd8; daily artifacts: data/h025_research/execution/1869b23b2c670808.
