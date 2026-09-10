#!/usr/bin/env python3
"""Episode-centric flush read with corrected measurement semantics.
Repairs over lb18_flush2/3: (a) touches, volume and speed use real NEW bars
(n_bars increments) only, never ffilled prints; (b) native 1-min state minutes,
no t%5 thinning; (c) flush events deduped by fill minute with multiplicity
reported; (d) speed recomputed with consistent indices; (e) prior_flush counts
prior flush EPISODES, not bars; (f) race economics per event with fill-bar and
same-bar ambiguity bounds, plus EOD censoring; (g) gap fills get a price
improvement sensitivity; (h) labels corrected (x20 is vs pre-flush c0;
bars_so_far is session bars, not name age).
Artifact: factory/artifacts/lb18_episodes.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"

L = 0.10
STOP_L = 0.10
WIN_MIN = 120
RACE_MIN = 60
FRICTION = 0.01
SESSION_END = 959


def load():
    pp = [pd.read_parquet(p) for p in sorted(LB.glob("path_*.parquet"))]
    ll = [pd.read_parquet(p) for p in sorted(LB.glob("lb_*.parquet"))]
    paths = (pd.concat(pp, ignore_index=True)
             .sort_values(["date", "ticker", "t"]).reset_index(drop=True))
    return paths, pd.concat(ll, ignore_index=True)


def build(paths):
    A = {}
    volx = np.full(len(paths), np.nan)
    npr = np.full(len(paths), np.nan)
    prior = np.zeros(len(paths), int)
    for key, g in paths.groupby(["date", "ticker"], sort=False):
        ix = g.index.to_numpy()
        t = g["t"].to_numpy(np.int32)
        o = g["o"].to_numpy(float); h = g["h"].to_numpy(float)
        l = g["l"].to_numpy(float); c = g["c"].to_numpy(float)
        v = g["v"].to_numpy(float); nb = g["n_bars"].to_numpy(np.int32)
        # new bar == n_bars increments; stale rows repeat last completed bar
        new = np.empty(len(nb), bool)
        new[0] = nb[0] > 0
        new[1:] = nb[1:] > nb[:-1]
        newpos = np.where(new)[0]
        vn = v[newpos]
        if len(vn) >= 5:
            roll = pd.Series(vn).rolling(20, min_periods=5).mean().to_numpy()
            vx = vn / roll
        else:
            vx = np.full(len(vn), np.nan)
        vxfull = np.full(len(t), np.nan); vxfull[newpos] = vx
        vxfull = pd.Series(vxfull).ffill().to_numpy()
        nfull = (pd.Series(new.astype(float)).rolling(20, min_periods=1)
                 .sum().to_numpy() / 20.0)
        cn = c[newpos]; ln = l[newpos]
        cm = np.maximum.accumulate(cn)
        under = ln <= cm * (1 - L)
        starts = under & ~np.concatenate([[False], under[:-1]])
        cnt = np.cumsum(starts) - starts
        prf = np.zeros(len(t), int); prf[newpos] = cnt
        prf = pd.Series(prf).ffill().fillna(0).astype(int).to_numpy()
        volx[ix] = vxfull; npr[ix] = nfull; prior[ix] = prf
        A[key] = dict(t=t, o=o, h=h, l=l, c=c, v=v, nb=nb, new=new,
                      newpos=newpos)
    paths["volx"] = volx
    paths["nprint20"] = npr
    paths["prior_flush"] = prior
    return A


def race_pair(d, p, pf, c0, tf):
    """Two bounds around fill-bar ordering: opt counts a fill-bar high>=target
    as a win (high after fill); pess ignores it (high may precede fill) and
    continues; same-bar both-touch -> pess loss / opt win. Returns per-event
    outcome triples + runner stats. Ambiguity is reported, never resolved."""
    tgt = c0; stop = pf * (1 - STOP_L)
    newpos = d["newpos"]; t = d["t"]; h = d["h"]; l = d["l"]; c = d["c"]
    p_end = max(p, int(np.searchsorted(t[newpos], tf + RACE_MIN, "right")) - 1)
    hops = newpos[p:p_end + 1]

    out_o = None
    both = False
    for j in hops:
        hh = h[j]; ll = l[j]
        if hh >= tgt and ll <= stop:
            both = True; out_o = "win"; break
        if hh >= tgt:
            out_o = "win"; break
        if ll <= stop:
            out_o = "loss"; break
    if out_o is None:
        out_o = "time"

    j0 = hops[0]
    if l[j0] <= stop:
        out_p = "loss"
    else:
        out_p = None
        for j in hops[1:]:
            hh = h[j]; ll = l[j]
            if hh >= tgt and ll <= stop:
                out_p = "loss"; break
            if hh >= tgt:
                out_p = "win"; break
            if ll <= stop:
                out_p = "loss"; break
        if out_p is None:
            out_p = "time"

    lc = float(c[hops[-1]])
    fmax = float(h[hops].max() / pf - 1)
    fmin = float(l[hops].min() / pf - 1)
    x20 = bool((h[hops] >= c0 * 1.20).any())
    rec = bool((h[hops] >= c0).any())
    return out_p, out_o, both, lc, fmax, fmin, x20, rec


def rets(out, pf, c0, lc):
    win = c0 / pf - 1
    if out == "win":
        return win
    if out == "loss":
        return -STOP_L
    return lc / pf - 1


def cell(s):
    r = {"n": int(len(s))}
    res = s[~s["censored"]]
    r["n_censored"] = int(len(s) - len(res))
    if len(res) == 0:
        return r
    r["win_pess"] = round(float(res["out_pess"].eq("win").mean()), 3)
    r["win_opt"] = round(float(res["out_opt"].eq("win").mean()), 3)
    r["loss"] = round(float(res["out_pess"].eq("loss").mean()), 3)
    r["time"] = round(float(res["out_pess"].eq("time").mean()), 3)
    r["amb"] = round(float(res["amb_both"].mean()), 3)
    r["pay_pess"] = round(float(res["ret_pess"].mean()), 4)
    r["pay_opt"] = round(float(res["ret_opt"].mean()), 4)
    r["pay_pess_net"] = round(r["pay_pess"] - FRICTION, 4)
    r["pay_opt_net"] = round(r["pay_opt"] - FRICTION, 4)
    r["x20"] = round(float(res["x20"].mean()), 3)
    r["med_fmax"] = round(float(res["fmax"].median()), 3)
    r["med_fmin"] = round(float(res["fmin"].median()), 3)
    return r


def main():
    paths, lb = load()
    A = build(paths)
    gpb = paths.groupby(["date", "ticker"], sort=False)
    paths["pullback"] = paths["c"] / gpb["c"].transform("cummax") - 1
    paths["r15"] = paths["c"] / gpb["c"].shift(15) - 1
    m = lb.merge(paths[["date", "ticker", "t", "c", "pullback", "r15", "volx",
                        "nprint20", "prior_flush", "n_bars"]],
                 on=["date", "ticker", "t"], how="left")
    state = (m["gain"] >= 1.0) & (m["pullback"] >= -0.01) & (m["r15"] >= 0.03)
    st = m[state.fillna(False)].copy()
    n_state = len(st)
    print(f"native state minutes: {n_state}")

    cands = []
    for key, g in st.groupby(["date", "ticker"], sort=False):
        d = A.get(key)
        if d is None:
            continue
        tn = d["t"][d["newpos"]]
        ln = d["l"][d["newpos"]]
        for r in g.itertuples():
            t0 = int(r.t); c0 = float(r.c)
            if c0 <= 0:
                continue
            bid = c0 * (1 - L)
            p0 = int(np.searchsorted(tn, t0 + 1, "left"))
            p1 = int(np.searchsorted(tn, t0 + WIN_MIN, "right"))
            if p0 >= p1:
                continue
            w = np.flatnonzero(ln[p0:p1] <= bid)
            if len(w) == 0:
                continue
            p = p0 + int(w[0])
            cands.append({
                "date": r.date, "ticker": r.ticker, "t0": t0, "c0": c0,
                "tf": int(tn[p]), "p": int(p), "rank": int(r.rank),
                "gain": float(r.gain),
                "volx": float(r.volx) if np.isfinite(r.volx) else np.nan,
                "nprint20": float(r.nprint20),
                "prior_flush": int(r.prior_flush),
                "r15": float(r.r15), "pullback": float(r.pullback),
                "bars_so_far": int(r.n_bars),
            })
    cdf = pd.DataFrame(cands)
    n_cand = len(cdf)
    print(f"state minutes with a bid fill: {n_cand}")
    ev = (cdf.sort_values(["date", "ticker", "tf", "t0"])
          .drop_duplicates(["date", "ticker", "tf"], keep="first")
          .reset_index(drop=True))
    multi = (cdf.groupby(["date", "ticker", "tf"])["t0"].size()
             .rename("mult").reset_index())
    ev = ev.merge(multi, on=["date", "ticker", "tf"], how="left")
    n_ev = len(ev)
    print(f"unique flush events: {n_ev}")

    rows = []
    for r in ev.itertuples():
        d = A[(r.date, r.ticker)]
        pf1 = r.c0 * (1 - L)
        fi = int(d["newpos"][r.p])
        cens = bool(r.tf + RACE_MIN > SESSION_END)
        out_p, out_o, both, lc, fmax, fmin, x20, rec = race_pair(
            d, r.p, pf1, r.c0, r.tf)
        rp1 = rets(out_p, pf1, r.c0, lc)
        ro1 = rets(out_o, pf1, r.c0, lc)
        o_f = float(d["o"][fi])
        pf2 = min(o_f, pf1)
        if pf2 < pf1:
            out_p2, _, _, lc2, *_ = race_pair(d, r.p, pf2, r.c0, r.tf)
            rp2 = rets(out_p2, pf2, r.c0, lc2)
        else:
            rp2 = rp1
        hp = d["h"][d["newpos"][:r.p + 1]]
        near = np.flatnonzero(hp >= 0.98 * r.c0)
        if len(near):
            sp_min = float(r.tf - d["t"][d["newpos"][near[-1]]])
            sp_bars = int(r.p - near[-1])
        else:
            sp_min = np.nan; sp_bars = np.nan
        rows.append({
            "date": r.date, "month": r.date[:7], "ticker": r.ticker,
            "rank": r.rank, "gain": r.gain, "t0": r.t0, "tf": r.tf,
            "mult": int(r.mult),
            "depth": float(1 - d["l"][fi] / r.c0),
            "overshoot": float(d["l"][fi] / pf1 - 1),
            "gap": bool(o_f < pf1), "bar_close_rec": bool(d["c"][fi] >= pf1),
            "fill_close": float(d["c"][fi] / pf1 - 1),
            "speed_min": sp_min, "speed_bars": sp_bars,
            "volx": r.volx, "nprint20": r.nprint20,
            "bars_so_far": r.bars_so_far, "px": r.c0, "r15": r.r15,
            "pullback": r.pullback, "prior_flush": r.prior_flush,
            "out_pess": out_p, "out_opt": out_o,
            "amb_both": both,
            "ret_pess": rp1, "ret_opt": ro1,
            "ret2_pess": rp2,
            "censored": cens, "fmax": fmax, "fmin": fmin,
            "x20": x20, "rec": rec,
        })
    d = pd.DataFrame(rows)
    d.to_parquet(ART / "lb18_episodes_events.parquet")

    mult_dist = {"1": int((ev["mult"] == 1).sum()),
                 "2": int((ev["mult"] == 2).sum()),
                 "3-5": int(ev["mult"].between(3, 5).sum()),
                 "6+": int((ev["mult"] >= 6).sum())}
    gap = d[d["gap"]]
    res = d[~d["censored"]]
    out = {
        "defs": {
            "state": "top-3 causal, native 1-min, gain>=1.0 & pullback>=-0.01 & r15>=0.03",
            "touch_bars": "new bars only (n_bars increments), never ffilled prints",
            "bid": f"-{int(L*100)}% below state-minute close, rests {WIN_MIN}m",
            "race": f"target=pre-flush c0, stop=-{int(STOP_L*100)}% below fill, {RACE_MIN}m; fill-bar high>=target counted as win only in opt bound (may have preceded the fill), pess continues; same-bar both-touch always ambiguous (amb_both)",
            "friction": FRICTION, "censored_rule": f"t_flush+{RACE_MIN}>959",
            "x20": "+20% above pre-flush c0 (not off the low)",
            "bars_so_far": "session bars so far at state minute (not name age)",
            "prior_flush": "prior flush episode starts (l<=cummax*0.9 on new bars)",
        },
        "counts": {
            "n_state_minutes": n_state, "n_state_min_filled": n_cand,
            "n_state_min_nofill": n_state - n_cand, "n_events": n_ev,
            "multiplicity": mult_dist,
            "mean_mult": round(float(ev["mult"].mean()), 2),
            "max_mult": int(ev["mult"].max()),
            "n_censored": int(d["censored"].sum()),
        },
        "fill_realism": {
            "gap": round(float(d["gap"].mean()), 4),
            "overshoot_med": round(float(d["overshoot"].median()), 4),
            "overshoot_q75": round(float(d["overshoot"].quantile(0.75)), 4),
            "bar_close_rec": round(float(d["bar_close_rec"].mean()), 4),
            "med_depth": round(float(d["depth"].median()), 4),
        },
        "race_overall": cell(d),
        "gap_sensitivity": {
            "n_gap": int(len(gap)),
            "pay_pess_gap_base": (round(float(gap[~gap["censored"]]["ret_pess"].mean()), 4)
                                  if len(gap[~gap["censored"]]) else None),
            "pay_pess_gap_improved": (round(float(gap[~gap["censored"]]["ret2_pess"].mean()), 4)
                                      if len(gap[~gap["censored"]]) else None),
        },
    }

    def table(col, edges, labels, src=None):
        s = d if src is None else src
        resx = {}
        for lo, hi, lb_ in zip(edges[:-1], edges[1:], labels):
            c = cell(s[(s[col] >= lo) & (s[col] < hi)])
            if c["n"]:
                resx[lb_] = c
                print(f"  {col:12s} {lb_:12s} n={c['n']:5d} winP={c.get('win_pess')} "
                      f"winO={c.get('win_opt')} loss={c.get('loss')} t={c.get('time')} "
                      f"payP_net={c.get('pay_pess_net')} payO_net={c.get('pay_opt_net')} "
                      f"x20={c.get('x20')} fmax={c.get('med_fmax')} fmin={c.get('med_fmin')}")
        return resx

    print("\n# by rank")
    out["rank"] = table("rank", [0.5, 1.5, 2.5, 3.5], ["r1", "r2", "r3"])
    print("\n# by flush tod")
    out["tod"] = table("tf", [570, 630, 690, 780, 960], ["am1", "am2", "mid", "pm"])
    print("\n# by state gain")
    out["gain"] = table("gain", [1, 2, 3, 6, 99], ["100-200", "200-300", "300-600", "600+"])
    print("\n# by depth")
    out["depth"] = table("depth", [0.10, 0.15, 0.25, 0.40, 1.0], ["10-15", "15-25", "25-40", "40+"])
    qs = d["volx"].quantile([0.25, 0.5, 0.75]).to_list()
    print("\n# by volx (new-bar volume ratio)")
    out["volx"] = table("volx", [0, *qs, 1e9], ["q1", "q2", "q3", "q4"])
    print("\n# by bars_so_far (session)")
    out["bars"] = table("bars_so_far", [0, 20, 40, 80, 1e9], ["<20", "20-40", "40-80", "80+"])
    print("\n# by px")
    out["px"] = table("px", [0, 2, 5, 15, 1e9], ["<2", "2-5", "5-15", "15+"])
    print("\n# by prior flush episodes")
    out["prior_flush"] = table("prior_flush", [0, 1, 2, 1e9], ["0", "1", "2+"])
    print("\n# by r15")
    out["r15"] = table("r15", [0.03, 0.06, 0.10, 0.20, 1e9], ["3-6", "6-10", "10-20", "20+"])
    print("\n# by speed (minutes from last >=0.98c0 new bar)")
    spd = d[d["speed_min"].notna()]
    out["speed"] = table("speed_min", [0, 1.001, 3.001, 10.001, 30.001, 1e9],
                         ["<=1", "2-3", "4-10", "11-30", "30+"], src=spd)

    print("\n# runner tail (all events, new bars)")
    fw = d[~d["censored"]]
    out["runner"] = {
        "x20_rate": round(float(fw["x20"].mean()), 3),
        "fmax_q": {str(q): round(float(fw["fmax"].quantile(q)), 3)
                   for q in (0.5, 0.75, 0.9)},
        "fmin_q": {str(q): round(float(fw["fmin"].quantile(q)), 3)
                   for q in (0.5, 0.75, 0.9)},
        "morning_x20": round(float(fw[fw["tf"] <= 630]["x20"].mean()), 3),
    }
    print(" ", out["runner"])

    print("\n# month stability (win_pess / pay_pess_net / n)")
    cells = {
        "all": d, "am1": d[d["tf"] <= 630],
        "am1_r1": d[(d["tf"] <= 630) & (d["rank"] == 1)],
        "am1_r1_300+": d[(d["tf"] <= 630) & (d["rank"] == 1) & (d["gain"] >= 3)],
    }
    stab = {}
    for name, s in cells.items():
        rr = []
        for mo, g in s.groupby("month"):
            c = cell(g)
            if c["n"] >= 10:
                rr.append((mo, c["n"], c.get("win_pess"), c.get("pay_pess_net")))
        if rr:
            stab[name] = rr
            print(f"  {name:12s} months={len(rr)} "
                  f"mean_winP={np.mean([x[2] for x in rr]):.3f} "
                  f"mean_payP_net={np.mean([x[3] for x in rr]):.4f} imax={min(x[2] for x in rr):.2f}/{max(x[2] for x in rr):.2f}")
    out["stability"] = stab

    (ART / "lb18_episodes.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_episodes.json'}")
    cols = ["date", "ticker", "t0", "tf", "mult", "depth", "speed_min", "out_pess",
            "ret_pess", "x20"]
    print("\nfirst events:")
    print(d[cols].head(8).to_string(index=False))


if __name__ == "__main__":
    main()
