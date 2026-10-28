# Measured Alpacatrader storage inventory

Measurement date: 2026-10-28. Scope: requested local paths. Size unit below is GiB (1 GiB = 1,048,576 KiB); file sizes/counts are exact as observed. No secrets were read or printed. No local files were moved/deleted; no data upload occurred.

## Status and measurement limits

- `data/` on Windows is a C:-direct 9p path. The root-level 9p `du` and file count completed (114.021 GiB apparent, 114.209 GiB allocated, 114,865 files). The per-subdirectory 9p scan only yielded the partial rows below; separate slow 9p chunks also stopped during `leaderboard` and `scratch_pattern`. Missing sizes/counts are explicitly not measured, not inferred.
- `/home/hillel/algo projects` and `/home/hillel/alpha-data` are on the WSL vhdx. Those roots and the requested WSL/alpha depth summaries were measured directly with `du -s --apparent-size`, plain `du -s`, and `find -type f` counts.
- The benchmark was skipped at Main agent direction: network SFTP port 23 is blocked in this environment; rclone mkdir timed out before a stream/object, SSH reported network unreachable, and purge was attempted but could not reach the host. Main agent will measure via the available WebDAV remote. No benchmark object was created by this run.
- Unit basis: `du` values in source are KiB (1,024 bytes); tables convert to GiB. Allocation can differ slightly from apparent size. mtime is newest regular-file mtime, UTC. “Dominant ext” is the most frequent suffix among files with a suffix.

## 1. Windows `Alpacatrader/data` — partial direct inventory

Root `du` completed: apparent 119,611,868 KiB (114.021 GiB), allocated 119,809,367 KiB (114.209 GiB), 114,865 files; the following subtrees finished the individual 9p scan; the `harvest01` row has only apparent size from the partial TSV. The 49-file OHLCV aggregates below were measured separately.

| `data/` path | Apparent GiB | Allocated GiB | Files | Newest file mtime (UTC) | Dominant extension |
|---|---:|---:|---:|---|---|
| `.cache` | 0.0000 | 0.0000 | 3 | 2026-08-26 19:07 UTC | `.metadata` |
| `.cache/huggingface` | 0.0000 | 0.0000 | 3 | 2026-08-26 19:07 UTC | `.metadata` |
| `atlas` | 52.4780 | 52.4951 | 9,564 | 2026-10-01 19:41 UTC | `.parquet` |
| `atlas/acquisition` | 0.3353 | 0.3358 | 238 | 2026-09-30 07:06 UTC | `.json` |
| `atlas/continuation` | 0.2706 | 0.2709 | 192 | 2026-10-01 19:41 UTC | `.parquet` |
| `atlas/geometry` | 2.2846 | 2.2848 | 174 | 2026-10-01 12:11 UTC | `.npz` |
| `atlas/observation` | 48.9026 | 48.9186 | 8,946 | 2026-10-01 04:15 UTC | `.parquet` |
| `atlas/sequence` | 0.6849 | 0.6850 | 14 | 2026-10-01 07:27 UTC | `.pt` |
| `backfill` | 10.1696 | 10.1872 | 8,929 | 2026-09-04 17:12 UTC | `.parquet` |
| `backfill/parts` | 2.5469 | 2.5528 | 3,014 | 2026-09-03 00:21 UTC | `.parquet` |
| `backfill/premarket_parts` | 0.3148 | 0.3265 | 5,891 | 2026-09-04 17:12 UTC | `.parquet` |
| `entry_ev` | 0.1606 | 0.1624 | 1,599 | 2026-10-05 22:43 UTC | `.parquet` |
| `entry_ev/stage_a` | 0.1606 | 0.1624 | 1,599 | 2026-10-05 22:43 UTC | `.parquet` |
| `forward` | 0.0541 | 0.0542 | 90 | 2026-10-28 04:00 UTC | `.jsonl` |
| `forward/2026-09-03` | 0.0000 | 0.0000 | 1 | 2026-09-03 22:06 UTC | `.jsonl` |
| `forward/2026-09-04` | 0.0055 | 0.0056 | 6 | 2026-09-11 02:12 UTC | `.jsonl` |
| `forward/2026-09-05` | 0.0020 | 0.0020 | 4 | 2026-09-05 20:11 UTC | `.jsonl` |
| `forward/2026-09-06` | 0.0020 | 0.0020 | 4 | 2026-09-06 20:11 UTC | `.jsonl` |
| `forward/2026-09-08` | 0.0037 | 0.0037 | 5 | 2026-09-11 02:12 UTC | `.jsonl` |
| `forward/2026-09-09` | 0.0067 | 0.0067 | 5 | 2026-09-11 02:12 UTC | `.jsonl` |
| `forward/2026-09-11` | 0.0028 | 0.0028 | 7 | 2026-09-11 20:09 UTC | `.jsonl` |
| `forward/2026-09-12` | 0.0020 | 0.0020 | 2 | 2026-09-12 20:11 UTC | `.jsonl` |
| `forward/2026-09-13` | 0.0020 | 0.0020 | 2 | 2026-09-13 20:09 UTC | `.jsonl` |
| `forward/2026-09-14` | 0.0027 | 0.0027 | 5 | 2026-09-14 20:10 UTC | `.jsonl` |
| `forward/2026-09-15` | 0.0027 | 0.0027 | 5 | 2026-09-15 20:10 UTC | `.jsonl` |
| `forward/2026-09-16` | 0.0010 | 0.0010 | 5 | 2026-09-16 15:51 UTC | `.jsonl` |
| `forward/bot` | 0.0016 | 0.0016 | 7 | 2026-09-16 15:52 UTC | `.jsonl` |
| `forward/depth_bot` | 0.0192 | 0.0193 | 32 | 2026-10-28 04:00 UTC | `.parquet` |
| `h025_research` | 0.0339 | 0.0395 | 3,986 | 2026-10-06 13:30 UTC | `.parquet` |
| `h025_research/core` | 0.0267 | 0.0316 | 3,133 | 2026-10-06 13:30 UTC | `.parquet` |
| `h025_research/entry_smoke` | 0.0001 | 0.0001 | 1 | 2026-10-06 13:14 UTC | `.json` |
| `h025_research/execution` | 0.0022 | 0.0023 | 169 | 2026-10-06 13:15 UTC | `.parquet` |
| `h025_research/exit` | 0.0000 | 0.0000 | 3 | 2026-10-06 13:14 UTC | `.parquet` |
| `h025_research/forward` | 0.0000 | 0.0000 | 3 | 2026-10-06 13:12 UTC | `.lock` |
| `h025_research/qualification` | 0.0049 | 0.0054 | 677 | 2026-10-06 13:15 UTC | `.parquet` |
| `harvest01` (partial) | 10.494 | 10.590 | 64,931 | not measured | not measured |

