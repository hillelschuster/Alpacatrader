# Git hygiene report — three checkouts (2026-10-28)

## 1. Main checkout `/home/hillel/algo projects/Alpacatrader` (branch main)

Start: 164 dirty (119 tracked-modified, 45 untracked).

### Triage before commit
99 of the 119 tracked modifications were **CRLF-only churn**, proven with
`git diff --ignore-cr-at-eol --numstat` returning empty (content identical, only line endings):
`.gitignore`, `factory/scripts/{build_features,replay_watchlist,train_ml}.py`,
`factory/scripts/observe_supervisor.sh`, `researches/{07-dominant-leader-program,CANONICAL_STATE}.md`,
and 98 files under `factory/artifacts/**` (incl. all `certification_*/checks.json|report_draft.md|*.log`,
`h006_results_2025-07/*`, `h008/h009_results*`, `ml/train_report_*`, `zz_leader_health.csv`).
These were restored with `git checkout --` (line-ending only; no content lost).

### Commits (pushed `650d6c1..4919011 main -> main`)
| hash | content |
|---|---|
| `4a65875` | `uv.lock` — real dependency change (pyproject already declared `huggingface-hub>=0.25`, `polars>=1.0`; lock was stale). Adds polars 1.44.1 + polars-runtime-32, huggingface-hub 1.30.0, fsspec, filelock, hf-xet, tqdm; ruff 8.4.1→8.5.0 |
| `0c95129` | real code/doc fixes in 8 `alpha_*.py` (quote_universe_acquire contract docstrings), `forward_observe.py` (`NewsRequest(symbols=<symbol>)` not a list — Alpaca API shape), `test_flush_bot.py` (`sys.path` from `__file__` instead of the Windows absolute path) |
| `a7e625c` | 20 previously-untracked research scripts (alpha news/HOD/capture/gap/shallow-depth/sparse-lead lanes, basket F2/F3/F8 modules, `basket_sim.py`, `build_pit_from_bundle.py`) + 7 test files + `researches/PRE-REG-H12R.md` |
| `0344882` | `researches/PRE-REG-DEPTH-01.md` — $90 → $300 live daily stop amendment (2026-10-27), research edge unchanged |
| `4919011` | `opencode.json` — agent tooling config (models → opencode-go, context7 MCP, snapshot off); no secrets |

`git log origin/main..main` = 0. `git status` now: 1 tracked-modified + 20 untracked.

### Remaining dirty (b) — research evidence, DO NOT commit (archive to box): 284,567,036 B
- `factory/artifacts/basket/phase2/` 261,715,313 B (untracked research runs)
- `factory/artifacts/overnight/` 20,617,560 B
- `factory/artifacts/basket/sip/phase2_session_calendar.json` 427,375 B (sha1 bd14b5c…; input, also present in both worktrees)
- 12 `factory/artifacts/alpha_*.json` state/checkpoint files 1,749,616 B total (all <1 MB except `alpha_night_extensions.json` 858,726 B, `alpha_search_20261008.json` 837,893 B)
- `capture-origin-final.md` 48,732 B (audit report of a patch whose file is not in HEAD)
- `factory/artifacts/flush_bot_ledger.json` modified, +420/−4 lines, 8,440 B (runtime ledger of the retired flush bot)

### Remaining dirty (c) — junk, deletion candidates (not deleted): 491,860 B
- `.omo/` 28,939 B (agent run state)
- `basket and different runs` 462,921 B (dumped opencode session transcript, not a directory)

### Lint (not fixed)
`uv run --no-sync ruff check` — 73 errors on the 10 modified scripts, 447 on the 24 new scripts
(pre-existing style debt: E741 ambiguous `l`, E501, etc.). Ruff is missing in the basket worktree's
`uv run --no-sync` env (no synced venv there).

## 2. Basket worktree `/home/hillel/algo projects/worktrees/Alpacatrader/basket-phase2-f1`

