#!/usr/bin/env python3
"""BASKET-01 race-by-view — member-level up-vs-dn ordering from raw chronological prints.

The committed race table (T3) pools every population/snapshot; this pass slices the
same measurement by view (pop,T) so the failure-asymmetry question can be read per
participation point: among filled main-set members, how often does a dn-L touch come
FIRST (a stylized -L release would fire before the member can ever show +H) versus
the up-H touch coming first, per (H, L) cell.

FIX 2: the ordering source is the raw trade chronology, not the minute-level ladders.
For every filled main-set member (accessible = not blocked) the path is the same as
T5's: state zero = the actual fill (fill bar minute start, fill price) + eligible
prints (sip_bars alpaca hl policy) with et >= fill_et, each carrying its ts_utc.
up_first = the first print at price >= fill*(1+H/100) comes strictly before the first
print at price <= fill*(1-L/100); dn_first = the reverse; amb = the same print both
(impossible for H, L > 0 - kept for schema compatibility); neither = no touch. The
fill state cannot cross either threshold. Members with no eligible print after the
fill are unresolved, reported, and excluded from shares.

Output: <root>/agg/race_by_view.json, per view+cell with counts/shares split by member
class (healthy_raw / provider_only / unknown, from the net coverage file) and
n_unresolved. Pure measurement: no release rule, no survivor rule, no chosen H or L.

Usage:
  .venv/bin/python factory/scripts/basket_race_by_view.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_race_by_view.py --days 2021-02-01
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_race_by_view.py --all --workers 3
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_race_by_view.py --merge-only
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from basket_t5_rawpaths import member_rows, sym_paths  # noqa: E402

ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
TRADES = ROOT / "data" / "sip" / "net" / "trades"
COV = ROOT / "data" / "sip" / "net" / "coverage"
STAGE = ROOT / "data" / "sip" / "race_raw"     # ponytail: staged per day like T5; merge reads only this
VIEWS = [("A_pm", 570), ("A_pm31", 570), ("A_open", 570), ("B", 575), ("B", 585),
         ("B", 600), ("B", 720)]
CELLS = [(30, 10), (30, 15), (50, 10), (50, 15), (100, 10), (100, 15)]
PRIMARY_N = 3
CLASSES = ("healthy_raw", "provider_only", "unknown")
LABELS = ("up_first", "dn_first", "amb", "neither")


def classify(prices: np.ndarray, fill_px: float, H: float, L: float):
    """First raw-print crossing of +H / -L. Returns (label, i_up, i_dn); -1 = no touch."""
    iu = int(np.argmax(prices >= fill_px * (1.0 + H / 100.0))) \
        if (prices >= fill_px * (1.0 + H / 100.0)).any() else -1
    idn = int(np.argmax(prices <= fill_px * (1.0 - L / 100.0))) \
        if (prices <= fill_px * (1.0 - L / 100.0)).any() else -1
    if iu < 0 and idn < 0:
        lab = "neither"
    elif idn < 0 or (iu >= 0 and iu < idn):
        lab = "up_first"
    elif iu < 0 or idn < iu:
        lab = "dn_first"
    else:
        lab = "amb"                    # same print both (impossible for H,L > 0)
    return lab, iu, idn


def label_of(up_ts, dn_ts) -> str:
    """Ordering from the crossing timestamps stored per cell (raw ts_utc)."""
    if up_ts is None and dn_ts is None:
        return "neither"
    if dn_ts is None or (up_ts is not None and up_ts < dn_ts):
        return "up_first"
    if up_ts is None or dn_ts < up_ts:
        return "dn_first"
    return "amb"


def stage_day(day: str, force: bool) -> str:
    sp = STAGE / f"{day}.json"
    if sp.exists() and not force:
        return "skip"
    ap = ANAT / f"{day}.jsonl"
    tp = TRADES / f"{day}.parquet"
    if not (ap.exists() and tp.exists()):
        return "missing"
    rows = [r for r in member_rows(json.load(open(ap)))
            if r["set"] == "main" and (r["pop"], r["T"]) in VIEWS]
    if not rows:
        return "empty"
    need = sorted({r["ticker"] for r in rows})
    trades = (pl.scan_parquet(tp).select(["symbol", "ts_utc", "price", "conditions", "tape"])
              .with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York")
                            .alias("tset"))
              .with_columns((pl.col("tset").dt.hour().cast(pl.Int32) * 60
                             + pl.col("tset").dt.minute().cast(pl.Int32)).alias("et"))
              .filter((pl.col("et") >= 570) & (pl.col("et") < 960))
              .collect())
    syms = sym_paths(trades, need)
    cp = COV / f"{day}.json"
    cls_map = {}
    if cp.exists():
        cls_map = {k: v.get("cls") for k, v in (json.load(open(cp)).get("per_symbol") or {}).items()}
    members, n_unres = [], 0
    for r in rows:
        got = syms.get(r["ticker"])
        cells = None
        if got is not None:
            px, ts, et = got
            mask = et >= r["fill_et"]
            if mask.any():
                pr, tv = px[mask], ts[mask]
                cells = {}
                for H, L in CELLS:
                    _lab, iu, idn = classify(pr, r["fill_px"], H, L)
                    cells[f"{H}/{L}"] = [int(tv[iu]) if iu >= 0 else None,
                                         int(tv[idn]) if idn >= 0 else None]
        if cells is None:
            n_unres += 1
        members.append({"pop": r["pop"], "T": r["T"], "ticker": r["ticker"],
                        "cls": cls_map.get(r["ticker"]), "fill_px": r["fill_px"],
                        "fill_et": r["fill_et"], "cells": cells})
    st = {"date": day, "n_members": len(members), "n_unresolved": n_unres, "members": members}
    STAGE.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(st, fh, separators=(",", ":"), default=str)
    os.replace(tmp, sp)
    return f"ok({len(members)})"


def new_counts() -> dict:
    return {"members": defaultdict(Counter), "unres": defaultdict(int),
            "race": defaultdict(int), "tup": defaultdict(int),
            "dnbup": defaultdict(int)}


def merge_counts(files) -> dict:
    counts = new_counts()
    for f in files:
        for m in json.load(open(f))["members"]:
            cls = m.get("cls") or "unknown"
            key = (m["pop"], m["T"])
            counts["members"][key][cls] += 1
            if m["cells"] is None:
                counts["unres"][(m["pop"], m["T"], cls)] += 1
                continue
            for H, L in CELLS:
                up_ts, dn_ts = m["cells"][f"{H}/{L}"]
                ck = (m["pop"], m["T"], H, L, cls)
                counts["race"][(*ck, label_of(up_ts, dn_ts))] += 1
                if up_ts is not None:
                    counts["tup"][ck] += 1
                if up_ts is not None and dn_ts is not None and dn_ts < up_ts:
                    counts["dnbup"][ck] += 1
    return counts


def _cell_block(counts: dict, pop, T, H, L, cls=None) -> dict:
    classes = CLASSES if cls is None else (cls,)      # cls=None = all classes pooled
    cc = {lab: sum(counts["race"].get((pop, T, H, L, c, lab), 0) for c in classes)
          for lab in LABELS}
    n = sum(cc.values())
    tup = sum(counts["tup"].get((pop, T, H, L, c), 0) for c in classes)
    dnbup = sum(counts["dnbup"].get((pop, T, H, L, c), 0) for c in classes)
    return {"n": n, **cc, "touched_up": tup,
            "dn_before_up_share": round(dnbup / tup, 4) if tup else None,
            "up_first_share": round(cc["up_first"] / n, 4) if n else None,
            "dn_first_share": round(cc["dn_first"] / n, 4) if n else None,
            "amb_share": round(cc["amb"] / n, 4) if n else None}


def finalize(counts: dict) -> dict:
    rows = []
    for view in VIEWS:
        key = tuple(view)
        mem = counts["members"].get(key, Counter())
        n_unres = sum(v for (p, t, _c), v in counts["unres"].items() if (p, t) == key)
        base = {"pop": view[0], "T": view[1], "members": sum(mem.values()),
                "n_unresolved": n_unres}
        for H, L in CELLS:
            cell = _cell_block(counts, view[0], view[1], H, L, None)
            # ponytail: cls=None block sums all classes because no member has cls None
            # (stage maps missing -> "unknown" at merge count time)
            by_class = {cls: _cell_block(counts, view[0], view[1], H, L, cls)
                        for cls in CLASSES}
            cell["by_class"] = by_class
            cell["n_unresolved"] = n_unres
            base[f"H{H}/L{L}"] = cell
        rows.append(base)
    return {"views": rows, "cells": [f"H{H}/L{L}" for H, L in CELLS],
            "method": "filled main-set members only (accessible fills); ordering = first raw "
                      "eligible SIP print (sip_bars alpaca hl policy, et>=fill_et, ts_utc order) "
                      "crossing +H / -L from the actual fill as state zero (fill cannot cross); "
                      "up_first = +H strictly before -L (or -L absent); dn_first = -L strictly "
                      "before +H; amb = same print both (impossible, schema-compatible); "
                      "neither = no touch. Members with no eligible print after the fill are "
                      "unresolved, reported, excluded from shares. Descriptive only - no release "
                      "rule is defined or implied.",
            "_producer": "basket_race_by_view.py"}


def merge(out_dir: Path) -> dict:
    files = sorted(glob.glob(str(STAGE / "*.json")))
    out = finalize(merge_counts(files))
    out["n_day_files"] = len(files)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "race_by_view.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"race_by_view: {len(files)} staged days -> {out_dir/'race_by_view.json'}")
    return out


def selftest():
    # raw-print ordering: -10% first, then +30% => dn_first for (30,10)
    lab, iu, idn = classify(np.array([9.0, 13.0, 11.0]), 10.0, 30, 10)
    assert (lab, iu, idn) == ("dn_first", 1, 0), (lab, iu, idn)
    lab, iu, idn = classify(np.array([13.0, 9.0]), 10.0, 30, 10)
    assert (lab, iu, idn) == ("up_first", 0, 1), (lab, iu, idn)
    lab, iu, idn = classify(np.array([10.5, 10.2]), 10.0, 30, 10)
    assert (lab, iu, idn) == ("neither", -1, -1), (lab, iu, idn)
    lab, iu, idn = classify(np.array([13.0]), 10.0, 30, 10)
    assert (lab, iu, idn) == ("up_first", 0, -1), (lab, iu, idn)
    assert label_of(None, None) == "neither" and label_of(1, 2) == "up_first"
    assert label_of(2, 1) == "dn_first" and label_of(1, 1) == "amb"
    counts = new_counts()
    k = ("A_pm", 570)
    counts["members"][k]["healthy_raw"] += 5
    counts["unres"][("A_pm", 570, "healthy_raw")] += 1   # no prints -> unresolved, excluded
    for _ in range(3):
        counts["race"][("A_pm", 570, 30, 10, "healthy_raw", "up_first")] += 1
        counts["tup"][("A_pm", 570, 30, 10, "healthy_raw")] += 1
    counts["race"][("A_pm", 570, 30, 10, "healthy_raw", "dn_first")] += 1
    counts["tup"][("A_pm", 570, 30, 10, "healthy_raw")] += 1
    counts["dnbup"][("A_pm", 570, 30, 10, "healthy_raw")] += 1
    out = finalize(counts)
    row = out["views"][0]
    assert row["members"] == 5 and row["n_unresolved"] == 1, row
    c = row["H30/L10"]
    assert c["n"] == 4 and c["up_first_share"] == 0.75 and c["dn_first_share"] == 0.25, c
    assert c["touched_up"] == 4 and c["dn_before_up_share"] == 0.25, c
    assert c["by_class"]["healthy_raw"]["n"] == 4, c
    assert c["by_class"]["provider_only"]["n"] == 0 and c["by_class"]["unknown"]["n"] == 0, c
    assert c["n_unresolved"] == 1, c
    empty = finalize(new_counts())
    assert empty["views"][0]["members"] == 0 and empty["views"][0]["n_unresolved"] == 0
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--months", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", default=str(ART / "agg"))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    if args.merge_only:
        merge(Path(args.out))
        return
    days = []
    if args.days:
        days = list(args.days)
    elif args.months or args.all:
        files = sorted(glob.glob(str(ANAT / "*.jsonl")))
        days = [Path(f).name[:10] for f in files]
        if args.months:
            months = set(args.months)
            days = [d for d in days if d[:7] in months]
    if not days:
        ap.error("provide --days/--months/--all/--merge-only")
    if args.workers > 1 and len(days) > 1:
        parts = [[] for _ in range(args.workers)]
        for i, d in enumerate(sorted(days)):
            parts[i % args.workers].append(d)
        procs = []
        for i, part in enumerate(parts):
            cmd = [sys.executable, "-u", str(Path(__file__)), "--days", *part]
            if args.force:
                cmd.append("--force")
            log = open(f"/tmp/opencode/race_w{i}.log", "w")
            procs.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log))
        for p, log in procs:
            rc = p.wait()
            log.close()
            print(f"worker rc={rc}")
    else:
        counts = defaultdict(int)
        for i, d in enumerate(days, 1):
            res = stage_day(d, args.force)
            counts[res.split("(")[0]] += 1
            if i % 25 == 0 or len(days) <= 5:
                print(f"[{i}/{len(days)}] {d}: {res}", flush=True)
        print(f"staged: {dict(counts)}")


if __name__ == "__main__":
    main()
