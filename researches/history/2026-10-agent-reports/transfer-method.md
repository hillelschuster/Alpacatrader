# Transfer method: ~100+ GB → Hetzner Storage Box (read-only design)

Author: transfer-method investigation. No writes to the repo, no uploads, no deletions.
Nothing was created on the box (`_bench` was never made), so no cleanup was required.

---

## 0. Hard constraint on this report (read first)

This agent's toolset has **no shell**. So the requested live benchmark could **not be executed here**;
`ssh://storagebox/...` also failed ("Failed to start SSH master"), so the box could not be inspected
directly either. Everything below is therefore split into:

1. **facts** read from the repo/config/box rules (file:line anchored),
2. **one real measurement** already recorded by this project,
3. **external measurements** of the same service (forum/vendor bench),
4. **a ready-to-run 6-minute benchmark** whose numbers the executor plugs into the plan,
5. **estimates**, explicitly labelled, with the formula to re-derive them.

No fabricated throughput numbers.

---

## 1. Current method (what exists today)

`factory/scripts/archive_to_storagebox.sh`
- Destination `storagebox:alpacatrader/archive/<path relative to $HOME>`.
- Month-batched dirs (`raw/`, entries `YYYY-MM*`) → one batch per month; everything else → one
  `other.tar`.
- Stream: `tar -cf - "$@" | gzip -1 | tee >(sha256sum > file) | rclone rcat "…/$name.tar.gz"`.
- Verification: `ssh storagebox sha256sum` on the box vs the locally teed hash; `.ok` markers in
  `~/.cache/storagebox_archive/<rel>/` make it re-runnable.
- `--delete-local` only when all batches verified; writes `MANIFEST.md` + a row in the box README.
- Parallelism: **hardcoded `xargs -P 3`** (line 57), no sizing cap, no dry-run, no mode choice.

Box rules (`storagebox:alpacatrader/README.md`, mirrored in `AGENTS.md` §Storage):
one object per day/month, never thousands of loose files (per-file upload ≈20× slower),
sha256 before delete, MANIFEST + README row, `backup/` untouchable.

**Measured fact already in the project:** 25 GB raw → 3.5 GB on the box in **≈8 min with 3 streams**
(task brief / prior run). That is **≈7.3 MB/s on the wire (~58 Mbit/s)** and ≈52 MB/s raw ingest
(tar+gzip+source read). This is the only throughput number that exists and it is consistent with a
**~60 Mbit/s home uplink being the bottleneck**, not rclone.

## 2. Environment facts that drive the design

| Fact | Evidence |
|---|---|
| rclone v1.75.0 at `~/.local/bin/rclone` | `factory/STATE.md:239` |
| Remote = sftp, port 23, key auth, `md5sum_command = none`, `sha1sum_command = none`, no `transfers`/`concurrency` overrides | `~/.config/rclone/rclone.conf` |
| → rclone **cannot** verify checksums itself; integrity must stay `ssh … sha256sum` | same |
| `~/.ssh/config` Host storagebox: no `Compression` → gzip stream is not double-compressed | `~/.ssh/config` |
| **zstd NOT installed** (`/usr/bin` has only `gzip`, `xz`; no `pigz`, no `pv`, no `mbuffer`, no python `zstandard`) | glob of `/usr/bin`, `/usr/local/bin`, `~/.local/lib/python3*/site-packages`, `~/.local/bin` |
| Parquet is already compressed: writers use `compression="zstd", compression_level=3` | `factory/scripts/sip_ingest.py:122,130,204`; other producers use pyarrow defaults (snappy) |
| Box allows ~100 MB/s sequential writes; modern rclone single SFTP stream ≈36 MiB/s; **rclone ≤1.53 was 2 MB/s** (why v1.75 here is fine) | vendor bench + rclone forum 36750 |
| data/ mix: dozens of 300–600 MB monthly `ohlcv_*.parquet` / `clean_ohlcv_*.parquet` **plus >10 000 small files** (scratch_pattern 4 000+, leaderboard 2 800+, scratch_census 1 400+, iex_tape 660+, npz/jsonl) | directory listing of `/mnt/c/Users/הלל/…/Alpacatrader/data` |
| data/ parquet bytes ≈ **45–50 GiB** (estimate: ~60 `clean_ohlcv_*` × ~430 MB + ~60 `ohlcv_*` × ~350 MB + backfill/ml_features; confirm with `du`) | same listing |

