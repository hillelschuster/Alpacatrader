# HARVEST01 — basket member anatomy: winners vs failures (dev only)

days covered: 1066 (2021-02-01..2026-05-29); members: 8528 (filled 8227, blocked/cash 301)

blocks present: B1 (days 2021-02-01..2023-12-29, n=5872), B2 (days 2025-02-03..2026-05-29, n=2656)

Classes are `mfe_adj` from the fills lane (>=1.00 giant, 0.30-1.00 runner, 0.10-0.30 mid, <0.10 dud). Blocked slots are cash; they never enter a class.

## 1. Class counts (filled members; blocked shown separately)

| entry clock | giant | runner | mid | dud | filled n | blocked/cash |
|---|---|---|---|---|---|---|
| 560 | 174 | 742 | 1268 | 1886 | 4070 | 194 |
| 600 | 147 | 620 | 1244 | 2146 | 4157 | 107 |
| pooled | 321 | 1362 | 2512 | 4032 | 8227 | 301 |

## 2. Time-to-MFE — minutes from fill to the max-high bar (et diff)

| class | n | median | q1 | q3 |
|---|---|---|---|---|
| giant | 321 | 212.0 | 110.0 | 324.0 |
| runner | 1362 | 93.0 | 39.0 | 231.8 |
| mid | 2512 | 37.5 | 16.0 | 154.0 |
| dud | 4032 | 10.0 | 1.0 | 37.2 |
| all | 8227 | 26.0 | 8.0 | 131.0 |

## 3. Damage tolerance — pre-MFE max adverse excursion vs fill

`pre-MFE MAE` is the worst low between fill and the MFE bar. `new low after +10/+30` is a share over members that actually touched the level (touch n in parens); untouched members are shown, not dropped.

| class | n | med pre-MFE MAE % | q1 % | q3 % | new low after +10 | new low after +30 |
|---|---|---|---|---|---|---|
| giant | 321 | -6.91 | -13.02 | -3.24 | 0.190 (n=321) | 0.093 (n=321) |
| runner | 1362 | -6.75 | -11.68 | -3.05 | 0.460 (n=1362) | 0.345 (n=1362) |
| mid | 2512 | -5.84 | -10.63 | -2.45 | 0.695 (n=2508) |  (n=0) |
| dud | 4032 | -2.75 | -6.57 | -0.76 |  (n=0) |  (n=0) |

## 4. Recovery cadence — close back at/above fill after first -10% damage

`damaged` share is over all members; `reclaimed` share and bars are over damaged members only; `bars` counts minute bars from the first damage bar to the first close back >= fill.

| class | damaged -10% share | damaged n | reclaimed share | bars med | bars q1 | bars q3 | never reclaimed | med EOD ret % |
|---|---|---|---|---|---|---|---|---|
| giant | 0.424 (n=321) | 136 | 0.963 | 15.0 | 4.5 | 37.0 | 5 | 80.18 |
| runner | 0.521 (n=1362) | 710 | 0.759 | 24.0 | 8.5 | 69.0 | 171 | 9.38 |
| mid | 0.729 (n=2512) | 1831 | 0.555 | 42.0 | 12.0 | 119.0 | 815 | -7.26 |
| dud | 0.745 (n=4032) | 3005 | 0.225 | 65.0 | 20.0 | 160.5 | 2330 | -11.81 |

## 5. Running-high cadence over the first 2h after fill

A running high is a bar whose high beats every high since fill; interval = minutes between consecutive running highs (fills with <2 running highs have no cadence).

| class | n | med new highs | q1 | q3 | n with >=2 highs | med interval | med of q1s | med of q3s |
|---|---|---|---|---|---|---|---|---|
| giant | 321 | 15.0 | 10.0 | 20.0 | 320 | 2.0 | 1.0 | 5.0 |
| runner | 1362 | 10.0 | 7.0 | 14.0 | 1349 | 2.0 | 1.0 | 5.0 |
| mid | 2512 | 6.0 | 4.0 | 9.0 | 2431 | 2.0 | 1.0 | 4.8 |
| dud | 4032 | 3.0 | 2.0 | 5.0 | 3071 | 2.5 | 1.5 | 4.8 |

## 6. Volume at the deepest pre-MFE damage — 5-bar / prior-20-bar ratio

Ratio >1 means the damage bar sat on an expanding tape; <1 means the damage came on fading volume. Members whose damage bar had <24 bars of history are excluded (n shown).

| class | n | median ratio | q1 | q3 |
|---|---|---|---|---|
| giant | 311 | 1.16 | 0.69 | 2.43 |
| runner | 1330 | 1.16 | 0.69 | 2.54 |
| mid | 2455 | 1.08 | 0.65 | 2.37 |
| dud | 3889 | 1.00 | 0.63 | 1.99 |
| all | 7985 | 1.06 | 0.65 | 2.20 |

## 7. Rank behavior after entry — variant=primary selected rows at 600/660/720

`present` means the ticker still sits in that clock's emitted top-4; an absent ticker has no stored rank (rank_known/rank_unfiltered are only serialised for emitted rows). `rank_known`/`rank_unfiltered` medians are over present members; `known<=4` is a share over present members.

### giant (n=321)

| rank clock | members | present | present share | med rank_known | q1 | q3 | med rank_unfiltered | known<=4 |
|---|---|---|---|---|---|---|---|---|
| 600 | 321 | 314 | 0.978 | 3.0 | 2.0 | 4.0 | 3.0 | 0.774 (n=314) |
| 660 | 321 | 314 | 0.978 | 2.0 | 1.0 | 4.0 | 2.5 | 0.825 (n=314) |
| 720 | 321 | 307 | 0.956 | 2.0 | 1.0 | 3.0 | 2.0 | 0.837 (n=307) |

### dud (n=4032)

| rank clock | members | present | present share | med rank_known | q1 | q3 | med rank_unfiltered | known<=4 |
|---|---|---|---|---|---|---|---|---|
| 600 | 4032 | 3073 | 0.762 | 3.0 | 2.0 | 5.0 | 3.0 | 0.742 (n=3073) |
| 660 | 4032 | 1913 | 0.474 | 3.0 | 2.0 | 4.0 | 3.0 | 0.751 (n=1913) |
| 720 | 4032 | 1659 | 0.411 | 3.0 | 2.0 | 4.0 | 3.0 | 0.764 (n=1659) |

## 8. Failures kept in view

- duds (mfe_adj < 0.10): 4032/8227 filled members (0.490); giants: 321.

- blocked slots booked as cash: 301 of 8528 basket slots (0.035).

- no post-touch observation of +10% after fill (never touched, or touch on the final bar): 4036/8227; same for +30%: 6544/8227.

- never damaged -10% after fill: 2545/8227.

- worst EOD close from fill (member level): 2025-04-07 SUNE@600 -89.9% (mfe 3.0%); 2026-01-09 MTEN@600 -89.6% (mfe 1.6%); 2025-04-07 SUNE@560 -88.9% (mfe 20.8%); 2023-12-07 MLGO@560 -85.2% (mfe 13.0%); 2023-12-07 MLGO@600 -84.1% (mfe 1.2%).

