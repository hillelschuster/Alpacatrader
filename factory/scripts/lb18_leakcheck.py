#!/usr/bin/env python3
"""Leak check for lb18_moments findings.

Path files contain the union of names that were top-3 at ANY point that day.
So a moment at time t on a name that only becomes top-3 later (ft > t) is
selected by future information (survivorship). Split every event by:
  in_top3     currently in top-3 at t (causal, unbiased)
  was_hot     not now, but first top-3 appearance BEFORE t (causal)
  future_hot  first top-3 appearance AFTER t (CONTAMINATED subset)
Only causal subsets count as evidence.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"


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
    gk = ["date", "ticker"]
    gb = paths.groupby(gk, sort=False)
    t, c, h, l, nb = (paths["t"], paths["c"], paths["h"], paths["l"],
                      paths["n_bars"])
    gc = paths["gain_c"]

    new_bar = nb.groupby([paths["date"], paths["ticker"]]).diff().fillna(1) > 0
    last_new = t.where(new_bar).groupby([paths["date"], paths["ticker"]]).ffill()
    prev_mins = (t - last_new).groupby([paths["date"], paths["ticker"]]).shift(1)
    ev_resume = new_bar & (prev_mins >= 3)

    hp = h >= h.groupby([paths["date"], paths["ticker"]]).cummax()
    prev_hp = t.where(hp).groupby([paths["date"], paths["ticker"]]).ffill()
    prev_hp = prev_hp.groupby([paths["date"], paths["ticker"]]).shift(1)
    ev_fresh = hp & ((t - prev_hp) >= 10)

    first = (lb.sort_values(["date", "ticker", "t"])
             .drop_duplicates(["date", "ticker"])[["date", "ticker", "t"]])
    first.columns = ["date", "ticker", "ft"]
    paths = paths.merge(first, on=["date", "ticker"], how="left")
    lbminute = lb[["date", "ticker", "t"]].assign(now=1)
    paths = paths.merge(lbminute, on=["date", "ticker", "t"], how="left")
    now = paths["now"].fillna(0).astype(bool)
    was_hot = ~now & (paths["ft"] <= paths["t"])
    fut_hot = ~now & (paths["ft"] > paths["t"])

    paths["f60"] = gb["c"].shift(-60) / c - 1
    paths["f120"] = gb["c"].shift(-120) / c - 1
    n = len(paths)
    ku = np.full(n, 999, dtype=np.int32)
    kd = np.full(n, 999, dtype=np.int32)
    cu = c.to_numpy()
    for k in range(1, 121):
        hu = gb["h"].shift(-k).to_numpy()
        hl = gb["l"].shift(-k).to_numpy()
        m1 = (hu >= cu * 1.20) & (ku == 999)
        ku[m1] = k
        m2 = (hl <= cu * 0.80) & (kd == 999)
        kd[m2] = k
    up = (ku < 999) & ((kd == 999) | (ku < kd))
    dn = (kd < 999) & ((ku == 999) | (kd < ku))

    def tab(mask, tag):
        s = paths[mask.fillna(False)]
        if len(s) < 30:
            print(f"  {tag:26s} n={len(s)}")
            return
        i = s.index.to_numpy()
        print(f"  {tag:26s} n={len(s):6d} gain={s['gain_c'].median()*100:5.0f}% "
              f"f60={s['f60'].median():+.3f} f120={s['f120'].median():+.3f} "
              f"upf={up[i].mean():.3f} dnf={dn[i].mean():.3f} "
              f"q75={np.nanquantile(s['f120'],0.75):+.3f}")

    for name, ev in (("resume", ev_resume), ("fresh_high", ev_fresh)):
        print(f"\n### {name} by membership timing (leak check)")
        tab(ev & now, "in_top3 (causal)")
        tab(ev & was_hot.fillna(False), "was_hot (causal)")
        tab(ev & fut_hot.fillna(False), "future_hot (CONTAMINATED)")
        # split future_hot by how much later
        fh = paths["ft"] - paths["t"]
        tab(ev & fut_hot.fillna(False) & (fh > 60), "future_hot, ft-t>60 (contam)")
        # the causal union
        this30 = paths["gain_c"].between(0.10, 0.30)
        tab(ev & (now | was_hot.fillna(False)) & this30, "causal, gain 10-30%")
        tab(ev & fut_hot.fillna(False) & this30, "contam, gain 10-30%")


if __name__ == "__main__":
    main()
