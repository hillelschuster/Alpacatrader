# Disk map + offload plan: /home/hillel/alpha-data, depth_shadow, caches, vhdx

Agent: **AlphaDataDiskMap** (read-only audit). Date 2026-10-28.

## 0. Method and confidence (read this first)

This agent had **no shell**: available tools were `read` (dir listings with per-file sizes),
`glob`, `grep`, MCP `codegraph`. `ssh://storagebox` failed ("Failed to start SSH master"),
so the *remote* listing could not be compared directly. Consequences:

| Figure | How obtained | Confidence |
|---|---|---|
| Per-file sizes in tables | exact, from directory listings | high |
| Per-directory totals | **sum of listed files + sampled per-day sizes × day count** (day-partitioned dirs cannot be summed with this toolset) | ±15–25% |
| "alpha-data ≈ 52 GB" | given fact, not re-measured | stated |
| .cache 2.8 GB / .local 3.4 GB / algo projects ~40 GB | given facts | stated |
| vhdx size | exact (`read` of /mnt/c/WSL/Ubuntu) | high |

**One command the orchestrator must run before deleting anything** (true numbers, apparent size):

```bash
du -s --apparent-size -B1 --inodes ~/alpha-data/open-search-v1/* \
  | sort -k1,1nr | awk '{printf "%.2f GB %s\n",$1/1e9,$2}'
du -s --apparent-size -B1 ~/.cache ~/.local ~/.omp ~/canary_runs ~/dp-out ~/discpool-backups
find ~/alpha-data -type f | wc -l
```

Everything below is safe as a *plan*; the exact GBs should be re-read from that `du` before
`--delete-local` is trusted (the archiver itself re-verifies by sha256 anyway).

---

## 1. Total picture

| Item | Size | Where | In vhdx? |
|---|---|---|---|
| `~/alpha-data` (only root: `open-search-v1`) | ~52 GB (given) | WSL ext4 | **yes** |
| `~/algo projects` (repo + 1 worktree + .venv + .git) | ~40 GB (given) | WSL ext4 | **yes** |
| `~/depth_shadow` (git worktree, docs+src) | ~0.3 GB est | WSL ext4 | yes |
| `~/.cache` | 2.8 GB (given) | ext4 | yes |
| `~/.local` | 3.4 GB (given) | ext4 | yes |
| `~/.omp` (agent state — **do not delete**) | ~0.5–1 GB est | ext4 | yes |
| Windows `Desktop/algo projects/Alpacatrader/data` (symlink target of repo `data/`) | **~25–30 GB** (measured: 36 × `clean_ohlcv_*.parquet` ≈ 430 MB = ~15.5 GB, 26 × `ohlcv_*.parquet` ≈ 350 MB = ~9 GB, plus `sip/`, `leaderboard/`, `scratch_pattern/`, `micro_payoff`-like day dirs) | **C: directly** | no |
| **WSL vhdx** | **139.9 GB** | `C:\WSL\Ubuntu\ext4.vhdx` | — |

Deleting Windows-side `data/` frees C: **immediately** (no compaction needed).
Deleting inside WSL frees **nothing** until the vhdx is compacted (see §6).

---

## 2. `~/alpha-data/open-search-v1` — per-directory inventory

Classes: **RAW** = expensive re-acquisition (keep on box, cold) · **DERIVED** = small lane
output (keep, archive per lane) · **MIN** = part of the minimum working set · **SCRATCH** =
delete without archive · **ONBOX** = already archived on the box.

### 2.1 The bulk

