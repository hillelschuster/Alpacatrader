# Junk & siblings audit (2026-10-28)

Measurements on this PC. Sizes are `du -sk` Apparent KiB unless noted; GiB = KiB/1,048,576.
Nothing was deleted. No secret contents were read (filenames only).
9p (`/mnt/c`) caveat: a `find -printf` inventory of the Windows tree returned 27,539 files while a
later `du` reported 26,875 under `factory/artifacts/basket` alone; per-file inventories are therefore
**lower bounds**. `du -s` totals are the authoritative numbers.

---

## 1. Caches / junk on this PC (WSL side)

| path | KiB | GiB | what | verdict | exact command |
|---|---:|---:|---|---|---|
| `~/.cache/camoufox` | 2,486,972 | 2.37 | camoufox (Firefox fork) browser+fonts download | **SAFE DELETE** (re-fetch) | `rm -rf ~/.cache/camoufox` then `uv tool run camoufox fetch` |
| `~/.cache/uv` | 262,580 | 0.25 | uv wheel/archive cache | **SAFE DELETE** | `uv cache clean` |
| `~/.cache/roundel-capture-profile` | 144,388 | 0.14 | Firefox profile of the "roundel" capture tool (`588e9e9c.sqlite` = cookies/session) | **SAFE DELETE**, but it holds logins | `rm -rf ~/.cache/roundel-capture-profile` |
| `~/.cache/fontconfig` | 9,296 | 0.009 | font cache | SAFE | `rm -rf ~/.cache/fontconfig` |
| `~/.cache/mesa_shader_cache` | 1,736 | 0.002 | GPU shader cache | SAFE | `rm -rf ~/.cache/mesa_shader_cache` |
| `~/.cache/pip` | 1,544 | 0.001 | pip cache | SAFE | `rm -rf ~/.cache/pip` |
| `/tmp` | 2,908,972 | 2.77 | harness/browser/rclone scratch (`tmpbv6welnn` 384M zip, `rclone-spool*` 257M, `pytest-of-hillel` 222M, `ccprof` 151M, `felt-shots` 137M, playwright profiles 16M) | **SAFE**; grew 1.4→2.9 GiB while archiving runs | `find /tmp -mindepth 1 -maxdepth 1 ! -name rclone-spool\* -exec rm -rf {} +` (skip active rclone spool, chrome is open) |
| repo `.codegraph` | 97,420 | 0.09 | CodeGraph index (48M db + 48M WAL, daemon.pid/sock live) | **SAFE DELETE** (rebuilt index) | `.codegraph` daemon stop, then `rm -rf .codegraph` |
| repo `.pytest_cache` | 192 | — | pytest cache | SAFE | `rm -rf .pytest_cache` |
| repo `.ruff_cache` | 40 | — | ruff cache | SAFE | `rm -rf .ruff_cache` |
| repo `__pycache__` (9 dirs) | 13,800 | 0.013 | bytecode | SAFE | `find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +` |
| `~/.bun/install` | 167,472 | 0.16 | bun package cache | SAFE | `~/.bun/bin/bun pm cache rm` |
| repo `.venv` | 1,618,936 | 1.54 | project venv | RECREATABLE | `rm -rf .venv && uv sync --frozen` |
| `worktrees/Alpacatrader/basket-phase2-f1/.venv` | 389,972 | 0.37 | worktree venv | RECREATABLE | `rm -rf` that venv, `uv sync` in worktree |
| Windows copy `.venv` | 429,364 | 0.41 | stale Windows venv | RECREATABLE | delete with the stale copy |
| `~/.local/share/pipx` | 923,720 | 0.88 | pipx venvs; only app = `hound-mcp` | RECREATABLE | `pipx uninstall hound-mcp` (890M under `venvs/`) |
| `~/.local/share/uv/tools` | 115,632 | 0.11 | uv tool venvs: `a0`, `camoufox`, `trafilatura` | RECREATABLE | `uv tool uninstall <name>` |
| Windows copy `.codegraph` | 10,784 | 0.01 | stale index | SAFE DELETE | with the copy |
| `~/.npm`, `~/.cargo` | — | 0 | do not exist | n/a | n/a |
| `~/dp-out` | 4 | 0.00 | empty | SAFE | `rmdir ~/dp-out` |

