# AGENTS.md — read this first (kept short on purpose)

You are in a money-making trading research project. Sole objective: find and exploit
a profitable top-gainer/momentum trading mechanism. No template worship; follow
evidence. (Full principle in the parent folder AGENTS.md.)

## Read order (small context, current truth first)

1. `researches/INTENT.md` — objective, stance, discipline (3 min).
2. `researches/STATE.md` — CURRENT truth snapshot: what's solid, what died, what's open.
3. `researches/HYPOTHESES.md` — living hypothesis ranking + falsifiers (top header is
   current; older sections are archived history).
4. `factory/STATE.md` — append-only operational chronicle (read the tail, not all).
5. `HANDOFF.md` — full cold-start handoff; **§16 = latest state / entry point**.

## Operating rules

* **Profitability is the priority.** Research, code, tooling and process exist only to help find, validate or execute an edge.
* **The user is the decision maker.** Follow explicit instructions strictly.
* Deviate only when there is a clearly better or more practical route toward profitability; state why briefly.
* Work directly. **Zero overengineering. Zero unnecessary frameworks, architecture, abstractions or process.**
* Use the smallest sufficient script, experiment, test or tool.
* Tasks requiring deep reasoning: **think deeply.**
* Tasks requiring hypothesis generation: **generate and investigate hypotheses.**
* Tasks requiring concise execution: **be concise and execute.**
* Use Context7 when current library/API/documentation context is needed.
* Do not inherit old assumptions, targets, models or experimental structures merely because code already exists.
* Discovery can be exploratory. Once something looks monetizable, validate it hard enough to distinguish edge from luck.
* Negative evidence applies to what was actually tested. Do not overgeneralize it or endlessly protect dead ideas.
* Prefer raw evidence and simple experiments over elaborate methodology.
* Before adding complexity, ask: **does this materially help profitability, evidence quality, execution correctness or capital protection?** If not, skip it.

## Working rules (learned expensively)

- Measurement contract: ET clocks, causal-only, 1-bar lag, next-bar-open fills,
  >=100bps friction, pre-registered kills.
- Power doctrine (post-2026-09-08): any selection/timing test needs >=6 months pooled
  dev + >=2 pre-registered unseen collision months. Small-n dev positives are 0-for-4.
- Never resurrect a closed/failed formulation. New formulation + more power only.
- Live path: `factory/scripts/flush_bot.py` (paper trader; `data/forward/bot/LIVE`
  arms it, `data/KILL` stops it, a bot edit needs the KILL-toggle restart —
  supervisor restarts only on exit). `factory/scripts/forward_observe.py` = evidence
  observer. Judge live fills against the **A3b baseline** (`HANDOFF.md` §14), never
  the frozen backtest; `flush_bot_ledger.py` partitions fills by `pf_est` / `n_strict_est`.
- Long compute: incremental per-day writes + resume; never write only at the end;
  verify artifacts exist before reasoning from them.
- Subagent runs have stalled in this setup; prefer direct execution for research
  scripts (user directive 2026-09-12).
- Research memory: append a few lines to `factory/STATE.md` after meaningful work;
  keep `researches/HYPOTHESES.md` and the ledgers current; commit producer scripts
  under `factory/scripts/`.
- Two workstreams: research (`factory/`, `researches/`) and the paper bot (`src/`,
  `tests/`). Keep their states separate; the bot implements only validated research.

## Everything else is history/evidence, clearly marked

- `researches/CANONICAL_STATE.md`, `researches/07-dominant-leader-program.md` — FROZEN
  historical snapshots. Do not cite as current.
- `researches/history/` — June bot-era audits (00-06) + archived research docs. Zero
  constraint on active research.
- `factory/artifacts/*.json` — committed evidence for every number in STATE/HYPOTHESES.
- `factory/HYPOTHESES.jsonl` / `factory/EXPERIMENTS.jsonl` — experiment ledgers.

## Repo state notes (2026-09-13)

- `origin/main` is current; pushes are expected after each work cycle. `data/` is
  gitignored by policy (no remote backup of market data — handle with care).
- Live paper bot: armed (`LIVE`), account flat, 0 fills; next session Mon 2026-09-14.
  Real-time SIP not purchased (not needed for the frozen strategy).
