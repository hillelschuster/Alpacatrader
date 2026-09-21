#!/usr/bin/env python3
"""BASKET-01 race-by-view — member-level up-vs-dn ordering on the read surface.

The committed race table (T3) pools every population/snapshot; this pass slices the
same measurement by view (pop,T) so the failure-asymmetry question can be read per
participation point: among filled main-set members, how often does a dn-L touch come
FIRST (a stylized -L release would fire before the member can ever show +H) versus
the up-H touch coming first, per (H, L) cell.

Pure measurement over committed anatomy day files. Rulers remain rulers: no release
rule, no survivor rule, no chosen H or L. Counts and shares only.

Output: <root>/agg/race_by_view.json

Usage:
  .venv/bin/python factory/scripts/basket_race_by_view.py --self-test
  BASKET_ART_ROOT=factory/artifacts/basket/sip .venv/bin/python factory/scripts/basket_race_by_view.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from basket_aggregate import race_label  # noqa: E402

ART = Path(os.environ.get("BASKET_ART_ROOT", str(ROOT / "factory" / "artifacts" / "basket")))
ANAT = ART / "anatomy"
VIEWS = [("A_pm", 570), ("A_pm31", 570), ("A_open", 570), ("B", 575), ("B", 585),
         ("B", 600), ("B", 720)]
CELLS = [(30, 10), (30, 15), (50, 10), (50, 15), (100, 10), (100, 15)]
PRIMARY_N = 3


def scan(files) -> dict:
    counts: dict = {}
    for f in files:
        rec = json.load(open(f))
        for s in rec.get("snapshots", []):
            key = (s["pop"], s["T"])
            if key not in VIEWS:
                continue
            buckets = counts.setdefault(key, Counter())
            for n in s["names"][:PRIMARY_N]:
                fl = n.get("fill")
                if not fl or fl.get("blocked"):
                    continue
                buckets["members"] += 1
                for H, L in CELLS:
                    buckets[("race", H, L, race_label(n, H, L))] += 1
    return counts


def finalize(counts: dict) -> dict:
    rows = []
    for view in VIEWS:
        key = tuple(view)
        c = counts.get(key, Counter())
        base = {"pop": view[0], "T": view[1], "members": c.get("members", 0)}
        for H, L in CELLS:
            tot = sum(c.get(("race", H, L, lab), 0) for lab in
                      ("up", "dn", "amb", "up_only", "dn_only", "neither"))
            up_first = c.get(("race", H, L, "up"), 0) + c.get(("race", H, L, "up_only"), 0)
            dn_first = c.get(("race", H, L, "dn"), 0) + c.get(("race", H, L, "dn_only"), 0)
            amb = c.get(("race", H, L, "amb"), 0)
            touched_up = up_first + c.get(("race", H, L, "dn"), 0) + amb
            dn_before_up = c.get(("race", H, L, "dn"), 0)
            base[f"H{H}/L{L}"] = {
                "n": tot, "up_first": up_first, "dn_first": dn_first, "amb": amb,
                "neither": c.get(("race", H, L, "neither"), 0),
                "touched_up": touched_up,
                "dn_before_up_share": (round(dn_before_up / touched_up, 4)
                                       if touched_up else None),
                "up_first_share": round(up_first / tot, 4) if tot else None,
                "dn_first_share": round(dn_first / tot, 4) if tot else None,
                "amb_share": round(amb / tot, 4) if tot else None}
        rows.append(base)
    return {"views": rows, "cells": [f"H{H}/L{L}" for H, L in CELLS],
            "method": "filled main-set members only; ordering = first-touch index from the "
                      "frozen ladders; up_first = up-H strictly before dn-L (or dn absent); "
                      "dn_first = dn-L strictly before up-H; amb = same-bar both. "
                      "Descriptive only - no release rule is defined or implied.",
            "_producer": "basket_race_by_view.py"}


def selftest():
    c = {(("A_pm", 570)): Counter({("race", 30, 10, "up"): 3, ("race", 30, 10, "dn"): 1,
                                   ("race", 30, 10, "neither"): 1, "members": 5})}
    out = finalize(c)
    row = out["views"][0]
    assert row["members"] == 5
    assert row["H30/L10"]["up_first_share"] == 0.6, row["H30/L10"]
    assert row["H30/L10"]["dn_first_share"] == 0.2, row["H30/L10"]
    empty = finalize({})
    assert empty["views"][0]["members"] == 0
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ART / "agg"))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    files = sorted(glob.glob(str(ANAT / "*.jsonl")))
    if not files:
        raise SystemExit(f"no anatomy files under {ART}")
    out = finalize(scan(files))
    out["n_day_files"] = len(files)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    with open(Path(args.out) / "race_by_view.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"race_by_view: {len(files)} day files, {len(out['views'])} views -> "
          f"{args.out}/race_by_view.json")


if __name__ == "__main__":
    main()
