#!/usr/bin/env python3
"""Simple causal order lifecycle on extreme-leader flush bids (one order per
ticker at a time). Lifecycle: a qualifying state minute (in top-3, gain>=1,
fresh, thrust) creates a resting bid at -10% below the decision close; the bid
rests WIN_MIN minutes then cancels (no replacement mid-rest); fill = first NEW
bar low <= bid; at fill-bar close: weak reaction (close < bid + ABORT_TH) exits
at next new-bar open, strong absorption stays and runs an exit policy (fixed
c0/stop, trailing stops, half+trail, EOD hold); the ticker can re-arm only at a
state minute after the order resolves. Semantics reused from lb18_episodes
(load/build: new-bar touches, no ffill bias). Artifact:
factory/artifacts/lb18_lifecycle.json
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import build, load  # noqa: E402

LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"

L = float(os.environ.get("H12_L", "0.10"))
ART_OUT = os.environ.get("H12_ART", "lb18_lifecycle.json")
STOP_L = 0.10
WIN_MIN = 120
RACE_MIN = 60
FRICTION = 0.01
TRAILS = [0.08, 0.10, 0.15, 0.20]


def sim_fixed(fut, o, h, l, c, t, B, c0, tf):
    stop = B * (1 - STOP_L)
    end_t = tf + RACE_MIN
    for j in fut:
        if t[j] > end_t:
            break
        if l[j] <= stop:
            return min(float(o[j]), stop) / B - 1, int(t[j])
        if h[j] >= c0:
            return c0 / B - 1, int(t[j])
    ok = fut[t[fut] <= end_t]
    if len(ok) == 0:
        return 0.0, tf
    j = int(ok[-1])
    return float(c[j]) / B - 1, int(t[j])


def sim_trail(fut, o, h, l, c, t, B, T):
    runmax = B
    for j in fut:
        lvl = runmax * (1 - T)
        if l[j] <= lvl:
            return min(float(o[j]), lvl) / B - 1, int(t[j])
        if h[j] > runmax:
            runmax = float(h[j])
    if len(fut) == 0:
        return 0.0, 0
    j = int(fut[-1])
    return float(c[j]) / B - 1, int(t[j])


def sim_trail_floor(fut, o, h, l, c, t, B, T):
    """Trail T from peak but never give the hard -10% floor back."""
    runmax = B
    for j in fut:
        lvl = max(B * (1 - STOP_L), runmax * (1 - T))
        if l[j] <= lvl:
            return min(float(o[j]), lvl) / B - 1, int(t[j])
        if h[j] > runmax:
            runmax = float(h[j])
    if len(fut) == 0:
        return 0.0, 0
    j = int(fut[-1])
    return float(c[j]) / B - 1, int(t[j])


def sim_scaleout(fut, o, h, l, c, t, B, c0, T=0.15):
    """Half at pre-flush c0, half floor-trail T."""
    h1 = None
    for j in fut:
        if h[j] >= c0:
            h1 = c0 / B - 1
            break
    if h1 is None:
        h1 = (float(c[fut[-1]]) / B - 1) if len(fut) else 0.0
    h2 = sim_trail_floor(fut, o, h, l, c, t, B, T)[0]
    return 0.5 * h1 + 0.5 * h2


def sim_trail_close(fut, o, h, l, c, t, B, T):
    """Close-based trail: exit at a bar close below runmax*(1-T) (no wick stop)."""
    runmax = B
    for j in fut:
        if h[j] > runmax:
            runmax = float(h[j])
        if c[j] <= runmax * (1 - T):
            return float(c[j]) / B - 1, int(t[j])
    if len(fut) == 0:
        return 0.0, 0
    j = int(fut[-1])
    return float(c[j]) / B - 1, int(t[j])


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
    st_groups = {k: g.sort_values("t").reset_index(drop=True)
                 for k, g in st.groupby(["date", "ticker"], sort=False)}
    print(f"tickers with state: {len(st_groups)}  state minutes: {len(st)}")

    rows = []
    n_nofill = 0
    for key, gs in st_groups.items():
        d = A.get(key)
        if d is None:
            continue
        t = d["t"]; o = d["o"]; h = d["h"]; l = d["l"]; c = d["c"]
        npx = d["newpos"]
        st_t = gs["t"].to_numpy()
        resolved_t = -10**9
        for si in range(len(gs)):
            t0 = int(st_t[si])
            if t0 <= resolved_t:
                continue
            pos0 = int(np.searchsorted(t, t0))
            if pos0 >= len(c):
                continue
            c0 = float(c[pos0])
            if c0 <= 0:
                continue
            B = c0 * (1 - L)
            j0 = int(np.searchsorted(npx, pos0, "right"))
            j1 = int(np.searchsorted(t[npx], t0 + WIN_MIN, "right"))
            fill_j = -1
            for jj in range(j0, j1):
                if l[npx[jj]] <= B:
                    fill_j = jj
                    break
            if fill_j < 0:
                n_nofill += 1
                resolved_t = t0 + WIN_MIN
                continue
            fp = int(npx[fill_j])
            tf = int(t[fp])
            r = gs.iloc[si]
            fc = float(c[fp]) / B - 1
            gap = bool(o[fp] < B)
            fw = npx[int(np.searchsorted(npx, fp, "right")):]
            if len(fw):
                ab_ret = float(o[fw[0]]) / B - 1
                ab_t = int(t[fw[0]])
            else:
                ab_ret = fc
                ab_t = tf
            fx = sim_fixed(fw, o, h, l, c, t, B, c0, tf)
            tr = {T: sim_trail(fw, o, h, l, c, t, B, T) for T in TRAILS}
            t15f = sim_trail_floor(fw, o, h, l, c, t, B, 0.15)
            t20f = sim_trail_floor(fw, o, h, l, c, t, B, 0.20)
            t25f = sim_trail_floor(fw, o, h, l, c, t, B, 0.25)
            t30f = sim_trail_floor(fw, o, h, l, c, t, B, 0.30)
            t15c = sim_trail_close(fw, o, h, l, c, t, B, 0.15)
            t20c = sim_trail_close(fw, o, h, l, c, t, B, 0.20)
            so15 = sim_scaleout(fw, o, h, l, c, t, B, c0, 0.15)
            eod_ret = (float(c[fw[-1]]) / B - 1) if len(fw) else 0.0
            eod_t = int(t[fw[-1]]) if len(fw) else tf
            half15 = 0.5 * fx[0] + 0.5 * tr[0.15][0]
            near = np.flatnonzero(h[npx[:fill_j + 1]] >= 0.98 * c0)
            sp_min = float(tf - t[npx[near[-1]]]) if len(near) else np.nan
            fmax_after = float(h[fw].max() / B - 1) if len(fw) else 0.0
            fmin_after = float(l[fw].min() / B - 1) if len(fw) else 0.0
            strong = fc >= 0
            x_t = fx[1] if strong else ab_t
            b_prem = b_fix = b_t15f = b_t20f = b_eod = np.nan
            if len(fw):
                B2 = float(o[fw[0]])
                b_prem = B2 / B - 1
                fw2 = npx[int(np.searchsorted(npx, fw[0], "right")):]
                te = int(t[fw[0]])
                b_fix = sim_fixed(fw2, o, h, l, c, t, B2, c0, te)[0]
                b_t15f = sim_trail_floor(fw2, o, h, l, c, t, B2, 0.15)[0]
                b_t20f = sim_trail_floor(fw2, o, h, l, c, t, B2, 0.20)[0]
                b_eod = (float(c[fw2[-1]]) / B2 - 1) if len(fw2) else 0.0
            rows.append({
                "date": r["date"], "month": r["date"][:7], "ticker": r["ticker"],
                "rank": int(r["rank"]), "gain": float(r["gain"]),
                "r15": float(r["r15"]),
                "volx": float(r["volx"]) if np.isfinite(r["volx"]) else np.nan,
                "prior_flush": int(r["prior_flush"]),
                "bars_so_far": int(r["n_bars"]),
                "t0": t0, "tf": tf, "c0": c0,
                "depth": float(1 - l[fp] / c0),
                "fill_close": fc, "gap": gap, "strong": strong,
                "speed_min": sp_min,
                "ret_abort": ab_ret - FRICTION,
                "ret_fixed": fx[0] - FRICTION,
                "ret_t08": tr[0.08][0] - FRICTION,
                "ret_t10": tr[0.10][0] - FRICTION,
                "ret_t15": tr[0.15][0] - FRICTION,
                "ret_t20": tr[0.20][0] - FRICTION,
                "ret_t15f": t15f[0] - FRICTION,
                "ret_t20f": t20f[0] - FRICTION,
                "ret_t25f": t25f[0] - FRICTION,
                "ret_t30f": t30f[0] - FRICTION,
                "ret_t15c": t15c[0] - FRICTION,
                "ret_t20c": t20c[0] - FRICTION,
                "ret_so15": so15 - FRICTION,
                "ret_half15": half15 - FRICTION,
                "ret_eod": eod_ret - FRICTION,
                "fmax_after": fmax_after, "fmin_after": fmin_after,
                "b_prem": b_prem,
                "b_fix": b_fix - FRICTION if np.isfinite(b_fix) else np.nan,
                "b_t15f": b_t15f - FRICTION if np.isfinite(b_t15f) else np.nan,
                "b_t20f": b_t20f - FRICTION if np.isfinite(b_t20f) else np.nan,
                "b_eod": b_eod - FRICTION if np.isfinite(b_eod) else np.nan,
                "exit_t": x_t, "resolved_t": x_t,
            })
            resolved_t = int(x_t)
    d = pd.DataFrame(rows)
    ORDERS_OUT = os.environ.get("H12_ORD", "lb18_lifecycle_orders.parquet")
    d.to_parquet(ART / ORDERS_OUT)
    pols = ["ret_abort", "ret_fixed", "ret_t08", "ret_t10", "ret_t15",
            "ret_t20", "ret_t15f", "ret_t20f", "ret_t25f", "ret_t30f",
            "ret_t15c", "ret_t20c", "ret_so15", "ret_half15", "ret_eod"]
    print(f"orders filled: {len(d)}  no-fill orders: {n_nofill}  "
          f"weak: {(~d['strong']).sum()}  strong: {d['strong'].sum()}")

    def pol_stats(s, tag):
        out = {"n": int(len(s))}
        for p in pols:
            v = s[p]
            out[p] = round(float(v.mean()), 4)
            out[p + "_med"] = round(float(v.median()), 4)
        print(f"  {tag:22s} n={out['n']:4d} " +
              " ".join(f"{p.replace('ret_','')}={out[p]:+.3f}" for p in pols))
        return out

    print("\n# policies: all fills")
    out = {"counts": {"orders_filled": int(len(d)), "no_fill": n_nofill,
                      "weak": int((~d["strong"]).sum()),
                      "strong": int(d["strong"].sum())}}
    out["all"] = pol_stats(d, "all")
    print("\n# policies: strong fills only (stay path)")
    out["strong_only"] = pol_stats(d[d["strong"]], "strong")
    print("\n# policies: weak fills only (abort path)")
    out["weak_only"] = pol_stats(d[~d["strong"]], "weak")

    print("\n# decision sensitivity: abort if fill_close<th, else trail15-floor")
    sens = {}
    base_ret = d["ret_t15f"].copy()
    for th in (-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.05):
        v = np.where(d["fill_close"] < th, d["ret_abort"], base_ret)
        vf = np.where(d["fill_close"] < th, 1.0, 0.0)
        sens[str(th)] = {"n_abort": int(vf.sum()),
                         "mean": round(float(v.mean()), 4),
                         "median": round(float(np.median(v)), 4)}
        print(f"  th={th:+.2f} aborts={sens[str(th)]['n_abort']:4d} "
              f"mean={sens[str(th)]['mean']:+.4f} med={sens[str(th)]['median']:+.4f}")
    out["decision_sensitivity"] = sens

    d["ret_combo"] = np.where(d["fill_close"] < 0, d["ret_abort"], d["ret_t15f"])
    d["ret_combo2"] = np.where(d["fill_close"] < 0.02, d["ret_abort"], d["ret_t15f"])
    out["combo"] = {
        "th0": {"n_abort": int((d["fill_close"] < 0).sum()),
                "mean": round(float(d["ret_combo"].mean()), 4),
                "median": round(float(d["ret_combo"].median()), 4)},
        "th2": {"n_abort": int((d["fill_close"] < 0.02).sum()),
                "mean": round(float(d["ret_combo2"].mean()), 4),
                "median": round(float(d["ret_combo2"].median()), 4)},
    }
    print(" ", out["combo"])

    print("\n# capture: strong fills, trail15-floor vs available fmax_after")
    s = d[d["strong"]].copy()
    s["cap"] = np.where(s["fmax_after"] > 0, s["ret_t15f"] / s["fmax_after"], np.nan)
    out["capture"] = {
        "med_fmax_after": round(float(s["fmax_after"].median()), 3),
        "med_ret_t15": round(float(s["ret_t15"].median()), 3),
        "med_capture": round(float(s["cap"].median()), 3),
        "share_fmax_ge_20": round(float((s["fmax_after"] >= 0.20).mean()), 3),
        "share_ge_50": round(float((s["fmax_after"] >= 0.50).mean()), 3),
    }
    print(" ", out["capture"])

    print("\n# conditioning of trail15-floor on strong fills")
    cond = {}
    for col, edges, labels in [
        ("r15", [0.03, 0.06, 0.10, 0.20, 9e9], ["3-6", "6-10", "10-20", "20+"]),
        ("gain", [1, 2, 3, 6, 99], ["100-200", "200-300", "300-600", "600+"]),
        ("fill_close", [0, 0.02, 0.05, 9e9], ["0-2", "2-5", "5+"]),
    ]:
        cond[col] = {}
        for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
            ss = s[(s[col] >= lo) & (s[col] < hi)]
            if len(ss) >= 10:
                cond[col][lab] = {"n": int(len(ss)),
                                  "ret_t15f": round(float(ss["ret_t15f"].mean()), 4),
                                  "ret_t20f": round(float(ss["ret_t20f"].mean()), 4),
                                  "ret_fixed": round(float(ss["ret_fixed"].mean()), 4),
                                  "ret_eod": round(float(ss["ret_eod"].mean()), 4)}
                print(f"  {col:10s} {lab:8s} {cond[col][lab]}")
    qs = s["volx"].quantile([0.5]).iloc[0]
    for lab, ss in [("volx>=med", s[s["volx"] >= qs]), ("volx<med", s[s["volx"] < qs]),
                    ("rank1", s[s["rank"] == 1]),
                    ("prior_flush>=2", s[s["prior_flush"] >= 2]),
                    ("speed<=1", s[s["speed_min"] <= 1])]:
        if len(ss) >= 10:
            cond[lab] = {"n": int(len(ss)),
                         "ret_t15f": round(float(ss["ret_t15f"].mean()), 4),
                         "ret_t20f": round(float(ss["ret_t20f"].mean()), 4),
                         "ret_fixed": round(float(ss["ret_fixed"].mean()), 4),
                         "ret_eod": round(float(ss["ret_eod"].mean()), 4)}
            print(f"  {lab:16s} {cond[lab]}")
    out["conditioning"] = cond

    d["ret_combo20f"] = np.where(d["fill_close"] < 0, d["ret_abort"], d["ret_t20f"])
    print("\n# full population: separation by pre-bid context")
    print("  (n, P(strong), mean ret_combo20f, mean ret_t15f, mean ret_abort)")
    sep = {}
    specs = [
        ("rank", [0.5, 1.5, 2.5, 3.5], ["r1", "r2", "r3"]),
        ("gain", [1, 2, 3, 6, 99], ["100-200", "200-300", "300-600", "600+"]),
        ("r15", [0.03, 0.06, 0.10, 0.20, 9e9], ["3-6", "6-10", "10-20", "20+"]),
        ("depth", [0.10, 0.13, 0.18, 1.0], ["10-13", "13-18", "18+"]),
        ("speed_min", [-1, 0.5, 1.5, 3.5, 9e9], ["0", "1", "2-3", "4+"]),
        ("prior_flush", [0, 1, 2, 1e9], ["0", "1", "2+"]),
        ("bars_so_far", [0, 20, 40, 80, 1e9], ["<20", "20-40", "40-80", "80+"]),
        ("c0", [0, 2, 5, 15, 1e9], ["<2", "2-5", "5-15", "15+"]),
        ("t0", [570, 600, 630, 690, 960], ["9:30-10", "10-10:30", "10:30-11:30", "11:30+"]),
        ("volx", [0, 0.75, 1.5, 3, 1e9], ["<0.75", "0.75-1.5", "1.5-3", "3+"]),
    ]
    for col, edges, labels in specs:
        sep[col] = {}
        for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
            ss = d[(d[col] >= lo) & (d[col] < hi)]
            if len(ss) >= 15:
                sep[col][lab] = {
                    "n": int(len(ss)),
                    "p_strong": round(float(ss["strong"].mean()), 3),
                    "combo20f": round(float(ss["ret_combo20f"].mean()), 4),
                    "t15f": round(float(ss["ret_t15f"].mean()), 4),
                    "abort": round(float(ss["ret_abort"].mean()), 4),
                }
                print(f"  {col:12s} {lab:10s} {sep[col][lab]}")
    for lab, mask in [
        ("gap fill", d["gap"]), ("clean fill", ~d["gap"]),
        ("rank1 & gain>=3", (d["rank"] == 1) & (d["gain"] >= 3)),
        ("r15>=0.20 & speed<=1", (d["r15"] >= 0.20) & (d["speed_min"] <= 1)),
        ("rank1 & r15>=0.20", (d["rank"] == 1) & (d["r15"] >= 0.20)),
        ("depth<=0.13 & rank1", (d["depth"] <= 0.13) & (d["rank"] == 1)),
    ]:
        ss = d[mask.fillna(False)]
        if len(ss) >= 10:
            sep[lab] = {
                "n": int(len(ss)),
                "p_strong": round(float(ss["strong"].mean()), 3),
                "combo20f": round(float(ss["ret_combo20f"].mean()), 4),
                "t15f": round(float(ss["ret_t15f"].mean()), 4),
                "abort": round(float(ss["ret_abort"].mean()), 4),
            }
            print(f"  {lab:22s} {sep[lab]}")
    out["separation"] = sep

    print("\n# stay rules (post-fill info decides): stay->policy, else abort")
    d["ret_abortc"] = d["fill_close"] - FRICTION
    rules = {}
    stay_specs = {
        "fc>=0": (d["fill_close"] >= 0),
        "fc>=0 & d<=13": (d["fill_close"] >= 0) & (d["depth"] <= 0.13),
        "fc>=0 & d<=13 & pf>=1": ((d["fill_close"] >= 0) & (d["depth"] <= 0.13)
                                  & (d["prior_flush"] >= 1)),
        "fc>=0.02 & d<=13": (d["fill_close"] >= 0.02) & (d["depth"] <= 0.13),
    }
    for sname, smask in stay_specs.items():
        for pname, pcol in [("t15f", "ret_t15f"), ("t20f", "ret_t20f")]:
            for aname, acol in [("ab_open", "ret_abort"), ("ab_close", "ret_abortc")]:
                v = np.where(smask, d[pcol], d[acol])
                mv = pd.Series(v, index=d.index).groupby(d["month"]).mean()
                key = f"{sname} | {pname} | {aname}"
                rules[key] = {
                    "n_stay": int(smask.sum()),
                    "mean": round(float(np.mean(v)), 4),
                    "median": round(float(np.median(v)), 4),
                    "months_pos": int((mv > 0).sum()),
                    "n_months": int(len(mv)),
                }
                print(f"  {key:38s} n_stay={rules[key]['n_stay']:4d} "
                      f"mean={rules[key]['mean']:+.4f} med={rules[key]['median']:+.4f} "
                      f"months+={rules[key]['months_pos']}/{rules[key]['n_months']}")
    out["stay_rules"] = rules

    print("\n# absorb-entry variant: no resting bid; after flush bar closes, "
          "enter at next new-bar open only if fc>=0 (skip weak), else no trade")
    taken = d[d["fill_close"] >= 0].copy()
    ev_total = len(d)
    absorb = {"n_events": ev_total, "n_taken": int(len(taken)),
              "prem_med": round(float(taken["b_prem"].median()), 4)}
    for pcol in ["b_fix", "b_t15f", "b_t20f", "b_eod"]:
        pt = taken[pcol].dropna()
        mv = taken.groupby("month")[pcol].mean().dropna()
        absorb[pcol] = {
            "mean_trade": round(float(pt.mean()), 4),
            "med_trade": round(float(pt.median()), 4),
            "per_event": round(float(pt.sum() / ev_total), 4),
            "months_pos": int((mv > 0).sum()), "n_months": int(len(mv)),
            "months": {k: round(float(x), 4) for k, x in mv.items()},
        }
        print(f"  {pcol}: n_trades={len(pt)} mean={absorb[pcol]['mean_trade']:+.4f} "
              f"med={absorb[pcol]['med_trade']:+.4f} per_event={absorb[pcol]['per_event']:+.4f} "
              f"months+={absorb[pcol]['months_pos']}/{absorb[pcol]['n_months']}")
    for lab, mask in [("fc>=0.02", taken["fill_close"] >= 0.02),
                      ("d<=13 & fc>=0", taken["depth"] <= 0.13),
                      ("d<=13 & fc>=0.02", (taken["depth"] <= 0.13) & (taken["fill_close"] >= 0.02))]:
        s2 = taken[mask]
        if len(s2) < 10:
            continue
        mv = s2.groupby("month")["b_t20f"].mean().dropna()
        absorb[lab] = {"n": int(len(s2)),
                       "mean_b_t20f": round(float(s2["b_t20f"].mean()), 4),
                       "med": round(float(s2["b_t20f"].median()), 4),
                       "months_pos": int((mv > 0).sum()), "n_months": int(len(mv))}
        print(f"  {lab}: {absorb[lab]}")
    out["absorb_entry"] = absorb

    print("\n# monthly (mean ret per order) for key policies")
    mon = d.groupby("month")[["ret_fixed", "ret_t15", "ret_t20f", "ret_so15",
                              "ret_eod", "ret_combo", "ret_combo20f"]].mean()
    mon["n"] = d.groupby("month").size()
    mon_out = {}
    for mo, r_ in mon.iterrows():
        mon_out[mo] = {k: round(float(v), 4) for k, v in r_.items()}
        print(f"  {mo} n={int(r_['n']):3d} fixed={r_['ret_fixed']:+.3f} "
              f"t20f={r_['ret_t20f']:+.3f} so15={r_['ret_so15']:+.3f} "
              f"c20f={r_['ret_combo20f']:+.3f} eod={r_['ret_eod']:+.3f}")
    out["monthly"] = mon_out
    pos = {p: int((mon[p] > 0).sum()) for p in ["ret_fixed", "ret_t15", "ret_t20f",
                                                "ret_so15", "ret_eod", "ret_combo",
                                                "ret_combo20f"]}
    out["months_positive"] = pos
    print("  months positive:", pos)

    ev = pd.read_parquet(ART / "lb18_episodes_events.parquet")
    out["reconciliation"] = {
        "events_total": int(len(ev)),
        "events_censored": int(ev["censored"].sum()),
        "gain_300_600_all": int(((ev["gain"] >= 3) & (ev["gain"] < 6)).sum()),
        "gain_600plus_all": int((ev["gain"] >= 6).sum()),
        "gain_ge3_depth_le15_noncens": int(((ev["gain"] >= 3) & (ev["depth"] <= 0.15) & ~ev["censored"]).sum()),
        "absorp_ge0_noncens": int(((ev["fill_close"] >= 0) & ~ev["censored"]).sum()),
        "absorp_lt0_noncens": int(((ev["fill_close"] < 0) & ~ev["censored"]).sum()),
    }
    print("\n# reconciliation vs committed events parquet")
    for k_, v_ in out["reconciliation"].items():
        print(f"  {k_}: {v_}")

    (ART / ART_OUT).write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/ART_OUT}")


if __name__ == "__main__":
    main()
