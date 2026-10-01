# HARVEST01 — PM snapshot lane audit (data/sip/pm_snapshots)

Audit date: 2026-10-02. Worktree: `basket-phase2-f1`. Data root: `/home/hillel/projects/Alpacatrader/data/sip/pm_snapshots` (read-only).
Producer: `factory/scripts/basket_pm_snapshots.py` (producer_sha256 `9a4cf51021c9e0bb9081aa60b1f28f41a5ed5dbf43615e766a36b37b8665889d`, identical across all 1066 manifests).

Dev calendar: `factory/artifacts/basket/sip/phase2_session_calendar.json` (`evidence`, 1066 days, 2021-02-01..2026-05-29; no 2024, no 2025-01). No producer code was edited.

Semantics audited (from producer `compact_snapshot`, `basket_pm_snapshots.py:114`): only SIP RAW minute bars in `[04:00, 09:30) ET`, `et<=569` (569 = 09:29 ET) are compacted. For clock `C` (minutes-of-day): `px_C` = close of last bar with `et <= C-1`; `et_C` = that bar's et; `fo_C` = open of first bar with `et >= C`; `fet_C` = that bar's et. `sym` universe = `sip_universe.pit_elig(day)` = latest PIT vintage <= day.

## Item 1 — manifest/pair/sha integrity — **PASS**

| check | count |
|---|---|
| `*.manifest.json` files | 1066 |
| `*.parquet` files | 1066 |
| manifests with `status != ok` | 0 |
| dev days missing manifest | 0 |
| dev days missing parquet | 0 |
| manifest days outside dev calendar | 0 |
| extra parquet days | 0 |
| sha256 mismatches (all 1066 pairs verified) | 0 |

Exception lists: **none** (all empty). Every manifest has `status=ok`, `errors=[]`; every parquet's recomputed sha256 equals `manifest.sha256`. `rows == symbols_with_data` for every day (one compact row per symbol). All 1066 manifests share one schema and one `producer_sha256`. `invalid_symbols>0` on 1062 days is syntax-skip only (symbols containing `^`/`/`); `errors` is empty on all 1066 days.

## Item 2 — index.jsonl vs dev calendar & manifests — **PASS**

| check | result |
|---|---|
| `index.jsonl` lines | 1066 |
| index days == dev calendar (1066) | yes |
| days in dev not in index | 0 |
| days in index not in dev | 0 |
| duplicate index days | 0 |
| index sorted ascending | yes |
| index fields (status, rows, bars_raw, symbols_with_data, sha256, producer_sha256) != manifest | 0 |

`index.jsonl` covers exactly the 1066 dev days and reproduces the manifest fields exactly. `dev_days.json` in the snapshot dir is identical to the calendar set (1066).

## Item 3 — coverage texture — **PASS** (report-only)

`symbols_requested` is `len(pit_elig(day))` (the full PIT universe, including syntax-skipped symbols); `symbols_with_data` = rows = symbols with >=1 PM print in `[04:00,09:30)` (`et<=569`). The PIT print share below is the aggregate `sum(symbols_with_data)/sum(symbols_requested)`; the daily-mean share is also given.

| year | days | mean symbols_requested | mean symbols_with_data | mean rows | mean bars_raw | PIT print share (agg) | PIT print share (daily mean) | median symbols_with_data | min/max symbols_with_data |
|---|---|---|---|---|---|---|---|---|---|
| 2021 | 233 | 5827.6 | 2327.3 | 2327.3 | 46505.7 | 0.3994 | 0.4006 | 2268 | 1392/3620 |
| 2022 | 251 | 6259.9 | 2263.4 | 2263.4 | 38378.8 | 0.3616 | 0.3615 | 2205 | 1872/3707 |
| 2023 | 250 | 5921.3 | 2252.8 | 2252.8 | 38216.6 | 0.3805 | 0.3805 | 2222 | 1853/3599 |
| 2025 | 230 | 5543.8 | 2990.9 | 2990.9 | 64909.4 | 0.5395 | 0.5395 | 2966 | 2363/3944 |
| 2026 | 102 | 5564.7 | 3177.7 | 3177.7 | 76426.4 | 0.5710 | 0.5710 | 3139 | 2830/3904 |