- Uncommitted working-tree dirt from other workstreams (ml/* logs, certification
  artifacts, `PRE-REG-H12R.md`, CRLF churn) is NOT research state — do not commit it
  blindly.

## Storage (IMPORTANT - read before writing anything large)

- **This machine's disk is nearly full.** The real host is Windows C: (~26 GB free on
  2026-10-27). WSL's `df /` shows a ~1 TB virtual disk and LIES about free space - check
  `df -h /mnt/c` instead. Never write multi-GB outputs locally.
- **Off-host store = Hetzner Storage Box** (FSN1-BX776, 1 TiB, ~975 GiB free at setup):
  host `u678314.your-storagebox.de`, user `u678314`, **port 23**. Key auth is configured
  (`~/.ssh/storagebox_ed25519`, alias `ssh storagebox`); rclone remote `storagebox:`.
  Password only in the gitignored `.env` (`STORAGEBOX_*`) - never commit or print it.
- Layout: `storagebox:alpacatrader/` = everything of ours (`alpha-data/` live research roots,
  `archive/` retired lanes and old raw data). `storagebox:backup/` is pre-existing - do not touch.
- Tidiness rules live in `storagebox:alpacatrader/README.md` (read it before writing): mirror the
  local path; archive as monthly, content-aware batches streamed with `tar | [gzip -1] | rclone rcat`
  (already-compressed data - parquet/npz/pt/zip/gz/zst/feather/joblib - goes into a plain `.tar`)
  (never thousands of loose files - per-file upload is ~20x slower), verify each batch with
  `ssh storagebox sha256sum` vs the local stream hash, write a `MANIFEST.md` + `RESTORE.sh`, add a row to the
  README index, and only then delete locally. Use `factory/scripts/archive_to_storagebox.sh <dir> "<why>" [--dest-name NAME] [--max-batch-bytes 6G] [--jobs 3] [--exclude PATTERN] [--dry-run] [--delete-local]` (does all of this; re-runnable). Example: the 2026-10-27
  delayed_sip_iex archive (MANIFEST on the box). Running lanes stage per day locally (< 1 GB)
  and move each day to `storagebox:alpacatrader/alpha-data/<same path>/`.

### Storage workflow for a machine with no data (`factory/scripts/box.sh`)

Full setup, measured numbers and rules: `NEW_MACHINE.md`. In short:

- **Pull, don't mount.** Measured: box downlink ~10-14 MB/s, but a FUSE mount answers a random
  512 KiB read in **631 ms** vs 0.073 ms locally (8,600x), so a column-selective parquet read that
  takes 0.08 s on disk costs ~30 s over a mount. Mount read-only only to browse (command in
  `NEW_MACHINE.md`).
- Data lives in a cache, `$CACHE_ROOT` (default `~/boxcache`, cap `CACHE_CAP_GB=20`, config
  `factory/box.conf`). `factory/scripts/box.sh link` points `~/alpha-data`, `~/sip` and the repo's
  `data/` symlink at it, so research producers find data **unchanged** (no path edits). It also
  links `$CACHE_ROOT/data/forward` to the live bot state (`BOT_FORWARD`, the Windows
  `data/forward`), so relinking `data/` never touches the running bot; `box link` refuses a real
  directory with files and is idempotent.
- Daily: `box status` → `box set depth-min` (or `box pull <dest> --match 'quotes_2025-*'`) → run →
  `box pull` verifies each batch's sha256 against the dataset's `MANIFEST.md` and skips verified
  ones → push the day's outputs with `box push <dir> <dest-name> --why "…"` (never
  `--delete-local` unless `--allow-delete-local`) → `box evict --lru 5G`.
- `box evict` only deletes files recorded as extracted from a verified batch, refuses when such a
  file was modified after the pull, and leaves unmanaged files alone. Never evict unpushed work.
- Named working sets (size + readiness shown by `box set --list`) live in
  `factory/box_working_sets.tsv`: `depth-min` (4.9 GB), `depth-bot`, `panel-data`, `basket-all`,
  `cold-basket`, `sip-tape` (26 GB, pull per month), `everything-derived`. `box set` refuses a set
  larger than the cache cap unless `--force`.
- Dest-name -> local path lives in `factory/box_datasets.tsv` (also the box README "Dataset map").
  Live data (`data/`, `alpha-data/`) extracts onto the linked paths; old trees (`basket-worktree`,
  `windows-project-copy`, `alpacatrader-repo-main`, `windows-data__forward`) restore to
  `$CACHE_ROOT/restore/<dest-name>/` so a cold pull cannot overwrite live state.
- Single-object datasets (no batches/MANIFEST: `alpacatrader-git` bundle, `omp-sessions` tar)
  restore with `box get <dest>`, which prints the `git clone` / `tar -xf` command. A dataset whose
  archive is still being written has no `MANIFEST.md`; `box pull` refuses it instead of guessing.
