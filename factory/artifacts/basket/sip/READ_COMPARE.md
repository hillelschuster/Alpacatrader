# BASKET read comparison — basket vs sip


## composition

| metric | basket | sip | delta |
|---|---|---|---|
| A_open/570/days | 1065 | 1066 | +1.0000 |
| A_pm/570/days | 229 | 1066 | +837.0000 |
| B/600/days | 1065 | 1066 | +1.0000 |

## containment

| metric | basket | sip | delta |
|---|---|---|---|
| B/600/N3/top1_in | 37.28% | 28.52% | -8.76pp |
| B/600/N3/top3_in | 23.10% | 20.83% | -2.27pp |
| B/600/N10/top1_in | 45.54% | 35.37% | -10.17pp |
| B/600/N10/top3_in | 34.55% | 28.71% | -5.84pp |
| A_open/570/N3/top1_in | 12.86% | 8.35% | -4.51pp |
| A_open/570/N3/top3_in | 6.29% | 4.13% | -2.16pp |

## joint_tail

| metric | basket | sip | delta |
|---|---|---|---|
| B/600/main/touch30/k>=1 | 30.61% | 39.59% | +8.98pp |
| B/600/main/touch30/k>=2 | 4.13% | 6.29% | +2.16pp |
| B/600/main/touch30/all3 | 0.28% | 0.56% | +0.28pp |
| B/600/main/exec30/k>=1 | 30.52% | 39.49% | +8.97pp |
| B/600/main/exec30/k>=2 | 4.13% | 6.29% | +2.16pp |
| B/600/main/exec30/all3 | 0.28% | 0.56% | +0.28pp |
| B/600/main/touch100/k>=1 | 6.01% | 7.97% | +1.96pp |
| B/600/main/touch100/k>=2 | 0.00% | 0.09% | +0.09pp |
| B/600/main/touch100/all3 | 0.00% | 0.00% | +0.00pp |
| B/600/main/exec100/k>=1 | 6.01% | 7.97% | +1.96pp |
| B/600/main/exec100/k>=2 | 0.00% | 0.09% | +0.09pp |
| B/600/main/exec100/all3 | 0.00% | 0.00% | +0.00pp |
| B/585/main/touch30/k>=1 | 30.42% | 38.65% | +8.23pp |
| B/585/main/touch30/k>=2 | 3.38% | 6.10% | +2.72pp |
| B/585/main/touch30/all3 | 0.19% | 0.28% | +0.09pp |
| B/585/main/exec30/k>=1 | 30.42% | 38.65% | +8.23pp |
| B/585/main/exec30/k>=2 | 3.38% | 6.10% | +2.72pp |
| B/585/main/exec30/all3 | 0.19% | 0.28% | +0.09pp |
| B/585/main/touch100/k>=1 | 4.51% | 6.66% | +2.15pp |
| B/585/main/touch100/k>=2 | 0.09% | 0.19% | +0.10pp |
| B/585/main/touch100/all3 | 0.00% | 0.00% | +0.00pp |
| B/585/main/exec100/k>=1 | 4.51% | 6.57% | +2.06pp |
| B/585/main/exec100/k>=2 | 0.09% | 0.19% | +0.10pp |
| B/585/main/exec100/all3 | 0.00% | 0.00% | +0.00pp |

## frontier

| metric | basket | sip | delta |
|---|---|---|---|
| B/600/main/H30/L10/F | 25.54% | 31.61% | +6.07pp |
| B/600/main/H30/L10/Q | 80.70% | 76.97% | -3.73pp |
| B/600/main/H100/L10/F | 4.23% | 5.63% | +1.40pp |
| B/600/main/H100/L10/Q | 70.31% | 70.93% | +0.62pp |
| B/600/main/H30/L15/F | 29.20% | 36.87% | +7.67pp |
| B/600/main/H30/L15/Q | 94.64% | 92.32% | -2.32pp |
| B/600/main/H10/L5/F | 56.15% | 57.97% | +1.82pp |
| B/600/main/H10/L5/Q | 63.89% | 57.76% | -6.13pp |
| A_open/570/main/H30/L10/F | 29.20% | 31.14% | +1.94pp |
| A_open/570/main/H30/L10/Q | 76.22% | 74.06% | -2.16pp |
| A_open/570/main/H100/L10/F | 6.57% | 6.29% | -0.28pp |
| A_open/570/main/H100/L10/Q | 73.68% | 75.00% | +1.32pp |
| A_open/570/main/H30/L15/F | 33.33% | 34.90% | +1.57pp |
| A_open/570/main/H30/L15/Q | 89.33% | 86.53% | -2.80pp |
| A_open/570/main/H10/L5/F | 55.31% | 62.85% | +7.54pp |
| A_open/570/main/H10/L5/Q | 58.08% | 58.41% | +0.33pp |

