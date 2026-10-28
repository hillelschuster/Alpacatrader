# Salvage: Windows project copy (read-only source)

Source dir: `/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader` (own `.git`, HEAD `78442f2`, 2026-09-22).
No deletions, no edits performed there. Landed work goes to worktree `basket-phase2-f1` only.

## 1. Code landed (commit `b48ba1f`, pushed to `origin/basket-phase2-f1`)

12 scripts recovered to `factory/scripts/` (were untracked on the Windows side and absent from
every branch here). Copied byte-for-byte, CR kept as LF (files were already LF):

| file | bytes |
|---|---|
| `basket_f1_run.py` | 7,009 |
| `basket_f1_multi.py` | 8,946 |
| `basket_f4_analysis.py` | 5,488 |
| `basket_f4_report.py` | 12,895 |
| `basket_f4_results.py` | 11,365 |
| `basket_f4_tests.py` | 1,613 |
| `basket_f9_weights.py` | 43,103 |
| `basket_f10_regime.py` | 27,970 |
| `basket_f11_pilot.py` | 14,102 |
| `basket_f11_sensitivity.py` | 12,134 |
| `test_basket_f11.py` | 2,199 |
| `test_basket_f67_capital.py` | 3,640 |

Checks: `uv run --no-sync python -m py_compile` OK on all 12; `ruff check` → 173 findings
(110 E501, 18 N806, 9 UP045, 6 B007, 6 I001, 5 E702, 4 B905, 4 C420, 4 F401, 2 F841, ...) left
unfixed by policy. No secrets, no Windows/absolute user paths (grep clean).
Known dangling ref: `test_basket_f67_capital.py` imports `basket_f67_capital`, which exists in no
branch of this repo and no commit of the Windows copy — the module was never written.

### `factory/scripts/basket_sim.py` — NOT landed (declined)

Three-way state: Windows 84,274 B / 2,023 lines; basket branch 170,122 B / 3,758 lines; main
83,680 B / 2,014 lines. Windows-vs-main delta is exactly the 15 lines noted earlier
(`import shutil`, `out_root` default `BASKET_ART_ROOT/"phase2"` in `RunConfig` + `--out-root`,
`engine_sha256` in the contract report, two hermetic `shutil.rmtree(cfg.run_dir)` calls).
Windows-vs-branch: the branch is a much larger engine (21 functions the Windows file lacks, one
function `_apply_pending` the branch lacks) and it does not contain any of those 15 lines.
The Windows file is therefore **not** a superset of the branch engine (≈45 % of its function set
is missing) → not landed; main's copy untouched. The hermetic-re-run and `engine_sha256` ideas are
still absent from the branch if anyone wants to port them deliberately.

## 2. Artifacts that exist ONLY on the Windows side — do NOT copy into git

All paths are relative to the Windows project dir. Determined by diffing the full Windows
`factory/artifacts/basket/phase2` inventory (21,217 files) against the union of the same tree on
branch `basket-phase2-f1` (183,942 files) and on main (1,551 files); 20,747 files are unique,
298,642,067 B total. Full per-file list: `local://SalvageManifest.txt`.

```
factory/artifacts/basket/phase2/F4_scaleout      6,393 files  180,312,173 B  (180.3 MB)
factory/artifacts/basket/phase2/F1_primitive     8,107 files   65,971,719 B  ( 66.0 MB)
factory/artifacts/basket/phase2/F9_weighting     6,052 files   51,080,870 B  ( 51.1 MB)
factory/artifacts/basket/phase2/F11_exec           166 files      623,210 B  (  0.6 MB)
factory/artifacts/basket/phase2/F10_regime          17 files      589,984 B  (  0.6 MB)
factory/artifacts/basket/phase2/sim_core/smoke5      9 files       29,775 B
factory/artifacts/basket/phase2/F2_golden/README.md 1 file        20,376 B
factory/artifacts/basket/phase2/F5_scalein/plan.md  1 file         8,068 B
factory/artifacts/basket/phase2/F6_capital/plan.md  1 file         5,892 B
```