### Root monthly OHLCV files (du totals)

| Family | Files | Apparent GiB | Allocated GiB | Class |
|---|---:|---:|---:|---|
| `ohlcv_*.parquet` | 49 | 17.150 | 17.150 | raw-data |
| `clean_ohlcv_*.parquet` | 49 | 20.931 | 20.931 | derived |

The du aggregate outputs were 17,983,587/17,983,684 KiB for raw apparent/allocated and 21,947,053/21,947,152 KiB for clean apparent/allocated. Direct code references include root monthly `ohlcv` and `clean_ohlcv` inputs plus `backfill`.

### Additional measured Windows data roots

| Path | Apparent GiB | Allocated GiB | Evidence / limits |
|---|---:|---:|---|
| `data/atlas` | 52.478 | 52.495 | 9,564 files; partial scan table above |
| `data/backfill` | 10.170 | 10.187 | 8,929 files; partial scan table above |
| `data/harvest01` | 10.494 | 10.590 | 64,931 files; app/allocated from partial output |
| `data/iex_tape` | 0.0277 | 0.0290 | allocated/apparent measured separately |
| `data/journal` | 0.000011 | 0.000019 | measured separately |
| `data/leaderboard` | 0.1405 | 0.1458 | measured separately |
| `data/ml_features` | 0.4532 | 0.4532 | measured separately |
| `data/pit` | 0.0440 | 0.0440 | measured separately |
| `data/scratch_census` | 0.0140 | 0.0162 | measured separately |
| `data/scratch_pattern` | 0.1849 | 0.1909 | measured separately |
| `data/scratch_pattern_events` | 0.0238 | 0.0251 | measured separately |
| `data/_scratch` | 0.0300 | not measured | apparent only from partial root scan |
| `data/_superseded` | 1.237 | not measured | apparent only from partial root scan |
| `data/sip` | 1.3288 | not measured | apparent-size completed; allocated/file count not measured |
| `data/subminute` | not measured | not measured | slow 9p chunk did not finish |

### Current code references vs no direct references found

Current `factory/scripts`/`src` path literals consume `data/sip/{candidates,universe/rth,pm_snapshots,net/{bars,quotes,trades}}`, root `ohlcv_*`/`clean_ohlcv_*`, `backfill`, `harvest01/base/splits.parquet`, `leaderboard`, `ml_features`, `atlas/sequence`, `forward/{bot,depth_bot}`, `iex_tape`, `subminute`, and `scratch_census`. `atlas` is only confirmed used under its `sequence` subtree in the search results. No direct data-path literal was found for `entry_ev`, `pit`, `journal`, `_scratch`, `_superseded`, `scratch_pattern`, or `scratch_pattern_events`; absence of a literal is not a deletion-safety proof. `forward/` is live control/state data and must be preserved.

## 2. Other Windows sibling projects

Top-level sibling directories found: `.agents`, `.obsidian`, `.omo`, `C++ bot`, `Hyperliquid-index`, `Polymarket bots`, `Vps`, `alphabot`, `alphabot2`, `kalshi bots`, `opencode`. Their directory sizes/file counts were not completed because the unbounded `9p` scan did not finish; no estimates are supplied. Root loose files (bytes, exact): `Polymarket_Israel_Tax_Legal_and_Bot_Memo_HE_EN.docx` 61,573; `AGENTS.md` 5,003; `mcp_config.json` 385; `desktop.ini` 46. Do not use these values to imply totals for sibling project folders.