Consequences:
- Compression must be **per-batch, content-based**: gzip only for text/jsonl/npz-json; **store raw
  tar for parquet/npz** (zstd-parquet, further deflate ≈0–5 % for 100 % CPU).
- Batch size must be **capped** (e.g. 8 GiB), because today `other.tar` for a 30 GB dir is a single
  object that a single network hiccup re-sends from byte 0.
- Small files must go inside batches (already the rule) — 10 k+ of them at ~50 KB each would be
  ~20× slower loose.

## 3. Benchmark to run (≈6 min, ~2.5 GiB total, self-cleaning)

Run as the executor (this agent cannot). Produces the missing table.

```bash
R=/home/hillel/.local/bin/rclone
B=storagebox:alpacatrader/_bench
$R mkdir "$B"          # once; parallel mkdir races fail on the box

bench() {              # $1 streams, $2 per-stream size
  local n=$1 sz=${2:-256M} t0 t1
  t0=$(date +%s.%N)
  seq "$n" | xargs -P "$n" -I@ sh -c "head -c $sz /dev/urandom | $R rcat $B/r{}.bin"
  t1=$(date +%s.%N)
  python3 -c "n=$n;sz='$sz';s=float('$sz'.rstrip('MG'))*1024**{'M':2,'G':3}['$sz'[-1]];e=$t1-$t0;print(f'rclone streams={n}: {n*s/e/1e6:.1f} MB/s aggregate ({s/e/1e6:.2f} per stream, {e:.0f}s)')"
}

# same matrix over plain ssh (no SFTP 32 KiB packet overhead) — decisive comparison
sshbench() {
  local n=$1 sz=${2:-256M} t0 t1
  t0=$(date +%s.%N)
  seq "$n" | xargs -P "$n" -I@ sh -c "head -c $sz /dev/urandom | ssh -o BatchMode=yes storagebox 'cat > alpacatrader/_bench/s{}.bin'"
  t1=$(date +%s.%N); echo "ssh-cat streams=$n: $t1 - $t0"
}

for n in 1 3 6; do bench $n 256M; done
for n in 1 3 6; do sshbench $n 256M; done      # ~2.5 GiB each way max, stop early if ≥1 tested faster

# real-data check: does tar+source-read keep up, and what does gzip -1 actually save?
cd "/mnt/c/Users/הלל/Desktop/algo projects/Alpacatrader/data"
time (tar -cf - clean_ohlcv_2025-10.parquet | $R rcat "$B/real-notar.bin")
time (tar -cf - clean_ohlcv_2025-10.parquet | gzip -1 | $R rcat "$B/real-gz.bin")
$R lsl "$B"     # raw parquet size vs stored-tar size vs gz size

# compression ratios (tail-cleanup: purge the whole prefix afterwards)
head -c 200M clean_ohlcv_2025-10.parquet | gzip -1 | wc -c      # expect ≈200 MB (±2%)
head -c 200M decisions.jsonl           | gzip -1 | wc -c        # text control, expect ≈25–40 MB
command -v zstd >/dev/null && head -c 200M decisions.jsonl | zstd -T0 -3 -c | wc -c
time (head -c 1G clean_ohlcv_2025-10.parquet | gzip -1 >/dev/null)   # gzip -1 MB/s on real data

$R purge "$B"   # cleanup, mandatory
```

Expected shape of the table (to be replaced by measured values):