| dir | size (est) | files (est) | class | action |
|---|---|---|---|---|
| `quote_universe_acquire/raw/` | **~32 GB** (14,993 ticker-day dirs, **46,121** `page_*.json`; sampled 2026-05-29 = 32.4 MB/day × 1003 days) | 46,121 | **RAW** (Alpaca quote API, 4 shard runs, ~2h+ API time; 366 M new rows) | archive (month-batched, compresses ~7:1) → `--delete-local` |
| `quote_universe_acquire/quotes/` | **~7.5–9 GB** (1,003 parquet, 974,374,748 rows, sampled 4–16 MB/day) | 1,003 | **RAW/analysis-ready** — every lane reads this | archive; restore on demand (see §3 note) |
| `quote_universe_acquire/parts/` | ~0.5 GB (46,121 parquet, 5–57 KB) | 46,121 | DERIVED (re-derivable from raw/) | archive with raw/ |
| `quote_universe_acquire/manifest/` | 7 MB (1,003 × ~7 KB) | 1,003 | DERIVED (provenance) | archive |
| `quote_universe_acquire/acquire_summary.json`, `plan_summary.json`, `producer_snapshot.py` | 36 KB | 3 | MIN (provenance) | keep on box + new machine |
| **subtotal** | **~40–42 GB** | **~94,250** | | |

Day-partitioned, all small (each verified by sampling; each is `YYYY-MM-DD.{json,parquet}` × ~500–1,100 days):