`F13_leaderboard`, `F3_release`, `F8_joint`, `sim_core` (except `smoke5/`) and the rest of
`F2_golden` already exist on the WSL side; `factory/artifacts/basket/sip/phase2_session_calendar.json`
(same 427,375 B) is present in the branch. `.omo/` and the other untracked non-artifact files
(F2 scripts, `build_pit_from_bundle.py`, `researches/PRE-REG-H12R.md`) are not artifacts.

### sha256 samples (3 per dir)

```
F1_primitive/plan.md                                                4be8cfc374926804af7fac94c3c598027de535396f3a980e25f3a1dc141e6166
F1_primitive/results.md                                             d9b4ae42f6b47402e9d06bceea2bbb9a36f9f7528a5dde6120612add2f1798b2
F1_primitive/runs/f1_A_open/A_open_N3_R0/parts/daily/2021-07.parquet 46c0563f6fa2c89174f31adc9e997b2f474a3d1cda31ecf6955b69d8b6ad4ce6

F4_scaleout/README.md                                               905a3686cc9b396b6abec987f07fb8457f39f43a668c5109bbc9035c56000e72
F4_scaleout/false_release.json                                      39e4ffc4b2b0ccb5eeb57d83a91a41744836e59ee8171a094d945407860df6f6
F4_scaleout/runs/N3_A_pm31_R2_10_5_EXIT/parts/daily/2025-02.parquet 904307092a17308a597da751dbcebdd2962014c900adbfdebcfcf0c1d27c2f36

F9_weighting/README.md                                              ebafcba4f56d2eaaac8616fd1b4c757a94affc46f2c4768f9e0f0168aac1d557
F9_weighting/comparison.json                                        2e9552b2e8dbbd4ab7733ef305dc81182d8b6a92ad54383d60d67a7c24ad3af0
F9_weighting/runs/A_pm_N3_R0_rank_linear/parts/daily/2021-07.parquet 619df7a8422c8dd8a0b32576fdbb0248d2428d2171120d963ccd1012fe95b3d2

F10_regime/plan.md                                                  9fe548ce615f217ee06557b0615eb3c6625b3a82f3795ba8e030e2a9f92624c6
F10_regime/runs/reused_cells.json                                   76b139d43f9cb3cc4cc10613e204977c1af603265ea07687f5e1f96091d91517
F10_regime/regime_labels.parquet                                    b58f473c4ff688787f5e384119fa3c6d452f4e947c8c070bf9559c32d206ab08

F11_exec/_pilot_progress.json                                       0c213edd20f3ec79d6dd3f2b8c718aa3edef604fb29326abcc077c712ecb4ffb
F11_exec/pilot_coverage.json                                        557f1634cd1e3bf1ed993fe570256cd8299900cf3a249f0d54cc8c9eaa7e3f07
F11_exec/smoke/runs/f1_A_pm/A_pm_N3_R0_bps0/daily.parquet            921ba0c1c9d466ab08e781a1b9f3837fe7a300dd5fc582af6c6337c76db3843e

F5_scalein/plan.md                                                  e379ab05fa8bef6c135f9670b6af8381aba1f583e1f86f1cb5f0fbf649d22efd
F6_capital/plan.md                                                  30030cd9d0ed64e61d3bd0186fda60546b2ecb02ade6d4c311322011e31018fc
F2_golden/README.md                                                 331a0906175768f2446e38d400b0aded815da9175dfd9cba46b0f18c4635da41
sim_core/smoke5/config.json                                         f5d39597f51ca4b2024ee5f20d2a621e0cd7afd4d3612d6d68d534968b1786e5
sim_core/smoke5/daily.parquet                                       0c3392fcb90d515e977193ff0d8191acbfbc77d3f13b37065a8ad3c30103cea1
```

### Archive command (run before retiring the Windows copy)

```
tar -C "/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader" -czf - \
  factory/artifacts/basket/phase2/{F1_primitive,F4_scaleout,F9_weighting,F10_regime,F11_exec,F5_scalein,F6_capital} \
  factory/artifacts/basket/phase2/sim_core/smoke5 \
  factory/artifacts/basket/phase2/F2_golden/README.md factory/scripts/{basket_sim.py,basket_f2_*.py,build_pit_from_bundle.py} \
  researches/PRE-REG-H12R.md \
  | rclone rcat storagebox:<remote>:<path>/windows-copy-phase2.tar.gz
```

Then delete the Windows project copy (keep `data/`, which is a directory there).
