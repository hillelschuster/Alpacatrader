# SIP trades coverage of HARVEST01 selected basket

Scope: 7 dev days that have `harvest01/base/selected/<day>.parquet` (2021-02-01 .. 2026-05-29). Source symbols read from `data/sip/net/trades/<day>.parquet` (symbol column only).

- Days with a net/trades file: **7/7** (none missing)
- Days with a sim/fills file (needed for outcome classes / probe): **3/7** (missing: 2021-02-02, 2021-02-03, 2021-02-04, 2021-02-05)

## Per-day coverage

| day | trades file | n_selected | n_covered_selected | n_leaders | n_covered_leaders | share_selected | share_leaders |
|---|---|---|---|---|---|---|---|
| 2021-02-01 | yes | 12 | 8 | 4 | 3 | 66.7% | 75.0% |
| 2021-02-02 | yes | 13 | 9 | 6 | 5 | 69.2% | 83.3% |
| 2021-02-03 | yes | 12 | 9 | 3 | 2 | 75.0% | 66.7% |
| 2021-02-04 | yes | 12 | 9 | 2 | 1 | 75.0% | 50.0% |
| 2021-02-05 | yes | 13 | 8 | 4 | 2 | 61.5% | 50.0% |
| 2022-05-09 | yes | 12 | 7 | 3 | 2 | 58.3% | 66.7% |
| 2026-05-29 | yes | 7 | 7 | 2 | 2 | 100.0% | 100.0% |

`n_selected` = distinct tickers appearing in selected rows (all variants listed/primary/raw, ranks 1-4). `n_leaders` = distinct tickers at rank==1. Coverage = ticker present in that day's net/trades symbol set.

## Aggregate shares

- **Over days (macro)**: selected 72.3% (mean of per-day share), leaders 70.2%.
- **Over days (pooled)**: selected 70.4% (57/81), leaders 70.8% (17/24).
- **Over members (pooled)**: 88.1% of selected member rows (1406/1596) have their ticker covered, counting every (day, variant, clock, rank, ticker) row.

## Outcome classes of covered member-days

Classes assigned from `mfe_adj` in `harvest01/sim/fills/<day>.parquet` (giant >=1.0, runner 0.3-1.0, mid 0.1-0.3, dud <0.1); blocked / no-fill rows are `unknown`. Only the 3 fills days are classifiable.

| scope | rows | giant | runner | mid | dud | unknown |
|---|---|---|---|---|---|---|
| covered member rows (all variants) | 1406 | 5 | 107 | 131 | 360 | 803 |
| covered member-days (variant-deduped) | 584 | 2 | 38 | 50 | 174 | 320 |

## Sub-minute probe sample (first 30 coverable member-days)

Coverable = ticker present in the day's net/trades file and fills available; variant-deduped on (`day`,`clock`,`ticker`,`rank`), ordered by day, clock, rank, ticker. All 30 come from 2021-02-01 (first fillable day): ALYA/ASM/LODE/VIE. Each row points at the local trades parquet to probe trade-by-trade prints.

| # | day | clock (ET) | ticker | rank | mfe_adj | outcome | trades_path | fills_path |
|---|---|---|---|---|---|---|---|---|
| 1 | 2021-02-01 | 510 | ALYA | 1 | 0.001307 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 2 | 2021-02-01 | 510 | ASM | 2 | 0.176471 | mid | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 3 | 2021-02-01 | 510 | ALYA | 3 | 0.001307 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 4 | 2021-02-01 | 510 | LODE | 3 | 0.985714 | runner | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 5 | 2021-02-01 | 510 | ASM | 4 | 0.176471 | mid | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 6 | 2021-02-01 | 510 | VIE | 4 | 0.002641 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 7 | 2021-02-01 | 540 | ASM | 1 | 0.009646 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 8 | 2021-02-01 | 540 | ALYA | 2 | 0.062092 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 9 | 2021-02-01 | 540 | LODE | 3 | 0.921659 | runner | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 10 | 2021-02-01 | 540 | ASM | 4 | 0.009646 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 11 | 2021-02-01 | 540 | VIE | 4 | 0.004536 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 12 | 2021-02-01 | 550 | ASM | 1 | 0.037415 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 13 | 2021-02-01 | 550 | ALYA | 2 | 0.000000 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 14 | 2021-02-01 | 550 | LODE | 3 | 0.904110 | runner | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 15 | 2021-02-01 | 550 | ASM | 4 | 0.037415 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 16 | 2021-02-01 | 550 | VIE | 4 | 0.003209 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 17 | 2021-02-01 | 555 | ASM | 1 | 0.017606 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 18 | 2021-02-01 | 555 | ALYA | 2 | 0.018315 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 19 | 2021-02-01 | 555 | LODE | 3 | 0.853333 | runner | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 20 | 2021-02-01 | 555 | ASM | 4 | 0.017606 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 21 | 2021-02-01 | 555 | VIE | 4 | 0.006629 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 22 | 2021-02-01 | 560 | ASM | 1 | 0.017857 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 23 | 2021-02-01 | 560 | ALYA | 2 | 0.068359 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 24 | 2021-02-01 | 560 | LODE | 3 | 0.674699 | runner | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 25 | 2021-02-01 | 560 | ASM | 4 | 0.017857 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 26 | 2021-02-01 | 560 | VIE | 4 | 0.006629 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 27 | 2021-02-01 | 565 | ASM | 1 | 0.025180 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 28 | 2021-02-01 | 565 | ALYA | 2 | 0.083168 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 29 | 2021-02-01 | 565 | LODE | 3 | 0.744770 | runner | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |
| 30 | 2021-02-01 | 565 | ASM | 4 | 0.025180 | dud | data/sip/net/trades/2021-02-01.parquet | data/harvest01/sim/fills/2021-02-01.parquet |

Coverable member-days available in total (fills days, variant-deduped): **264** across days 2021-02-01, 2022-05-09, 2026-05-29.