| dir | size (est) | files (est) | class | action |
|---|---|---|---|---|
| `days/` (panel) | ~0.36 GB (2,132 entries, ~170 KB parquet/day) | 2,132 | **MIN** (panel days, every lane's input) | keep |
| `micro_payoff/corpus/` + `late/` | ~0.3 GB (~812 days × 2, avg 350 KB) | ~1,624 | MIN (model corpus; models are tiny) | keep |
| `liquidity_recovery_five_min/days/` + 135 trades parquet + `summary.json` 6 MB | ~0.1 GB | ~1,270 | DERIVED | archive |
| `delayed_sip_iex/states/` + `replay/` + `results/` | ~30 MB (1,163 entries × ~14 KB) | ~1,180 | DERIVED, **ONBOX** (archived 2026-10-27) | delete local after box MANIFEST check |
| `depth_universe/derived/` (2,331) + `plan/` (582) + `STATUS/results` | ~70 MB (derived files ~17–50 KB; many are 65 B `*.offloaded` markers) | ~2,925 | DERIVED, raw part **ONBOX** (`archive/alpha-data/.../depth_universe/raw/`); `_staging/` is **empty** | keep derived (tiny), delete empty `_staging` |
| `entry_microstructure/days/{validation,confirmation}` | ~10 MB (250 × 3.7–46 KB) + `results.json` 7.6 MB | ~510 | DERIVED | archive |
| `provider_fee_ledger/{days,trades,cohorts}` | ~20 MB (5 cohort dirs × 8 jsonl × 300 KB) | ~60 | DERIVED | archive |
| `sparse_execution_frontier/{late143,val61,fetched_quotes}` | ~3 MB | ~40 | DERIVED | archive |
| `learned/` (models + strategy_*/replay) | **1.4 MB models** (`payoff_h15/h60/h390.joblib` 313–318 KB each + `feature_order.json`) + ~1 MB | ~25 | **MIN — highest-value bytes in the tree** | keep, also copy to box root `alpha-data/.../learned/models/` |
| `quote_hourly_payoff/models`, `micro_payoff/models`, `sequence/models`, `selector_upgrade/{current,compact,monotone_compact}.joblib` | ~1.5 MB | ~15 | MIN | keep |
| `adaptive_depth_sizing/{results.json,states,replay,selection_freeze}` | ~20 MB | ~200 | **MIN** (sizing curves; bot input) | keep |
| `retained_*` (h390_lane, delay_threshold, target_exits, entry_delay, score_sizing, exit_grid, book_shape, timing) | 15 dirs, each 0.3–7 MB (largest `retained_exit_grid/results.json` 6.9 MB) | ~15–60 each | DERIVED | archive |
| `quote_*` lanes (aware_frequency, aware_frequency_v2, completeness_audit, entry_delay, staleness_probe, cross-name…), `multi_session_carry`, `opening_range_pullback`, `passive_quote_confirmation`, `basket_reborn`, `bid_backed_burst`, `micro_flow`, `micro_reclaim`, `overnight`, `events`, `proven_push`, `short_diagnostic`, `signal_supply`, `depth_plateau`, `shallow_depth_lane`, `sparse_lead_quote_audit`, `learned_sparse_*`, `sparse_exit_management`, `alpha_retained_mechanism`, `cross_name_rotation`, `candidates/`, `qual/` | each 0.5–30 MB (`results.json` 0.2–7 MB + parquet/KB-scale); **sum ≈ 0.6–1 GB** | ~2–2,500 each (`liquidity_recovery_five_min` 135 parquet) | DERIVED | archive per lane in one batch |
| `_smoke_quote_delay/`, `_smoke_quote_delay3/`, `_smoke_quote_aware_v2/` | 3 × 1–2 MB | ~30 | **SCRATCH** | **delete, no archive** |
| `contract.json` | 1.3 KB | 1 | MIN | keep |

### 2.2 Sum check

Measured/sampled classes ≈ 41 GB vs stated 52 GB total ⇒ the `raw/` and/or `quotes/`
figures carry the error (raw page JSON varies 4 KB–1.3 MB per ticker-day). Treat
**`quote_universe_acquire` = 78–95 % of all of alpha-data**; everything else is ≈ 2–3 GB.
That conclusion is robust to the ±25 % sampling error.

File count: **≈ 110,000 files**, of which ~94,250 are in `quote_universe_acquire`
(46 k raw JSON + 46 k part parquet). Upload must therefore be tar-streamed in monthly
batches — per-file `rclone` would take ~20× longer (AGENTS.md).

---

## 3. Minimum working set for the new machine

| Component | GB | Why |
|---|---|---|
| repo `git clone origin` (+ `.git`) | ~0.5 est | all code, docs, `factory/scripts`, tests |
| `alpha-data/open-search-v1/learned/models/*` (h15/h60/h390 + feature_order) | 0.0013 | immutable 2021-22 LightGBM — the "same model" every lane reuses |
| `open-search-v1/days/` (panel days) | 0.4 | every producer's input |
| `micro_payoff/corpus` + `models`, `quote_hourly_payoff/models`, `sequence/models`, `selector_upgrade/*.joblib`, `adaptive_depth_sizing/{results,states}`, `entry_microstructure/days`, `depth_universe/{derived,plan}`, all `results.json` + `selection_freeze.json` + `contract.json` | ~2.0 | DERIVED state needed to resume (no re-run) |
| **alpha-data minimum (no bulk raw)** | **≈ 2.5 GB** | |
| *optional* `quote_universe_acquire/quotes/` (merged 974 M quote rows) | ~8 | avoids a ~2–7 h re-harvest + API spend; pull on demand |
| *optional* Windows `data/` `clean_ohlcv_*.parquet` + `ohlcv_*.parquet` | ~25 | PIT OHLCV panel — needed for any bar/backtest work |
| **Resume research, comfortable** | **≈ 12 GB** (repo + alpha minimum + quotes) | |
| **Full analysis incl. OHLCV panel** | **≈ 36 GB** | |

The bot (`depth_shadow`) needs **no data at all** — only the worktree + `.env` (secrets,
re-issued on the new machine, never archived).

---

## 4. `~/.cache` (2.8 GB) and `~/.local` (3.4 GB)

| path | size | class | action |
|---|---|---|---|
| `~/.cache/uv` (`archive-v0`, `wheels-v6`, `sdists-v9`, `interpreter-v4`) | ~2.0 GB est (dominant) | cache | `uv cache clean` — safe, rebuilds on demand |
| `~/.cache/camoufox` (+ `~/camoufox` = empty) | ~0.4 GB est (browser build 156.0.1) | cache | delete |
| `~/.cache/roundel-capture-profile` (Firefox profile: places.sqlite 5 MB, favicons 5 MB, 54 files) | ~0.15 GB est | scratch | delete |
| `~/.cache/mesa_shader_cache`, `~/.cache/fontconfig` | ~15 MB | cache | delete |
| `~/.cache/storagebox_archive` | markers only | **state** | keep until every archive is verified+deleted |
| `~/.local/bin` (`omp` 243 MB, `rclone` 81 MB, `py-spy`, `ffmpeg`…) | ~0.4 GB | needed | keep (re-install on new machine from package sources) |
| `~/.local/share/uv/python` + `tools` | ~1.5–2 GB est | **needed** — uv interpreters/tools | keep |
| `~/.local/share/polymarket-anomaly/anomaly.db` | **633 MB** | other project (not Alpacatrader) | owner decision; can archive separately |
| `~/.local/share/{opencode,rtk,pipx,jupyter}` | ~0.3 GB | mixed | keep opencode/rtk state; delete `pipx/venvs`, `jupyter/` only if unused |
| `~/.omp` (agent.db, history.db, models.db, blobs, puppeteer/chrome, browser-profiles, logs) | ~0.5–1 GB est | **agent state — never propose deleting** | keep |
| `~/canary_runs` | ~0.1 GB (largest `inspect_chunks/chunk_2025.json` 37 MB) | scratch run logs | archive-optional / delete |
| `~/dp-out`, `~/.depth_sweep` (logs only), `~/discpool-backups`, `~/libs/debs`, `~/references`, `~/oc-*` | each < 0.3 GB est | unrelated/scratch | owner decision, out of scope |
| `~/polymarket-bot-vps.sqlite` (632 MB), `polymarket-bot-dev.db.legacy-stale` (40 MB) | 0.67 GB | other project | out of scope |

---

## 5. `~/depth_shadow` and other >500 MB dirs under /home/hillel

| dir | size | class | action |
|---|---|---|---|
| `~/depth_shadow` (git worktree: `src/`, 32 tests, `researches/` docs; `.venv`/`.env`/`data` are symlinks/0 B) | ~0.3 GB est | **git worktree**, content lives in origin (`main`) + `researches/` docs | `git -C ~/depth_shadow status` → commit+push any dirty files → `git worktree remove` (no archive needed; do **not** tar — it contains `.env`) |
| `~/algo projects/Alpacatrader` (`data` → Windows; `.venv`, `.git`, `factory/artifacts`, `logs/`) | ~40 GB given | source of truth in origin | `.env` stays; nothing to archive except possible `logs/` |
| `~/algo projects/worktrees/Alpacatrader/basket-phase2-f1` | part of the 40 GB | git worktree | remove (commit first) |
| `~/algo projects/data/Alpacatrader` | part of the 40 GB | unknown; verify with `du` | verify |
| `/home/hillel/projects/money-making-trading-bot` | unknown | other repo | out of scope |

---

## 6. WSL virtual disk

- **Path on C:: `C:\WSL\Ubuntu\ext4.vhdx`, apparent size 139.9 GB** (`/mnt/c/WSL/Ubuntu/ext4.vhdx`).
  (Distro is an imported one under `C:\WSL\Ubuntu`, not a Store package — `AppData/Local/Packages/Canonical*` has no Ubuntu and `AppData/Local/wsl/` is empty.)
- VHDX is dynamic: blocks freed by `rm` inside WSL are **not** returned to C:.
- Shrink recipe (Windows, admin PowerShell/CMD, after all WSL work stops):
  ```
  wsl --shutdown
  diskpart
    select vdisk file="C:\WSL\Ubuntu\ext4.vhdx"
    attach vdisk readonly
    compact vdisk
    detach vdisk
    exit
  ```
  (or `Optimize-VHD -Path C:\WSL\Ubuntu\ext4.vhdx -Mode Full` if the Hyper-V module exists)
- **Expected recovery**: deleting ~42 GB (alpha-data bulk) + ~0.5 GB (scratch/caches) inside WSL
  should return roughly **40–55 GB** to C: (vhdx 139.9 GB → ~85–100 GB). Windows-side `data/`
  deletion (~25 GB) frees C: with no compaction at all. Combined ≈ **65–75 GB free** on a
  machine that currently has ~19 GB.

---

## 7. Concrete execution order (orchestrator)

Prereqs: `rclone about storagebox:` sanity + `rclone cat storagebox:alpacatrader/README.md`.

1. **Scratch first (instant, no upload).**
   ```bash
   rm -rf ~/alpha-data/open-search-v1/_smoke_quote_delay ~/alpha-data/open-search-v1/_smoke_quote_delay3 \
          ~/alpha-data/open-search-v1/_smoke_quote_aware_v2 \
          ~/alpha-data/open-search-v1/depth_universe/_staging \
          ~/canary_runs ~/.depth_sweep
   uv cache clean; rm -rf ~/.cache/camoufox ~/camoufox ~/.cache/mesa_shader_cache \
          ~/.cache/fontconfig ~/.cache/roundel-capture-profile
   ```
2. **delayed_sip_iex** — already on the box (2026-10-27 MANIFEST). Verify batch list, then
   `rm -rf ~/alpha-data/open-search-v1/delayed_sip_iex` (~30 MB).
3. **The big one** (does 99 % of the work):
   ```bash
   factory/scripts/archive_to_storagebox.sh \
     ~/alpha-data/open-search-v1/quote_universe_acquire \
     "RAW quote harvest: 1003 panel days, 14993 ticker-days, 974M merged quote rows (4 shard runs)" \
     --delete-local
   ```
   Month batching applies automatically (`raw/`, `parts/`, `quotes/`, `manifest/` all start
   with `YYYY-MM`). ~42 GB → ~6 GB on the box, 3 streams, **est. 20–30 min** at the measured
   25 GB→3.5 GB / 8 min rate. Re-runnable; deletes only after all batches sha256-verify.
4. **Rest of alpha-data** — `for d in ~/alpha-data/open-search-v1/*/; do …` skipping `days/`,
   `learned/`, `adaptive_depth_sizing/`, `micro_payoff/` (or include them too, they are small,
   but they are the minimum set; simplest is to archive everything and restore the minimum
   from the box). ~2.5 GB → ~0.4 GB, one command per lane, `--delete-local`.
5. **depth_shadow**: `git status` → commit+push → `git worktree remove ~/depth_shadow`.
   Never archive a directory containing `.env`.
6. **Windows side** (other agent): delete/archive `Desktop/algo projects/Alpacatrader/data/*.parquet`
   (clean_ohlcv + ohlcv + backfill) once on the box → frees ~25 GB on C: directly.
7. **Compact the vhdx** (§6).
8. Keep: `~/.omp`, `~/.local/bin`, `~/.local/share/uv`, repo `.env`, `~/.cache/storagebox_archive`
   (only until step 3–4 verified; delete afterwards).

---

## 8. Risks / blockers

1. **`.env` leakage** — `archive_to_storagebox.sh` tars *everything* under the given dir. Never
   point it at the repo, a worktree, or `$HOME`; only at `alpha-data/*`. Storage-box + Alpaca
   creds must not land on the box in plaintext.
2. **Sampling error** (§0) — the per-dir GBs are ±15–25 %. Run the `du` block first; the plan
   (what is raw / derived / scratch) does not change with the numbers.
3. **`quotes/` is the one judgement call** — it is *both* "raw" and "the input every lane reads".
   Recommendation: archive it (box), and restore it on the new machine if lanes are re-run;
   it is only ~8 GB to pull back.
4. **Remote state unverified** — `ssh storagebox` from this agent failed, so "already on the
   box" is taken from AGENTS.md/HANDOFF.md facts, not from a live listing. Re-check
   `rclone lsf storagebox:alpacatrader -R --dirs-only --max-depth 5` before step 2.
5. **Worktree dirty state** — `depth_shadow/researches/history/*.md` (17 h old) may be
   uncommitted; removing the worktree would drop them. Commit+push first, or copy those 7 files
   into the main checkout.
6. **Compaction requires Windows admin + `wsl --shutdown`** — it kills any running omp session
   (including this one) and the `depth-shadow-restart` service. Do it last.
