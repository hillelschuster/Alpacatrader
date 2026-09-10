#!/usr/bin/env python3
"""Deep split of the two positive-asymmetry moments found in lb18:
  resume      first new bar after >=4 min without one (halt resume)
  fresh_high  new session-high print after >=10 min since prior high print
Splits (all causal, all info <= t):
  resume: gap length, direction (c_resume vs c_prehalt), tod, gain level, in_top3
  fresh_high: pause length, tod, gain level, in_top3
Forward stats from c[t]: f15/f30/f60/f120 median, q75_f120, to_close,
P(+50%/120), up/dn first-touch +20% within 120, overlap flags.
Artifact: factory/artifacts/lb18_moments.json (compact).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"


def load():
    pp, ll = [], []
    for p in sorted(LB.glob("path_*.parquet")):
        pp.append(pd.read_parquet(p))
    for p in sorted(LB.glob("lb_*.parquet")):
        ll.append(pd.read_parquet(p))
    return (pd.concat(pp, ignore_index=True).sort_values(["date", "ticker", "t"])
            .reset_index(drop=True), pd.concat(ll, ignore_index=True))


def stats(sub, ctx):
    i = sub["_f"].to_numpy()
    d = {"n": int(len(sub)), "med_gain": round(float(sub["gain_c"].median() * 100), 1)}
    for k in ("f15", "f30", "f60", "f120", "fclose"):
        d[f"med_{k}"] = round(float(sub[k].median()), 4)
    d["q75_f120"] = round(float(np.nanquantile(sub["f120"], 0.75)), 4)
    d["upf20"] = round(float(ctx["up"][i].mean()), 4)
    d["dnf20"] = round(float(ctx["dn"][i].mean()), 4)
    d["e50_120"] = round(float(ctx["e50"][i].mean()), 4)
    d["f50_120"] = round(float(ctx["f50"][i].mean()), 4)
    return d


def main():
    paths, lb = load()
    gk = ["date", "ticker"]
    gb = paths.groupby(gk, sort=False)
    t, c, h, l, nb = (paths["t"], paths["c"], paths["h"], paths["l"],
                      paths["n_bars"])
    gc = paths["gain_c"]

    new_bar = nb.groupby([paths["date"], paths["ticker"]]).diff().fillna(1) > 0
    last_new = t.where(new_bar).groupby([paths["date"], paths["ticker"]]).ffill()
    mins_since = t - last_new
    prev_mins = mins_since.groupby([paths["date"], paths["ticker"]]).shift(1)
    ev_resume = new_bar & (prev_mins >= 3)
    # gap = minutes between last pre-gap bar and resume bar
    prev_last_new = last_new.groupby([paths["date"], paths["ticker"]]).shift(1)
    gap = (t - prev_last_new).where(ev_resume)
    # c at the minute before the resume row = frozen pre-gap close
    pre_c = c.groupby([paths["date"], paths["ticker"]]).shift(1)
    dirr = (c / pre_c - 1).where(ev_resume)

    hp = h >= h.groupby([paths["date"], paths["ticker"]]).cummax()
    prev_hp = t.where(hp).groupby([paths["date"], paths["ticker"]]).ffill()
    prev_hp = prev_hp.groupby([paths["date"], paths["ticker"]]).shift(1)
    ev_fresh = hp & ((t - prev_hp) >= 10)
    pause = (t - prev_hp).where(ev_fresh)

    lbkey = lb.assign(inlb=1).set_index(["date", "ticker", "t"])["inlb"]
    inlb = pd.MultiIndex.from_frame(paths[["date", "ticker", "t"]]).map(lbkey)
    paths["in_top3"] = pd.Series(inlb, index=paths.index).fillna(0).astype(bool)

    paths["f15"] = gb["c"].shift(-15) / c - 1
    paths["f30"] = gb["c"].shift(-30) / c - 1
    paths["f60"] = gb["c"].shift(-60) / c - 1
    paths["f120"] = gb["c"].shift(-120) / c - 1
    lastc = gb["c"].transform("last")
    paths["fclose"] = lastc / c - 1

    n = len(paths)
    ku = np.full(n, 999, dtype=np.int32)
    kd = np.full(n, 999, dtype=np.int32)
    ku50 = np.full(n, 999, dtype=np.int32)
    kd50 = np.full(n, 999, dtype=np.int32)
    cu = c.to_numpy()
    for k in range(1, 121):
        hu = gb["h"].shift(-k).to_numpy()
        hl = gb["l"].shift(-k).to_numpy()
        m1 = (hu >= cu * 1.20) & (ku == 999)
        ku[m1] = k
        m2 = (hl <= cu * 0.80) & (kd == 999)
        kd[m2] = k
        m3 = (hu >= cu * 1.50) & (ku50 == 999)
        ku50[m3] = k
        m4 = (hl <= cu * 0.50) & (kd50 == 999)
        kd50[m4] = k
    paths["_f"] = np.arange(n)
    ctx = {
        "up": (ku < 999) & ((kd == 999) | (ku < kd)),
        "dn": (kd < 999) & ((ku == 999) | (kd < ku)),
        "e50": ku50 < 999,
        "f50": kd50 < 999,
    }

    out = {}
    events = {"resume": (ev_resume, gap, dirr), "fresh_high": (ev_fresh, pause, None)}
    for name, (flag, span, d) in events.items():
        sub = paths[flag.fillna(False)].copy()
        sub["_span"] = span.loc[sub.index]
        print(f"\n### {name}: n={len(sub)}  med_gain={sub['gain_c'].median()*100:.0f}%")
        out[name] = {"overall": stats(sub, ctx)}
        # overlap flags
        ol = {}
        if name == "resume":
            fh = ev_fresh.loc[sub.index]
            ol["also_fresh_high"] = round(float(fh.mean()), 3)
            ol["dir_up"] = round(float((sub["_span"] if d is not None else sub["_span"]).gt(0.01).mean()), 3)
            ol["in_top3"] = round(float(sub["in_top3"].mean()), 3)
            ol["gap_dist"] = sub["_span"].describe(percentiles=[.25, .5, .75]).round(1).to_dict()
            ol["dir_dist"] = sub["_span"].describe(percentiles=[.25, .5, .75]).round(3).to_dict()
            dd = d.loc[sub.index]
        print(f"   overlap/info: {ol}")
        out[name]["overlap"] = ol

        def slice_stats(mask, tag):
            s2 = sub[mask]
            if len(s2) < 40:
                return
            st = stats(s2, ctx)
            out[name].setdefault("splits", {})[tag] = st
            print(f"   {tag:34s} n={st['n']:6d} g={st['med_gain']:5.0f}% "
                  f"f30={st['med_f30']:+.3f} f60={st['med_f60']:+.3f} "
                  f"f120={st['med_f120']:+.3f} upf={st['upf20']:.2f} dnf={st['dnf20']:.2f} "
                  f"e50={st['e50_120']:.2f}")

        if name == "resume":
            for lo, hi, tag in [(3, 5, "gap 3-4m"), (5, 10, "gap 5-9m"),
                                (10, 30, "gap 10-29m"), (30, 10**9, "gap 30m+")]:
                slice_stats((sub["_span"] >= lo) & (sub["_span"] < hi), tag)
            dd2 = d.loc[sub.index]
            for lo, hi, tag in [(-1, -0.01, "dir down<-1%"), (-0.01, 0.01, "dir flat"),
                                (0.01, 1, "dir up>1%")]:
                slice_stats((dd2 >= lo) & (dd2 < hi), tag)
        else:
            for lo, hi, tag in [(10, 15, "pause 10-14m"), (15, 30, "pause 15-29m"),
                                (30, 60, "pause 30-59m"), (60, 10**9, "pause 60m+")]:
                slice_stats((sub["_span"] >= lo) & (sub["_span"] < hi), tag)

        for lo, hi, tag in [(571, 630, "tod 9:31-10:30"), (631, 720, "tod 10:31-12:00"),
                            (721, 840, "tod 12:01-14:00"), (841, 959, "tod 14:01-16:00")]:
            slice_stats((sub["t"] >= lo) & (sub["t"] <= hi), tag)
        for lo, hi, tag in [(0, 0.25, "gain<25"), (0.25, 0.50, "gain 25-50"),
                            (0.50, 1.0, "gain 50-100"), (1.0, 3.0, "gain 100-300"),
                            (3.0, 10**9, "gain 300+")]:
            slice_stats((sub["gain_c"] >= lo) & (sub["gain_c"] < hi), tag)
        slice_stats(sub["in_top3"], "in_top3")
        slice_stats(~sub["in_top3"], "not_in_top3")

    (ART / "lb18_moments.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_moments.json'}")


if __name__ == "__main__":
    main()
