#!/usr/bin/env python3
"""box - work on Alpacatrader data that lives on the Hetzner Storage Box.

Decision this tool encodes (measured 2026-10-28, see NEW_MACHINE.md):
the box is ~10-14 MB/s downlink and ~60-90 ms away, so a FUSE/network mount turns a
column-selective parquet read into hundreds of 0.6 s round-trips. Therefore: PULL the
batches you need into a local cache, work on the local copy, push results back, evict.
A read-only mount is only for browsing (the exact command is in the docs).

Layout: every dataset is extracted under CACHE_ROOT mirroring the local path it was
archived from; `box link` points ~/alpha-data, ~/sip and <repo>/data at that cache so the
research producers find their data without any code change.

Commands
  box ls [dest]                  list datasets (dest -> cache path -> size) or one dataset;
                                 `box ls live:<root>` for an un-archived live tree
  box pull <dest> [--match GLOB] [--to DIR] [--dry-run] [--force]
                                 download + extract matching batches, sha256-verified against
                                 the dataset's MANIFEST.md, skipping batches already verified
  box get <dest> [--to DIR] [--dry-run]
                                 copy a single-object dataset (git bundle, session tar) and
                                 print its restore command; no batches, no MANIFEST
  box set <name> [--dry-run]     pull every row of factory/box_working_sets.tsv for <name>
  box set --list                 show the working sets, their size and readiness
  box status                     what is extracted, GB used vs cap, link state
  box evict <dest> [--force]     delete extracted files of one dataset (only files that are
                                 verified present on the box; never un-archived local work)
  box evict --lru N              evict least-recently-pulled datasets until N GB are freed
  box push <dir> <dest> [--why TEXT] [-- ...]
                                 wrapper for factory/scripts/archive_to_storagebox.sh
                                 (never --delete-local unless --allow-delete-local is given)
  box link [--force] [--dry-run] (re)create the cache symlinks: ~/alpha-data, ~/sip and
                                 <repo>/data; --force moves a non-empty real directory aside
                                 instead of failing; BOT_FORWARD is kept as
                                 $CACHE_ROOT/data/forward so the live bot state survives the
                                 data relink

Config: factory/box.conf (REMOTE, CACHE_ROOT, CACHE_CAP_GB, LINKS, BOT_FORWARD), every key
overridable by an env var of the same name. Dataset -> local path mapping:
factory/box_datasets.tsv (dest-names under alpha-data/... map automatically; cold restores
go under $CACHE_ROOT/restore/).

Exit codes: 0 ok, 1 failure (verification/transfer), 2 usage or safety refusal.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

PROG = "box"
REPO = Path(__file__).resolve().parents[2]
CONF_PATH = REPO / "factory" / "box.conf"
DATASETS_TSV = REPO / "factory" / "box_datasets.tsv"
SETS_TSV = REPO / "factory" / "box_working_sets.tsv"
STATE_DIRNAME = ".boxstate"
GB = 10**9

CONF_DEFAULTS = {
    "REMOTE": "storagebox:alpacatrader",
    "RCLONE": "rclone",
    "CACHE_ROOT": "~/boxcache",
    "CACHE_CAP_GB": "20",
    "LINKS": "alpha-data,sip,repo-data",
    "BOT_FORWARD": "",
    "RCLONE_FLAGS": "",
}


# --------------------------------------------------------------------------- helpers


def die(msg: str, code: int = 2) -> NoReturn:
    print(f"{PROG}: {msg}", file=sys.stderr)
    raise SystemExit(code)


def note(msg: str) -> None:
    print(msg, flush=True)


def human(n: float) -> str:
    """Bytes -> '1.23 GiB'."""
    for unit, size in (("TiB", 1024**4), ("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if abs(n) >= size:
            return f"{n / size:.2f} {unit}"
    return f"{n:.0f} B"


def parse_gb(text: str) -> float:
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([KMGT]?)i?B?\s*", text, re.IGNORECASE)
    if not m:
        die(f"bad size: {text!r} (use e.g. 500M, 5G)")
    val, unit = float(m.group(1)), m.group(2).upper()
    return val * {"": 1 / GB, "K": 10**3 / GB, "M": 10**6 / GB, "G": 1, "T": 10**3}[unit]


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_conf() -> dict:
    conf = dict(CONF_DEFAULTS)
    if CONF_PATH.is_file():
        for raw in CONF_PATH.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, val = line.split("=", 1)
            conf[key.strip()] = val.strip()
    for key in list(conf):
        if key in os.environ and os.environ[key].strip() != "":
            conf[key] = os.environ[key]
    root = os.path.expandvars(os.path.expanduser(conf["CACHE_ROOT"]))
    conf["CACHE_ROOT"] = str(Path(root).absolute())
    try:
        conf["CACHE_CAP_BYTES"] = float(conf["CACHE_CAP_GB"]) * GB
    except ValueError:
        die(f"CACHE_CAP_GB is not a number: {conf['CACHE_CAP_GB']!r}")
    return conf


CONF = load_conf()
CACHE = Path(CONF["CACHE_ROOT"])
STATE = CACHE / STATE_DIRNAME


def cache_path(*parts: str) -> Path:
    return CACHE.joinpath(*parts)


# --------------------------------------------------------------------------- rclone


def rclone(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [CONF["RCLONE"], *args]
    flags = CONF.get("RCLONE_FLAGS", "")
    if flags:
        cmd[1:1] = shlex.split(flags)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"rclone {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}", 1)
    return proc


_LS: dict[str, int] | None = None


def remote_map() -> dict[str, int]:
    """Every object under REMOTE -> size in bytes (one listing for the whole invocation)."""
    global _LS
    if _LS is None:
        proc = rclone(["lsjson", "-R", "--files-only", CONF["REMOTE"] + "/"])
        try:
            rows = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            die("could not parse `rclone lsjson` output", 1)
        _LS = {r["Path"]: int(r.get("Size") or 0) for r in rows}
    return _LS


def archive_listing(dest: str) -> dict[str, int] | None:
    """Batch files of one archive dataset (name -> size), or None if it does not exist.

    One non-recursive listing of just that directory: much cheaper than remote_map().
    """
    proc = rclone(["lsjson", CONF["REMOTE"] + f"/archive/{dest}"], check=False)
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    out: dict[str, int] = {}
    for row in rows:
        name = row.get("Path", "")
        if not name or row.get("IsDir") or "/" in name:
            continue
        if name in ("MANIFEST.md", "RESTORE.sh", "README.md"):
            continue
        out[name] = int(row.get("Size") or 0)
    return out


def read_manifest(dest: str) -> dict[str, str] | None:
    """Batch name -> sha256 from archive/<dest>/MANIFEST.md, or None when there is none."""
    path = f"{CONF['REMOTE']}/archive/{dest}/MANIFEST.md"
    proc = rclone(["cat", path], check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return parse_manifest(proc.stdout)


def archive_manifest(dest: str) -> dict[str, str]:
    """Batch name -> sha256 for an archive dataset; dies when the manifest is missing."""
    batches = read_manifest(dest)
    if batches is None:
        die(
            f"no MANIFEST.md at archive/{dest} on the box: cannot verify a pull.\n"
            f"  (single-object datasets: `box get {dest}`; in-progress archives have no manifest\n"
            f"   yet; live data: `rclone copy {CONF['REMOTE']}/{dest} <dir> --include '<glob>'`)",
            1,
        )
    return batches


def parse_manifest(text: str) -> dict[str, str]:
    batches: dict[str, str] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        sha = next((c for c in cells if re.fullmatch(r"[0-9a-f]{64}", c)), None)
        if sha and cells and cells[0] and cells[0].lower() not in ("batch", ""):
            batches[cells[0]] = sha
    return batches


# --------------------------------------------------------------------------- datasets


_ROW = {"bytes": 0, "batches": 0, "manifest": False, "live": False}


def dataset_table() -> dict[str, dict]:
    """dest-name -> {bytes, batches, manifest, live} for everything on the box."""
    table: dict[str, dict] = {}
    for path, size in remote_map().items():
        parts = path.split("/")
        if not parts:
            continue
        if parts[0] == "archive" and len(parts) >= 3:
            dest = "/".join(parts[1:-1])
            row = table.setdefault(dest, dict(_ROW))
            row["bytes"] += size
            if parts[-1] in ("MANIFEST.md", "RESTORE.sh", "README.md"):
                row["manifest"] = row["manifest"] or parts[-1] == "MANIFEST.md"
            elif parts[-1].endswith((".tar", ".tar.gz")):
                row["batches"] += 1
        elif parts[0] in ("alpha-data", "data") and len(parts) >= 3:
            # Live (un-archived) trees have no MANIFEST/batches: keep them in their own row so
            # `box pull alpha-data/...` and `box set` never confuse them with archive/<same name>.
            dest = "live:" + "/".join(parts[:3])
            row = table.setdefault(dest, dict(_ROW, live=True))
            row["bytes"] += size
            row["batches"] += 1
    return table


def load_dataset_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not DATASETS_TSV.is_file():
        return mapping
    for raw in DATASETS_TSV.read_text().splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) < 2 or not cells[0].strip() or not cells[1].strip():
            die(f"{DATASETS_TSV.name}: need 'dest-name<TAB>cache-path<TAB>note': {line!r}")
        mapping[cells[0].strip()] = cells[1].strip()
    return mapping


def local_root_for(dest: str, to: str | None) -> Path:
    """Where an archive dataset is extracted locally."""
    if to:
        return Path(os.path.expandvars(os.path.expanduser(to))).absolute()
    if dest.startswith("live:"):
        die(
            f"{dest} is a live (un-archived) tree: copy what you need with\n"
            f"  rclone copy {CONF['REMOTE']}/{dest[5:]} <dir> --include '<glob>'"
        )
    if dest.startswith("alpha-data/"):
        return cache_path(dest)
    mapping = load_dataset_map()
    if dest in mapping:
        return cache_path(mapping[dest])
    # Unmapped dest-name (e.g. a one-off `box push`): restore it as a cold tree, never onto a
    # live path. Add a row to box_datasets.tsv to control the location, or pass --to DIR.
    root = cache_path("restore", dest)
    note(f"note: {dest!r} has no row in {DATASETS_TSV.name}; using {root} (--to DIR overrides)")
    return root


def cache_rel(dest: str) -> str:
    """Cache-relative local path for a dest-name (no notes, unlike local_root_for)."""
    if dest.startswith("live:"):
        return dest[len("live:") :]
    if dest.startswith("alpha-data/"):
        return dest
    mapped = load_dataset_map().get(dest)
    if mapped:
        return mapped
    return str(Path("restore") / dest)


# --------------------------------------------------------------------------- markers


def marker_dir(dest: str) -> Path:
    return STATE / "extracted" / dest


def marker_path(dest: str, batch: str) -> Path:
    return marker_dir(dest) / f"{batch}.json"


def read_marker(dest: str, batch: str) -> dict | None:
    path = marker_path(dest, batch)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def write_marker(dest: str, batch: str, data: dict) -> None:
    path = marker_path(dest, batch)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def extracted_datasets() -> dict[str, list[dict]]:
    """dest -> list of markers, newest pull first."""
    out: dict[str, list[dict]] = {}
    root = STATE / "extracted"
    if not root.is_dir():
        return out
    for mpath in sorted(root.rglob("*.json")):
        try:
            data = json.loads(mpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        dest = str(mpath.parent.relative_to(root))
        data["_path"] = str(mpath)
        data["_mtime"] = mpath.stat().st_mtime
        out.setdefault(dest, []).append(data)
    return out


def cache_usage() -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(CACHE):
        for name in filenames:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, name)).st_size
    return total


# --------------------------------------------------------------------------- pull


def batch_entries_from_tar_listing(names_file: str) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    try:
        text = Path(names_file).read_text(errors="replace")
    except OSError:
        return entries
    for line in text.splitlines():
        name = line.strip()
        if not name or name.startswith("/") or ".." in Path(name).parts:
            continue
        while name.startswith("./"):
            name = name[2:]
        if name and name not in seen:
            seen.add(name)
            entries.append(name)
    return entries


def pull_batch(dest: str, batch: str, sha: str, root: Path) -> int:
    """Stream one batch through tar into root, verify sha256, record a marker."""
    url = f"{CONF['REMOTE']}/archive/{dest}/{batch}"
    tar_flags = "-xzvf" if batch.endswith(".gz") else "-xvf"
    root.mkdir(parents=True, exist_ok=True)
    fd_names, names_path = tempfile.mkstemp(prefix="box-names-", suffix=".list")
    os.close(fd_names)
    fd_hash, hash_path = tempfile.mkstemp(prefix="box-sha-", suffix=".hex")
    os.close(fd_hash)
    cmd = (
        "set -o pipefail; "
        f"{shlex.quote(CONF['RCLONE'])} cat "
        f"{' '.join(shlex.quote(f) for f in shlex.split(CONF.get('RCLONE_FLAGS', '')))} "
        f"{shlex.quote(url)} "
        f"| tee >(sha256sum | cut -d' ' -f1 > {shlex.quote(hash_path)}) "
        f"| tar {tar_flags} - -C {shlex.quote(str(root))} > {shlex.quote(names_path)}"
    )
    started = time.time()
    proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    entries = batch_entries_from_tar_listing(names_path)

    got = ""
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            got = Path(hash_path).read_text().strip()
        except OSError:
            got = ""
        if re.fullmatch(r"[0-9a-f]{64}", got):
            break
        time.sleep(0.1)
    for p in (names_path, hash_path):
        Path(p).unlink(missing_ok=True)

    def _rollback() -> None:
        for rel in entries:
            target = root / rel
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
            except OSError:
                pass

    if proc.returncode != 0:
        _rollback()
        die(f"{batch}: tar/rclone failed: {(proc.stderr or proc.stdout).strip()[:400]}", 1)
    if got != sha:
        _rollback()
        die(
            f"{batch}: sha256 mismatch (expected {sha}, got {got or '<none>'}) "
            f"- extracted files rolled back",
            1,
        )

    bytes_written = sum((root / rel).stat().st_size for rel in entries if (root / rel).is_file())
    write_marker(
        dest,
        batch,
        {
            "batch": batch,
            "sha256": sha,
            "root": str(root),
            "pulled_at": now_iso(),
            "seconds": round(time.time() - started, 1),
            "bytes": bytes_written,
            "files": entries,
        },
    )
    rate = bytes_written / max(time.time() - started, 0.001) / 1e6
    note(f"  ok  {batch}  {human(bytes_written)}  ({rate:.1f} MB/s on disk, {len(entries)} files)")
    return bytes_written


def select_batches(
    dest: str, batches: dict[str, str], patterns: list[str], sizes: dict[str, int]
) -> dict[str, str]:
    missing = [name for name in batches if name not in sizes]
    if missing:
        die(
            f"MANIFEST.md of archive/{dest} lists {len(missing)} batch(es) that are not on the box "
            f"(e.g. {missing[0]}); archive incomplete or being written - refusing",
            1,
        )
    if not patterns:
        return batches
    chosen: dict[str, str] = {}
    for pat in patterns:
        matched = {n: s for n, s in batches.items() if fnmatch.fnmatch(n, pat)}
        if not matched:
            sample = ", ".join(sorted(batches)[:5])
            die(f"--match {pat!r} matched no batch of archive/{dest} (e.g. {sample} ...)")
        chosen.update(matched)
    return chosen


def cmd_pull(argv: list[str], *, warn_cap: bool = True) -> int:
    dest, patterns, to, dry, force = "", [], None, False, False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--match":
            i += 1
            patterns.append(argv[i] if i < len(argv) else die("--match needs a glob"))
        elif arg == "--to":
            i += 1
            to = argv[i] if i < len(argv) else die("--to needs a directory")
        elif arg in ("--dry-run", "-n"):
            dry = True
        elif arg == "--force":
            force = True
        elif arg.startswith("-"):
            die(f"unknown option {arg} (see `box --help`)")
        elif not dest:
            dest = arg.rstrip("/")
        else:
            die(f"unexpected argument {arg!r}")
        i += 1
    if not dest:
        die("usage: box pull <dest-name> [--match GLOB] [--to DIR] [--dry-run] [--force]")
    if dest.startswith("live:"):
        dest = dest[len("live:") :]
        die(
            f"{dest} is a live (un-archived) tree: copy what you need with\n"
            f"  rclone copy {CONF['REMOTE']}/{dest} <dir> --include '<glob>'"
        )

    sizes = archive_listing(dest)
    if sizes is None:
        live = rclone(["lsf", "--max-depth", "1", CONF["REMOTE"] + f"/{dest}"], check=False)
        if live.returncode == 0 and live.stdout.strip():
            die(
                f"{dest!r} is a LIVE dataset (no archive/, no batches, no manifest): copy what "
                f"you need with `rclone copy {CONF['REMOTE']}/{dest} <dir> --include '<glob>'`"
            )
        die(f"no archive/{dest} on {CONF['REMOTE']} (see `box ls`)")
    manifest = read_manifest(dest)
    if manifest is None:
        die(
            f"archive/{dest} holds single object(s) only ({', '.join(sorted(sizes))}): no batches, "
            f"no MANIFEST, so there is nothing to verify against. Restore it with\n"
            f"  box get {dest}",
            1,
        )

    root = local_root_for(dest, to)
    batches = select_batches(dest, manifest, patterns, sizes)

    plan: list[tuple[str, str, int, str]] = []
    for name in sorted(batches):
        marker = read_marker(dest, name)
        fresh = bool(marker) and marker.get("sha256") == batches[name] and not force
        plan.append((name, batches[name], sizes[name], "skip" if fresh else "pull"))

    pull_bytes = sum(size for _n, _s, size, state in plan if state == "pull")
    note(f"dataset  archive/{dest}")
    note(f"local    {root}")
    note(
        f"batches  {sum(1 for p in plan if p[3] == 'pull')} to pull, "
        f"{sum(1 for p in plan if p[3] == 'skip')} already verified "
        f"({human(pull_bytes)} to download)"
    )
    if dry:
        for name, sha, size, state in plan:
            if state == "pull":
                note(f"  {name:<44} {human(size):>10}  {sha[:12]}")
        return 0
    if not pull_bytes:
        note("nothing to do")
        return 0

    used_before = cache_usage()
    if warn_cap and used_before + pull_bytes > CONF["CACHE_CAP_BYTES"]:
        note(
            f"warning: cache would exceed the {CONF['CACHE_CAP_GB']} GB cap "
            f"({human(used_before)} used + {human(pull_bytes)}); "
            f"run `box evict --lru N` first or raise CACHE_CAP_GB"
        )

    total = 0
    for name, sha, _size, state in plan:
        if state == "skip":
            note(f"  skip {name} (verified {read_marker(dest, name).get('pulled_at', '?')})")
            continue
        total += pull_batch(dest, name, sha, root)
    note(f"done: {human(total)} extracted under {root}")
    return 0


def cmd_get(argv: list[str]) -> int:
    """box get <dest> [--to DIR] [--dry-run] - copy a single-object dataset (no batches)."""
    dest, to, dry = "", None, False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--to":
            i += 1
            to = argv[i] if i < len(argv) else die("--to needs a directory")
        elif arg in ("--dry-run", "-n"):
            dry = True
        elif arg.startswith("-"):
            die(f"unknown option {arg} (see `box --help`)")
        elif not dest:
            dest = arg.rstrip("/")
        else:
            die(f"unexpected argument {arg!r}")
        i += 1
    if not dest:
        die("usage: box get <dest-name> [--to DIR] [--dry-run]")

    sizes = archive_listing(dest)
    if sizes is None:
        die(f"no archive/{dest} on {CONF['REMOTE']} (see `box ls`)")
    manifest = read_manifest(dest) or {}
    if manifest:
        die(
            f"archive/{dest} has {len(manifest)} verified batch(es); pull them with "
            f"`box pull {dest} [--match GLOB]` instead"
        )
    root = local_root_for(dest, to)
    note(f"dataset  archive/{dest}")
    note(f"local    {root}")
    total = 0
    for name in sorted(sizes):
        target = root / name
        marker = read_marker(dest, name)
        if (
            marker
            and marker.get("kind") == "object"
            and target.is_file()
            and marker.get("bytes") == target.stat().st_size
        ):
            note(f"  skip {name} (present, {human(target.stat().st_size)})")
            continue
        if dry:
            note(f"  would copy {name}  {human(sizes[name])}")
            continue
        started = time.time()
        target.parent.mkdir(parents=True, exist_ok=True)
        rclone(["copyto", f"{CONF['REMOTE']}/archive/{dest}/{name}", str(target)])
        got = target.stat().st_size if target.is_file() else -1
        if got != sizes[name]:
            target.unlink(missing_ok=True)
            die(f"{name}: size mismatch (expected {sizes[name]}, got {got}); file removed", 1)
        write_marker(
            dest,
            name,
            {
                "batch": name,
                "kind": "object",
                "sha256": None,
                "root": str(root),
                "pulled_at": now_iso(),
                "seconds": round(time.time() - started, 1),
                "bytes": got,
                "files": [name],
            },
        )
        total += got
        note(
            f"  ok  {name}  {human(got)}  ({got / max(time.time() - started, 1e-6) / 1e6:.1f} MB/s)"
        )
    if dry:
        return 0
    note(f"done: {human(total)} under {root}")
    for name in sorted(sizes):
        if name.endswith(".bundle"):
            note(f"  restore: git clone {root / name} <dir>")
        elif name.endswith((".tar", ".tar.gz")):
            note(f"  restore: mkdir -p <dir> && tar -xf {root / name} -C <dir>")
    return 0


# --------------------------------------------------------------------------- sets


def load_sets() -> dict[str, list[dict]]:
    if not SETS_TSV.is_file():
        die(f"missing {SETS_TSV}")
    sets: dict[str, list[dict]] = {}
    for raw in SETS_TSV.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cells = raw.split("\t")
        if len(cells) < 4:
            die(f"{SETS_TSV.name}: need 'set<TAB>dest<TAB>glob<TAB>est_gb<TAB>note': {raw!r}")
        name, dest, glob, est = (c.strip() for c in cells[:4])
        rows = sets.setdefault(name, [])
        rows.append(
            {
                "dest": dest,
                "glob": glob,
                "est_gb": float(est or 0),
                "note": cells[4].strip() if len(cells) > 4 else "",
            }
        )
    return sets


def cmd_set(argv: list[str]) -> int:
    name, dry, force, list_only = "", False, False, False
    for arg in argv:
        if arg in ("--list", "-l"):
            list_only = True
        elif arg in ("--dry-run", "-n"):
            dry = True
        elif arg == "--force":
            force = True
        elif arg.startswith("-"):
            die(f"unknown option {arg}")
        elif not name:
            name = arg
        else:
            die(f"unexpected argument {arg!r}")

    sets = load_sets()
    if list_only or not name:
        on_box = dataset_table()
        for set_name in sorted(sets):
            rows = sets[set_name]
            present = sum(r["est_gb"] for r in rows if r["dest"] in on_box)
            pending = sorted({r["dest"] for r in rows if r["dest"] not in on_box})
            state = "ready" if not pending else ("partial" if present else "pending")
            note(
                f"{set_name:<18} {state:<8} ~{present:6.2f} GB now"
                + (f", pending archive: {', '.join(pending)}" if pending else "")
            )
            for r in rows:
                glob = r["glob"] or "*"
                mark = "ready  " if r["dest"] in on_box else "PENDING"
                note(f"    {mark} {r['dest']:<52} {glob:<24} {r['est_gb']:6.2f} GB  {r['note']}")
        return 0
    if name not in sets:
        die(f"unknown working set {name!r} (known: {', '.join(sorted(sets))})")

    on_box = dataset_table()
    groups: dict[str, list[str]] = {}
    pending: dict[str, list[str]] = {}
    for row in sets[name]:
        if row["dest"] not in on_box:
            pending.setdefault(row["dest"], []).append(row["glob"] or "*")
            continue
        groups.setdefault(row["dest"], []).append(row["glob"] or "*")
    for dest, globs in pending.items():
        note(f"pending archive, skipped: {dest}  (batches {', '.join(globs)} not on the box yet)")

    total_est = sum(r["est_gb"] for r in sets[name] if r["dest"] in on_box)
    note(
        f"working set {name}: ~{total_est:.2f} GB in {len(groups)} dataset(s) "
        f"(cap {CONF['CACHE_CAP_GB']} GB)"
    )
    if total_est > CONF["CACHE_CAP_BYTES"] / GB and not (force or dry):
        die(
            f"refusing: {name} needs ~{total_est:.2f} GB but the cache cap is "
            f"{CONF['CACHE_CAP_GB']} GB; pull the rows you need with `box pull <dest> --match "
            f"GLOB` (see `box set --list`), raise CACHE_CAP_GB, or pass --force",
            2,
        )
    rc = 0
    for dest, globs in groups.items():
        args = [dest]
        for g in globs:
            args += ["--match", g]
        if dry:
            args.append("--dry-run")
        if force:
            args.append("--force")
        note(f"--- {dest}")
        rc = max(rc, cmd_pull(args, warn_cap=False))
    return rc


# --------------------------------------------------------------------------- ls / status


def cmd_ls(argv: list[str]) -> int:
    if not argv:
        table = dataset_table()
        extracted = extracted_datasets()
        if not table:
            note("no datasets on the box")
            return 0
        note(
            f"{'DATASET':<50} {'KIND':<7} {'ON BOX':>10} {'FILES':>5} {'LOCAL':>9}  "
            f"{'-> CACHE_ROOT'}"
        )
        for dest in sorted(table):
            row = table[dest]
            local = sum(m.get("bytes", 0) for m in extracted.get(dest, []))
            note(
                f"{dest:<50} {'live' if row['live'] else 'archive':<7} {human(row['bytes']):>10} "
                f"{row['batches']:>5} {(human(local) if local else '-'):>9}  "
                f"{cache_rel(dest) or '?'}"
            )
        return 0

    dest = argv[0].rstrip("/")
    if dest.startswith("live:"):
        dest = dest[len("live:") :]
        return ls_live(dest)
    sizes = archive_listing(dest)
    if sizes is not None:
        batches = read_manifest(dest) or {}
        note(f"archive/{dest}  ({len(sizes)} objects, {human(sum(sizes.values()))})")
        note(f"local    {local_root_for(dest, None)}")
        note(f"{'BATCH':<46} {'SIZE':>10}  STATE")
        for name in sorted(sizes):
            marker = read_marker(dest, name)
            if not batches:
                state = "object (box get)"
                if marker and marker.get("kind") == "object":
                    state = (
                        "object copied"
                        if marker.get("bytes") == sizes[name]
                        else "object (size changed)"
                    )
            elif name in batches:
                state = (
                    "verified"
                    if marker and marker.get("sha256") == batches[name]
                    else ("extracted (manifest changed)" if marker else "not pulled")
                )
            elif name.endswith((".tar", ".tar.gz")):
                state = "NOT IN MANIFEST"
            else:
                state = "extra file (not a batch)"
            note(f"{name:<46} {human(sizes[name]):>10}  {state}")
        return 0
    return ls_live(dest)


def ls_live(dest: str) -> int:
    """List an un-archived (live) tree, one level down, with sizes."""
    prefix = f"{dest}/"
    rows = {p: s for p, s in remote_map().items() if p.startswith(prefix)}
    if not rows:
        die(f"nothing at {CONF['REMOTE']}/{dest} (see `box ls`)")
    agg: dict[str, list[int]] = {}
    for path, size in rows.items():
        child = path[len(prefix) :].split("/")[0]
        agg.setdefault(child, [0, 0])
        agg[child][0] += size
        agg[child][1] += 1
    note(f"live: {CONF['REMOTE']}/{dest}  ({human(sum(rows.values()))})")
    note(f"local (after rclone copy)  {CACHE / dest}")
    for child, (size, count) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        note(f"  {child:<52} {human(size):>10} {count:>6} files")
    note(
        f"live data has no MANIFEST/batches: copy it with\n"
        f"  rclone copy {CONF['REMOTE']}/{dest} {CACHE / dest} --include '<glob>'"
    )
    return 0


def link_targets() -> list[tuple[str, Path, Path]]:
    """(name, link path, cache target) for the links enabled in box.conf."""
    wanted = {x.strip() for x in CONF["LINKS"].split(",") if x.strip()}
    rules = [
        ("alpha-data", Path.home() / "alpha-data", cache_path("alpha-data")),
        ("sip", Path.home() / "sip", cache_path("data", "sip")),
        ("repo-data", REPO / "data", cache_path("data")),
    ]
    return [r for r in rules if r[0] in wanted]


def link_state() -> list[tuple[str, str, str]]:
    out = []
    for name, link, target in link_targets():
        if link.is_symlink():
            resolved = os.readlink(link)
            state = "ok" if Path(resolved) == target else f"points elsewhere ({resolved})"
        elif link.exists():
            state = "real directory (not managed)"
        else:
            state = "missing"
        out.append((name, str(link), state))
    return out


def cmd_status(argv: list[str]) -> int:
    extracted = extracted_datasets()
    used = cache_usage()
    cap = CONF["CACHE_CAP_BYTES"]
    note(f"cache    {CACHE}   cap {CONF['CACHE_CAP_GB']} GB")
    pct = 100 * used / cap if cap else 0
    note(f"used     {human(used)} ({pct:.0f}% of cap), {human(max(cap - used, 0))} free")
    if not extracted:
        note("nothing extracted yet (`box pull <dest>` or `box set <name>`)")
    else:
        note(f"\n{'DATASET':<52} {'BATCH':>6} {'LOCAL':>10}  LAST PULL")
        for dest in sorted(extracted):
            markers = extracted[dest]
            local = sum(m.get("bytes", 0) for m in markers)
            last = max((m.get("pulled_at", "") for m in markers), default="")
            note(f"{dest:<52} {len(markers):>6} {human(local):>10}  {last}")
    note("\nlinks")
    for name, link, state in link_state():
        note(f"  {name:<12} {link:<42} {state}")
    live = bot_forward_path()
    if live is not None:
        forward = cache_path("data", "forward")
        if forward.is_symlink():
            state = f"-> {os.readlink(forward)}"
        elif forward.exists():
            state = "real directory (not the configured live state)"
        else:
            state = "MISSING (run `box link`)"
        note(f"  {'bot-forward':<12} {str(forward):<42} {state}")
    if used > cap:
        note(f"\nOVER CAP by {human(used - cap)}: run `box evict --lru N`")
    return 0


# --------------------------------------------------------------------------- evict


def evict_one(dest: str, *, dry: bool, force: bool) -> int:
    """Delete the files of one extracted dataset that are verified on the box."""
    markers = extracted_datasets().get(dest)
    if not markers:
        die(f"nothing extracted for {dest!r} (see `box status`)")
    sizes = archive_listing(dest)
    if sizes is None:
        die(f"refusing: no archive/{dest} on the box", 2)
    manifest = read_manifest(dest) or {}
    for marker in markers:
        batch, sha = marker.get("batch"), marker.get("sha256")
        if marker.get("kind") == "object":
            if sizes.get(batch) != marker.get("bytes"):
                die(
                    f"refusing: object {batch} of {dest} is missing from the box or changed size "
                    f"(copy it again with `box get {dest}`, or --force)",
                    2,
                )
            continue
        if manifest.get(batch) != sha:
            die(
                f"refusing: {batch} of {dest} is not verified in the box MANIFEST "
                f"(re-pull it, or --force if you accept the risk)",
                2,
            )

    default_root = local_root_for(dest, None)
    victims: list[tuple[Path, float]] = []
    newer: list[str] = []
    roots: set[Path] = set()
    for marker in markers:
        mtime = marker["_mtime"]
        root = Path(marker.get("root", default_root))
        roots.add(root)
        for rel in marker.get("files", []):
            path = root / rel
            if not (path.is_file() or path.is_symlink()):
                continue
            try:
                st = path.lstat()
            except OSError:
                continue
            if st.st_mtime > mtime:
                newer.append(str(path))
            victims.append((path, float(st.st_size)))
    if newer and not force:
        shown = "\n  ".join(newer[:5])
        die(
            f"refusing: {len(newer)} file(s) under {root} were written AFTER the pull, so they are "
            f"not on the box:\n  {shown}\n  push them first (`box push`), or `--force` to "
            f"delete anyway",
            2,
        )

    freed = int(sum(size for _p, size in victims))
    scope = ", ".join(str(r) for r in sorted(roots))
    if dry:
        note(f"would free {human(freed)} from {len(victims)} files under {scope}")
        return freed
    for path, _size in victims:
        try:
            path.unlink()
        except OSError as exc:
            note(f"  warning: {path}: {exc}")
    # prune now-empty directories, deepest first, never above the dataset root
    dirs = sorted({p.parent for p, _s in victims}, key=lambda p: len(p.parts), reverse=True)
    for directory in dirs:
        if not any(directory == root or root in directory.parents for root in roots):
            continue
        while any(directory == root or root in directory.parents for root in roots):
            try:
                directory.rmdir()
            except OSError:
                break
            if any(directory == root for root in roots):
                break
            directory = directory.parent
    shutil.rmtree(marker_dir(dest), ignore_errors=True)
    note(f"evicted {dest}: freed {human(freed)} ({len(victims)} files); still on the box")
    return freed


def cmd_evict(argv: list[str]) -> int:
    dest, lru_gb, dry, force = "", None, False, False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--lru":
            i += 1
            lru_gb = parse_gb(argv[i] if i < len(argv) else die("--lru needs a size, e.g. 5G"))
        elif arg in ("--dry-run", "-n"):
            dry = True
        elif arg == "--force":
            force = True
        elif arg.startswith("-"):
            die(f"unknown option {arg}")
        elif not dest:
            dest = arg.rstrip("/")
        else:
            die(f"unexpected argument {arg!r}")
        i += 1
    if not dest and lru_gb is None:
        die("usage: box evict <dest> [--force] | box evict --lru N (e.g. 5G) [--dry-run]")

    if dest:
        evict_one(dest, dry=dry, force=force)
        return 0

    assert lru_gb is not None
    target = lru_gb * GB
    extracted = extracted_datasets()
    order = sorted(extracted.items(), key=lambda kv: max(m["_mtime"] for m in kv[1]))
    freed = 0
    for dest, markers in order:
        if freed >= target:
            break
        note(f"--- {dest} (last pull {max(m.get('pulled_at', '') for m in markers)})")
        try:
            freed += evict_one(dest, dry=dry, force=force)
        except SystemExit as exc:
            note(f"  skipped: {exc}")
    note(f"{'would free' if dry else 'freed'} {human(freed)} (target {human(target)})")
    if freed < target:
        note("warning: not enough verified extracted data to reach the target")
    return 0


# --------------------------------------------------------------------------- push / link


def cmd_push(argv: list[str]) -> int:
    """box push <local_dir> <dest-name> [--why TEXT] [--allow-delete-local] [-- archiver args]"""
    if len(argv) < 2 or argv[0].startswith("-") or argv[1].startswith("-"):
        die(
            "usage: box push <local_dir> <dest-name> [--why TEXT] [--allow-delete-local] "
            "[-- archiver args]"
        )
    src, dest, why, extra, allow_delete = argv[0], argv[1].rstrip("/"), "", [], False
    i = 2
    while i < len(argv):
        arg = argv[i]
        if arg == "--why":
            i += 1
            why = argv[i] if i < len(argv) else die("--why needs text")
        elif arg == "--allow-delete-local":
            allow_delete = True
        elif arg == "--":
            extra.extend(argv[i + 1 :])
            break
        else:
            extra.append(arg)
        i += 1
    if dest in ("backup", "archive") or dest.startswith(("backup/", "archive/")):
        die(
            "dest-name must not start with backup/ or archive/ (it is placed under archive/ "
            "for you)"
        )
    if "/../" in f"/{dest}/" or "//" in dest:
        die(f"invalid dest-name: {dest}")
    path = Path(os.path.expandvars(os.path.expanduser(src))).absolute()
    if not path.is_dir():
        die(f"not a directory: {path}")
    if ("--delete-local" in extra) and not allow_delete:
        die("refusing --delete-local: pass --allow-delete-local if you really mean it")
    archiver = REPO / "factory" / "scripts" / "archive_to_storagebox.sh"
    if not archiver.is_file():
        die(f"missing {archiver}")
    why = why or f"pushed {now_iso()} by box push from {path}"
    cmd = ["bash", str(archiver), str(path), why, "--dest-name", dest, *extra]
    note(" ".join(shlex.quote(c) for c in cmd[1:]))
    return subprocess.call(cmd)


def bot_forward_path() -> Path | None:
    """Configured live bot state dir (kept reachable as CACHE/data/forward), or None."""
    raw = CONF.get("BOT_FORWARD", "").strip()
    if not raw:
        return None
    return Path(os.path.expandvars(os.path.expanduser(raw))).absolute()


def link_bot_forward(dry: bool) -> None:
    """Keep the live bot state reachable as $CACHE_ROOT/data/forward after the data relink."""
    live = bot_forward_path()
    if live is None:
        return
    forward = cache_path("data", "forward")
    if not live.is_dir():
        note(f"  warn  BOT_FORWARD {live} does not exist; data/forward left alone")
        return
    if forward.is_symlink():
        if Path(os.readlink(forward)) == live:
            note(f"  ok    {forward} -> {live} (live bot state)")
        else:
            note(f"  relink {forward} (was {os.readlink(forward)}) -> {live}")
            if not dry:
                forward.unlink()
                forward.symlink_to(live)
        return
    if forward.exists():
        note(
            f"  stop  {forward} is a real directory, not a symlink: live bot state left "
            f"untouched (move it aside yourself if you want the link)"
        )
        return
    note(f"  create {forward} -> {live} (live bot state)")
    if not dry:
        forward.parent.mkdir(parents=True, exist_ok=True)
        forward.symlink_to(live)


def empty_tree(path: Path) -> bool:
    """True when path holds no files, symlinks or other content (only empty directories)."""
    for dirpath, dirnames, filenames in os.walk(path):
        if filenames:
            return False
        for name in dirnames:
            if (Path(dirpath) / name).is_symlink():
                return False
    return True


def cmd_link(argv: list[str]) -> int:
    force = "--force" in argv
    dry = "--dry-run" in argv or "-n" in argv
    for arg in argv:
        if arg not in ("--force", "--dry-run", "-n"):
            die(f"unknown option {arg}")
    if not dry:
        CACHE.mkdir(parents=True, exist_ok=True)
    for _name, link, target in link_targets():
        if not dry:
            target.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if Path(os.readlink(link)) == target:
                note(f"  ok     {link} -> {target}")
                continue
            note(f"  relink {link} (was {os.readlink(link)}) -> {target}")
            if not dry:
                link.unlink()
                link.symlink_to(target)
            continue
        if link.exists() and link.is_dir() and empty_tree(link):
            note(f"  rmdir  {link} (holds no files) then symlink -> {target}")
            if not dry:
                shutil.rmtree(link)
                link.symlink_to(target)
            continue
        if link.exists():
            if not force:
                msg = (
                    f"{link} is a real directory with data; move it aside or use --force "
                    f"(which renames it to {link.name}.pre-link-<timestamp>, it is never deleted)"
                )
                if dry:
                    note(f"  would refuse: {msg}")
                    continue
                die(msg, 2)
            aside = link.with_name(f"{link.name}.pre-link-{time.strftime('%Y%m%d-%H%M%S')}")
            note(f"  move   {link} -> {aside} (then symlink)")
            if not dry:
                link.rename(aside)
                link.symlink_to(target)
            continue
        note(f"  create {link} -> {target}")
        if not dry:
            link.symlink_to(target)
    link_bot_forward(dry)
    note("done. Resolution after linking:")
    probes = (
        Path.home() / "alpha-data" / "open-search-v1",
        REPO / "data",
        Path.home() / "sip" / "net",
        REPO / "data" / "forward",
    )
    for probe in probes:
        note(f"  {probe} -> {os.path.realpath(probe)}")
    return 0


# --------------------------------------------------------------------------- main


def usage() -> None:
    print(__doc__.strip())


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        usage()
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "pull":
        return cmd_pull(rest)
    handlers = {
        "ls": cmd_ls,
        "get": cmd_get,
        "set": cmd_set,
        "status": cmd_status,
        "evict": cmd_evict,
        "push": cmd_push,
        "link": cmd_link,
    }
    if cmd not in handlers:
        die(f"unknown command {cmd!r} (see `box --help`)")
    return handlers[cmd](rest)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        raise SystemExit(130) from None
    except BrokenPipeError:  # e.g. `box ls | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(0) from None
