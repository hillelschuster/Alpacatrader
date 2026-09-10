#!/usr/bin/env python3
"""Discovery #5: event-moment study on the 18-month top-3 paths.

Causal event definitions (computed per date/ticker from path rows, all info <= t):
  resume        first new bar after >=3 minutes without one (halt resume)
  fresh_high    new session-high print after >=10 min pause since prior high print
  tight_break   r3 >= +3% out of a tight 5-bar consolidation (<3% range)
  flush         r3 <= -8% while causal gain >= 25%
  first_top3    first minute this (date,ticker) appears in the top-3
  cross_25/50/100/200  first time causal gain crosses that level that day

For each event (first minute of each run): subsequent path stats from c[t] —
f5..f120 medians, q25/q75, P(+-20% within 60/120), P(+50%/120),
SYMMETRIC first-touch ordering +10%/10% and +20%/20% within 120 bars
(no target committed; pure description), med_tmax/med_tmin.
Dedup: events kept t%1==0 but runs reduced to first minute; stats on all kept.
Artifact: factory/artifacts/lb18_events.json
"""
import json
import sys
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


def main():
    paths, lb = load()
    g = ["date", "ticker"]
    gb = paths.groupby(g, sort=False)
    t, c, h, l, nb = (paths["t"], paths["c"], paths["h"], paths["l"],
                      paths["n_bars"])
    gc = paths["gain_c"]

    new_bar = nb.groupby([paths["date"], paths["ticker"]]).diff().fillna(1) > 0
    last_new = t.where(new_bar).groupby([paths["date"], paths["ticker"]]).ffill()
    mins_since = t - last_new
    prev_mins = mins_since.groupby([paths["date"], paths["ticker"]]).shift(1)
    ev_resume = new_bar & (prev_mins >= 3)

    hp = h >= h.groupby([paths["date"], paths["ticker"]]).cummax()
    prev_hp = t.where(hp).groupby([paths["date"], paths["ticker"]]).ffill()
    prev_hp = prev_hp.groupby([paths["date"], paths["ticker"]]).shift(1)
    ev_fresh = hp & ((t - prev_hp) >= 10)

    r3 = c / gb["c"].shift(3) - 1
    rng5 = (gb["c"].transform(lambda s: s.rolling(5, min_periods=5).max())
            / gb["c"].transform(lambda s: s.rolling(5, min_periods=5).min()) - 1)
    ev_tight = (r3 >= 0.03) & (rng5.shift(1) < 0.03)
    ev_flush = (r3 <= -0.08) & (gc >= 0.25)

    first = lb.sort_values(["date", "ticker", "t"]).drop_duplicates(["date", "ticker"])
    ev_first = paths.merge(first[["date", "ticker", "t"]].assign(first_top3=1),
                           on=["date", "ticker", "t"], how="left")["first_top3"].fillna(0).astype(bool)

    events = {"resume": ev_resume, "fresh_high": ev_fresh,
              "tight_break": ev_tight, "flush": ev_flush,
              "first_top3": ev_first}
    prior_max = gc.groupby([paths["date"], paths["ticker"]]).cummax().shift(1)
    for L in [0.25, 0.50, 1.00, 2.00]:
        events[f"cross_{int(L*100)}"] = (gc >= L) & (prior_max < L)

    # forward path stats + orderings
    for hz in [5, 15, 30, 60, 120]:
        paths[f"f{hz}"] = gb["c"].shift(-hz) / c - 1
    n = len(paths)
    fmax = {60: np.full(n, np.nan), 120: np.full(n, np.nan)}
    fmin = {60: np.full(n, np.nan), 120: np.full(n, np.nan)}
    for hz in (60, 120):
        fm = np.full(n, -np.inf)
        fn = np.full(n, np.inf)
        for k in range(1, hz + 1):
            f = (gb["c"].shift(-k) / c - 1).to_numpy()
            fm = np.maximum(fm, np.nan_to_num(f, nan=-np.inf))
            fn = np.minimum(fn, np.nan_to_num(f, nan=np.inf))
        fm[fm == -np.inf] = np.nan
        fn[fn == np.inf] = np.nan
        fmax[hz], fmin[hz] = fm, fn
    # timing of max/min within 120
    tmaxk = np.full(n, np.nan)
    tmink = np.full(n, np.nan)
    for k in range(1, 121):
        f = (gb["c"].shift(-k) / c - 1).to_numpy()
        upd = np.isfinite(f) & (f >= np.nan_to_num(fmax[120], nan=-np.inf) - 1e-9) & np.isnan(tmaxk)
        tmaxk[upd] = k
        upd2 = np.isfinite(f) & (f <= np.nan_to_num(fmin[120], nan=np.inf) + 1e-9) & np.isnan(tmink)
        tmink[upd2] = k
    # symmetric first-touch ordering within 120 bars
    def order_sym(x):
        ku = np.full(n, 999, dtype=np.int32)
        kd = np.full(n, 999, dtype=np.int32)
        cu = c.to_numpy()
        for k in range(1, 121):
            hu = gb["h"].shift(-k).to_numpy()
            hl = gb["l"].shift(-k).to_numpy()
            m1 = (hu >= cu * (1 + x)) & (ku == 999)
            ku[m1] = k
            m2 = (hl <= cu * (1 - x)) & (kd == 999)
            kd[m2] = k
        up = (ku < 999) & ((kd == 999) | (ku < kd))
        dn = (kd < 999) & ((ku == 999) | (kd < ku))
        return np.where(up, 1, np.where(dn, -1, 0))
    ord10, ord20 = order_sym(0.10), order_sym(0.20)

    paths["_f"] = np.arange(n)
    ctx = {"fmax60": fmax[60], "fmin60": fmin[60], "fmax120": fmax[120],
           "fmin120": fmin[120], "tmax": tmaxk, "tmin": tmink,
           "ord10": ord10, "ord20": ord20}

    out = {}
    tod_buckets = [(571, 630), (631, 720), (721, 959)]
    for name, flag in events.items():
        sub = paths[flag.fillna(False)]
        if len(sub) == 0:
            continue
        i = sub["_f"].to_numpy()
        d = {
            "n": int(len(sub)),
            "med_gain_pct": round(float(sub["gain_c"].median() * 100), 1),
            "med_tod": int(sub["t"].median()),
        }
        for hz in [5, 15, 30, 60, 120]:
            d[f"med_f{hz}"] = round(float(sub[f"f{hz}"].median()), 4)
        d["q25_f120"] = round(float(np.nanquantile(sub["f120"], 0.25)), 4)
        d["q75_f120"] = round(float(np.nanquantile(sub["f120"], 0.75)), 4)
        d["p_e20_60"] = round(float((ctx["fmax60"][i] >= 0.20).mean()), 4)
        d["p_f20_60"] = round(float((ctx["fmin60"][i] <= -0.20).mean()), 4)
        d["p_e50_120"] = round(float((ctx["fmax120"][i] >= 0.50).mean()), 4)
        d["p_f50_120"] = round(float((ctx["fmin120"][i] <= -0.50).mean()), 4)
        d["med_tmax"] = round(float(np.nanmedian(ctx["tmax"][i])), 1)
        d["med_tmin"] = round(float(np.nanmedian(ctx["tmin"][i])), 1)
        for x, arr in (("10", ctx["ord10"]), ("20", ctx["ord20"])):
            o = arr[i]
            d[f"upfirst{x}"] = round(float((o == 1).mean()), 4)
            d[f"dnfirst{x}"] = round(float((o == -1).mean()), 4)
        for lo, hi in tod_buckets:
            m = (sub["t"] >= lo) & (sub["t"] <= hi)
            if m.sum() < 50:
                continue
            dd = {
                "n": int(m.sum()),
                "med_f60": round(float(sub.loc[m, "f60"].median()), 4),
                "p_e20_60": round(float((ctx["fmax60"][i][m.to_numpy()] >= 0.20).mean()), 4),
                "p_f20_60": round(float((ctx["fmin60"][i][m.to_numpy()] <= -0.20).mean()), 4),
                "upfirst20": round(float((ctx["ord20"][i][m.to_numpy()] == 1).mean()), 4),
                "dnfirst20": round(float((ctx["ord20"][i][m.to_numpy()] == -1).mean()), 4),
            }
            d.setdefault("tod", {})[f"{lo}-{hi}"] = dd
        out[name] = d
        print(f"\n== {name}: n={d['n']} med_gain={d['med_gain_pct']}% "
              f"tod~{d['med_tod']}")
        print(f"   f15={d['med_f15']:+.3f} f60={d['med_f60']:+.3f} "
              f"f120={d['med_f120']:+.3f} (q25 {d['q25_f120']:+.3f} q75 {d['q75_f120']:+.3f})")
        print(f"   e20/60={d['p_e20_60']:.3f} f20/60={d['p_f20_60']:.3f} "
              f"e50/120={d['p_e50_120']:.3f} f50/120={d['p_f50_120']:.3f}")
        print(f"   upf10/dnf10={d['upfirst10']:.3f}/{d['dnfirst10']:.3f} "
              f"upf20/dnf20={d['upfirst20']:.3f}/{d['dnfirst20']:.3f} "
              f"tmax~{d['med_tmax']} tmin~{d['med_tmin']}")

    (ART / "lb18_events.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART/'lb18_events.json'}")


if __name__ == "__main__":
    main()
