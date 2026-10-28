# Alpacatrader — off-host migration map & plan (read-only investigation)

Date: 2026-10-28. Scope: the Alpacatrader project (WSL + Windows copies), the git worktrees,
`/mnt/c/Users/הלל/Desktop/algo projects/`, `/home/hillel/algo projects/`, plus the research root
`/home/hillel/alpha-data`.

## 0. Method & measurement caveat (read first)

This agent ran with **no shell** (tools: read/find/grep/glob + codegraph only), so `du`/`find | wc`
were impossible. Sizes below are therefore:
- **[M]** measured = summed from directory listings (the listing shows every file's byte size),
- **[E]** estimate = listing-derived extrapolation (identical shards × count) or owner-documented,
- **[?]** unmeasured — must be measured by the executor before any delete.

Before executing, run (read-only, no writes). Stagger these: two sibling scout agents are walking
the same trees right now (§7).
```bash
du -x -s --apparent-size /home/hillel/alpha-data/open-search-v1/*  | sort -h
du -x -s --apparent-size "/home/hillel/algo projects/data/Alpacatrader/sip/net"/*
du -x -s --apparent-size "/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader/data"/*
for d in /home/hillel/.cache /home/hillel/.local /home/hillel/canary_runs /home/hillel/dp-out \
         /home/hillel/discpool-backups /home/hillel/camoufox /home/hillel/depth_shadow \
         "/home/hillel/algo projects/worktrees/Alpacatrader/basket-phase2-f1"; do
  du -x -s --apparent-size "$d"; done
df -h /mnt/c; df -h /
```

## 1. Topology (who is the real file, who is a link)

| Path | Kind | Points at |
|---|---|---|
| `/home/hillel/projects/Alpacatrader` | symlink | `/home/hillel/algo projects/Alpacatrader` |
| `/home/hillel/algo projects/Alpacatrader` | **real git repo** (main worktree, `.git/`) | — |
| `…/Alpacatrader/data` (listed `data 0B`) | symlink | `/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader/data` (**real bytes on C:**) |
| `/mnt/c/…/Alpacatrader/data/sip/net` (`net 0B`) | symlink (Windows-side, effectively dangling) | the WSL SIP tree |
| `/home/hillel/algo projects/data/Alpacatrader/sip/net` | **real dir, in ext4.vhdx** | — (SIP bars/quotes/trades corpus) |
| `/home/hillel/sip` | symlink | the SIP tree above |
| `/home/hillel/depth_shadow` | **real linked worktree** | `.git` file → `…/Alpacatrader/.git/worktrees/depth_shadow` |
| `/home/hillel/depth_shadow/data` (`data 0B`) | symlink | **same** C: data dir as the repo's `data/` |
| `/home/hillel/algo projects/worktrees/Alpacatrader/basket-phase2-f1` | **real linked worktree** | `.git/worktrees/basket-phase2-f1` |

Consequences:
1. **There is exactly one copy of the market data**, reached through 3–4 path aliases. `rm` on a
   symlink is harmless, but a recursive delete / `rsync --delete` from the wrong root removes real bytes.
2. Deleting inside `/home/hillel/**` frees space **only inside ext4.vhdx**, i.e. **not on C:** until
   the vhdx is compacted from Windows. Deleting under `/mnt/c/**` frees C: immediately.
3. `depth_shadow` and the repo share one `data/`; the depth bot's state therefore lives on C:.

## 2. Git state (the class-A gate)

`git worktree list` equivalent, reconstructed from `.git/worktrees/` + refs files:

| Worktree/path | branch | local sha | origin sha | pushed? | notes |
|---|---|---|---|---|---|
| `/home/hillel/algo projects/Alpacatrader` | `main` | `650d6c19ed375f6b…` | `650d6c19ed375f6b…` | **YES, identical** | main worktree |
| `…/worktrees/Alpacatrader/basket-phase2-f1` | `basket-phase2-f1` | `203ddafbd545e535…` | `203ddafbd545e535…` | **YES, identical** | index last written 3w ago |
| `/home/hillel/depth_shadow` | **detached HEAD** `a785b8d4…` | — | — | **UNKNOWN** | not a branch tip; must be proven reachable |

Uncommitted work **cannot** be detected without running git. The executor MUST run, before deleting:
```bash
for d in "/home/hillel/algo projects/Alpacatrader" \
         "/home/hillel/algo projects/worktrees/Alpacatrader/basket-phase2-f1" \
         /home/hillel/depth_shadow; do
  echo "== $d"; git -C "$d" status --porcelain=v1 | head -50
  git -C "$d" log --oneline @{u}..HEAD 2>&1 | head       # unpushed
done
git -C /home/hillel/depth_shadow branch -r --contains a785b8d4   # is the detached commit pushed?
```
Gate: a worktree is A-deletable only if `status` is clean **and** the detached commit is contained
in a remote ref (or is cherry-picked onto a branch and pushed first).
Gitignored-but-present in every tree (never recoverable from GitHub): `.env` (1.3 KB — Alpaca +
Storage Box creds), `logs/`, `data/`, `.codegraph/`. Preserve `.env` offline (password manager);
never upload it.

## 3. Class A — tracked in git (recoverable from origin)

| Item | Size | Files | Class | Action |
|---|---|---|---|---|
| `/home/hillel/algo projects/Alpacatrader` working tree (code, docs, committed `factory/artifacts/*.json`, ledgers) | ~0.3–0.6 GB [E]; largest singles: SPEC.md 234 KB, HANDOFF 54 KB, factory/STATE 212 KB, uv.lock 294 KB | ~1.5–2 k | A | push is the backup; the new machine clones. Delete locally only after that clone exists |
| repo `.git/` (objects for main, basket branch, depth_shadow) | [?] est 100–400 MB | — | A | delete with the tree after `git fsck` / push check |
| `…/worktrees/Alpacatrader/basket-phase2-f1` | ~0.15 GB [E] (source + `.ruff_cache`) | ~600 | A | branch pushed → `git worktree remove` + `git worktree prune` |
| `/home/hillel/depth_shadow` (source only; `data` is a symlink; `.venv` is a symlink 0B) | ~0.12 GB [E] (tests are 300 KB+; `src/`, `researches/`, `docs/`) | ~650 | A **iff** `a785b8d4` is on a remote and tree clean | `git worktree remove`; otherwise push the detached commit first |
| Windows stale repo copy `/mnt/c/.../algo projects/Alpacatrader` (source only; **no `.git`** listed) | ~0.15 GB [E] | ~1.2 k | **A/D** | ~1 month behind upstream (its `factory/STATE.md` 168.5 KB vs 211.8 KB, `HYPOTHESES.md` 62.2 KB vs 81.7 KB). Diff `src/ tests/ factory/scripts/` against main; if 0 unique files → delete. **Before that** merge its `.env` (1.1 KB) into the WSL `.env`, and decide on `.opencode/`, `.omo/`, `.codegraph/`, 11 `logs/` files |
| `logs/` (WSL repo: 43 files; Windows: 11 files) | ~0.01 GB [M] | ~54 | A→B | gitignored despite the name; tar once to `storagebox:alpacatrader/archive/Alpacatrader/logs/`, then delete |

## 4. Class B — gitignored but valuable → must reach the box

### 4a. On **C:** (deleting here frees C: immediately)

Root: `/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader/data` ≈ **32 GB [E]**, ~35–40 k files.

| Second-level item | Size | Files | Contents / why it matters |
|---|---|---|---|
| `clean_ohlcv_YYYY-MM.parquet` ×36 (2021-01 … 2026-02) | **~15.7 GB [M]**, 350–560 MB each | 36 | back-adjusted monthly OHLCV |
| `ohlcv_YYYY-MM.parquet` ×~35 (2021-01 … 2026-03) | **~12.6 GB [M/E]**, 275–470 MB each | ~35 | raw (unadjusted) monthly OHLCV |
| `backfill/` | ~1.6 GB [E] | ~40 | `ohlcv_2026-05` 583 MB, `premarket_ohlcv_*` 12×22–37 MB, `premarket_parts/` |
| `harvest01/` (minute, mgmt, friction, window, basket, policies, lifecycle, sim, base, report, logs) | ~1.3 GB [E] (minute ≈1068×0.95 MB; report ≈100 MB) | ~12 k | H-harvest corpus, 1068 days × 5 grids |
| `ml_features/` | ~0.6 GB [M] (20 files ≈27 MB) | 21 | monthly ML feature panels |
| `subminute/` | ~0.35 GB [E] (1029 files, 0.25–0.6 MB) | 1029 | per-event exit quotes |
| `scratch_pattern/`, `scratch_pattern_events/`, `scratch_census/`, `leaderboard/`, `iex_tape/` | ~0.6 GB [E] | ~8.3 k | research intermediates (archive; candidate for class-C delete *after* upload) |
| `sip/` (pm_snapshots, funnel_stage, race_raw, t5_raw, candidates, audit_stage, t9b_raw, universe, quotes, trades, derived; `net` is a symlink) | ~0.7 GB [E] | ~9 k | SIP funnel intermediates, 10-day quote/trade pulls |
| `h025_research/` (core 347 day dirs, qualification, execution, entry_smoke, exit, forward) | ~0.3 GB [E] | ~2 k | H025 program |
| `atlas/`, `entry_ev/`, `forward/` | ~0.06 GB (forward day files 1.5–4 MB ×14) | ~120 | `forward/` = live bot evidence |
| `decisions.jsonl` + `.1…​.5` + `decisions_rebuild.jsonl` | ~0.06 GB [M] | 7 | bot decision journal |
| `journal/`, `KILL`, `positions*.json`, `pnl_ledger.json`, `universe_tags.parquet`, `split_flags.parquet` | <1 MB [M] | 9 | live-bot control/state |

Subtotal ≈ **32 GB**, ~35–40 k files. On the box with the standard recipe (`tar | gzip -1 | rclone
rcat`) expect **~10–14 GB**, ~1–1.5 h (measured reference: 25 GB → 3.5 GB in 8 min, 3 streams).

Same root, non-data: `logs/` (11 files) → class A→B; `.codegraph/` 10.5 MB, `.pytest_cache/`,
`.opencode/`, `.omo/`, `opencode.json`, `AGENTS.md` → class C / A.

### 4b. Inside the WSL vhdx

| Item | Size | Files | Contents |
|---|---|---|---|
| `…/algo projects/data/Alpacatrader/sip/net/quotes` | **~4.8 GB [E]** (1068 × 0.8–16 MB, avg ≈4.5 MB + 1068 manifests) | 2 136 | SIP quotes, per day |
| `…/sip/net/trades` | **~16 GB [E]** (1068 × 7–56 MB, avg ≈15 MB + manifests) | 2 136 | SIP trades, per day |
| `…/sip/net/bars` | ~0.24 GB [M/E] (1068 × 120–370 KB) | 1 068 | SIP minute bars |
| `…/sip/net/manifest.jsonl`, `manifest_index.jsonl` | 2 MB [M] | 2 | acquisition manifest |
| **`sip/net` subtotal** | **≈21 GB [E]** | ~5.3 k | this is the bulk of the "~40 GB `algo projects`" (rest = `.git`, worktrees) |
| `/home/hillel/alpha-data/open-search-v1` (40+ lanes) | **52 GB [owner-documented]**, [?] files | [?] | live research roots. Lanes I could measure are small (delayed_sip_iex states 2136×13 KB ≈30 MB; depth_universe derived 2316×18 KB ≈42 MB, plan 584×40 KB ≈23 MB; entry_microstructure days ~560×4–26 KB). The mass is in the acquisition lanes: `quote_universe_acquire/{raw,quotes}` (13 553 listing lines of day/ticker dirs), `sparse_execution_frontier/fetched_quotes`, `provider_fee_ledger/{trades,days}`, `multi_session_carry/{qual,replay}`, `micro_payoff/corpus`, `liquidity_recovery_five_min*/days`, `quote_completeness_audit/raw`, `quote_staleness_probe/raw` — **needs §0 du** |

### 4c. Already on the box — verify, do not re-upload
- `storagebox:alpacatrader/archive/alpha-data/open-search-v1/delayed_sip_iex/` (done). Local
  `alpha-data/…/delayed_sip_iex/` residue is only ~35 MB (`states/` 2136×13 KB, `replay/`,
  `results.json` 1.1 MB, `selection_freeze.json` 392 KB) → no second bulk upload; tar the residue to
  the same prefix or keep it, it is negligible.
- `storagebox:alpacatrader/alpha-data/open-search-v1/depth_universe/raw/` (done). Local raw days
  carry `*.offloaded` markers. Nothing to do.
- `storagebox:backup/` is off-limits.

## 5. Class C — regenerable junk (delete, no upload)

| Item | Size | Note |
|---|---|---|
| repo `.codegraph/` (`codegraph.db` 47.8 MB + `-wal` 47.1 MB + log) | **0.095 GB [M]** | rebuilds with `codegraph init` |
| repo `__pycache__/`, `src|config|tests/__pycache__`, `.pytest_cache/`, `.ruff_cache/` | ~0.02 GB [E] | |
| Windows copy `.pytest_cache/`, `src/__pycache__/`, `.codegraph/` 10.5 MB | ~0.02 GB [M/E] | |
| `/home/hillel/canary_runs/` (20+ run dirs: full, runD, race, sequence, geometry, inspect*) | [?] est 0.5–3 GB | full-corpus-audit scratch; regenerable — confirm nothing cited in `capture-origin-final.md` depends on a file only here |
| `/home/hillel/dp-out/` | [?] | throwaway output |
| Windows `data/_scratch/` | ~0.02 GB [E] (270 files) | probe dumps |
| `/home/hillel/.cache` | **2.8 GB** [owner] | uv/pip/opencode/codegraph/browser caches |
| `/home/hillel/.bun/install`, `~/.npm`, `~/.config/{chromium,google-chrome-for-testing}`, `~/.omp/{browser-profiles,cache,puppeteer}`, `~/.camoufox`, `~/camoufox` | [?] est 1–3 GB | `.omp/` also holds live session/agent state — delete only its cache subdirs |
| `~/.local/{bin,share}` | **3.4 GB** [owner] | **do NOT delete now** (installed tooling: uv tools, opencode). Revisit after the last local run |
| `.venv` dirs | [?] | `depth_shadow/.venv` is a symlink (0B); Windows bots have real ones |

## 6. Class D — unknown / needs the owner

| Item | Size | Why D / recommendation |
|---|---|---|
| `alpha-data/open-search-v1/*` live-vs-retired split | within the 52 GB | Owner call. Proposal: lanes with final `results.json` + frozen `contract.json` → `archive/…` (+ keep the small json/contract locally); still-written lanes (`depth_universe`, `depth_plateau`, `_staging`, `selector_upgrade`, `entry_microstructure`, `signal_supply`, `basket_reborn`) stay until closed |
| `/home/hillel/discpool-backups/{other-components-original-git-dir, other-components-2026-09-30.git}` | [?] | old git dirs. If the repo still exists elsewhere → delete; if it is the only copy → `archive/` |
| `/home/hillel/polymarket-bot-vps.sqlite` 632 MB [M] + `polymarket-bot-dev.db.legacy-stale` 40.5 MB [M] | 0.67 GB | not Alpacatrader; archive to `archive/polymarket/` or delete with owner OK |
| `/mnt/c/.../algo projects/{Polymarket bots, kalshi bots, alphabot, alphabot2, Hyperliquid-index, C++ bot, Vps, opencode, .obsidian, Polymarket_Israel…docx}` | [?] est 1–5 GB | separate projects inside the requested target. Several have no `.git` (`C++ bot`, `alphabot*`) and carry their own `.env`/`state/` → one tar each to `archive/other/<name>/`, or leave on C: |
| `Vps/` (`algo-vps pem key.pem`, `vps_phone_ssh_access.txt`), `~/.ssh/{bots-vps.pem, algo-vps-agent, storagebox_ed25519, id_ed25519}`, every `.env` | <20 KB | **SECRETS** — password manager / age-encrypted tarball only |

## 7. In use right now (do not pull the rug)

- `depth-shadow-restart-2026-10-27` service: **exited** (was up 10 h 11 m).
  `data/forward/depth_bot/ALERT` = `previous_session_incomplete`, 2026-10-27, mode sim; `journal/`
  last write ≈2 h ago (from a test run, not the service).
- `data/forward/bot/LIVE` exists and `data/KILL` exists → bot armed, not running.
- Two sibling omp agents are running now (`TransferPlanDesign`, `MapAlphaDataAndHome`) reading the
  same trees — stagger full-tree scans.
- The Windows `data/` is the bot's live path (via two symlinks). Archive closed months only, never
  `rm -rf data`; keep `forward/`, `KILL`, `positions*.json`, `pnl_ledger.json` until the new machine
  takes over.

## 8. Duplicates / double counting

1. `data/` reachable as `projects/Alpacatrader/data`, `depth_shadow/data` and the real `/mnt/c/…` =
   **one** copy.
2. `/home/hillel/sip` and `data/Alpacatrader/sip/net` = one copy.
3. Windows `Alpacatrader/` (source) vs the WSL repo = **stale duplicate**, ~1 month behind, no `.git`.
4. `clean_ohlcv_*` vs `ohlcv_*` = adjusted vs raw, archive **both**.
5. `alpha-data/.../depth_universe/derived` vs `data/harvest01/*` = different studies, no byte-level dup.
6. Box already holds `delayed_sip_iex` and `depth_universe/raw` — do not re-upload.

## 9. Recommended plan (ordered, each step verifiable)

**Phase 0 — gate (no writes).** §0 `du`s; §2 `git status`, `@{u}..HEAD`, `branch -r --contains`.

**Phase 1 — unblock C: first (immediate win).** Per closed-month batch, via the existing tool:
```bash
cd "/home/hillel/algo projects/Alpacatrader"
factory/scripts/archive_to_storagebox.sh \
  "/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader/data" \
  "retired OHLCV + harvest corpus after 2026-10-28 host migration" \
  --delete-local     # deletes only after sha256 verify + MANIFEST + README row
```
Target `storagebox:alpacatrader/archive/Alpacatrader/data/…` (mirror the local path, per the box
README). ~32 GB → ~12 GB, ~1.5 h. **C: 19 GB → ~51 GB**, no vhdx work needed.

**Phase 2 — WSL SIP corpus.** After Phase 1 bandwidth frees up:
```bash
factory/scripts/archive_to_storagebox.sh \
  "/home/hillel/algo projects/data/Alpacatrader/sip/net" \
  "SIP bars/quotes/trades 2021-02…2026-05, retired" --delete-local
```
→ `archive/Alpacatrader/sip/net/`, ~21 GB → ~6–9 GB. Frees 21 GB **in the vhdx only**.

**Phase 3 — `alpha-data` per lane.** One invocation per lane (re-runnable/resumable) for retired
lanes; keep the small `results.json` / `contract.json` / `producer_snapshot.py` for provenance.
Live lanes stay.

**Phase 4 — class C sweep.** `.codegraph`, `__pycache__`, `.pytest_cache`, `.ruff_cache`,
`~/.cache`, `canary_runs`, `dp-out`, `_scratch`, browser caches. Keep `~/.local`.

**Phase 5 — reclaim inside the vhdx, from Windows (admin):**
```powershell
wsl --shutdown
# diskpart → select vdisk file="C:\...\ext4.vhdx" → attach vdisk readonly → compact vdisk → detach
# or: Optimize-VHD -Path <ext4.vhdx> -Mode Full
```
Only this converts Phases 2–4 into C: free space. **Then** remove the stale trees
(`git worktree remove` ×2, Windows project copy, `discpool-backups` per owner).

**Phase 6 — hand-off.** New machine: clone main + `basket-phase2-f1`, `rclone copy` the lanes it
needs from `storagebox:alpacatrader/`, restore `.env` from the password manager. Then delete the
repo/`.git` here.

## 10. Space accounting

| Bucket | Frees on C: | Frees inside vhdx (needs compact) |
|---|---|---|
| B — C: `data/` (Phase 1) | **+32 GB [E]** | 0 |
| B — `sip/net` (Phase 2) | 0 | +21 GB [E] |
| B — `alpha-data` retired lanes (Phase 3) | 0 | up to +52 GB (owner split) |
| C — caches / scratch (Phase 4) | ~+0.1 GB (Win side) | +3.5–9 GB [E] |
| C — `~/.local` (only if regenerated elsewhere) | 0 | +3.4 GB [owner] |
| A — repo + 2 worktrees + `.git` | +0.15 GB (Win copy) | +0.6–1.2 GB [E] |
| D — Polymarket/kalshi/other | up to ~+5 GB [E] if approved | — |
| **Total** | **≈ +32 GB (C: 19 → ~51 GB)** | **≈ +80 GB**, realized only after Phase 5 |

Bottom line: **Phase 1 alone fixes this PC** (19 → ~51 GB free). Nothing in WSL buys a single byte on
C: until the vhdx is compacted, so run Phases 2–4 while Phase 1 uploads, then compact once.

## 11. Risks / blockers

1. **`git status` was not run** (no shell). Deleting worktrees before it can destroy uncommitted
   research → hard gate.
2. `depth_shadow` is on a **detached HEAD**; if `a785b8d4` is not on a remote it is the only copy.
3. `.env` / SSH keys are the only non-recoverable secrets (gitignored, not on the box). Reconcile
   the two `.env` copies — WSL 1.3 KB is newer than Windows 1.1 KB.
4. The Windows project copy has no `.git`; safe to delete only because WSL `main` == origin/main —
   still diff first.
5. `data/` is written by an armed (currently stopped) bot; tar closed months, and never let the
   archiver `--delete-local` a directory a writer may reopen.
6. `storagebox:backup/` untouched; all uploads go under `storagebox:alpacatrader/`.
7. Two scout agents are scanning the same trees — stagger heavy `du`/tar work.
8. All [E] sizes must be re-measured before `--delete-local`; the archiver's MANIFEST + sha256
   verification, not these numbers, is the real safety net.