**Needed, not junk:** `~/.local/share/uv/python` 217,412 KiB (0.21 GiB) = CPython 3.11.15 + 3.14.4
(3.11/3.14 are symlinks to the versioned dirs — not duplicates).
**Owner decisions, not junk:** `~/canary_runs` 182,620 KiB (0.17 GiB; atlas observation canary logs +
evidence, 2026-09-30..10-01, no code reference, not inside the repo);
`~/.local/share/polymarket-anomaly/anomaly.db` 648,500 KiB (0.62 GiB; another project's DB);
repo `logs/` 158,200 KiB (0.15 GiB; bot logs, `paper-loop-opencode.log` 105M is the bulk; 43 files,
grep for `APCA-`/`SECRET_KEY`/`api_secret` matched **0** files, but contents were not otherwise read).

**Totals:** strictly safe/junk deletes = **5.81 GiB**; including recreatable venvs/tools = **9.12 GiB**;
plus owner-decision items = up to **10.06 GiB**. (`/tmp` figure varies ±1.4 GiB.)

---

## 2. Sibling projects under `/mnt/c/Users/הלל/Desktop/algo projects/`

Measured with `timeout 600 du -s --apparent-size` per directory (all completed).

| dir | KiB | size | classification | notes |
|---|---:|---|---|---|
| `Polymarket bots` | 40,027,715 | **38.17 GiB** | other project (polybot fleet) | subprojects `esports-polybot`, `favorites-polybot`, `football-polybot`, `poly-vault-favorites-bot`, `polymarket bot`, `polymarket factory`, `polymarket-alpha-vault`, `edge-lab`, `polyfleet-tools`; has own `.codegraph`/`.omo` |
| `Hyperliquid-index` | 1,043,494 | 1.00 GiB | other project | has `.git`, `.venv`, `.env` |
| `kalshi bots` | 449,526 | 0.43 GiB | other project | `kalshi-microscalper`, `kalshi-sell`; `.git` |
| `C++ bot` | 236,964 | 0.23 GiB | other project | CMake/bridge bot; `.git`; `.env` |
| `alphabot2` | 20,721 | 20.2 MiB | other project | `.git`, `.venv`, `.env` |
| `alphabot` | 13,071 | 12.8 MiB | other project | `.git`, `.env.live`, `config.json` |
| `.obsidian` | 3,825 | 3.7 MiB | Obsidian config | not a project |
| `opencode` | 2,793 | 2.7 MiB | tool clone (Go) | `.git` |
| `.agents` | 1 | ~0 | harness config (`mcp_config.json`) | not a project |
| `.omo` | 4 | ~0 | harness run-continuation state | not a project |
| `Vps` | 2 | ~0 | **secret store** | `algo-vps pem key.pem`, `vps_phone_ssh_access.txt`, `algo.txt` |
| loose | 62 KB | — | `Polymarket_Israel_Tax_...docx`, `AGENTS.md`, `mcp_config.json`, `desktop.ini` | |

**Secrets present (names only, contents never read):**
`C++ bot/.env`; `Hyperliquid-index/.env`; `Polymarket bots/{esports-polybot,football-polybot,poly-vault-favorites-bot,polymarket bot,polymarket-alpha-vault/production_engine}/.env`;
`alphabot/.env.live`; `alphabot2/.env`; `Vps/algo-vps pem key.pem`; `Vps/vps_phone_ssh_access.txt`;
`kalshi bots/kalshi-microscalper/kalshi_private_key.pem`.
→ If any of these move off the PC, do **not** stream them into `storagebox:alpacatrader/` (box is a data
store, not a secret store); copy them over an encrypted channel or keep the PC copy.

**Recommendation:** none of these belong to top-gainers; the 206 GB migration total in the plan does not
include them. Total of all 11 sibling dirs = 40,048,116 KiB = **38.19 GiB**, of which `Polymarket bots`
alone is 38.17 GiB — it would need its own archiver run if it must leave the PC. Nothing here should be
deleted without the owner.

---

## 3. Stale Windows-side copy `.../algo projects/Alpacatrader/` (non-`data` part)

Facts (measured):
- It **does have** a working `.git` (writable, `git status` in 7 s). `HEAD = 78442f2` "research(basket):
  Phase 2 open - freeze family-level PRE-REG-02 + canonical sim contract", 2026-09-22; remote
  `github.com/hillelschuster/Alpacatrader`. `origin/main` ref is stale at `e3595f7` (2026-09-21), so
  the clone reports 1 commit not on its (stale) remote — but **78442f2 already exists in the WSL repo
  and is an ancestor of main HEAD**: no unique commit is stranded there.
- Tracked files: 647 at win HEAD vs 752 at main HEAD; `comm` shows **0 tracked paths that exist only
  on Windows**. 16 tracked files were deleted from the Windows worktree (`.dockerignore`, `Dockerfile`,
  `HANDOFF.md`, `factory/RESEARCH_GOAL.md`, `archive/*.md`).
- 108 worktree-modified tracked files: **107 are CRLF-only byte-identical** to the WSL copy
  (sha1 after `\r\n`→`\n`); only `opencode.json` (harness config) really differs.
- Untracked, outside `.omo/` and `factory/artifacts/`: 24 files.
  - **Unique (no such path in any ref): 12 uncommitted scripts**, 150,464 B total:
    `basket_f1_run.py` 7,009 · `basket_f1_multi.py` 8,946 · `basket_f4_analysis.py` 5,488 ·
    `basket_f4_report.py` 12,895 · `basket_f4_results.py` 11,365 · `basket_f4_tests.py` 1,613 ·
    `basket_f9_weights.py` 43,103 · `basket_f10_regime.py` 27,970 · `basket_f11_pilot.py` 14,102 ·
    `basket_f11_sensitivity.py` 12,134 · `test_basket_f11.py` 2,199 · `test_basket_f67_capital.py` 3,640.
  - `basket_sim.py` (84,274 B, mtime 2026-09-22 18:44) is untracked there, tracked in main (`a7e625c`,
    2026-10-28, mtime 11:04 on Sep 22) — and it is **15 real lines AHEAD of main**, not stale:
    `import shutil`, hermetic `shutil.rmtree(cfg.run_dir)` before re-runs (2 call sites), `engine_sha256`
    in the report, engine-hash line in the report markdown, and `--out-root` default
    `BASKET_ART_ROOT/"phase2"` vs main's `SIM_CORE`. This is the F-line's reproducibility fix; it must
    be diffed and landed, not discarded.
  - 11 files are already identical in main (`basket_f2_*`, `basket_f3_release`, `basket_f8_joint`,
    `build_pit_from_bundle.py`, `researches/PRE-REG-H12R.md`).
- Untracked artifacts: `factory/artifacts/basket/` = **1.14 GiB / 26,875 files** (WSL copy: 921 MiB /
  7,209 files). File-name comparison: ~20,000 names absent from WSL (measured ≥292 MB of those:
  `phase2/F4_scaleout` 180 MB, `phase2/F1_primitive` 66 MB, `phase2/F9_weighting` 45 MB,
  `phase2/F11_exec` 0.6 MB, `phase2/F10_regime` 0.6 MB), while 4,727 WSL names are absent there —
  i.e. the two baskets are different generations of the same lanes, not subsets.
- Size of the copy: `factory/scripts` 3.9 MB, `factory/artifacts` ≥1.14 GiB (basket alone),
  `.venv` 429,364 KiB (0.41 GiB), `.codegraph` 10,784 KiB, `logs/` and `archive/` empty,
  `data/` = the real 114 GiB payload.

**What is worth keeping from it (small):**
1. The 12 uncommitted scripts (`factory/scripts/`) — unique research code, ~150 KB.
   Plus **`factory/scripts/basket_sim.py`** (84 KB), which is 15 lines ahead of main (hermetic re-run
   fix + engine sha256 + `--out-root` default). Total unique source = 234,738 B.
2. `factory/artifacts/basket/phase2/{F9_weighting,F10_regime,F11_exec}` (~46 MB: 44.8 + 0.6 + 0.6) — the
   only artifacts of those lanes anywhere (shared `agg/` 33 MB also exists in main with newer content).
   `F4_scaleout` (180 MB) / `F1_primitive` (66 MB) are older generations of lanes that WSL has
   regenerated (owner decision).
3. Nothing else: tracked content, `archive/`, docs, `researches/`, `.venv`, `.codegraph` are all
   duplicates or older (`uv.lock` and all `.json/.log/.md` artifacts are CRLF copies of files present
   in main; `.env` is a July copy — compare locally, never upload).

Recommended transfer: `tar -C "/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader" -czf - factory/scripts/{basket_sim.py,basket_f1_run.py,basket_f1_multi.py,basket_f4_analysis.py,basket_f4_report.py,basket_f4_results.py,basket_f4_tests.py,basket_f9_weights.py,basket_f10_regime.py,basket_f11_pilot.py,basket_f11_sensitivity.py,test_basket_f11.py,test_basket_f67_capital.py} factory/artifacts/basket/{phase2/F9_weighting,phase2/F10_regime,phase2/F11_exec} | rclone rcat storagebox:...`
then delete the Windows project copy (keeps `data/` — see §4: `data/` is a *directory* there and the
WSL repo's `data` is a **symlink to it**, so the copy must never be deleted with `--delete-local`).

---

## 4. Redundant material inside the Windows `data/` dir

| item | size | files | what / evidence | recommendation |
|---|---:|---:|---|---|
| `_superseded/` | 1.24 GiB apparent (0 B unique) | 5 | Only `clean_ohlcv_2025-05/06/07.parquet` in `stage_2025-07/` + `stage_h008/`. `stat` shows **hardlinks** to the root files: inodes `6755399442070976` (links=2), `130041439240370542` (links=3), `27584547717718838` (links=3) — same inodes as `data/clean_ohlcv_2025-0{5,6,7}.parquet`. `factory/STATE.md:528`: "Moved: data/stage_*{md5-verified identical} -> data/_superseded/ + receipt" | **DELETE** (frees ≈0 B; removes double-count) |
| `_scratch/` | 30.8 MiB | 272 | Scratch IO of path/pattern programs: `collide_path_model.py`, `build_path_panel.py`, `capture_variants.py`, `freeze_path_model.py` (9 path literals). No committed artifact reads it | **SKIP-REGENERABLE** (re-run those scripts) |
| `scratch_census/` | 14.3 MiB | — | `factory/scripts/event_census.py` CACHE (writes only) | **SKIP-REGENERABLE**: `python factory/scripts/event_census.py` |
| `scratch_pattern/` | 189.4 MiB | — | `factory/scripts/pattern_digest.py` SCRATCH (writes only) | **SKIP-REGENERABLE** |
| `scratch_pattern_events/` | 24.4 MiB | — | `factory/scripts/pattern_digest_events.py` CACHE_DIR | **SKIP-REGENERABLE** |
| `harvest01/` | 10.49 GiB | 64,931 | Per-day parquet lanes of the basket-harvest lineage (`base/{bars,leaders,selected,champs,splits}` 7,464 files = 0.35 GiB, `basket` 2,132 = 0.04 GiB, `friction` 2,130 = 0.01 GiB, `lifecycle` **40,269 files** (per-dir `du` timed out at 300 s), `mgmt` 2,133, `minute` 2,132, `policies` 2,133, `sim` 3,198, `window` 3,198, `report` 55, `logs` 87; remainder ≈ 10.1 GiB by subtraction). **Written/read by 58 scripts on branch `basket-phase2-f1`** (`basket_harvest_select/bars/basket/…`). Current main-HEAD code references only `harvest01/base/splits.parquet` (3 scripts: `alpha_premarket_gap_lane.py`, `alpha_open_overnight.py`, `alpha_multi_session_carry.py`, 5 refs) | **UPLOAD-LOW-PRIORITY** if the F-line continues (tar.gz batches; per-file upload of 65k files is ~20× slower). Regenerable: `basket_harvest_select.py` → `basket_harvest_bars.py --dev-days --workers 3` (Alpaca SIP bars) → `basket_harvest_basket.py --dev-days` (needs `atlas/observation`); delete only if the lane is closed |
| `atlas/observation/` | 48.90 GiB | 8,946 | Output root of `factory/scripts/basket_tape_atlas_observation.py` (branch; default `ATLAS_OBS_DATA_ROOT`). Breakdown: `v0/` 46.69 GiB (`race.minute_full` **36.34 GiB**, `prints.selected_paths` **8.98 GiB**, `race.checkpoint_full` 1.18 GiB, rest ~3 MB), `sample_check/` 2.13 GiB, `rank_probe/` 0.08 GiB. `race.minute_full` is read by `basket_harvest_select.py`, `basket_harvest_minute.py`, `basket_harvest_leaders.py`; `memberships.parquet` read by `phase2/ATLAS/EV01/code/*`. No other consumer | **UPLOAD-LOW-PRIORITY** (derived, very expensive to rebuild) or **SKIP-REGENERABLE** if the F-line is closed: `.venv/bin/python factory/scripts/basket_tape_atlas_observation.py --stage contract` then `--stage full --workers N` (needs `data/sip/net/trades`, which is being uploaded). Do not upload the 36 GiB board unless the lane lives |
| `atlas/geometry/` | 2.28 GiB | 174 | Output of `basket_tape_atlas_geometry.py --root data/atlas/observation/v0 --out data/atlas/geometry/v0`; branch-only consumer (`sequence` build) | **UPLOAD-LOW-PRIORITY** (rebuildable from observation) |
| `atlas/sequence/` | 0.68 GiB | 14 | **Referenced by current code**: `alpha_sequence_payoff.py:63`, `alpha_search_report.py:3482` (`data/atlas/sequence/v1/runs/causal/block/embeddings.npz`) | **UPLOAD** (current top-gainers input) |
| `atlas/continuation/` | 0.27 GiB | 192 | `basket_current_dollar_base.py` / `_curve.py` output (`cv01/base`), read by `basket_ownership_continuity.py` (branch constants) | **UPLOAD-LOW-PRIORITY** |
| `atlas/acquisition/` | 0.34 GiB | 238 | `atlas_acquire_sip_bars.py` payloads (`atlas/acquisition/v3|v4`), re-fetchable from Alpaca | **UPLOAD-LOW-PRIORITY** / regenerate |

Notes for the migration:
- `data/` in the WSL repo is a **symlink** → this Windows directory. Any `--delete-local` on that path
  must be blocked, or the repo loses its data root.
- `forward/` (0.054 GiB, newest file 2026-10-28 04:00) is live bot state — must be uploaded, never
  deleted.
- `entry_ev`, `pit`, `journal`, `_scratch`, `_superseded`, `scratch_pattern*` have no data-path literal
  in current code (only the scripts that generate scratch). Absence of a literal is not proof.

---

## Items needing an owner decision

1. **F-line survival** (`basket-phase2-f1` + Windows F9/F10/F11 work): if no, skip uploading
   `harvest01` (10.5 GiB) + `atlas/observation` (48.9 GiB) + `atlas/geometry` (2.3 GiB) and let them be
   regenerated. If yes, they are the two biggest non-raw uploads and must go as tar batches.
2. **The 12 uncommitted Windows basket scripts + F9/F10/F11 artifacts (~46 MB)** — commit them to
   `basket-phase2-f1` or archive to the box before deleting the Windows copy.
3. **Sibling projects (40.05 GiB total, 38.17 GiB = `Polymarket bots`)** — stay on the PC, or a
   separate archiver run?
4. **Secrets** (`Vps/*.pem`, `vps_phone_ssh_access.txt`, `kalshi_private_key.pem`, 10 `.env` files) —
   transport method; must not land in `storagebox:alpacatrader/`.
5. **Owner-only deletions**: `~/canary_runs` 0.17 GiB, repo `logs/` 0.15 GiB,
   `~/.local/share/polymarket-anomaly/anomaly.db` 0.62 GiB.
6. **`_superseded` delete** (0 B freed, but removes a misleading 1.3 GiB from every inventory).