| test | expectation | why |
|---|---|---|
| rclone rcat, 1 stream | ~5–35 MiB/s | forum: 36 MiB/s modern, 2 MiB/s on rclone ≤1.53 |
| rclone rcat, 3 streams | ≈3× single until the uplink saturates | 3 independent SSH/SFTP sessions |
| rclone rcat, 6 streams | ≈ uplink cap, maybe +10–20 % | box handles ~100 MB/s aggregate |
| ssh `cat >`, 1 stream | usually ≥ rclone rcat | no 32 KiB SFTP packet round-trips; plain SSH channel |
| tar + gzip -1 of a 400 MB parquet | gzip ≈ 100 % of input, ~60–120 MB/s | data already zstd-compressed |
| gzip -1 of jsonl | 5–8× smaller | text |
| zstd -3 vs gzip -1 (if installed) | zstd ~2× faster, ~15 % smaller | not installed → not worth adding |

**Decision rule after the bench:** streams = `ceil(single_stream_rate / aggregate_rate)` rounded up,
capped at 6; if `ssh cat` beats `rclone rcat` by >20 % at the winning stream count, switch the
archiver transport to `ssh storagebox "cat > <path>"` (same sha256 verification, one fewer moving
part). If aggregate ≈ single, you are uplink-bound: parallelism ≥3 buys nothing beyond hiding
tail latency, keep 3.

## 4. Compression recommendation

**Do not install zstd or pigz.** zstd is absent; `apt install zstd` is a new system dependency for
zero wall-clock gain on a ~60 Mbit/s uplink, and it would add a restore-time dependency on the new
machine. `xz` is present but far too slow for streaming.

| content | today | recommended | why |
|---|---|---|---|
| `*.parquet` (zstd-3/snappy, ~50 GiB here) | gzip -1 | **store (plain `tar`)** | already compressed; deflate buys 0–5 % and burns 1 core/stream. `head -c 200M` test above decides; expect ratio 1.00–1.05 |
| `*.npz/*.npy/*.zip/*.gz/*.zst/*.feather/*.arrow/png/jpg` | gzip -1 | **store** | same |
| `*.jsonl/*.json/*.csv/*.md/*.py/logs` (reports, ledgers, scribbles) | gzip -1 | **gzip -1** | 5–8× typical, gzip -1 is ~2–4× faster than -6 and within 5 % of its ratio on text |
| mixed dir | gzip -1 whole batch | **split batches by content class** (one `tar` per class) | one rule per object, no per-file decisions, restore stays trivial |

Why gzip -1 not -9: at 60 Mbit/s the compressor never bottlenecks; -1 keeps CPU free and its ratio
on JSONL is within a few percent.

## 5. Batching / parallelism recommendation

- **Batch target 4–8 GiB** (`--max-batch-bytes 8G`), hard cap. Failure cost ≤ ~8 GiB re-send
  (≈18 min at 7.5 MB/s), not 30 GB.
