#!/usr/bin/env python3
"""H12 discovery: monthly stability of the key asymmetric event moments
(resume, fresh_high, tight_break, cross_25, first_top3) + raw example paths.

Recomputes events per month over the 18-month top-3 paths, prints:
  month | n | med_f60 | med_f120 | upf20 | dnf20  (per event)
Plus 2 raw example paths (resume + fresh_high) to ground the stats.
"""
import sys
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
    dt = paths["date"]

    new_bar = nb.groupby([dt, paths["ticker"]]).diff().fillna(1) > 0
    last_new = t.where(new_bar).groupby([dt, paths["ticker"]]).ffill()
    mins_since = t - last_new
    prev_mins = mins_since.groupby([dt, paths["ticker"]]).shift(1)
    ev_resume = new_bar & (prev_mins >= 3)

    hp = h >= h.groupby([dt, paths["ticker"]]).cummax()
    prev_hp = t.where(hp).groupby([dt, paths["ticker"]]).ffill()
    prev_hp = prev_hp.groupby([dt, paths["ticker"]]).shift(1)
    ev_fresh = hp & ((t - prev_hp) >= 10)

    r3 = c / gb["c"].shift(3) - 1
    rng5 = (gb["c"].transform(lambda s: s.rolling(5, min_periods=5).max())
            / gb["c"].transform(lambda s: s.rolling(5, min_periods=5).min()) - 1)
    tb = (r3 >= 0.03) & (rng5.shift(1) < 0.03)
    ev_tight = tb & ~tb.groupby([dt, paths["ticker"]]).shift(1, fill_value=False)

    prior_max = gc.groupby([dt, paths["ticker"]]).cummax().shift(1)
    ev_c25 = (gc >= 0.25) & (prior_max < 0.25)

    first = lb.sort_values(["date", "ticker", "t"]).drop_duplicates(["date", "ticker"])
    ev_first = paths.merge(first[["date", "ticker", "t"]].assign(x=1),
                           on=["date", "ticker", "t"], how="left")["x"].fillna(0).astype(bool)

    events = {"resume": ev_resume, "fresh_high": ev_fresh, "tight_break": ev_tight,
              "cross_25": ev_c25, "first_top3": ev_first}
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
    paths["upf"] = (ku < 999) & ((kd == 999) | (ku < kd))
    paths["dnf"] = (kd < 999) & ((ku == 999) | (kd < ku))
    paths["_m"] = paths["date"].str[:7]

    for name, flag in events.items():
        d = paths[flag.fillna(False)]
        print(f"\n== {name}")
        rows = []
        for m, dd in d.groupby("_m"):
            rows.append((m, len(dd), dd["f60"].median(), dd["f120"].median(),
                         dd["upf"].mean(), dd["dnf"].mean()))
        df = pd.DataFrame(rows, columns=["month", "n", "f60", "f120", "upf20", "dnf20"])
        df[["f60", "f120", "upf20", "dnf20"]] = (df[["f60", "f120", "upf20", "dnf20"]] * 100).round(1)
        pos = (pd.to_numeric(df["upf20"]) > pd.to_numeric(df["dnf20"])).mean()
        print(f"   months up>dn: {pos:.0%}  med monthly n: {df['n'].median():.0f}")
        print(df.to_string(index=False))

    # raw examples: first 2 resume + first 2 fresh_high for 2025-07
    ex = paths[paths["_m"] == "2025-07"]
    for name, flag in (("resume", ev_resume), ("fresh_high", ev_fresh)):
        f = flag.loc[ex.index]
        picks = ex[f.fillna(False)].head(2)
        for _, r in picks.iterrows():
            k = ex[(ex["date"] == r["date"]) & (ex["ticker"] == r["ticker"])]
            k = k[(k["t"] >= r["t"] - 6) & (k["t"] <= r["t"] + 25)]
            print(f"\n-- {name} {r['date']} {r['ticker']} t={r['t']} "
                  f"gain={r['gain_c']*100:.0f}%")
            print(k[["t", "c", "gain_c", "n_bars", "upf", "dnf"]].to_string(index=False))


if __name__ == "__main__":
    main()