Overall (1066 days): mean symbols_requested 5865.0, mean symbols_with_data 2519.3, (= 2,685,599 rows / 1066), daily-mean PIT print share 0.433 (min 0.248, max 0.715). Total rows 2,685,599. Print share rises over time (2022 trough 0.362 → 2026 0.571), consistent with thinner early SIP pre-market coverage of the broad PIT universe and improving recent liquidity. Note: `symbols_requested` counts the full PIT list; the provider-queryable set excludes `^`/`/` syntax symbols, so the true share among queryable symbols is marginally higher.

## Item 4 — provider spot-check (px_560 / fo_560) — **PASS**

8 random days (seed 20261002), all years present in the dev set (2021,2022,2023,2025,2026; 2024 absent by design), including early-close 2021-11-26. Per day, 5 stored symbols were drawn at random and re-fetched read-only from Alpaca SIP minute bars 04:00-09:30 ET (`Adjustment.RAW`, same `.env` credentials), then re-compacted with the producer's exact logic. Exact equality required for `px_560`, `et_560`, `fo_560`, `fet_560`: **40 symbols × 4 fields = 160 comparisons, 0 mismatches.**

### 2021-03-31 (stored symbols: 2065) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| CELH | 7 | 46.2 | 46.2 | 534/534 | 46.25 | 46.25 | 561/561 | OK |
| LOTZ | 31 | 7.26 | 7.26 | 558/558 | 7.3 | 7.3 | 564/564 | OK |
| NSH | 17 | 10.01 | 10.01 | 554/554 | 10.03 | 10.03 | 567/567 | OK |
| PLAG | 2 | 2.33 | 2.33 | 448/448 | — | — | —/— | OK |
| YJ | 5 | 2.03 | 2.03 | 482/482 | — | — | —/— | OK |

### 2021-11-26 (stored symbols: 3261) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| MT | 77 | 27.58 | 27.58 | 559/559 | 27.58 | 27.58 | 560/560 | OK |
| RMNI | 2 | 6.89 | 6.89 | 484/484 | — | — | —/— | OK |
| ROG | 3 | 271.0 | 271.0 | 557/557 | — | — | —/— | OK |
| UPST | 101 | 200.03 | 200.03 | 559/559 | 200.5 | 200.5 | 560/560 | OK |
| WAFU | 18 | 4.99 | 4.99 | 496/496 | 5.01 | 5.01 | 567/567 | OK |

### 2022-09-12 (stored symbols: 2326) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| AEM | 22 | 45.6 | 45.6 | 556/556 | 45.39 | 45.39 | 560/560 | OK |
| FTVI | 3 | — | — | —/— | 9.92 | 9.92 | 565/565 | OK |
| INMD | 8 | 33.5 | 33.5 | 503/503 | — | — | —/— | OK |
| KMI | 15 | 18.47 | 18.47 | 556/556 | 18.47 | 18.47 | 561/561 | OK |
| SD | 4 | 18.97 | 18.97 | 479/479 | 19.62 | 19.62 | 569/569 | OK |

### 2023-02-24 (stored symbols: 2346) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| BOOT | 2 | 76.35 | 76.35 | 524/524 | — | — | —/— | OK |
| CIDM | 12 | 0.4798 | 0.4798 | 559/559 | 0.4688 | 0.4688 | 560/560 | OK |
| OMER | 2 | — | — | —/— | 3.98 | 3.98 | 567/567 | OK |
| TROO | 3 | 3.5001 | 3.5001 | 558/558 | — | — | —/— | OK |
| TTC | 2 | 111.0 | 111.0 | 527/527 | — | — | —/— | OK |

