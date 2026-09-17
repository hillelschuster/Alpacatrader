# BASKET-01 data provenance (pre-SIP certification)

Established 2026-09-17 from code + files, not from documentation alone. Machine-readable
version: `factory/artifacts/basket/data_provenance.json`. Unknowns are labeled unknown.

## What BASKET actually consumed

| Layer | Source | Feed | Resolution | Built by |
|---|---|---|---|---|
| `clean_ohlcv_*` 2021-01..2023-12, 2025-02..2026-02 | HF `mito0o852/OHLCV-1m` (upstream Finnhub, documented) | unknown | 1-min provider bars | `download_month.py` + `clean_month.py` |
| `backfill/clean_ohlcv_*` 2026-03..2026-08 | Alpaca | **SIP** | 1-min provider bars | `backfill_alpaca.py` + `clean_month.py` |
| `premarket_ohlcv_2025-*` | Alpaca | **SIP** | 1-min provider bars, 04:00-09:29:59 ET | `backfill_premarket_2025.py` |
| `leaderboard/lb_*`, `path_*` | inherits clean_ohlcv mix | mixed | 1-min rebased to decision grid 571..959 | `lb18.py` |
| `iex_tape/path_*` | Alpaca | IEX (experimental lane) | bars | `lb18_iex_tape.py` |
| `subminute/*_exit_quotes` | Alpaca | **SIP quotes**, on demand | quote slices at exit moments only | `lb18_subminute.py` |
| `pit/pit_symbols` | `yolo22/stock-pit-archives` | - | universe vintages 2021+ | `build_pit_from_bundle.py` |

## Transforms baked into `clean_ohlcv` (before any BASKET logic)

1. ET conversion; RTH filter `[09:30, 16:00)`.
2. Dedup `(timestamp, ticker)` keep-first.
3. **Price floor: bar close >= $2.00**; **bar volume >= 100**.
4. PIT join and split exclusion in `clean_month.py` are TODO no-ops (never implemented there;
   handled downstream by BASKET/an audit logic).

## Known breaks and gaps

- **2024 raw + clean absent** while 2024 leaderboard files exist (252+252) -> 2024 is not
  regenerable or verifiable from local data.
- 2025-01 absent (premarket files exist; no RTH clean).
- `clean_ohlcv_2025-02.parquet` exists with **no local raw sibling** (origin unresolved).
- **Provenance break at 2026-03**: feed switches HF/Finnhub -> Alpaca SIP. `ohlcv_2026-03`
  exists on both feeds -> one overlapping month for cross-feed comparison.
- HF months: trade conditions, cancellations/corrections, and the underlying feed are unknown.

## Unresolvable from local code/files

- HF bar construction source feed and condition handling; Alpaca bar condition policy;
  how 2024 leaderboard was produced.

## Implications for the SIP certification layer

- SIP trades/quotes must sit **under** a derived replication of these transforms: raw event
  record first, then the `$2/volume>=100/RTH/dedup` interpretation as an explicit derived layer.
- The BRP 2022-03-10 probe (max SIP trade 25.76 in 09:40-10:35 ET vs the 1092.71 print in the
  HF clean tape) already shows at least one HF-era corrupted print absent from SIP.
- 2026-03..08 are already SIP bars from Alpaca; the open question is whether provider *bars*
  differ from bars we reconstruct from SIP *trades* (timestamps, auction prints, conditions).
