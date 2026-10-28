#!/usr/bin/env python3
"""Run discovery anatomy only after all first533 corrected inputs commit.

Blocks on corrected per-day input dependencies rather than partially analyzing whatever
exists. Second-half files are not loaded. One clock per subprocess bounds RSS.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def complete(root: Path, day: str) -> bool:
    paths = (root / "_done" / f"{day}.json", root / "manifest" / f"{day}.json")
    if not all(p.exists() for p in paths):
        return False
    roster, tape = (json.loads(p.read_text()) for p in paths)
    return (roster.get("schema_version") == "lifecycle-v2.7" and roster.get("status") == "ok"
            and tape.get("producer_version") == "2.0.1" and tape.get("status") == "ok")


def wait_inputs(root: Path, days: list[str]):
    pending = {d for d in days if not complete(root, d)}
    print(f"waiting for {len(pending)}/{len(days)} corrected discovery inputs", flush=True)
    deadline = time.monotonic() + 6*3600
    while pending:
        if time.monotonic() > deadline:
            raise TimeoutError(f"required discovery inputs did not commit: {sorted(pending)[:5]}")
        # A small frontier keeps dependency checking cheap even on the Windows volume.
        for day in sorted(pending)[:32]:
            if complete(root, day):
                pending.remove(day)
        if pending:
            time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root",type=Path,required=True)
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    days = json.loads((root / "split.json").read_text())["discovery_days"]
    wait_inputs(root, days)
    scripts = Path(__file__).resolve().parent
    env = dict(os.environ, POLARS_MAX_THREADS="2", OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="1")
    for clock in (540,560,569,571):
        print(f"discovery anatomy starting clock={clock}",flush=True)
        subprocess.run([sys.executable,str(scripts/"lifecycle_anatomy.py"),"--stage","all",
                        "--which","discovery","--clocks",str(clock),"--data-root",str(a.data_root)],
                       env=env,check=True)
    subprocess.run([sys.executable,str(scripts/"lifecycle_anatomy.py"),"--stage","pm",
                    "--which","discovery","--clocks","540,560,569,571",
                    "--data-root",str(a.data_root)],env=env,check=True)
    print("complete first533 behavior map; rules/model/action selection can now follow",flush=True)


if __name__ == "__main__":
    main()