## runner_paths

| metric | basket | sip | delta |
|---|---|---|---|
| main/mfe>=30/retr_pre_hi | — | — | — |
| main/mfe>=30/retr_after_hi | — | — | — |
| main/mfe>=30/eod_vs_hi | — | — | — |
| main/mfe>=30/time_to_hi | — | — | — |

## member_mfe_mae

| metric | basket | sip | delta |
|---|---|---|---|
| B/main/mfe/p50 | 0.0716 | 0.0854 | +0.0139 |
| B/main/mae/p50 | -0.0829 | -0.1063 | -0.0233 |

## mfe_ranks

| metric | basket | sip | delta |
|---|---|---|---|
| main/rank1/p50 | 0.1744 | 0.2222 | +0.0478 |
| main/rank2/p50 | 0.0642 | 0.0805 | +0.0163 |
| main/rank3/p50 | 0.0204 | 0.0249 | +0.0044 |

## overnight

| metric | basket | sip | delta |
|---|---|---|---|
| main/all/p50 | -0.0073 | -0.0133 | -0.0060 |
| main/mfe>=30/p50 | -0.0416 | -0.0496 | -0.0080 |

## random_control

| metric | basket | sip | delta |
|---|---|---|---|
| T600/touch10/k>=1 | 10.79% | 12.40% | +1.61pp |
| T600/touch30/k>=1 | 0.83% | 1.65% | +0.82pp |
| T600/touch50/k>=1 | 0.00% | 0.41% | +0.41pp |

## continuous

| metric | basket | sip | delta |
|---|---|---|---|
| B/600/member_mfe/p50 | 0.0772 | 0.0926 | +0.0154 |
| B/600/member_mfe/p90 | 0.3687 | 0.4527 | +0.0840 |
| B/600/member_mfe/p99 | 1.4257 | 1.6875 | +0.2618 |
| B/600/day_max/p90 | 0.7462 | 0.8695 | +0.1233 |
| B/600/day_max/p99 | 2.2683 | 2.3992 | +0.1309 |
| B/600/ordinary_share | 0.2469 | 0.1895 | -0.0574 |
| B/720/member_mfe/p50 | 0.0642 | 0.0798 | +0.0156 |
| B/720/member_mfe/p90 | 0.2776 | 0.3744 | +0.0968 |
| B/720/member_mfe/p99 | 0.9815 | 1.2260 | +0.2445 |
| B/720/day_max/p90 | 0.5370 | 0.6735 | +0.1365 |
| B/720/day_max/p99 | 1.5492 | 1.7344 | +0.1852 |
| B/720/ordinary_share | 0.3202 | 0.2120 | -0.1082 |
| A_open/570/member_mfe/p50 | 0.0752 | 0.0941 | +0.0189 |
| A_open/570/member_mfe/p90 | 0.4115 | 0.4337 | +0.0222 |
| A_open/570/member_mfe/p99 | 1.6572 | 1.7882 | +0.1310 |
| A_open/570/day_max/p90 | 0.8901 | 0.8254 | -0.0647 |
| A_open/570/day_max/p99 | 2.6972 | 2.9526 | +0.2554 |
| A_open/570/ordinary_share | 0.2272 | 0.1623 | -0.0649 |

## buckets

| metric | basket | sip | delta |
|---|---|---|---|
| B/main/filled | 34594 | 35625 | +1031.0000 |
| B/main/gap_blocked | 3691 | 2738 | -953.0000 |
| B/main/unfilled | 55 | 13 | -42.0000 |

## notes

- absent in basket: 4 paths
- absent in sip: 4 paths