## 3. WSL `/home/hillel/algo projects` — second-level

Root `du`: apparent 41,487,478 KiB = 39.565 GiB; allocated 42,086,028 KiB = 40.138 GiB; 278,240 regular files. Requested explicit nested measurements: `data/Alpacatrader/sip/net` 27,601,947 KiB apparent (26.323 GiB), 27,621,520 KiB allocated (26.342 GiB), 7,478 files; `worktrees/Alpacatrader` 5,924,851 KiB / 6,395,272 KiB, 229,109 files; main `.git` 341,495 / 355,024 KiB, 4,867 files; main `.venv` 1,542,354 / 1,618,936 KiB, 28,308 files.

| Second-level path | Apparent GiB | Allocated GiB | Files |
|---|---:|---:|---:|
| `worktrees/Alpacatrader` | 5.6504 | 6.0990 | 229,109 |
| `data/Alpacatrader` | 26.3233 | 26.3419 | 7,478 |
| `Alpacatrader/.codegraph` | 0.0929 | 0.0929 | 6 |
| `Alpacatrader/.venv` | 1.4709 | 1.5439 | 28,308 |
| `Alpacatrader/.pytest_cache` | 0.0002 | 0.0002 | 5 |
| `Alpacatrader/.ruff_cache` | 0.0000 | 0.0000 | 7 |
| `Alpacatrader/src` | 0.0011 | 0.0013 | 74 |
| `Alpacatrader/factory` | 5.5442 | 5.5605 | 8,062 |
| `Alpacatrader/.omo` | 0.0000 | 0.0005 | 135 |
| `Alpacatrader/__pycache__` | 0.0000 | 0.0000 | 1 |
| `Alpacatrader/logs` | 0.1508 | 0.1509 | 43 |
| `Alpacatrader/docs` | 0.0000 | 0.0000 | 1 |
| `Alpacatrader/tests` | 0.0045 | 0.0047 | 81 |
| `Alpacatrader/config` | 0.0000 | 0.0000 | 5 |
| `Alpacatrader/.git` | 0.3257 | 0.3386 | 4,867 |
| `Alpacatrader/archive` | 0.0002 | 0.0002 | 5 |
| `Alpacatrader/researches` | 0.0004 | 0.0004 | 36 |

### WSL SIP `net` component details

| Component | Apparent GiB | Allocated GiB | Files |
|---|---:|---:|---:|
| `bars` | 0.2161 | 0.2182 | 1,068 |
| `coverage` | 0.0125 | 0.0182 | 2,136 |
| `quotes` | 3.8509 | 3.8562 | 2,136 |
| `trades` | 22.2428 | 22.2483 | 2,136 |
| `manifest.jsonl` | 0.0000 | 0.0000 | 1 |
| `manifest_index.jsonl` | 0.0020 | 0.0020 | 1 |

## 4. `/home/hillel/alpha-data` — second/third-level folders

Root `du`: apparent 54,058,616 KiB = 51.555 GiB; allocated 54,684,004 KiB = 52.151 GiB; 206,704 regular files. Lane table is second-level under `open-search-v1`; third-level table lists all immediate lane subdirectories. File counts are recursive per listed directory.

