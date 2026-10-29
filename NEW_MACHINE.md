# NEW_MACHINE — working on Alpacatrader when all data lives on the box

The repo is just code. **Every** dataset (research roots, panel days, quotes, SIP tape, the
Windows `data/` panel, lane outputs) lives on the Hetzner Storage Box under
`storagebox:alpacatrader/` and is pulled into a small local cache only while you use it.
Box layout + tidiness rules: `rclone cat storagebox:alpacatrader/README.md`; repo summary in
`AGENTS.md` §Storage.

Tool: **`factory/scripts/box.sh`** (python3 stdlib; config `factory/box.conf`).
It pulls verified batches, links the cache into the paths the producers already use, and evicts
safely.

---

## 1. Why pull instead of mounting the box

Measured on 2026-10-28 from this machine (uplink busy with the migration queue, so downlink
numbers are a floor):

| test | result |
|---|---|
| `rclone cat` one 122 MB batch, single stream | 122.5 MB / 12.3 s = **10.0 MB/s** (85 MB / 8.3 s = 10.3 MB/s) |
| `rclone cat` × 4 parallel, aggregate | 295.6 MB / 26.9 s = **11.0 MB/s** → parallelism buys ~10 % only (line-bound) |
| `rclone cat … \| tar -xf -` (pull + extract) | 122.5 MB in **8.5 s ≈ 14 MB/s** (variance; box pull reported 12.5 MB/s) |
| FUSE mount, sequential read (cold) | **12.4 MB/s** (117 MiB / 9.47 s) — same as a pull, but nothing is kept |
| FUSE mount, re-read after VFS cache filled | 0.27 s (~460 MB/s) — only for data already cached |
| FUSE mount, **random 512 KiB read** (cold) | **631 ms per read** |
| local disk, random 512 KiB read | **0.073 ms per read** (8,600× faster) |
| local polars, 1.05 M rows × 4 cols from a pulled parquet | **0.08 s** |

FUSE works here (`/dev/fuse`, `fusermount3`, `rclone mount` mounts fine), but a parquet reader
issues dozens of small ranged reads per file; at ~0.6 s each a single column read that costs
0.08 s on local disk would cost ~30 s over the mount, and repeated scans re-pay it.
**Decision: pull the batches you need (`box pull` / `box set`), work on the local copy, push
results back, evict.** A read-only mount is fine for browsing and one-off spot checks:

```bash
mkdir -p ~/box-mnt
rclone mount storagebox:alpacatrader/archive ~/box-mnt --read-only \
  --vfs-cache-mode full --vfs-cache-max-size 2G --dir-cache-time 10m --daemon
# … look around …
fusermount3 -u ~/box-mnt
```

---

## 2. Setup from zero (no secrets in git)

```bash
# 1. code
git clone <origin-url> ~/projects/Alpacatrader && cd ~/projects/Alpacatrader
uv sync                     # first time only

# 2. rclone (static binary, no root)
curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/r.zip
unzip -o /tmp/r.zip -d /tmp && install -m755 /tmp/rclone-*/rclone ~/.local/bin/rclone

# 3. SSH material — copy OUT OF BAND (password manager / USB), never through this repo:
#    ~/.ssh/storagebox_ed25519 (+ .pub), ~/.ssh/storagebox_known_hosts, and in ~/.ssh/config:
#      Host storagebox
#          HostName u678314.your-storagebox.de
#          User u678314
#          Port 23
#          IdentityFile ~/.ssh/storagebox_ed25519
#          UserKnownHostsFile ~/.ssh/storagebox_known_hosts
#          ServerAliveInterval 30
chmod 600 ~/.ssh/storagebox_ed25519

# 4. ~/.config/rclone/rclone.conf — key auth only, no password:
#      [storagebox]
#      type = sftp
#      shell_type = unix
#      md5sum_command = none
#      sha1sum_command = none
#      known_hosts_file = /home/<user>/.ssh/storagebox_known_hosts
#      host = u678314.your-storagebox.de
#      user = u678314
#      port = 23
#      key_file = /home/<user>/.ssh/storagebox_ed25519

# 5. project secrets: copy the repo's .env (ALPACA_*, STORAGEBOX_*) out of band, chmod 600.

# 6. sanity + link the cache into the paths the producers use
factory/scripts/box.sh ls                 # datasets on the box
factory/scripts/box.sh link               # ~/alpha-data, ~/sip, <repo>/data -> ~/boxcache/...
factory/scripts/box.sh set depth-min      # ~4.7 GB working set
```

