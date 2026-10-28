# ArchiverUpgrade — archive_to_storagebox.sh migration upgrade

File: `factory/scripts/archive_to_storagebox.sh` (single bash script, rewritten; `AGENTS.md` Storage
bullet updated in place). No new helper scripts under the repo; the test harness lived in /tmp and was
removed.

## Interface

```
archive_to_storagebox.sh <local_dir> "<why>" [--dest-name NAME] [--max-batch-bytes SIZE]
                         [--jobs N] [--exclude PATTERN]... [--dry-run] [--delete-local]
```

* `--dest-name NAME` — destination under `storagebox:alpacatrader/archive/NAME`; required when the source
  is not under `$HOME` (the Windows `/mnt/c/...` tree). Default stays the `$HOME`-relative mirror.
  Validated: no `..`, no `//`, never `backup/`.
* `--max-batch-bytes` (default 6G, K/M/G/T) — packs a directory's files greedily by cumulative size
  from a NUL-separated `find` list, sorted by path, so batch membership is deterministic.
* `--jobs` (default 3) — parallel upload+verify workers (`xargs -P`).
* `--exclude` repeatable — basename glob, or a path glob if it contains `/`.
  Always excluded: `.env`, `.env.*`, `*.pem`, `id_rsa*`, `id_ed25519*`, and `.git`, `__pycache__`,
  `.pytest_cache`, `.ruff_cache`, `node_modules`, `.venv` at any depth.
* `--dry-run` — batch table (name, kind, files, bytes) + totals + estimated upload/time at 8 MiB/s.
* `--delete-local` — only after every batch verified; refuses when archivable symlinks escape the source.

## Batch model

* Kinds: extensions `parquet npz pt zip gz zst feather joblib` -> **plain `.tar`** (gzip buys ~0%);
  everything else -> **`.tar.gz`** with `gzip -1`. The two kinds are never mixed in one file, and a
  group emits at most one stream per kind (more only when the size cap forces a split).
* Names: `<group>__<plain|gz>_<NNN>`, 3-digit per (group, kind). Deterministic => re-runs rebuild the
  identical plan and skip everything already verified.
* Grouping: a top-level dir whose children look like `YYYY-MM*` is batched per month
  (`<dir>__<YYYY-MM>__gz_001`), with any non-month sibling collected into `<dir>__other__gz_001`
  (the old script silently dropped those). Other top-level dirs are one group; loose files at the
  source root form `root`. Nested `YYYY-MM*` dirs deeper than one level are not month-split (only the
  top level, as before).
* Empty directories are not archived (files-only lists).

## Verification / resume

* Each batch: `tar --null -T list -cf - [| gzip -1] | tee >(sha256sum) >(wc -c) | rclone rcat` — no
  local temp copy. The two `tee` writers are reaped with `wait` before the sha/size files are read
  (removes the old tee race), then compared with `ssh storagebox sha256sum` **and** `stat` size.
  Any pipe failure, empty hash or mismatch fails the batch; one retry, then reported.
* Markers: `~/.cache/storagebox_archive/<dest-name>/{<batch>.ok,.sha,.size,.meta,plan.tsv,lists/}`.
  A marker only counts if the file is still on the box with the recorded size (one `rclone lsf --format sp`).
* Resume proof: second run printed `9 already verified, 0 to upload`, `verified 9/9`, 0s of streaming.
* On full verification: `MANIFEST.md` (kind/files/source GiB/archive GiB/sha256 per batch) and
  `RESTORE.sh` (`rclone cat ... | tar -xf -` / `-xzf -`) are written into the archive dir, and one README
  row is appended (idempotent — the old script's `grep` compared a string it never wrote, so it
  duplicated rows; fixed to the actual `archive/<dest>/` cell).

## Accepted risk / notes

* Batch names changed vs the old script (`other.tar.gz` -> `root__gz_001.tar.gz` etc.). The one
  pre-existing archive (`archive/alpha-data/open-search-v1/delayed_sip_iex`) keeps its old names; its
  local state markers were rebuilt from its box MANIFEST (29/29 `.ok`+`.sha`), but re-running that old
  command under the new script would re-upload under new names. Do not re-run it casually.
* `--delete-local` deletes only regular files that were archived, removes only dirs left empty, and
  prints what remains (excluded files, symlinks). It never calls `rm -rf`.
* Throughput measured here: single 64 MiB stream 6.0 MiB/s; the 1.45 GiB acceptance run moved 0.97 GiB
  in 285 s = **3.5 MiB/s aggregate with `--jobs 3`** (two ~500 MB plain batches in parallel, each
  ~2 MiB/s). Parallel streams did not beat one stream on this uplink; `--jobs 2` or 1 is worth trying for
  the multi-hundred-GB Windows move, and the dry-run estimate assumes 8 MiB/s.
* `stat`/`sha256sum` on the box need the restricted-shell forms used here (no pipes on port 23).