| Second-level lane | Apparent GiB | Allocated GiB | Files |
|---|---:|---:|---:|
| `_smoke_quote_aware_v2` | 0.0007 | 0.0007 | 19 |
| `_smoke_quote_delay` | 0.0007 | 0.0008 | 13 |
| `_smoke_quote_delay3` | 0.0014 | 0.0014 | 14 |
| `adaptive_depth_sizing` | 0.0424 | 0.0486 | 2,914 |
| `alpha_retained_mechanism` | 0.0002 | 0.0002 | 3 |
| `basket_reborn` | 0.0109 | 0.0120 | 584 |
| `bid_backed_burst` | 0.0079 | 0.0104 | 1,156 |
| `cross_name_rotation` | 0.2255 | 0.2351 | 4,928 |
| `days` | 0.1524 | 0.1580 | 2,132 |
| `delayed_sip_iex` | 0.0231 | 0.0297 | 2,916 |
| `depth_plateau` | 0.0389 | 0.0471 | 2,912 |
| `depth_universe` | 0.0391 | 0.0471 | 2,923 |
| `entry_microstructure` | 0.0135 | 0.0140 | 583 |
| `events` | 0.0140 | 0.0141 | 21 |
| `learned` | 0.0021 | 0.0022 | 26 |
| `learned_sparse_daily` | 0.0035 | 0.0047 | 604 |
| `learned_sparse_extension` | 0.0002 | 0.0002 | 11 |
| `liquidity_recovery_five_min` | 0.1103 | 0.1127 | 1,264 |
| `liquidity_recovery_five_min_v2` | 0.0787 | 0.0809 | 1,130 |
| `micro_flow` | 0.0014 | 0.0020 | 308 |
| `micro_payoff` | 0.1751 | 0.1780 | 1,145 |
| `micro_reclaim` | 0.0040 | 0.0051 | 599 |
| `multi_session_carry` | 0.2185 | 0.2746 | 20,751 |
| `opening_range_pullback` | 0.0249 | 0.0294 | 2,189 |
| `overnight` | 0.0043 | 0.0043 | 16 |
| `passive_quote_confirmation` | 0.0452 | 0.0518 | 2,914 |
| `proven_push` | 0.0018 | 0.0018 | 12 |
| `provider_fee_ledger` | 0.0118 | 0.0119 | 56 |
| `quote_aware_frequency` | 0.0407 | 0.0471 | 2,914 |
| `quote_aware_frequency_v2` | 0.0442 | 0.0505 | 2,914 |
| `quote_completeness_audit` | 0.0012 | 0.0016 | 99 |
| `quote_entry_delay` | 0.0739 | 0.0783 | 2,914 |
| `quote_hourly_payoff` | 0.1551 | 0.1660 | 4,923 |
| `quote_staleness_probe` | 0.0001 | 0.0001 | 7 |
| `quote_universe_acquire` | 49.7481 | 50.1076 | 109,244 |
| `retained_book_shape` | 0.0126 | 0.0201 | 2,914 |
| `retained_delay_threshold` | 0.0099 | 0.0121 | 1,170 |
| `retained_entry_delay` | 0.0056 | 0.0076 | 1,170 |
| `retained_exit_grid` | 0.0107 | 0.0141 | 1,168 |
| `retained_h390_lane` | 0.0065 | 0.0094 | 1,170 |
| `retained_score_sizing` | 0.0098 | 0.0179 | 2,914 |
| `retained_target_exits` | 0.0043 | 0.0072 | 1,173 |
| `retained_timing` | 0.0027 | 0.0045 | 590 |
| `selector_upgrade` | 0.1128 | 0.1306 | 8,734 |
| `sequence` | 0.0028 | 0.0028 | 19 |
| `shallow_depth_lane` | 0.0221 | 0.0258 | 1,500 |
| `short_diagnostic` | 0.0002 | 0.0002 | 4 |
| `signal_supply` | 0.0355 | 0.0652 | 8,989 |
| `sparse_execution_frontier` | 0.0028 | 0.0029 | 20 |
| `sparse_exit_management` | 0.0004 | 0.0004 | 6 |
| `sparse_lead_quote_audit` | 0.0001 | 0.0001 | 4 |

