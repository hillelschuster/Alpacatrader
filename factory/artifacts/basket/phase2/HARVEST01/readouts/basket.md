# HARVEST01 — basket handling: where the released money goes (dev only)

delta = policy basket-day return minus base_hold in the same cell (pp); all policies release on the member's own causal trigger and act at bar opens.


## N=3, end=660 — top policies
| clock | policy | end | mean Δ pp | t | pos | B1 Δ | B2 Δ | n |
|---|---|---|---|---|---|---|---|---|
| 510 | gb10|cash | 660 | +2.79 | 7.7 | 0.66 | 2.59 | 3.17 | 1065 |
| 540 | gb10|cash | 660 | +2.46 | 7.2 | 0.66 | 2.17 | 2.98 | 1064 |
| 550 | gb10|cash | 660 | +2.23 | 6.4 | 0.65 | 1.71 | 3.05 | 1065 |
| 555 | gb10|cash | 660 | +2.14 | 6.3 | 0.65 | 1.56 | 3.25 | 1064 |
| 569 | gb10|cash | 660 | +2.12 | 6.6 | 0.66 | 1.71 | 2.83 | 1063 |
| 560 | gb10|cash | 660 | +2.07 | 6.3 | 0.65 | 1.68 | 2.92 | 1065 |
| 565 | gb10|cash | 660 | +1.99 | 6.0 | 0.66 | 1.68 | 2.39 | 1064 |
| 571 | gb10|cash | 660 | +1.91 | 6.0 | 0.67 | 1.61 | 2.57 | 1063 |
| 575 | gb10|cash | 660 | +1.89 | 6.8 | 0.66 | 1.70 | 2.42 | 1065 |
| 510 | scale|reserve_cash | 660 | +1.67 | 12.2 | 0.71 | 1.54 | 2.01 | 1065 |
| 540 | scale|reserve_cash | 660 | +1.52 | 11.6 | 0.72 | 1.37 | 2.01 | 1064 |
| 560 | scale|reserve_cash | 660 | +1.51 | 12.3 | 0.72 | 1.25 | 2.28 | 1065 |
| 555 | scale|reserve_cash | 660 | +1.49 | 12.0 | 0.71 | 1.22 | 2.19 | 1064 |
| 550 | scale|reserve_cash | 660 | +1.46 | 11.4 | 0.71 | 1.21 | 2.05 | 1065 |

## N=3, end=720 — top policies
| clock | policy | end | mean Δ pp | t | pos | B1 Δ | B2 Δ | n |
|---|---|---|---|---|---|---|---|---|
| 510 | gb10|cash | 720 | +2.87 | 6.4 | 0.68 | 2.76 | 3.11 | 1064 |
| 540 | gb10|cash | 720 | +2.77 | 6.5 | 0.68 | 2.71 | 2.89 | 1063 |
| 550 | gb10|cash | 720 | +2.61 | 6.2 | 0.67 | 2.40 | 2.86 | 1064 |
| 555 | gb10|cash | 720 | +2.58 | 6.3 | 0.67 | 2.22 | 3.30 | 1063 |
| 569 | gb10|cash | 720 | +2.46 | 6.2 | 0.67 | 2.30 | 2.76 | 1062 |
| 560 | gb10|cash | 720 | +2.40 | 5.9 | 0.67 | 2.22 | 2.99 | 1064 |
| 575 | gb10|cash | 720 | +2.29 | 6.4 | 0.67 | 2.26 | 2.44 | 1064 |
| 565 | gb10|cash | 720 | +2.25 | 5.5 | 0.68 | 2.11 | 2.51 | 1063 |
| 571 | gb10|cash | 720 | +2.19 | 5.5 | 0.67 | 2.06 | 2.34 | 1062 |
| 580 | gb10|cash | 720 | +1.76 | 4.7 | 0.66 | 1.64 | 2.06 | 1065 |
| 510 | scale|reserve_cash | 720 | +1.70 | 10.4 | 0.73 | 1.59 | 1.99 | 1064 |
| 555 | scale|reserve_cash | 720 | +1.64 | 11.2 | 0.73 | 1.44 | 2.21 | 1063 |
| 540 | scale|reserve_cash | 720 | +1.64 | 10.6 | 0.73 | 1.56 | 1.97 | 1063 |
| 560 | scale|reserve_cash | 720 | +1.62 | 11.0 | 0.73 | 1.43 | 2.30 | 1064 |

## N=3, all endpoints — top policies
| clock | policy | end | mean Δ pp | t | pos | B1 Δ | B2 Δ | n |
|---|---|---|---|---|---|---|---|---|
| 569 | gb10|market_leader | 779 | +6.37 | 1.2 | 0.67 | -2.13 | 14.87 | 6 |
| 540 | gb10|cash | 779 | +6.26 | 1.2 | 0.50 | -1.78 | 22.34 | 6 |
| 565 | gb10|market_leader | 779 | +5.58 | 1.3 | 0.71 | -1.37 | 14.83 | 7 |
| 595 | gb10|market_leader | 779 | +5.26 | 1.3 | 0.83 | 0.69 | 9.83 | 6 |
| 580 | scale|dip10 | 779 | +5.00 | 1.8 | 0.71 | 4.35 | 5.86 | 7 |
| 540 | gb10|cash | 959 | +4.79 | 9.1 | 0.73 | 4.96 | 4.12 | 1019 |
| 510 | gb10|cash | 959 | +4.76 | 8.9 | 0.71 | 4.87 | 4.50 | 1020 |
| 550 | gb10|cash | 959 | +4.67 | 8.9 | 0.71 | 4.64 | 4.47 | 1021 |
| 555 | gb10|cash | 959 | +4.62 | 8.9 | 0.70 | 4.42 | 4.92 | 1018 |
| 575 | gb10|cash | 959 | +4.53 | 9.4 | 0.71 | 4.71 | 3.47 | 1015 |
| 569 | gb10|cash | 959 | +4.49 | 8.1 | 0.71 | 4.76 | 3.57 | 987 |
| 560 | gb10|cash | 959 | +4.47 | 8.6 | 0.71 | 4.59 | 4.30 | 1020 |
| 615 | gb10|market_leader | 779 | +4.45 | 0.5 | 0.33 | 4.43 | 4.47 | 6 |
| 595 | gb10|best_survivor | 779 | +4.41 | 1.0 | 0.50 | -4.49 | 13.31 | 6 |

## Policy mean across all clocks (N=3, end=720)
- gb10|cash: +1.65 pp
- scale|reserve_cash: +1.28 pp
- scale|single50: +1.15 pp
- scale|split630: +0.95 pp
- scale|single20: +0.79 pp
- nohigh20d10|cash: +0.72 pp
- failrec_a5|cash: +0.42 pp
- scale|dip15: +0.36 pp
- gb10|best_survivor: +0.25 pp
- gb10|equal_survivors: +0.22 pp
- scale|dip10: +0.18 pp
- base_hold: +0.00 pp
- decay_r50|cash: -0.27 pp
- nohigh20d10|best_survivor: -0.29 pp
- nohigh20d10|equal_survivors: -0.39 pp
- failrec_a5|best_survivor: -0.48 pp
- failrec_a5|equal_survivors: -0.57 pp
- nohigh20d10|market_leader: -0.57 pp
- failrec_a5|market_leader: -0.76 pp
- gb10|market_leader: -0.88 pp
- decay_r50|market_leader: -1.01 pp
- decay_r50|best_survivor: -1.07 pp
- decay_r50|equal_survivors: -1.11 pp
