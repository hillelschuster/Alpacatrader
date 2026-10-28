#!/usr/bin/env python3
"""Rebuild the point-in-time universe from the US-Stock-Symbols git bundle
(yolo22/stock-pit-archives mirror: per-commit daily exchange ticker snapshots).

Walks every commit, reads nasdaq/nyse/amex full_tickers.json at that snapshot,
drops junk security names (warrant/right/unit/preferred/note/debenture/series/
depositary/subscription) and odd symbols, emits rows [vintage, exchange, symbol].

Usage:
  python build_pit_from_bundle.py --repo /tmp/opencode/ussyms \
      --ref refs/remotes/ussyms/main --since 2021-01-01 \
      --out data/pit/pit_symbols_bundle.parquet
Resumable by year: writes data/pit/parts_bundle/pit_<year>.parquet first.
"""
import argparse
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

import pandas as pd

EXCHANGES = ("nasdaq", "nyse", "amex")
FILES = {ex: f"{ex}/{ex}_full_tickers.json" for ex in EXCHANGES}
JUNK = re.compile(
    r"\b(warrants?|rights?|units?|preferred|notes?|debentures?|depositary|"
    r"subscription|series)\b",
    re.I,
)


def commits(repo: str, ref: str, since: str):
    out = subprocess.run(
        ["git", "-C", repo, "log", "--format=%H|%ad", "--date=short", "--reverse", ref],
        capture_output=True, text=True, check=True).stdout
    for line in out.strip().splitlines():
        sha, day = line.split("|")
        if day >= since:
            yield day, sha


def snapshot_rows(repo: str, sha: str):
    proc = subprocess.run(
        ["git", "-C", repo, "archive", sha, *FILES.values()],
        capture_output=True)
    if proc.returncode != 0:
        return []  # early commits predate the ticker files
    rows = []
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
        for ex, name in FILES.items():
            try:
                fobj = tf.extractfile(name)
                if fobj is None:
                    continue
                data = json.load(fobj)
            except Exception:
                continue
            for e in data:
                sym = str(e.get("symbol", "")).strip().upper()
                nm = str(e.get("name", "") or "")
                if not sym or "." in sym or "-" in sym or "/" in sym:
                    continue
                if JUNK.search(nm):
                    continue
                rows.append((sym, ex))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ref", default="refs/remotes/ussyms/main")
    ap.add_argument("--since", default="2021-01-01")
    ap.add_argument("--out", default="data/pit/pit_symbols_bundle.parquet")
    a = ap.parse_args()

    parts = Path("data/pit/parts_bundle")
    parts.mkdir(parents=True, exist_ok=True)
    by_year = {}
    for day, sha in commits(a.repo, a.ref, a.since):
        year = day[:4]
        if (parts / f"pit_{year}.parquet").exists():
            continue
        for sym, ex in snapshot_rows(a.repo, sha):
            by_year.setdefault(year, []).append((day, ex, sym))
        if len(by_year.get(year, [])) and day[5:] == "12-31":
            pass
    for year, rows in by_year.items():
        df = pd.DataFrame(rows, columns=["vintage", "exchange", "symbol"])
        df.to_parquet(parts / f"pit_{year}.parquet", index=False)
        print(f"{year}: {df.vintage.nunique()} vintages, {len(df):,} rows")

    frames = [pd.read_parquet(f) for f in sorted(parts.glob("pit_*.parquet"))]
    full = pd.concat(frames, ignore_index=True).drop_duplicates()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    full.to_parquet(out, index=False)
    print(f"{out}: {full.vintage.nunique()} vintages "
          f"{full.vintage.min()}..{full.vintage.max()}, {len(full):,} rows")


if __name__ == "__main__":
    main()