| Third-level directory | Apparent GiB | Allocated GiB | Files |
|---|---:|---:|---:|
| `_smoke_quote_aware_v2/replay` | 0.0001 | 0.0001 | 9 |
| `_smoke_quote_aware_v2/states` | 0.0001 | 0.0001 | 6 |
| `_smoke_quote_delay/replay` | 0.0001 | 0.0001 | 6 |
| `_smoke_quote_delay/states` | 0.0001 | 0.0002 | 4 |
| `_smoke_quote_delay3/replay` | 0.0001 | 0.0001 | 6 |
| `_smoke_quote_delay3/states` | 0.0002 | 0.0002 | 4 |
| `adaptive_depth_sizing/replay` | 0.0187 | 0.0219 | 1,746 |
| `adaptive_depth_sizing/states` | 0.0221 | 0.0250 | 1,164 |
| `basket_reborn/daily` | 0.0109 | 0.0120 | 582 |
| `bid_backed_burst/days` | 0.0062 | 0.0086 | 1,128 |
| `cross_name_rotation/features` | 0.1585 | 0.1633 | 2,006 |
| `cross_name_rotation/models` | 0.0014 | 0.0014 | 7 |
| `cross_name_rotation/replay` | 0.0275 | 0.0303 | 1,746 |
| `cross_name_rotation/states` | 0.0359 | 0.0380 | 1,164 |
| `delayed_sip_iex/replay` | 0.0148 | 0.0185 | 1,746 |
| `delayed_sip_iex/states` | 0.0067 | 0.0097 | 1,164 |
| `depth_plateau/replay` | 0.0166 | 0.0219 | 1,746 |
| `depth_plateau/states` | 0.0221 | 0.0250 | 1,164 |
| `depth_universe/_staging` | 0.0000 | 0.0000 | 0 |
| `depth_universe/derived` | 0.0159 | 0.0227 | 2,328 |
| `depth_universe/plan` | 0.0225 | 0.0237 | 582 |
| `depth_universe/replay` | 0.0002 | 0.0002 | 5 |
| `entry_microstructure/days` | 0.0061 | 0.0066 | 582 |
| `events/quote_audit_confirmation` | 0.0000 | 0.0000 | 2 |
| `learned/models` | 0.0018 | 0.0018 | 7 |
| `learned/quote_confirmation` | 0.0000 | 0.0000 | 2 |
| `learned/quote_validation` | 0.0000 | 0.0000 | 2 |
| `learned/replay` | 0.0001 | 0.0001 | 3 |
| `learned/strategy_confirmation` | 0.0001 | 0.0001 | 4 |
| `learned/strategy_validation` | 0.0000 | 0.0001 | 4 |
| `learned_sparse_daily/collect_parts` | 0.0019 | 0.0029 | 582 |
| `learned_sparse_daily/quote_audit` | 0.0005 | 0.0005 | 16 |
| `learned_sparse_extension/quote_confirmation` | 0.0000 | 0.0000 | 2 |
| `liquidity_recovery_five_min/days` | 0.0920 | 0.0942 | 1,128 |
| `liquidity_recovery_five_min_v2/days` | 0.0785 | 0.0806 | 1,128 |
| `micro_flow/days` | 0.0008 | 0.0014 | 291 |
| `micro_payoff/corpus` | 0.1735 | 0.1763 | 1,128 |
| `micro_payoff/late` | 0.0000 | 0.0000 | 5 |
| `micro_payoff/models` | 0.0015 | 0.0015 | 5 |
| `micro_payoff/replay` | 0.0000 | 0.0000 | 2 |
| `micro_reclaim/days` | 0.0035 | 0.0046 | 582 |
| `multi_session_carry/candidates` | 0.0057 | 0.0087 | 1,459 |
| `multi_session_carry/missing_legs` | 0.0000 | 0.0000 | 3 |
| `multi_session_carry/qual` | 0.0001 | 0.0001 | 6 |
| `multi_session_carry/replay` | 0.2105 | 0.2636 | 19,278 |
| `opening_range_pullback/replay` | 0.0152 | 0.0181 | 1,311 |
| `opening_range_pullback/states` | 0.0085 | 0.0101 | 874 |
| `passive_quote_confirmation/replay` | 0.0182 | 0.0219 | 1,746 |
| `passive_quote_confirmation/states` | 0.0258 | 0.0287 | 1,164 |
| `provider_fee_ledger/cohorts` | 0.0011 | 0.0011 | 3 |
| `provider_fee_ledger/days` | 0.0076 | 0.0077 | 24 |
| `provider_fee_ledger/producer_snapshot` | 0.0001 | 0.0001 | 1 |
| `provider_fee_ledger/trades` | 0.0018 | 0.0019 | 24 |
| `quote_aware_frequency/replay` | 0.0157 | 0.0194 | 1,746 |
| `quote_aware_frequency/states` | 0.0245 | 0.0272 | 1,164 |
| `quote_aware_frequency_v2/replay` | 0.0191 | 0.0226 | 1,746 |
| `quote_aware_frequency_v2/states` | 0.0245 | 0.0272 | 1,164 |
| `quote_completeness_audit/manifest` | 0.0001 | 0.0001 | 2 |
| `quote_completeness_audit/quotes` | 0.0002 | 0.0003 | 44 |
| `quote_completeness_audit/raw` | 0.0004 | 0.0007 | 52 |
| `quote_entry_delay/replay` | 0.0255 | 0.0279 | 1,746 |
| `quote_entry_delay/states` | 0.0455 | 0.0474 | 1,164 |
| `quote_hourly_payoff/labels` | 0.1134 | 0.1183 | 2,006 |
| `quote_hourly_payoff/models` | 0.0007 | 0.0007 | 2 |
| `quote_hourly_payoff/replay` | 0.0171 | 0.0203 | 1,746 |
| `quote_hourly_payoff/states` | 0.0229 | 0.0257 | 1,164 |
| `quote_staleness_probe/quotes` | 0.0000 | 0.0000 | 3 |
| `quote_staleness_probe/raw` | 0.0001 | 0.0001 | 3 |
| `quote_universe_acquire/manifest` | 0.0073 | 0.0093 | 1,003 |
| `quote_universe_acquire/parts` | 1.7316 | 1.9371 | 61,114 |
| `quote_universe_acquire/quotes` | 4.3530 | 4.3550 | 1,003 |
| `quote_universe_acquire/raw` | 43.6562 | 43.8062 | 46,121 |
| `retained_book_shape/replay` | 0.0105 | 0.0148 | 1,746 |
| `retained_book_shape/states` | 0.0016 | 0.0048 | 1,164 |
| `retained_delay_threshold/collect_parts` | 0.0075 | 0.0097 | 1,164 |
| `retained_entry_delay/signal_parts` | 0.0046 | 0.0065 | 1,164 |
| `retained_exit_grid/collect_parts` | 0.0039 | 0.0072 | 1,164 |
| `retained_h390_lane/collect_parts` | 0.0053 | 0.0082 | 1,164 |
| `retained_score_sizing/replay` | 0.0073 | 0.0119 | 1,746 |
| `retained_score_sizing/states` | 0.0017 | 0.0052 | 1,164 |
| `retained_target_exits/parts` | 0.0024 | 0.0053 | 1,164 |
| `retained_timing/collect_parts` | 0.0013 | 0.0030 | 583 |
| `selector_upgrade/compact` | 0.0358 | 0.0417 | 2,910 |
| `selector_upgrade/current` | 0.0408 | 0.0470 | 2,910 |
| `selector_upgrade/monotone_compact` | 0.0347 | 0.0404 | 2,910 |
| `sequence/models` | 0.0000 | 0.0000 | 6 |
| `shallow_depth_lane/replay` | 0.0073 | 0.0091 | 750 |
| `shallow_depth_lane/states` | 0.0135 | 0.0154 | 746 |
| `signal_supply/parts` | 0.0354 | 0.0651 | 8,988 |
| `sparse_execution_frontier/fetched_quotes` | 0.0004 | 0.0004 | 12 |
| `sparse_execution_frontier/late143` | 0.0004 | 0.0004 | 1 |
| `sparse_execution_frontier/producer_snapshot` | 0.0001 | 0.0001 | 2 |
| `sparse_execution_frontier/val61` | 0.0003 | 0.0003 | 1 |

