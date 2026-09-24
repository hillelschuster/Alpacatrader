"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Merge parallel carry-fetch part roots into the canonical carry root.

Usage: carry_merge.py <canonical_root> <part_root> [<part_root> ...]

Day files are UNIONed per (ticker, et) (part rows win on overlap) so a part root
that only requested a subset of a day's tickers can never drop the tickers the
canonical root already had.  Manifest entries are unioned per (day, ticker) with
part entries taking precedence, rows of tickers certified `no_bars` are removed,
and every merged day's sha256 is re-published with the merged manifest.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import polars as pl

SCHEMA = ["date", "ticker", "et", "open", "high", "low", "close", "volume"]
canonical = Path(sys.argv[1])
parts = [Path(p) for p in sys.argv[2:]]

merged = json.loads((canonical / "manifest.json").read_text())
days = merged.setdefault("days", {})
touched: dict[str, dict] = {}

for part in parts:
    part_manifest = json.loads((part / "manifest.json").read_text())
    for day, entry in part_manifest.get("days", {}).items():
        touched.setdefault(day, {"frames": [], "entry": None})
        src = part / f"{day}.parquet"
        if src.exists():
            touched[day]["frames"].append(pl.read_parquet(src, columns=SCHEMA))
        touched[day]["entry"] = entry

written = 0
for day, payload in touched.items():
    dst = canonical / f"{day}.parquet"
    frames = []
    if dst.exists():
        frames.append(pl.read_parquet(dst, columns=SCHEMA))
    frames.extend(payload["frames"])
    if not frames:
        continue
    frame = pl.concat(frames, how="vertical_relaxed")
    entry = payload["entry"]
    no_bars = {t for t, info in entry.get("tickers", {}).items() if info.get("status") == "no_bars"}
    if no_bars:
        frame = frame.filter(~pl.col("ticker").is_in(sorted(no_bars)))
    frame = frame.unique(subset=["ticker", "et"], keep="last").sort(["ticker", "et"])
    tmp = dst.with_suffix(".tmp")
    frame.select(SCHEMA).write_parquet(tmp)
    os.replace(tmp, dst)
    written += 1

    current = days.setdefault(day, {"tickers": {}})
    current["session_end"] = entry.get("session_end", current.get("session_end"))
    current["source"] = entry.get("source", current.get("source"))
    current.setdefault("tickers", {}).update(entry.get("tickers", {}))
    current["source"]["overlay_sha256"] = hashlib.sha256(dst.read_bytes()).hexdigest()

tmp = canonical / "manifest.json.tmp"
tmp.write_text(json.dumps(merged, indent=1, sort_keys=True))
os.replace(tmp, canonical / "manifest.json")

bad = []
for day, entry in days.items():
    path = canonical / f"{day}.parquet"
    expected = entry.get("source", {}).get("overlay_sha256")
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if expected and actual != expected:
        bad.append(day)
    if actual is None and any(info.get("status") == "bars"
                               for info in entry.get("tickers", {}).values()):
        bad.append(day)
print(json.dumps({"merged_days": written, "manifest_days": len(days),
                  "sha_or_missing_mismatches": len(bad), "examples": bad[:5]}))
