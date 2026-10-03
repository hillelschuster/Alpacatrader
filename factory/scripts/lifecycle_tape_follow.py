#!/usr/bin/env python3
"""Enrich each roster after its corrected atomic commit; never consume an old version.

DrvFS does not deliver inotify events in this environment. A worker sleeps between
checks of its ONE required input marker. This is internal dependency scheduling, not
polling research jobs/logs or repeatedly scanning the corpus from the assistant.
"""
from __future__ import annotations
import os
os.environ.setdefault("POLARS_MAX_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import argparse
import json
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor
from lifecycle_tape import process_day


def ready(root: Path, day: str, version: str) -> bool:
    marker = root / "_done" / f"{day}.json"
    if not marker.exists():
        return False
    value = json.loads(marker.read_text())
    return value.get("schema_version") == version and value.get("status") in ("ok", "empty")


def await_roster(root: Path, day: str, version: str):
    deadline = time.monotonic() + 6*3600
    while not ready(root, day, version):
        if time.monotonic() > deadline:
            raise TimeoutError(f"required corrected roster did not commit: {day}")
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--schema", default="lifecycle-v2.8")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    split = json.loads((a.root / "split.json").read_text())
    days = split["discovery_days"] + split["validation_days"]
    def build(day):
        await_roster(a.root, day, a.schema)
        return day, process_day(day, a.data_root, split, False)
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i, (day, result) in enumerate(pool.map(build, days), 1):
            if i % 25 == 0 or i == len(days):
                print(f"tape committed {i}/{len(days)} through {day}: {result}", flush=True)
    print("complete tape dependency chain", flush=True)


if __name__ == "__main__":
    main()
