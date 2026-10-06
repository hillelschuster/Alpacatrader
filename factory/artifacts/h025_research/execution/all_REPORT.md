# H025 execution decomposition

Computed 5 legitimate all days (2021-02-01 .. 2021-02-05).

Original qualification unchanged. All results are development/temporal replication, not new OOS.

## Full lifecycle, 100bps

| Policy / chronology | fills | mean net/fill | return units/day | stop-gap units lost |
|---|---:|---:|---:|---:|
| all_orders_legacy | 20 | -0.045931 | -0.183725 | 0.851111 |
| pf2_POSTHOC_legacy | 14 | -0.008949 | -0.025058 | 0.295556 |
| all_orders_pessimistic | 20 | -0.056487 | -0.225947 | 0.851111 |
| pf2_POSTHOC_pessimistic | 14 | -0.024029 | -0.067281 | 0.295556 |
| all_orders_optimistic | 20 | -0.035376 | -0.141503 | 0.851111 |
| pf2_POSTHOC_optimistic | 14 | -0.008949 | -0.025058 | 0.295556 |
| pf2_preorder_legacy | 17 | -0.047043 | -0.159947 | 0.851111 |
| pf2_preorder_pessimistic | 17 | -0.059462 | -0.202169 | 0.851111 |
| pf2_preorder_optimistic | 17 | -0.047043 | -0.159947 | 0.851111 |

## Interpretation and evidence boundaries

- Original H025 name qualification held fixed; raw/reference source not certified.
- Legacy unchanged. Pessimistic/optimistic are OHLC chronology scenarios, not exact broker fill bounds.
- Paired fill-fixed sensitivities distinct from full-lifecycle counterfactual rearm/refresh.
- Minute volume / local ALL-print volume are support proxies, never empirical queue probabilities.
- Qbuy/Qsell, service fraction and latency unknown. Frontier values are assumed scenarios.
- Historical quotes normalize round lots x100; an as-of quote is not full depth or queue ahead.
- No protected OOS prices read; NET micro only original533. Old stripped caches cannot establish condition/exchange eligibility.
- Integer qty=floor(ORDER notional/B), costs charged on deployed notional; idle order cash earns zero.
- Sum-return units/day is fixed order-notional turnover PnL, NOT account return or compounded return.

See summary JSON for the actual $250/$500/$1000 integer-order PnL frontier at 100/150/200bps, monthly zero-day-inclusive economics, occupancy, tails, missing windows and local NET whole-window source discrepancies.

Paper journal alignment: served 085b200 on Sep15/16 is descriptive source provenance only. Journal write is not broker filled_at; this producer reads no broker records, sends no orders and changes no flags.

Resume signature: c76dd402d4a850d47a265fe4d14ebee632055b6a0fc0fe25edd0892a05936723; daily artifacts: /home/hillel/projects/Alpacatrader/data/h025_research/execution/c76dd402d4a850d4.