## 5. Git worktree status (read-only)

Status from `git status --porcelain --ignored=matching`; sizes are unique file bytes per category, avoiding nested status-path double counting. Full top-20 status paths by category are included below. `.env` was only seen as a filename/status and its value was never read.

### main

| Status | Paths | Bytes (unique, disjoint) |
|---|---:|---:|
| modified-tracked | 119 | 4,551,077 |

Top 20 `modified-tracked` paths (status-path byte total; dirs are aggregate):
- `factory/artifacts/h006_results_2025-07/h006_summary.json` — 1,090,173 B
- `factory/artifacts/h006_results_2025-07/cost40bps/h006_summary.json` — 1,090,124 B
- `factory/artifacts/h006_results_2025-07/cost40bps/run.log` — 300,764 B
- `uv.lock` — 300,752 B
- `factory/artifacts/h006_results_2025-07/run.log` — 300,590 B
- `factory/scripts/alpha_delayed_sip_iex.py` — 131,296 B
- `factory/scripts/alpha_cross_name_rotation.py` — 127,056 B
- `factory/scripts/alpha_multi_session_carry.py` — 125,326 B
- `factory/scripts/alpha_touch_hourly_payoff.py` — 106,298 B
- `factory/scripts/alpha_passive_quote_confirmation.py` — 105,917 B
- `factory/scripts/alpha_opening_range_pullback.py` — 104,140 B
- `factory/scripts/alpha_liquidity_recovery.py` — 98,113 B
- `factory/scripts/alpha_quote_entry_delay.py` — 95,211 B
- `factory/artifacts/h009_results_2025-07/h009_summary_c20.json` — 58,329 B
- `factory/artifacts/ml/train_report_v2.json` — 51,863 B
- `factory/artifacts/ml/train_report_v1e.json` — 51,838 B
- `factory/artifacts/ml/train_report_v1.json` — 51,819 B
- `factory/artifacts/zz_leader_health.csv` — 50,768 B
- `researches/07-dominant-leader-program.md` — 19,462 B
- `factory/artifacts/h009_results_2025-07/run_c20.log` — 18,136 B
| untracked | 45 | 285,836,034 |

Top 20 `untracked` paths (status-path byte total; dirs are aggregate):
- `factory/artifacts/basket/phase2/` — 261,715,313 B
- `factory/artifacts/overnight/` — 20,617,560 B
- `factory/artifacts/alpha_night_extensions.json` — 858,726 B
- `factory/artifacts/alpha_search_20261008.json` — 837,893 B
- `basket and different runs` — 462,921 B
- `factory/artifacts/basket/sip/phase2_session_calendar.json` — 427,375 B
- `factory/scripts/alpha_shallow_depth_lane.py` — 149,521 B
- `factory/scripts/basket_sim.py` — 83,680 B
- `factory/scripts/alpha_passive_spread_capture.py` — 76,115 B
- `factory/scripts/basket_f3_release.py` — 69,560 B
- `factory/scripts/alpha_premarket_gap_lane.py` — 50,787 B
- `capture-origin-final.md` — 48,732 B
- `factory/scripts/alpha_panel_hod_break.py` — 42,662 B
- `factory/scripts/alpha_sparse_lead_quote_audit.py` — 39,788 B
- `factory/scripts/basket_f8_joint.py` — 31,029 B
- `tests/test_alpha_passive_quote_confirmation.py` — 29,196 B
- `.omo/` — 28,939 B
- `tests/test_alpha_opening_range_pullback.py` — 28,751 B
- `factory/scripts/basket_f2_features.py` — 26,475 B
- `tests/test_alpha_touch_hourly_payoff.py` — 24,360 B
| ignored | 140 | 7,197,086,246 |

Top 20 `ignored` paths (status-path byte total; dirs are aggregate):
- `.venv/` — 1,579,369,917 B
- `factory/artifacts/basket/sip/bars/` — 174,083,362 B
- `factory/artifacts/basket/sip/anatomy/` — 164,883,507 B
- `logs/` — 161,886,171 B
- `factory/artifacts/halt_reopen_all.json` — 147,933,959 B
- `factory/artifacts/basket/anatomy/` — 133,465,478 B
- `factory/artifacts/basket/bars/` — 123,148,650 B
- `.codegraph/` — 99,733,656 B
- `factory/artifacts/ranked_2025-07-31.parquet` — 82,086,427 B
- `factory/artifacts/ranked_2025-05-12.parquet` — 78,404,928 B
- `factory/artifacts/ranked_2025-07-30.parquet` — 77,688,550 B
- `factory/artifacts/ranked_2025-07-16.parquet` — 76,477,281 B
- `factory/artifacts/ranked_2025-06-23.parquet` — 76,408,568 B
- `factory/artifacts/ranked_2025-07-23.parquet` — 75,700,808 B
- `factory/artifacts/ranked_2025-07-07.parquet` — 75,559,409 B
- `factory/artifacts/ranked_2025-07-15.parquet` — 75,248,267 B
- `factory/artifacts/ranked_2025-05-08.parquet` — 75,015,347 B
- `factory/artifacts/ranked_2025-05-13.parquet` — 74,696,450 B
- `factory/artifacts/ranked_2025-06-13.parquet` — 74,612,933 B
- `factory/artifacts/ranked_2025-07-22.parquet` — 74,494,625 B

