#!/usr/bin/env python3
"""Canonical consistent simulation for the flush-bid trade (reconciliation of
lb18_scaleout vs lb18_lifecycle discrepancies).

Rule (single consistent, executable interpretation):
  c0 = state-minute close; B = 0.9*c0; fill = first NEW bar with t>t0, low<=B.
  After fill (fill bar inclusive, conservative ordering):
    * hard stop: full position at 0.9*B (-10% from fill);
    * scale: half at c0 (limit);
    * remainder: trail T from high-water, floor 0.9*B;
    * within a bar: stop checked BEFORE target (conservative); a scaled
      remainder can exit same bar only at the trail level (adverse ordering);
    * end of session: residual exits at last close.
  Variant W: if fill-bar close < B (weak), exit at next bar open instead of
  continuing; otherwise run the canonical rule.

Outputs comparison vs prior sims; artifact lb18_canon.json + per-event parquet.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "factory" / "artifacts"
FRICTION = 0.01
STOP = 0.10
F1 = 0.5


def canon_ret(fut, o, h, l, c, B, c0, T):
    stop = B * (1 - STOP)
    scaled = False
    runmax = B
    for j in fut:
        if not scaled:
            if l[j] <= stop:
                return min(float(o[j]), stop) / B - 1
            if h[j] >= c0:
                scaled = True
                runmax = c0
                lvl = max(stop, runmax * (1 - T))
                if l[j] <= lvl:
                    return F1 * (c0 / B - 1) + (1 - F1) * (min(float(o[j]), lvl) / B - 1)
                continue
        else:
            lvl = max(stop, runmax * (1 - T))
            if l[j] <= lvl:
                return F1 * (c0 / B - 1) + (1 - F1) * (min(float(o[j]), lvl) / B - 1)
            if h[j] > runmax:
                runmax = float(h[j])
    last = float(c[fut[-1]])
    if not scaled:
        return last / B - 1
    return F1 * (c0 / B - 1) + (1 - F1) * (last / B - 1)


def main():
    paths, _ = load()
    store = {}
    for key, g in paths.groupby(["date", "ticker"], sort=False):
        g = g.sort_values("t")
        t = g["t"].to_numpy(np.int32)
        o = g["o"].to_numpy(float); h = g["h"].to_numpy(float)
        l = g["l"].to_numpy(float); c = g["c"].to_numpy(float)
        nb = g["n_bars"].to_numpy(np.int32)
        new = np.empty(len(nb), bool)
        new[0] = nb[0] > 0
        new[1:] = nb[1:] > nb[:-1]
        store[key] = (t, o, h, l, c, np.where(new)[0])

    def run(ev):
        rows = []
        for r in ev.itertuples():
            key = (r.date, r.ticker)
            if key not in store:
                continue
            t, o, h, l, c, npx = store[key]
            jf = int(np.searchsorted(t[npx], r.tf))
            if jf >= len(npx) or int(t[npx[jf]]) != int(r.tf):
                continue
            fut = npx[jf:]
            B = r.px * 0.9
            c0 = r.px
            r15 = canon_ret(fut, o, h, l, c, B, c0, 0.15) - FRICTION
            r20 = canon_ret(fut, o, h, l, c, B, c0, 0.20) - FRICTION
            if len(fut) > 1 and r.fill_close < 0:
                ab = min(float(o[npx[jf + 1]]), c[npx[jf]]) / B - 1 - FRICTION
            else:
                ab = None
            rows.append({"date": r.date, "month": r.date[:7], "ticker": r.ticker,
                         "fc": r.fill_close, "gain": r.gain, "rank": r.rank,
                         "depth": r.depth, "r15": r.r15, "tf": r.tf,
                         "c15": r15, "c20": r20, "abort": ab})
        return pd.DataFrame(rows)

    ev = pd.read_parquet(ART / "lb18_episodes_events.parquet")
    ev = ev[~ev["censored"]]
    d = run(ev)
    d.to_parquet(ART / "lb18_canon.parquet")

    def cell(s, col):
        v = s[col].dropna()
        if len(v) == 0:
            return None
        mo = s.groupby("month")[col].mean().dropna()
        return (f"n={len(v):4d} mean={v.mean():+.4f} med={v.median():+.4f} "
                f"pos={(v > 0).mean():.2f} months+={int((mo > 0).sum())}/{len(mo)}")

    print(f"events n={len(d)}")
    print("\n== canonical (no weak abort) ==")
    for lab, q in [("ALL", np.ones(len(d), bool)), ("fc<0", (d.fc < 0).values),
                   ("fc 0-2", ((d.fc >= 0) & (d.fc < 0.02)).values),
                   ("fc>=2", (d.fc >= .02).values)]:
        print(f"{lab:7s} c15: {cell(d[q], 'c15')}")
    print(f"\n{'ALL':7s} c20: {cell(d, 'c20')}")

    print("\n== weak-abort variant (fc<0 -> next open, else canonical) ==")
    d["wa"] = np.where(d["fc"] < 0, d["abort"], d["c15"])
    d["wa20"] = np.where(d["fc"] < 0, d["abort"], d["c20"])
    for col in ("wa", "wa20"):
        print(f"{col}: {cell(d, col)}")
    for lab, q in [("fc<0", (d.fc < 0).values), ("fc>=0", (d.fc >= 0).values)]:
        s = d[q].copy(); s["x"] = np.where(s["fc"] < 0, s["abort"], s["c15"])
        print(f"  {lab:6s}: {cell(s, 'x')}")

    out = {"n": len(d)}
    (ART / "lb18_canon.json").write_text(json.dumps(out, indent=1))
    print(f"\n-> {ART/'lb18_canon.parquet'}")


if __name__ == "__main__":
    main()