`box link` never deletes data: a real directory holding only empty directories is replaced
(rmdir tree), a real directory WITH files is refused, and `--force` renames it to
`<name>.pre-link-<timestamp>` before creating the symlink. It is idempotent (re-runs print `ok`)
and also keeps the live bot state reachable: `BOT_FORWARD` in `factory/box.conf` (the Windows
`data/forward` directory) is linked as `$CACHE_ROOT/data/forward`, so `REPO/data -> cache`
never orphans the running bot. Nothing inside it is read, written or moved by `box`.

### How the cache is found by existing code (no edits to research producers)

| what code uses | resolves to |
|---|---|
| `Path.home()/"alpha-data"/...` (research root) | `~/alpha-data` → `$CACHE_ROOT/alpha-data` |
| `REPO/data` (e.g. `PANEL_DATA_ROOT`, `data/sip/net`) | `REPO/data` → `$CACHE_ROOT/data` |
| `/home/hillel/sip/net` (absolute refs in scripts) | `~/sip` → `$CACHE_ROOT/data/sip` |
| `data/forward/...` (live depth_shadow bot state, journals) | `$CACHE_ROOT/data/forward` → the Windows `data/forward` (BOT_FORWARD), untouched |

Datasets are extracted under `$CACHE_ROOT`, mirroring the path they were archived from, e.g.
`$CACHE_ROOT/alpha-data/open-search-v1/quote_universe_acquire/quotes/2022-03-01.parquet` or
`$CACHE_ROOT/data/sip/net/trades/`. Trees that no longer exist locally (old worktrees, stale
project copies, the point-in-time bot state) restore to `$CACHE_ROOT/restore/<dest-name>/`, so a
cold pull can never land on a live path. `$CACHE_ROOT` defaults to `~/boxcache`
(`factory/box.conf`, override with `CACHE_ROOT=...`), cap `CACHE_CAP_GB=20`.

The dest-name -> local path mapping is `factory/box_datasets.tsv`, the same table is on the box
README ("Dataset map"). `box ls` prints it per dataset.

---

## 3. Daily workflow

```bash
cd ~/projects/Alpacatrader

factory/scripts/box.sh status                 # what is local, GB used vs cap, link state
factory/scripts/box.sh set depth-min          # pull the working set (skips verified batches)
factory/scripts/box.sh pull alpha-data/open-search-v1/quote_universe_acquire --match 'quotes_2025-*'

uv run --no-sync factory/scripts/alpha_<lane>.py ...   # work happens locally

# push the day's outputs (archive mirrors the local path; --why goes into the box README)
factory/scripts/box.sh push ~/alpha-data/open-search-v1/<lane> \
  alpha-data/open-search-v1/<lane> --why "lane X, run 2026-10-29"
#   extra archiver flags go after the dest, e.g.  -- --jobs 2 --exclude '*.tmp'
#   local deletion is opt-in only:  --allow-delete-local

factory/scripts/box.sh evict --lru 5G         # free space, oldest pulls first
factory/scripts/box.sh evict <dest-name>      # or one dataset; --dry-run first if unsure
```

Pull is verify-first: every batch's sha256 is checked against the dataset's `MANIFEST.md` while
it streams, and a mismatch rolls the extracted files back. A batch pulled correctly is recorded
in `$CACHE_ROOT/.boxstate/` and skipped next time; `--force` re-pulls.

Working sets (`factory/box_working_sets.tsv`, sizes measured 2026-10-29):