### basket-phase2-f1

| Status | Paths | Bytes (unique, disjoint) |
|---|---:|---:|
| modified-tracked | 1 | 17,363 |

Top 20 `modified-tracked` paths (status-path byte total; dirs are aggregate):
- `factory/scripts/h025_research_entry.py` — 17,363 B
| untracked | 1,117 | 4,008,449,646 |

Top 20 `untracked` paths (status-path byte total; dirs are aggregate):
- `factory/artifacts/basket/phase2/F5/F5/` — 2,239,981,299 B
- `factory/artifacts/basket/phase2/F4_C1/` — 257,589,091 B
- `factory/artifacts/basket/phase2/F4/F4/` — 211,636,187 B
- `factory/artifacts/basket/phase2/F5_DIAG/` — 209,358,338 B
- `factory/artifacts/basket/phase2/F11/run_20260923_full/sim_runs/` — 160,444,462 B
- `factory/artifacts/h025_research/entry/allowlist_monthly.csv` — 147,830,537 B
- `factory/artifacts/basket/phase2/F1_C1/` — 138,608,026 B
- `factory/artifacts/basket/phase2/F1/F1/` — 106,916,974 B
- `factory/artifacts/h025_research/entry/dev533_monthly.csv` — 75,281,735 B
- `factory/artifacts/h025_research/entry/replication_monthly.csv` — 75,121,585 B
- `factory/artifacts/basket/phase2/F11/run_20260923_full/runs/` — 59,470,745 B
- `factory/artifacts/basket/phase2/F6_DIAG2/` — 40,797,867 B
- `factory/artifacts/basket/phase2/F6_COMBO/` — 26,147,213 B
- `factory/artifacts/basket/phase2/F3/structural_map/structural_daily.parquet` — 22,256,273 B
- `factory/artifacts/basket/phase2/DERISK_DIAG/` — 22,070,610 B
- `factory/artifacts/basket/phase2/HARVEST_DIAG/` — 21,563,022 B
- `factory/artifacts/basket/phase2/F3_C1/` — 20,535,547 B
- `factory/artifacts/basket/phase2/SEGMENT_DIAG/` — 17,145,750 B
- `factory/artifacts/h025_research/entry/allowlist_yearly.csv` — 16,316,259 B
- `factory/artifacts/basket/phase2/F2_F12/shards/` — 16,303,140 B
| ignored | 3,672 | 1,396,082,552 |