### 2025-03-14 (stored symbols: 2852) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| AEG | 16 | 6.35 | 6.35 | 550/550 | — | — | —/— | OK |
| BXP | 2 | 64.25 | 64.25 | 508/508 | — | — | —/— | OK |
| CFG | 2 | 40.1 | 40.1 | 476/476 | — | — | —/— | OK |
| EMBC | 4 | 13.0 | 13.0 | 375/375 | — | — | —/— | OK |
| UPC | 59 | 0.103 | 0.103 | 555/555 | 0.0992 | 0.0992 | 566/566 | OK |

### 2025-07-01 (stored symbols: 2748) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| ARVN | 6 | 7.4 | 7.4 | 505/505 | 7.4 | 7.4 | 569/569 | OK |
| BE | 30 | 23.74 | 23.74 | 559/559 | 23.92 | 23.92 | 562/562 | OK |
| COLD | 2 | 16.63 | 16.63 | 505/505 | — | — | —/— | OK |
| KMX | 4 | 67.21 | 67.21 | 534/534 | — | — | —/— | OK |
| VANI | 2 | 1.3497 | 1.3497 | 526/526 | — | — | —/— | OK |

### 2025-12-17 (stored symbols: 2944) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| FTFT | 3 | 0.9702 | 0.9702 | 480/480 | — | — | —/— | OK |
| IGR | 3 | 4.28 | 4.28 | 553/553 | — | — | —/— | OK |
| MRT | 2 | 2.4 | 2.4 | 480/480 | — | — | —/— | OK |
| NRDY | 19 | 1.26 | 1.26 | 559/559 | 1.24 | 1.24 | 560/560 | OK |
| ORIS | 32 | 0.1048 | 0.1048 | 553/553 | 0.1026 | 0.1026 | 562/562 | OK |

### 2026-04-14 (stored symbols: 3171) — all_match=True

| symbol | provider bars | px_560 stored | px_560 provider | et_560 s/p | fo_560 stored | fo_560 provider | fet_560 s/p | match |
|---|---|---|---|---|---|---|---|---|
| EQNR | 53 | 38.97 | 38.97 | 553/553 | 38.96 | 38.96 | 561/561 | OK |
| NUVL | 3 | 103.68 | 103.68 | 253/253 | — | — | —/— | OK |
| ROKU | 7 | 103.8 | 103.8 | 456/456 | 103.94 | 103.94 | 560/560 | OK |
| TOVX | 80 | 0.2653 | 0.2653 | 559/559 | 0.2668 | 0.2668 | 560/560 | OK |
| ZSPC | 294 | 0.0702 | 0.0702 | 559/559 | 0.0702 | 0.0702 | 561/561 | OK |

Notes: null stored values (no bar in the relevant sub-interval) match null provider values (e.g. `PLAG`, `YJ` 2021-03-31; `FTVI`, `OMER`). `2021-11-26` is the 13:00-ET early close (`session_end=779`); the PM window 04:00-09:30 is unaffected and all 5 symbols matched. Per-day `all_match=True` for all 8 days.

## Item 5 — semantic sanity sweep (all 2,685,599 rows) — **PASS**

| rule | violations |
|---|---|
| `pm_last_et > 569` | 0 |
| `et_C > C-1` (any clock C, non-null) | 0 |
| `fo_C` non-null and `fet_C < C` | 0 |
| `fo_C` null but `fet_C` non-null (inconsistent) | 0 |
| `fo_C` non-null but `fet_C` null (inconsistent) | 0 |

Every row of every parquet was scanned (columns: `pm_last_et`, `et_{510,540,550,555,560,565,569}`, `fo_*`, `fet_*`). Zero violations on all rules, including the paired null/non-null consistency of `fo_C`/`fet_C`.

## Verdict

| item | result |
|---|---|
| 1. manifests / completeness / sha256 | **PASS** |
| 2. index.jsonl coverage & manifest agreement | **PASS** |
| 3. coverage texture | **PASS** (reported; no acceptance threshold) |
| 4. 8-day provider spot-check (px_560/fo_560) | **PASS** |
| 5. pm_last_et/et_C/fo_C sanity | **PASS** |

All 1066 dev days are present, complete, sha-verified, index-consistent, semantically sound, and reproduce the provider exactly on the sampled days. No exceptions found.