```bash
factory/scripts/box.sh set --list        # state + size per set and per row
factory/scripts/box.sh set depth-min     # 4.9 GB: quotes 48 months + panel days + models + sizing
factory/scripts/box.sh set depth-bot     # 0.3 GB: models + sizing + days + SIP bars (live bot)
factory/scripts/box.sh set panel-data    # 10.1 GB: backfill + root__plain_007 + ml_features + pit
factory/scripts/box.sh set basket-all    # 5.3 GB: basket worktree + basket_reborn
factory/scripts/box.sh set cold-basket   # 16.3 GB: stale project copy + harvest01 (archaeology only)
factory/scripts/box.sh set sip-tape --dry-run   # 26.3 GB, larger than the cap: pull one month
```

Single-object datasets (no batches, no MANIFEST) restore with `box get`, which copies the object
and prints the command:

```bash
factory/scripts/box.sh get alpacatrader-git     # then: git clone $CACHE_ROOT/restore/alpacatrader-git/*.bundle <dir>
factory/scripts/box.sh get omp-sessions         # then: tar -xf $CACHE_ROOT/restore/omp-sessions/*.tar -C <dir>
```

### Batch names (read this before writing a --match)

- `<lane>__gz_001.tar.gz` holds the gzip-compressible files of a lane (json/txt/py),
  `<lane>__plain_001.tar` the already-compressed ones (parquet/npz/joblib); a lane usually has
  BOTH, so use `<lane>__*` unless you want one. The old `days__*`-style patterns match nothing.
- Month-scoped tape: `days_2023-01__plain_001.tar`, `bars_2025-02__plain_001.tar`,
  `quotes_2021-05__plain_001.tar`, `trades_2024-06__...` - the month is in the batch name.
- `windows-data` `root__plain_00N.tar` (7 x ~6.3 GB) and `backfill__plain_00N.tar` are split by
  SIZE, not by month: a single month cannot be pulled alone (the cheapest monthly-OHLCV proof is
  one 3.05 GiB `root__plain_007.tar`). Measured throughput: one 3 GB batch 21.5 MB/s; 200 small
  batches 5.3 MB/s; 106 tiny `bars_*` batches 1.1 MB/s (per-batch SSH/tar overhead dominates).
- `archive/alpha-data/open-search-v1` also carries the lane snapshot `delayed_sip_iex__*`, which
  extracts INTO `delayed_sip_iex/` - the same directory as the nested dataset
  `alpha-data/open-search-v1/delayed_sip_iex`. Pull one or the other, not both.
- Pulling a dest-name with no row in `factory/box_datasets.tsv` (e.g. after `box push`) extracts to
  `$CACHE_ROOT/restore/<dest-name>/`; add a row or use `--to DIR` to choose a live path.

---

## 4. Rules

1. **Never bulk-upload loose files** — `box push` streams `tar | gzip -1 | rclone rcat` batches and
   verifies sha256 on the box; per-file upload is ~20× slower.
2. **Never evict work that is not on the box.** `box evict` deletes only files recorded as extracted
   from a verified batch, and refuses when such a file was modified after the pull. Unmanaged files
   in the same directory are left alone and the directory is kept.
3. **Never `--delete-local` without an explicit decision** (`box push … --allow-delete-local`).
4. **Keep the local cache under the cap** — `box status` prints GB used vs cap; `box evict --lru N`
   is the pressure valve. The old machine's C: filled up this way.
5. **Secrets never go to the box**: `.env`, keys. Don't point `box push` at the repo root or at a
   worktree; push data directories only.
6. `storagebox:backup/` and `archive/` object names are off limits to ad-hoc writes; `box push`
   places datasets under `archive/<dest-name>/` for you.
7. Datasets whose archive is still being written have no `MANIFEST.md` yet and can't be pulled —
   `box pull` says so instead of guessing. Single-object datasets (`alpacatrader-git`,
   `omp-sessions`) have no batches at all: `box get <dest>`.
8. `flush_bot`'s kill switch is `data/KILL` (check: `factory/scripts/flush_bot.py`). It was archived
   inside `archive/windows-data/root__plain_001.tar` during the migration, so after `box link` the
   path does not exist: if flush_bot is ever launched from here, `touch data/KILL` first so it stops
   at startup. The running depth_shadow bot uses `data/forward/depth_bot/{KILL,ARMED}` instead,
   which lives in the untouched Windows state.
