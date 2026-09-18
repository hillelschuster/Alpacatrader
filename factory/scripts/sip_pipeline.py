#!/usr/bin/env python3
"""BASKET-01 SIP regeneration pipeline — per-day netbars -> anatomy, disjoint-day workers.

For every day that has Layer-2 net trades (data/sip/net/trades/<day>.parquet):
  1. sip_netbars.build_day  -> merged SIP substrate + coverage classification
  2. sip_anatomy.build_day  -> regenerated Phase-1 day record (SIP prev close, full-universe
                               winners/audit patch)
Both steps are resumable (manifest / skip-existing). Workers are disjoint-day subprocesses;
each worker owns a private log; there is no shared mutable artifact.

Usage:
  .venv/bin/python factory/scripts/sip_pipeline.py --self-test
  .venv/bin/python factory/scripts/sip_pipeline.py --day 2021-02-01
  .venv/bin/python factory/scripts/sip_pipeline.py --all --workers 5
  .venv/bin/python factory/scripts/sip_pipeline.py --days 2021-02-01 2021-02-02   # worker mode
"""
from __future__ import annotations

import argparse
import glob
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

LOG_DIR = Path("/tmp/opencode")


def net_days() -> list:
    return sorted(Path(p).stem for p in glob.glob(str(ROOT / "data" / "sip" / "net" / "trades" / "*.parquet")))


def process_day(day: str, force: bool = False) -> dict:
    import sip_anatomy as sa
    import sip_netbars as nb

    r1 = nb.build_day(day, force=force)
    r2 = sa.build_day(day, force=force) if r1.get("status") in ("ok", "skip") else {"status": "skipped_no_frame"}
    return {"day": day, "netbars": r1.get("status"), "anatomy": r2.get("status"),
            "classes": r1.get("classes")}


def chunk_round_robin(items: list, n: int) -> list:
    return [items[i::n] for i in range(n)]


def selftest():
    assert chunk_round_robin([1, 2, 3, 4, 5], 2) == [[1, 3, 5], [2, 4]]
    assert chunk_round_robin([], 3) == [[], [], []]
    assert net_days() == sorted(net_days())
    print(f"self-test OK ({len(net_days())} net days present)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None)
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return

    if args.day or args.days or (not args.workers and not args.all):
        days = ([args.day] if args.day else []) + (args.days or [])
        if not days:
            ap.error("--day/--days required (or --all --workers N)")
        for d in days:
            print(process_day(d, force=args.force))
        return

    todo = net_days()
    if args.workers and args.workers > 1:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        chunks = chunk_round_robin(todo, args.workers)
        procs = []
        for i, ch in enumerate(chunks):
            if not ch:
                continue
            log = open(LOG_DIR / f"pipe_w{i}.log", "w")
            cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "--days", *ch]
            if args.force:
                cmd.append("--force")
            procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log, i))
        print(f"launched {len(procs)} workers over {len(todo)} days")
        for p, log, i in procs:
            p.wait()
            log.close()
            print(f"worker {i} done rc={p.returncode}")
        return

    t0 = time.time()
    ok = skip = fail = 0
    for d in todo:
        r = process_day(d, force=args.force)
        if r["anatomy"] == "ok":
            ok += 1
        elif r["anatomy"] == "skip":
            skip += 1
        else:
            fail += 1
        if (ok + fail) % 25 == 0:
            print(f"{d}: {r} ({time.time()-t0:.0f}s)")
    print(f"pipeline: ok={ok} skip={skip} fail={fail} of {len(todo)}")


if __name__ == "__main__":
    main()
