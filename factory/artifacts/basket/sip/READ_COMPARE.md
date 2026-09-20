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
| B/600/N3/top1_in | 37.28% | 29.27% | -8.01pp |
| B/600/N3/top3_in | 23.10% | 21.20% | -1.90pp |
| B/600/N10/top1_in | 45.54% | 36.30% | -9.24pp |
| B/600/N10/top3_in | 34.55% | 28.99% | -5.56pp |
| A_open/570/N3/top1_in | 12.86% | 8.72% | -4.14pp |
| A_open/570/N3/top3_in | 6.29% | 4.03% | -2.26pp |

## joint_tail

| metric | basket | sip | delta |
|---|---|---|---|
| B/600/main/touch30/k>=1 | 30.61% | 39.21% | +8.60pp |
| B/600/main/touch30/k>=2 | 4.13% | 6.29% | +2.16pp |
| B/600/main/touch30/all3 | 0.28% | 0.56% | +0.28pp |
| B/600/main/exec30/k>=1 | 30.52% | 39.12% | +8.60pp |
| B/600/main/exec30/k>=2 | 4.13% | 6.29% | +2.16pp |
| B/600/main/exec30/all3 | 0.28% | 0.56% | +0.28pp |
| B/600/main/touch100/k>=1 | 6.01% | 7.79% | +1.78pp |
| B/600/main/touch100/k>=2 | 0.00% | 0.09% | +0.09pp |
| B/600/main/touch100/all3 | 0.00% | 0.00% | +0.00pp |
| B/600/main/exec100/k>=1 | 6.01% | 7.79% | +1.78pp |
| B/600/main/exec100/k>=2 | 0.00% | 0.09% | +0.09pp |
| B/600/main/exec100/all3 | 0.00% | 0.00% | +0.00pp |
| B/585/main/touch30/k>=1 | 30.42% | 38.09% | +7.67pp |
| B/585/main/touch30/k>=2 | 3.38% | 6.10% | +2.72pp |
| B/585/main/touch30/all3 | 0.19% | 0.28% | +0.09pp |
| B/585/main/exec30/k>=1 | 30.42% | 38.09% | +7.67pp |
| B/585/main/exec30/k>=2 | 3.38% | 6.10% | +2.72pp |
| B/585/main/exec30/all3 | 0.19% | 0.28% | +0.09pp |
| B/585/main/touch100/k>=1 | 4.51% | 6.38% | +1.87pp |
| B/585/main/touch100/k>=2 | 0.09% | 0.19% | +0.10pp |
| B/585/main/touch100/all3 | 0.00% | 0.00% | +0.00pp |
| B/585/main/exec100/k>=1 | 4.51% | 6.29% | +1.78pp |
| B/585/main/exec100/k>=2 | 0.09% | 0.19% | +0.10pp |
| B/585/main/exec100/all3 | 0.00% | 0.00% | +0.00pp |

## frontier

| metric | basket | sip | delta |
|---|---|---|---|
| B/600/main/H30/L10/F | 25.54% | 31.33% | +5.79pp |
| B/600/main/H30/L10/Q | 80.70% | 77.19% | -3.51pp |
| B/600/main/H100/L10/F | 4.23% | 5.44% | +1.21pp |
| B/600/main/H100/L10/Q | 70.31% | 70.24% | -0.07pp |
| B/600/main/H30/L15/F | 29.20% | 36.59% | +7.39pp |
| B/600/main/H30/L15/Q | 94.64% | 92.46% | -2.18pp |
| B/600/main/H10/L5/F | 56.15% | 57.88% | +1.73pp |
| B/600/main/H10/L5/Q | 63.89% | 57.89% | -6.00pp |
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
| main/mfe>=30/retr_pre_hi | -0.1587 | -0.1707 | -0.0120 |
| main/mfe>=30/retr_after_hi | -0.2716 | -0.2984 | -0.0267 |
| main/mfe>=30/eod_vs_hi | -0.2182 | -0.2315 | -0.0133 |
| main/mfe>=30/time_to_hi | 134.0000 | 135.0000 | +1.0000 |

## member_mfe_mae

| metric | basket | sip | delta |
|---|---|---|---|
| B/main/mfe/p50 | 0.0716 | 0.0844 | +0.0128 |
| B/main/mae/p50 | -0.0829 | -0.1049 | -0.0220 |

## mfe_ranks

| metric | basket | sip | delta |
|---|---|---|---|
| main/rank1/p50 | 0.1744 | 0.2180 | +0.0436 |
| main/rank2/p50 | 0.0642 | 0.0794 | +0.0152 |
| main/rank3/p50 | 0.0204 | 0.0249 | +0.0045 |

## overnight

| metric | basket | sip | delta |
|---|---|---|---|
| main/all/p50 | -0.0073 | -0.0121 | -0.0049 |
| main/mfe>=30/p50 | -0.0416 | -0.0486 | -0.0070 |

## random_control

| metric | basket | sip | delta |
|---|---|---|---|
| T600/touch10/k>=1 | 10.79% | 12.40% | +1.61pp |
| T600/touch30/k>=1 | 0.83% | 1.65% | +0.82pp |
| T600/touch50/k>=1 | 0.00% | 0.41% | +0.41pp |

## continuous

| metric | basket | sip | delta |
|---|---|---|---|
| B/600/member_mfe/p50 | 0.0772 | 0.0918 | +0.0146 |
| B/600/member_mfe/p90 | 0.3687 | 0.4487 | +0.0800 |
| B/600/member_mfe/p99 | 1.4257 | 1.6807 | +0.2550 |
| B/600/day_max/p90 | 0.7462 | 0.8628 | +0.1166 |
| B/600/day_max/p99 | 2.2683 | 2.3992 | +0.1309 |
| B/600/ordinary_share | 0.2469 | 0.1923 | -0.0546 |
| B/720/member_mfe/p50 | 0.0642 | 0.0798 | +0.0156 |
| B/720/member_mfe/p90 | 0.2776 | 0.3641 | +0.0865 |
| B/720/member_mfe/p99 | 0.9815 | 1.2164 | +0.2349 |
| B/720/day_max/p90 | 0.5370 | 0.6688 | +0.1318 |
| B/720/day_max/p99 | 1.5492 | 1.7107 | +0.1615 |
| B/720/ordinary_share | 0.3202 | 0.2111 | -0.1091 |
| A_open/570/member_mfe/p50 | 0.0752 | 0.0941 | +0.0189 |
| A_open/570/member_mfe/p90 | 0.4115 | 0.4337 | +0.0222 |
| A_open/570/member_mfe/p99 | 1.6572 | 1.7882 | +0.1310 |
| A_open/570/day_max/p90 | 0.8901 | 0.8254 | -0.0647 |
| A_open/570/day_max/p99 | 2.6972 | 2.9526 | +0.2554 |
| A_open/570/ordinary_share | 0.2272 | 0.1623 | -0.0649 |

## buckets

| metric | basket | sip | delta |
|---|---|---|---|
| B/main/filled | 34594 | 35688 | +1094.0000 |
| B/main/gap_blocked | 3691 | 2677 | -1014.0000 |
| B/main/unfilled | 55 | 11 | -44.0000 |