- **Split by content class and by month/day** (existing month rule is good); split `other` into
  `other-0001…` of ≤8 GiB. Never one giant tar for a 30 GB dir (today's behaviour).
- **Streams: 3** (same as today) until the bench says the uplink supports more; 4 only if
  aggregate/ single ≥ 3.5. Six only for the `_bench` test — real batches at 6 streams just add
  contention on a 60 Mbit/s link.
- **One SSH session per stream** is fine; do not raise `--sftp-concurrency` (it is a read-side knob).
- **Batches must be independent** so the existing `.ok` marker/resume logic keeps working: each
  batch one object, one sha256, one README row.
- **Parallel verification**: run `ssh storagebox sha256sum` for finished batches concurrently (box
  reads ~100 MB/s; 100 GB verification ≈ 17 min serial, ~5 min with 3–4 parallel).
- **Do not use `rclone copy` of loose files** anywhere (AGENTS.md: ≈20× slower).

## 6. Exact changes to `factory/scripts/archive_to_storagebox.sh`

Minimal diff, keeps the current contract (mirror path, one object per batch, sha256, MANIFEST,
README row, `--delete-local` last).

1. **Configurable parallelism** — replace `xargs -P 3` (line 57) with `JOBS=${JOBS:-3}` /
   `--jobs N`; log effective value in MANIFEST.
2. **Content-based store mode** — in `push()`, before the stream:
   ```bash
   if [ -z "$(find "$@" -type f ! -regex '.*\.\(parquet\|npz\|npy\|zip\|gz\|zst\|xz\|feather\|arrow\|png\|jpe\?g\)$' -print -quit)" ]; then
     mode=store; ext=tar
   else
     mode=gzip;  ext=tar.gz
   fi
   ```
   Stream `tar -cf - "$@" | tee >(sha256sum …) | rclone rcat …/$name.$ext` for store,
   `… | gzip -1 | tee …` for gzip. (Extension change is the only visible difference; see item 6.)
3. **Batch cap / split** — `--max-batch-bytes 8G`: build NUL-separated file lists
   (`find "$@" -type f -printf '%P\0'`) and split by accumulated `stat -c%s`; feed tar via
   `tar --null -T list -cf -` (also removes the current `$p*` glob / ARG_MAX fragility).
4. **`--dry-run`** — print `batch | files | bytes (du -sb / stat sum) | mode | est. minutes at
   last measured MB/s`; exit before `rclone mkdir`. Use it to confirm the map before the first
   upload.
5. **`--bench [size]`** — the §3 matrix (rclone rcat vs ssh cat, 1/3/6 streams), prints
   MB/s, writes `_bench` under `alpacatrader/_bench/`, and **purges it itself** in a `trap`.
6. **Manifest/restore must know the mode** — record `mode` per batch; restore snippet becomes:
   ```bash
   for f in $(rclone lsf storagebox:$DST); do
     case $f in
       *.tar.gz) rclone cat storagebox:$DST/$f | tar -xzf - ;;
       *.tar)    rclone cat storagebox:$DST/$f | tar -xf  - ;;
     esac
   done
   ```
   Also write an executable `RESTORE.sh` next to the batches (same text) so a new machine runs
   one command.
7. **Fix the sha race** — `tee >(sha256sum …)` can be compared before it flushes (today: `sleep 1`).
   Write to `$name.sha.tmp` and busy-wait until non-empty; also record the byte count
   (`tee` + `wc -c`) and require `rclone lsl` size == it, so a stale hash can never pass.
8. **Optional transport switch** — if §3 shows `ssh cat` faster, replace `rclone rcat` with
   `ssh -o BatchMode=yes storagebox "cat > alpacatrader/archive/$REL/$name.$ext"`. Keep
   `rclone mkdir`, `rclone lsf`, `rclone purge` (those are not throughput-critical).
9. **Guard** — refuse `--delete-local` when `$SRC` resolves through a symlink into `/mnt/c/...`
   unless a new `--confirm-windows-delete` is passed (today `realpath` hides that `data/` is the
   Windows copy, and `rm -rf` would delete the C: copy irreversibly).

## 7. Time estimate for ~100 GB

Formula: `hours = wire_GB / (MBps × 3.6)` + 15 % for verification/manifest/sshd setup.
Wire GB ≈ raw GB for parquet-heavy dirs (store mode), ≈ raw/2.5 for mixed text.

| uplink / aggregate (measured, plug in from §3) | 100 GB wire | with +15 % |
|---|---|---|
| 7.3 MB/s (project's recorded run, ~58 Mbit/s) | 3.8 h | **≈4.4 h** |
| 12 MB/s (~100 Mbit/s) | 2.3 h | ≈2.7 h |
| 20 MB/s (~160 Mbit/s) | 1.4 h | ≈1.6 h |
| 36 MB/s (box/forum practical ceiling) | 0.8 h | ≈0.9 h |

Text-heavy lanes compress ~6:1 → subtract ~40 % of the text share. If the measured aggregate saturates
at ≤1.5× single-stream, the uplink is the wall: **schedule one overnight run**, not several.
Everything here is an estimate except the 7.3 MB/s row, which derives from the project's own
25 GB/3.5 GB/8 min record.

## 8. New-machine restore

Setup (no secrets in git; `.env` and the key are gitignored and must travel out-of-band):

```bash
# 1. rclone (static, no root needed)
curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/r.zip && \
  unzip -o /tmp/r.zip -d /tmp && install -m755 /tmp/rclone-*/rclone ~/.local/bin/rclone
# 2. ssh material (copy from the old machine: USB / password manager — never via this repo)
#    ~/.ssh/storagebox_ed25519  (+ .pub), ~/.ssh/storagebox_known_hosts, the Host storagebox block
#    from ~/.ssh/config  (HostName u678314.your-storagebox.de, User u678314, Port 23, IdentityFile)
chmod 600 ~/.ssh/storagebox_ed25519
# 3. rclone.conf — only the [storagebox] block, no password (key auth):
#    type=sftp / host / user / port=23 / key_file=/home/<u>/.ssh/storagebox_ed25519
#    shell_type=unix / md5sum_command=none / sha1sum_command=none
#    known_hosts_file=/home/<u>/.ssh/storagebox_known_hosts
# 4. project secrets: copy the repo's .env (ALPACA_*, STORAGEBOX_*), chmod 600, never commit
# 5. sanity
rclone lsf storagebox:alpacatrader/archive/ | head
ssh -o BatchMode=yes storagebox 'ls alpacatrader'
```

Restore, two modes:
- **Pull the working set (recommended for compute).** Restore only the batches a session needs
  (one monthly parquet batch ≈ 0.4–0.5 GB, so a 6-month window ≈ 3 GB):
  `mkdir -p ~/restore && cd ~/restore && rclone cat storagebox:alpacatrader/archive/<rel>/<batch>.tar.gz | tar -xzf -`
  (or `.tar` + `tar -xf -`). Whole tree in one shot: `rclone cat …/RESTORE.sh | bash`.
  Proportional cost: 3 GB at 7.3 MB/s ≈ 7 min; full 100 GB ≈ 4 h.
- **Mount (browse/inspect only).**
  `rclone mount storagebox:alpacatrader ~/box --read-only --vfs-cache-mode full --vfs-cache-max-size 20G --dir-cache-time 24h --daemon`
  Fine for listing, sha256 spot checks and sequential parquet reads; **do not train/backtest off the
  mount** — 60–90 ms RTT turns column-selective parquet reads into many small ranged requests.
  Needs `fuse3` on the new box.

## 9. Risks / blockers

1. **No shell in this agent** → the §3 table is the executor's job; every rate above except the
   project's recorded 7.3 MB/s is an estimate. Do not ship the plan without running §3.
2. **Box connectivity unverified from here** (`ssh://` handler failed; the rclone remote was only
   read from config). Step 5 of §8 is the gate.
3. **`data/` is a symlink to C:** — `--delete-local` would `rm -rf` the Windows copy via `realpath`.
   Add the guard (change 9) before any delete.
4. **mingw/9p source read** (`/mnt/c`) adds metadata latency for the >10 k small files; keep them
   inside batches (never `rclone copy` loose), and expect tar traversal, not the uplink, to dominate
   the first minutes of a small-file batch.
5. **rclone cannot checksum** (`md5sum_command=none`): any `rclone check` is meaningless here; do not
   replace the `ssh sha256sum` step.
6. **`other.tar` monolith** is the main data-loss/time risk today (a 30 GB object re-sent on a
   hiccup) — the 8 GiB cap is the highest-value change.
7. **Compaction**: deleting inside WSL does not shrink `ext4.vhdx`; after the migration the disk win
   only materialises after a Windows-side `Optimize-VHD`/`diskpart compact` (out of scope here, but
   plan for it).
