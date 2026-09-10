#!/usr/bin/env python3
"""Runner management at bar 10: snapshot the open trade state (half-at-c0
already sold or not, runmax, price) and compare continuing the backbone
(sell 0.5 at c0, trail 15% floor -10%) vs cut / tighten / widen FROM bar 10
onward. Only the post-signal leg is compared, so no lookahead.
Discovery-grade. Artifact: lb18_runnermanage.parquet + .json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import load  # noqa: E402

ART = ROOT / "factory" / "artifacts"
FRICTION = 0.01
FLOOR_L = 0.10
DEC = 10


def run(bars, o, h, l, c, B, c0, T=0.15, start=None):
    """Walk bars with stop/trail lvl = max(floor, runmax*(1-T)); exit at
    min(open, lvl). Half sold at c0 on first touch. start=(idx, half_sold,
    half_pnl, runmax) resumes at idx. Returns total ret."""
    if start is None:
        idx0, half_sold, half_pnl, runmax = 0, False, 0.0, B
    else:
        idx0, half_sold, half_pnl, runmax = start
    for idx in range(idx0, len(bars)):
        j = bars[idx]
        lvl = max(B * (1 - FLOOR_L), runmax * (1 - T))
        if l[j] <= lvl:
            px = min(float(o[j]), lvl)
            units = 0.5 if half_sold else 1.0
            return half_pnl + units * (px / B - 1)
        if not half_sold and h[j] >= c0:
            half_pnl = 0.5 * (c0 / B - 1)
            half_sold = True
        if h[j] > runmax:
            runmax = float(h[j])
    units = 0.5 if half_sold else 1.0
    return half_pnl + units * (float(c[bars[-1]]) / B - 1)


def snapshot(bars, o, h, l, c, B, c0, dec=DEC):
    """Walk backbone until exit or bar dec; return state at dec."""
    half_sold, half_pnl, runmax = False, 0.0, B
    for idx in range(len(bars)):
        j = bars[idx]
        lvl = max(B * (1 - FLOOR_L), runmax * 0.85)
        if l[j] <= lvl:
            units = 0.5 if half_sold else 1.0
            return {"closed": True,
                    "ret": half_pnl + units * (min(float(o[j]), lvl) / B - 1)}
        if not half_sold and h[j] >= c0:
            half_pnl = 0.5 * (c0 / B - 1)
            half_sold = True
        if h[j] > runmax:
            runmax = float(h[j])
        if idx == dec:
            return {"closed": False, "half_sold": half_sold,
                    "half_pnl": half_pnl, "runmax": runmax,
                    "px": float(c[j]),
                    "state": (idx + 1, half_sold, half_pnl, runmax)}
    units = 0.5 if half_sold else 1.0
    return {"closed": True,
            "ret": half_pnl + units * (float(c[bars[-1]]) / B - 1)}


def main():
    ev = pd.read_parquet(ART / "lb18_episodes_events.parquet")
    ev = ev[~ev["censored"]].copy()
    paths, _ = load()
    store = {}
    for key, g in paths.groupby(["date", "ticker"], sort=False):
        g = g.sort_values("t")
        nb = g["n_bars"].to_numpy(np.int32)
        new = np.empty(len(nb), bool)
        new[0] = nb[0] > 0
        new[1:] = nb[1:] > nb[:-1]
        store[key] = (g["t"].to_numpy(np.int32), g["o"].to_numpy(float),
                      g["h"].to_numpy(float), g["l"].to_numpy(float),
                      g["c"].to_numpy(float), np.where(new)[0])

    rows = []
    for r in ev.itertuples():
        if r.fill_close < 0.02:
            continue
        key = (r.date, r.ticker)
        if key not in store:
            continue
        t, o, h, l, c, npx = store[key]
        c0 = r.px
        B = c0 * 0.9
        j0 = int(np.searchsorted(t[npx], r.t0 + 1, "left"))
        jf = None
        for jj in range(j0, len(npx)):
            if l[npx[jj]] <= B:
                jf = jj
                break
        if jf is None:
            continue
        post = npx[jf + 1:]
        if len(post) < DEC + 3:
            continue
        p10 = post[:DEC]
        retests = int((l[p10] <= B).sum())
        f3h = float(h[post[:3]].max() / B - 1)
        snap = snapshot(post, o, h, l, c, B, c0)
        rec = {"date": r.date, "month": r.date[:7], "ticker": r.ticker,
               "fc": r.fill_close, "retests": retests, "f3h": f3h}
        if snap["closed"]:
            rec.update({"ever_open": 0, "base": snap["ret"] - FRICTION,
                        "cut": snap["ret"] - FRICTION,
                        "tight": snap["ret"] - FRICTION,
                        "wide": snap["ret"] - FRICTION})
        else:
            st = snap["state"]
            base = run(post, o, h, l, c, B, c0, T=0.15, start=st)
            tight = run(post, o, h, l, c, B, c0, T=0.10, start=st)
            wide = run(post, o, h, l, c, B, c0, T=0.25, start=st)
            units = 0.5 if st[1] else 1.0
            cut = st[2] + units * (snap["px"] / B - 1)
            rec.update({"ever_open": 1, "base": base - FRICTION,
                        "cut": cut - FRICTION, "tight": tight - FRICTION,
                        "wide": wide - FRICTION,
                        "px_dec": snap["px"] / B - 1})
        rows.append(rec)
    d = pd.DataFrame(rows)
    d.to_parquet(ART / "lb18_runnermanage.parquet")
    print(f"strong fills >= {DEC+3} post bars: {len(d)} "
          f"(open at dec: {int(d['ever_open'].sum())})")

    def tab(mask, tag):
        s = d[mask]
        if len(s) < 15:
            return None
        so = s[s["ever_open"] == 1]
        r = {"n": int(len(s)), "n_open": int(len(so)),
             "closed_before_dec": int((s["ever_open"] == 0).sum())}
        for p in ("base", "cut", "tight", "wide"):
            v = so[p].dropna()
            if len(v) < 10:
                continue
            mv = so.groupby("month")[p].mean().dropna()
            r[p] = {"mean": round(float(v.mean()), 4),
                    "med": round(float(v.median()), 4),
                    "months+": f"{int((mv > 0).sum())}/{len(mv)}"}
        return r

    out = {}
    groups = {"ALL": np.ones(len(d), bool),
              "rt0": (d.retests == 0).values,
              "rt1-2": d.retests.between(1, 2).values,
              "rt3+": (d.retests >= 3).values}
    for gname, m in groups.items():
        r = tab(m, gname)
        if r:
            out[gname] = r
            print(f"\n[{gname}] n={r['n']} open_at_dec={r['n_open']} "
                  f"closed_before={r['closed_before_dec']}")
            for p in ("base", "cut", "tight", "wide"):
                if p in r:
                    print(f"  {p:6s} mean={r[p]['mean']:+.4f} "
                          f"med={r[p]['med']:+.4f} months+={r[p]['months+']}")

    (ART / "lb18_runnermanage.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\n-> {ART/'lb18_runnermanage.parquet'}")


if __name__ == "__main__":
    main()
