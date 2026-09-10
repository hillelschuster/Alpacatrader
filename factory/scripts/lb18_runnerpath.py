#!/usr/bin/env python3
"""Runner-path decomposition: what do the first post-fill bars reveal about the
runner tail? Extracts early post-fill features (bounce, new lows, retests,
volume response, reclaim timing) + outcomes (c0 beyond-tail, scale-out PnL) on
the committed event set. Discovery-grade; no pre-registration.
Artifact: factory/artifacts/lb18_runnerpath.parquet + lb18_runnerpath.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import load  # noqa: E402
from lb18_scaleout import FRICTION, scaleout, trail_exit  # noqa: E402

ART = ROOT / "factory" / "artifacts"


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
                      g["c"].to_numpy(float), g["v"].to_numpy(float),
                      np.where(new)[0])

    rows = []
    for r in ev.itertuples():
        key = (r.date, r.ticker)
        if key not in store:
            continue
        t, o, h, l, c, v, npx = store[key]
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
        fp = int(npx[jf])
        post = npx[jf + 1:]
        if len(post) < 5:
            continue

        p5, p10 = post[:5], post[:10]
        base_v = float(v[npx[max(0, jf - 20):jf]].mean())
        vol5x = float(v[p5].mean() / base_v) if base_v > 0 else np.nan
        newlow = bool(l[p10].min() < l[fp])
        retests = int((l[p10] <= B).sum())
        upfrac5 = float((c[p5] > o[p5]).mean())
        b1h = float(h[p5[0]] / B - 1)
        f3h = float(h[post[:3]].max() / B - 1)
        f5l = float(l[p5].min() / B - 1)

        hi = h[post]
        iu = np.flatnonzero(hi >= c0)
        hit = bool(len(iu) > 0)
        fmaxB = float(hi.max() / B - 1)
        end_ret = float(c[post[-1]] / B - 1)
        tmax_min = float(t[post[int(np.argmax(hi))]] - t[fp])
        if hit:
            k = int(iu[0])
            hit_bars = k + 1
            hit_min = float(t[post[k]] - t[fp])
            pre_low = float(l[post[:k + 1]].min() / B - 1)
            pk = post[k + 1:]
            if len(pk):
                c0_rmax = float(h[pk].max() / c0 - 1)
                giveback = float((h[pk].max() - c[pk[-1]]) / c0)
                c0_fmin = float(l[pk].min() / c0 - 1)
            else:
                c0_rmax, giveback, c0_fmin = 0.0, 0.0, 0.0
        else:
            hit_bars, hit_min, pre_low = None, None, None
            c0_rmax = giveback = c0_fmin = None

        rows.append({
            "date": r.date, "month": r.date[:7], "ticker": r.ticker,
            "rank": r.rank, "gain": r.gain, "depth": r.depth, "fc": r.fill_close,
            "r15": r.r15, "px": c0, "t0": r.t0, "tf": r.tf,
            "b1h": b1h, "f3h": f3h, "f5l": f5l, "newlow": newlow,
            "retests": retests, "upfrac5": upfrac5, "vol5x": vol5x,
            "hit": hit, "hit_bars": hit_bars, "hit_min": hit_min,
            "pre_low": pre_low, "fmaxB": fmaxB, "end_ret": end_ret,
            "tmax_min": tmax_min, "c0_rmax": c0_rmax, "c0_fmin": c0_fmin,
            "giveback": giveback,
            "npost": len(post),
            "t15f": trail_exit(post, o, h, l, c, B, B, 0.15, B * 0.9) - FRICTION,
            "so050": scaleout(post, o, h, l, c, B, c0, 0.5, 0.15) - FRICTION,
            "so067": scaleout(post, o, h, l, c, B, c0, 2 / 3, 0.15) - FRICTION,
        })
    d = pd.DataFrame(rows)
    d.to_parquet(ART / "lb18_runnerpath.parquet")
    print(f"events with >=5 post bars: {len(d)}")

    def ptype(hit, hit_bars, newlow):
        if hit:
            fast = hit_bars <= 10
            if newlow:
                return "reclaim_deep" if fast else "reclaim_slow_deep"
            return "reclaim_fast" if fast else "reclaim_slow"
        return "fade_deep" if newlow else "fade"

    d["ptype"] = [ptype(*x) for x in zip(d["hit"], d["hit_bars"], d["newlow"])]

    def tab(mask, by, name):
        res = {}
        for k, g in d[mask].groupby(by, observed=True):
            if len(g) < 15:
                continue
            so = g["so050"].dropna()
            mv = g.groupby("month")["so050"].mean().dropna()
            r = {"n": int(len(g)),
                 "hit": round(float(g["hit"].mean()), 3),
                 "med_fmaxB": round(float(g["fmaxB"].median()), 3),
                 "p30": round(float((g["fmaxB"] >= 0.30).mean()), 3),
                 "p50": round(float((g["fmaxB"] >= 0.50).mean()), 3),
                 "c0_rmax_med": (round(float(g["c0_rmax"].median()), 3)
                                 if g["c0_rmax"].notna().any() else None),
                 "c0_p25": (round(float((g["c0_rmax"] >= 0.25).mean()), 3)
                            if g["c0_rmax"].notna().any() else None),
                 "so050": round(float(so.mean()), 4),
                 "so050_med": round(float(so.median()), 4),
                 "months+": f"{int((mv > 0).sum())}/{len(mv)}"}
            res[str(k)] = r
            print(f"  {name:10s} {str(k):16s} n={r['n']:4d} hit={r['hit']:.2f} "
                  f"mfB={r['med_fmaxB']:+.2f} p50={r['p50']:.2f} "
                  f"c0rm_med={r['c0_rmax_med']} so050={r['so050']:+.4f} "
                  f"({r['months+']})")
        return res

    print("\n# path types (all events)")
    out = {"path_types": tab(np.ones(len(d), bool), "ptype", "all")}
    print("\n# path types (strong fc>=0.02)")
    out["path_types_fc002"] = tab((d.fc >= 0.02).values, "ptype", "strong")
    print("\n# strong: bounce bucket (first-3-bar high vs entry)")
    s = d[d.fc >= 0.02].copy()
    s["bounce"] = pd.cut(s["f3h"], [-1, 0.02, 0.08, 0.15, 9],
                         labels=["<2%", "2-8%", "8-15%", "15%+"])
    out["bounce"] = {}
    for k, g in s.groupby("bounce", observed=True):
        if len(g) < 15:
            continue
        so = g["so050"].dropna()
        mv = g.groupby("month")["so050"].mean().dropna()
        r = {"n": int(len(g)), "c0_p25": round(float((g["c0_rmax"] >= 0.25).mean()), 3),
             "so050": round(float(so.mean()), 4),
             "months+": f"{int((mv > 0).sum())}/{len(mv)}"}
        out["bounce"][str(k)] = r
        print(f"  bounce {str(k):8s} n={r['n']:4d} c0rm>=25%={r['c0_p25']:.2f} "
              f"so050={r['so050']:+.4f} ({r['months+']})")

    print("\n# strong: retests of bid in first 10 bars")
    s["rt"] = pd.cut(s["retests"], [-1, 0, 2, 99], labels=["0", "1-2", "3+"])
    out["retests"] = {}
    for k, g in s.groupby("rt", observed=True):
        if len(g) < 15:
            continue
        so = g["so050"].dropna()
        mv = g.groupby("month")["so050"].mean().dropna()
        r = {"n": int(len(g)), "c0_p25": round(float((g["c0_rmax"] >= 0.25).mean()), 3),
             "so050": round(float(so.mean()), 4),
             "months+": f"{int((mv > 0).sum())}/{len(mv)}"}
        out["retests"][str(k)] = r
        print(f"  retests {str(k):6s} n={r['n']:4d} c0rm>=25%={r['c0_p25']:.2f} "
              f"so050={r['so050']:+.4f} ({r['months+']})")

    print("\n# strong: volume response quintiles (vol5x)")
    s["vq"] = pd.qcut(s["vol5x"], 5, labels=False, duplicates="drop")
    out["vol5x"] = {}
    for k, g in s.groupby("vq", observed=True):
        so = g["so050"].dropna()
        mv = g.groupby("month")["so050"].mean().dropna()
        r = {"n": int(len(g)), "c0_p25": round(float((g["c0_rmax"] >= 0.25).mean()), 3),
             "so050": round(float(so.mean()), 4),
             "months+": f"{int((mv > 0).sum())}/{len(mv)}"}
        out["vol5x"][str(int(k))] = r
        print(f"  vq{int(k)} n={r['n']:4d} c0rm>=25%={r['c0_p25']:.2f} "
              f"so050={r['so050']:+.4f} ({r['months+']})")

    (ART / "lb18_runnerpath.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\n-> {ART/'lb18_runnerpath.parquet'}")


if __name__ == "__main__":
    main()