Start: 1,118 dirty (1 modified, 1,117 untracked). Commit `87ef2c2` (pushed `203ddaf..87ef2c2`):
- `factory/scripts/h025_research_entry.py` — real fix: `np.minimum` on nullable polars Series now
  cast to float64 numpy (`.astype("float64").fillna(np.inf).to_numpy(float)`) and accumulators
  re-normalised to ndarray.
- 4 previously-untracked lifecycle research scripts (`lifecycle_tape.py` 62.9 K,
  `lifecycle_quote_audit.py` 68.3 K, `lifecycle_leg_map.py` 5.4 K, `lifecycle_discovery_follow.py` 2.6 K).

Remaining (b): 1,113 untracked paths under `factory/artifacts/basket/phase2/` — **4,008,307,144 B**
(3.73 GiB, 183,942 files, 742 files >1 MiB; largest groups F4_C1 257.6 MB, F11 220.8 MB,
F2_F12 32.4 MB, F4 213.8 MB, F1 110.2 MB). `git log origin/basket-phase2-f1..basket-phase2-f1` = 0.

**Divergence warning:** `factory/scripts/basket_f8_joint.py` and `basket_sim.py` now exist on BOTH
branches with different content. Main has the working-tree variants used by the local runs
(f8_joint +689 lines vs branch; basket_sim −1,886 lines, i.e. it lacks the branch's
2026-09-24 carry-bars substrate correction). `basket-phase2-f1` stays authoritative for basket work;
resolution of the future merge needs the owner.

## 3. `/home/hillel/depth_shadow` (detached `a785b8d`)

- Only commit history: `a785b8d`, which **is an ancestor of `main`** (`git log main..a785b8d` empty).
- Its working diff (`KILL_PATH` → `data/forward/depth_bot/KILL`, non A–Z SIP ticker filter) is
  already in `650d6c1`; `git diff main -- factory/scripts/depth_bot.py` shows main is +396 lines
  (round-3 work), i.e. nothing unique here. No stash.
- Untracked: `factory/artifacts/basket/sip/phase2_session_calendar.json` (byte-identical to main's,
  sha1 bd14b5c…) and `.venv` (symlink). Total 307 MB of tracked files.
- **Removable: yes.** `.env` → `projects/Alpacatrader/.env`, `data` → `projects/Alpacatrader/data`
  (itself → `/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader/data`), `.venv` →
  `algo projects/Alpacatrader/.venv`. Recipe that cannot follow them:
  ```
  unlink /home/hillel/depth_shadow/.env /home/hillel/depth_shadow/.venv /home/hillel/depth_shadow/data
  cd "/home/hillel/algo projects/Alpacatrader"
  git worktree remove --force /home/hillel/depth_shadow   # --force required (dirty/untracked)
  git worktree prune
  ```
  Warnings: never `cp -rL`, `rsync -L`, `tar -ch`, `find -L … -delete`, `rm -rf $(readlink -f …)`,
  or recursive `chmod/chown` on this tree — any of those would traverse into the 114 GiB Windows
  `data/` or the shared `.venv`. `git worktree remove` itself unlinks symlinks, and the `unlink`
  pre-step makes that guaranteed.

## 4. New-machine instruction for the depth bot

Pin to `origin/main` ≥ `650d6c1` — as of this run that is **`4919011`** (`main` == `origin/main`).

```
git clone https://github.com/hillelschuster/Alpacatrader.git && cd Alpacatrader
git checkout 4919011            # or: git checkout main && git pull   (must be >= 650d6c1)
```
- Restore `data/` from the Storage Box first (at minimum `data/forward/depth_bot/` journal + ARMED,
  and the SIP/PIT data the bot reads); kill switch is `data/forward/depth_bot/KILL` (round-3 moved it
  off `data/KILL`).
- Do **not** deploy `a785b8d`/the depth_shadow worktree: it predates the round-3 fixes (closed-order
  adoption, overdue exits, true exit slippage, supervisor backoff/give-up, checklist command,
  SIP-safe symbol filter).