Top 20 `ignored` paths (status-path byte total; dirs are aggregate):
- `.venv/` — 379,263,022 B
- `data/` — 360,530,678 B
- `factory/artifacts/basket/phase2/ATLAS/parts/` — 285,394,826 B
- `factory/artifacts/basket/phase2/ATLAS/panel.parquet` — 280,578,680 B
- `factory/artifacts/basket/phase2/ATLAS/window_profile.json` — 40,367,994 B
- `factory/artifacts/basket/phase2/ATLAS/smoke/` — 12,003,643 B
- `factory/scripts/__pycache__/` — 8,200,362 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-06.parquet` — 624,960 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-03.parquet` — 621,615 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-07.parquet` — 604,797 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-08.parquet` — 604,609 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-12.parquet` — 603,639 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-10.parquet` — 596,035 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2022-03.parquet` — 588,434 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-11.parquet` — 576,938 B
- `factory/artifacts/basket/phase2/PLACEBO_DIAG/PLACEBO/A_pm_N3_placebo_s11_bps100/parts/tickets/` — 570,043 B
- `factory/artifacts/basket/phase2/PLACEBO_DIAG/PLACEBO/A_pm_N3_placebo_s12_bps100/parts/tickets/` — 569,958 B
- `factory/artifacts/basket/phase2/PLACEBO_DIAG/PLACEBO/A_pm_N3_placebo_bps100/parts/tickets/` — 569,943 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2021-09.parquet` — 566,631 B
- `factory/artifacts/basket/phase2/F3/structural_map/parts/month=2025-10.parquet` — 564,107 B

### depth_shadow

| Status | Paths | Bytes (unique, disjoint) |
|---|---:|---:|
| modified-tracked | 1 | 124,746 |

Top 20 `modified-tracked` paths (status-path byte total; dirs are aggregate):
- `factory/scripts/depth_bot.py` — 124,746 B
| untracked | 2 | 137 |

Top 20 `untracked` paths (status-path byte total; dirs are aggregate):
- `factory/artifacts/basket/sip/phase2_session_calendar.json` — 92 B
- `.venv` — 45 B
| ignored | 3 | 652,829 |

Top 20 `ignored` paths (status-path byte total; dirs are aggregate):
- `factory/scripts/__pycache__/` — 652,751 B
- `.env` — 39 B
- `data` — 39 B


## 6. Storage Box benchmark

Skipped on Main agent instruction; Main owns this measurement via `storagebox-dav:`. Initial SFTP test: rclone mkdir did not connect (port 23 timed out); SSH failed with “Network is unreachable”; `rclone purge` was attempted but did not connect. Since mkdir failed before the stream, no test object was created. No MB/s was measured.

## 7. Overlap / duplicates

- Path aliases verified: `/home/hillel/projects/Alpacatrader/data` -> Windows `Alpacatrader/data`; `/home/hillel/depth_shadow/data` -> that same alias; Windows `data/sip/net` -> `/home/hillel/sip/net`; `/home/hillel/sip` -> `/home/hillel/algo projects/data/Alpacatrader/sip`. These are one physical copy per target, not separate data sets.
- Exact duplicate test keyed by basename + byte size: Windows data vs WSL `sip/net` had 2 shared keys (two 957-byte manifests); both full SHA-256 comparisons differed. Windows data vs alpha-data had 16 shared basename/size pairs; 5 full-file SHA-256 comparisons differed. WSL SIP net vs alpha-data had no basename+size matches. No exact duplicates were found in these comparisons; no claim beyond the tested matches.
- Prior map estimates corrected by measured paths: `sip/net` is 26.323 GiB apparent (not ~21 GiB); `alpha-data` is 51.555 GiB apparent; monthly OHLCV root files are 17.150 GiB raw + 20.931 GiB cleaned (49 each).

## Ranked top 15 inventory items

Ranked by apparent size; folders and their children can both appear, so these rows are not additive. `C: direct` means Windows 9p; `vhdx` means WSL ext4.

| # | Path | Apparent size | Files | Location | Class |
|---:|---|---:|---:|---|---|
| 1 | Windows `Alpacatrader/data` | 114.021 GiB | 114,865 | C: direct | unknown / mixed |
| 2 | Windows `data/atlas` | 52.478 GiB | 9,564 | C: direct | derived |
| 3 | `/home/hillel/alpha-data` | 51.555 GiB | 206,704 | vhdx | unknown / mixed |
| 4 | `alpha-data/open-search-v1/quote_universe_acquire` | 49.748 GiB | 109,244 | vhdx | raw-data / derived |
| 5 | `quote_universe_acquire/raw` | 43.656 GiB | 46,121 | vhdx | raw-data |
| 6 | `/home/hillel/algo projects` | 39.565 GiB | 278,240 | vhdx | unknown / mixed |
| 7 | `data/Alpacatrader/sip/net` | 26.323 GiB | 7,478 | vhdx | raw-data |
| 8 | Windows root `clean_ohlcv_*.parquet` | 20.931 GiB | 49 | C: direct | derived |
| 9 | Windows root `ohlcv_*.parquet` | 17.150 GiB | 49 | C: direct | raw-data |
| 10 | Windows `data/harvest01` | 10.494 GiB | 64,931 | C: direct | derived |
| 11 | Windows `data/backfill` | 10.167 GiB | 8,929 | C: direct | raw-data |
| 12 | `/home/hillel/algo projects/worktrees/Alpacatrader` | 5.650 GiB | 229,109 | vhdx | unknown / dirty worktree |
| 13 | WSL main `Alpacatrader/factory` | 5.544 GiB | 8,062 | vhdx | derived / unknown |
| 14 | `quote_universe_acquire/quotes` | 4.353 GiB | 1,003 | vhdx | raw-data |
| 15 | `quote_universe_acquire/parts` | 1.732 GiB | 61,114 | vhdx | derived |

## Measured class subtotals (non-overlapping named paths only)

These are classified measured subtrees, not a whole-PC total: the Windows 9p root and sibling-project scans did not complete, and class assignment cannot safely classify the unmeasured remainder. Parent directories (`data/`, alpha root, workspace root) are intentionally not added to child totals.

| Class | Measured named-path subtotal | Included measured paths |
|---|---:|---|
| raw-data | 101.65 GiB | Windows root `ohlcv` 17.150; `backfill` 10.170; WSL SIP net 26.323; alpha quote-universe raw 43.656; alpha quote-universe quotes 4.353 |
| derived | 85.63 GiB | Windows `clean_ohlcv` 20.931, `atlas` 52.478, `harvest01` 10.494; alpha quote-universe parts 1.732 |
| git-recoverable | 1.21 GiB tracked working-tree logical bytes | main 319,125,134 B; basket worktree 662,514,074 B; depth_shadow 318,962,230 B (logical working copies) |
| cache-junk | 4.32 GiB measured subset | `~/.cache` 2.76 GiB; main `.venv` 1.47 GiB; main `.codegraph` ~0.093 GiB (plus small measured caches) |
| other-project | not measured | Windows sibling project trees listed in §2 |
| unknown / unclassified | not fully measured | remainder includes Windows data not scanned and remaining alpha-data lanes/subtrees not safely classed; do not treat as zero |

Class subtotals overlap neither parent roots nor the other measured named path rows. Raw/derived are lower-bound measured subtotals; incomplete Windows tree means they are not an exhaustive migration-total accounting.
