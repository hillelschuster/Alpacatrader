#!/usr/bin/env python3
"""PRE-REG-DAYTYPE-01: day/session context conditioning of the flush edge.

Five pre-declared causal cuts over the frozen fill populations, frozen gate,
seen-data measurement only. Producer: factory/scripts/lb18_daytype.py
Artifacts: factory/artifacts/lb18_daytype.json + .parquet
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LBDIR = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"
L = 0.10
NMIN = 390  # minutes 570..959


def _cum_by_first(first_t):
    """count of tickers whose first event minute <= m, for m = 570..959."""
    a = np.zeros(NMIN, dtype=np.int64)
    for t0 in np.asarray(list(first_t), dtype=float):
        idx = int(t0) - 570
        if 0 <= idx < NMIN:
            a[idx:] += 1
    return a


def arrays_from_frames(p, lb):
    """Causal day arrays for one date from its path + lb frames."""
    p = p.sort_values(["ticker", "t"]).reset_index(drop=True)
    g = p.groupby("ticker", sort=False)
    p["pull"] = p["c"] / g["c"].transform("cummax") - 1
    p["r15"] = p["c"] / g["c"].shift(15) - 1
    j = lb.merge(p[["ticker", "t", "pull", "r15"]], on=["ticker", "t"], how="left")
    j["strict"] = ((j["gain"] >= 1.0) & (j["pull"] >= -0.01)
                   & (j["r15"] >= 0.03) & j["pull"].notna() & j["r15"].notna())
    fs = j.loc[j["strict"]].groupby("ticker")["t"].min()
    p["new"] = g["n_bars"].diff().fillna(1) > 0
    p["cm"] = g["c"].transform("cummax")
    nb = p.loc[p["new"]].copy()
    nb["under"] = nb["l"] <= nb["cm"] * (1 - L)
    nb["start"] = nb["under"] & ~nb.groupby("ticker")["under"].shift(1).fillna(False)
    ff = nb.loc[nb["start"]].groupby("ticker")["t"].min()
    return _cum_by_first(fs), _cum_by_first(ff)


def day_arrays(date):
    p = pd.read_parquet(LBDIR / f"path_{date}.parquet")
    lb = pd.read_parquet(LBDIR / f"lb_{date}.parquet")
    return arrays_from_frames(p, lb), None


def build_day_table(dates):
    rows = []
    for d in dates:
        try:
            (ns, nf), _ = day_arrays(d)
        except FileNotFoundError:
            continue
        rows.append(pd.DataFrame({"date": d, "t": np.arange(570, 960),
                                  "n_strict": ns, "n_flush": nf}))
    return pd.concat(rows, ignore_index=True)


def load_fills():
    a = pd.read_parquet(ART / "lb18_oos_oos.parquet")
    a["pop"] = "oos"
    b = pd.read_parquet(ART / "lb18_oos_dev.parquet")
    b["pop"] = "dev"
    f = pd.concat([a, b], ignore_index=True)
    f = f.sort_values(["date", "tf"]).reset_index(drop=True)
    f["prior_stop_today"] = False
    for _, sub in f.groupby("date"):
        for j, (i, row) in enumerate(sub.iterrows()):
            prev = sub.iloc[:j]
            if len(prev) and ((prev["exit_t"] < row["tf"]) & (prev["ret"] < -0.05)).any():
                f.at[i, "prior_stop_today"] = True
    return f


def attach_features(f):
    tab = build_day_table(sorted(f["date"].unique()))
    f = f.copy()
    f["t_join"] = f["tf"] - 1
    m = f.merge(tab, left_on=["date", "t_join"], right_on=["date", "t"],
                how="left", suffixes=("", "_d"))
    m["n_strict"] = m["n_strict"].fillna(0).astype(int)
    m["n_flush"] = m["n_flush"].fillna(0).astype(int)
    return m


CUTS = {
    "n_strict_names_sofar": lambda d: np.where(d["n_strict"] <= 1, "0-1", "2+"),
    "n_flush_names_sofar": lambda d: np.where(d["n_flush"] <= 1, "0-1", "2+"),
    "rank": lambda d: np.where(d["rank"] == 1, "1", "2-3"),
    "prior_stop_today": lambda d: np.where(d["prior_stop_today"], "yes", "no"),
    "session": lambda d: np.where(d["tf"] < 720, "AM", "PM"),
}


def stats(sub):
    if len(sub) == 0:
        return {"n": 0}
    r = sub["ret"]
    monthly = sub.groupby("month")["ret"].mean()
    return {
        "n": int(len(sub)), "mean": float(r.mean()), "median": float(r.median()),
        "win": float((r > 0).mean()), "months_pos": int((monthly > 0).sum()),
        "months": int(len(monthly)), "worst_month": float(monthly.min()),
        "worst5_sum": float(r.nsmallest(5).sum()), "total": float(r.sum()),
    }


def block_of(months_sorted):
    """3 chronological blocks over sorted months (split as 5/5/4)."""
    idx = np.arange(len(months_sorted))
    third = len(months_sorted) // 3
    blk = np.where(idx < third, 0, np.where(idx < 2 * third, 1, 2))
    return {m: int(b) for m, b in zip(months_sorted, blk)}


def evaluate(m, tag):
    oos = m[m["pop"] == "oos"]
    dev = m[m["pop"] == "dev"]
    months = sorted(oos["month"].unique())
    blocks = block_of(months)
    pop_mean_oos = float(oos["ret"].mean())
    pop_mean_dev = float(dev["ret"].mean())
    out = {"population": {"oos": stats(oos), "dev": stats(dev)}, "cuts": {}}
    for name, fn in CUTS.items():
        bins = fn(m)
        m2 = m.assign(_bin=bins)
        res = {"bins": {}}
        bstats = {}
        for b, sub in m2.groupby("_bin"):
            bstats[b] = stats(sub)
        res["bins"] = bstats
        for b in bstats:
            ob = m2[(m2["pop"] == "oos") & (m2["_bin"] == b)]
            db = m2[(m2["pop"] == "dev") & (m2["_bin"] == b)]
            cond = {}
            n_b = len(ob)
            mean_b = float(ob["ret"].mean()) if n_b else None
            cond["a"] = bool(n_b >= 50 and mean_b is not None
                             and mean_b <= pop_mean_oos - 0.003)
            if n_b:
                oth = m2[(m2["pop"] == "oos") & (m2["_bin"] != b)]
                cond["b"] = bool(float(oth["ret"].mean()) - pop_mean_oos >= 0.002)
                cond["d"] = bool(len(oth) >= 0.60 * len(oos))
                bl = (ob.assign(_blk=ob["month"].map(blocks))
                      .groupby("_blk")["ret"].mean().reindex([0, 1, 2]))
                pb = (oos.assign(_blk=oos["month"].map(blocks))
                      .groupby("_blk")["ret"].mean().reindex([0, 1, 2]))
                cond["c"] = bool(((bl < pb).sum()) >= 2)
                cond["e"] = bool(len(db) and float(db["ret"].mean()) < pop_mean_dev)
            else:
                cond["b"] = cond["c"] = cond["d"] = cond["e"] = False
            res["bins"][b]["gate"] = cond
            res["bins"][b]["gate_pass"] = all(cond.values())
            res["bins"][b]["pf2"] = stats(ob[ob["prior_flush"] >= 2])
        res["candidate"] = any(v.get("gate_pass") for v in res["bins"].values() if isinstance(v, dict))
        out["cuts"][name] = res
    return out


def selftest():
    t = np.arange(570, 590)
    closes = np.linspace(10, 12, len(t))
    lows = closes.copy()
    lows[10] = 0.9 * closes[:11].max()  # flush start at t=580 for A
    p = pd.concat([
        pd.DataFrame({"date": "2024-01-02", "t": t, "ticker": "A",
                      "o": closes, "h": closes, "l": lows, "c": closes,
                      "v": 1.0, "gain_c": 1.2,
                      "n_bars": np.arange(1, len(t) + 1)}),
        pd.DataFrame({"date": "2024-01-02", "t": t, "ticker": "B",
                      "o": closes, "h": closes, "l": closes, "c": closes,
                      "v": 1.0, "gain_c": 0.5,
                      "n_bars": np.arange(1, len(t) + 1)}),
    ], ignore_index=True)
    lb = pd.DataFrame({"date": "2024-01-02", "t": [586], "rank": [1],
                       "ticker": ["A"], "gain": [1.2], "px": [closes[-1]]})
    ns, nf = arrays_from_frames(p, lb)
    assert nf[579 - 570] == 0 and nf[580 - 570] == 1, ("flush", nf[579 - 570], nf[580 - 570])
    assert ns[585 - 570] == 0 and ns[586 - 570] == 1, ("strict", ns[585 - 570], ns[586 - 570])
    print("SELFTEST OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    f = attach_features(load_fills())
    m = f[f["n_strict"].notna()].copy()
    result = evaluate(m, "all")
    result["features"] = {"per_fill": "lb18_daytype.parquet"}
    (ART / "lb18_daytype.json").write_text(json.dumps(result, indent=1, default=str))
    keep = ["date", "month", "pop", "ticker", "rank", "tf", "ret", "exit_t",
            "prior_flush", "n_strict", "n_flush", "prior_stop_today"]
    m[keep].to_parquet(ART / "lb18_daytype.parquet", index=False)
    print(f"fills={len(m)} -> {ART/'lb18_daytype.json'}")
    for name, res in result["cuts"].items():
        line = []
        for b, s in res["bins"].items():
            if s["n"]:
                line.append(f"{b}:n={s['n']},mean={s['mean']*100:+.2f}%,gate={s.get('gate_pass')}")
        print(f"  {name}: " + " | ".join(line))
    print("pop oos mean %+.3f%% | dev %+.3f%%" % (
        result["population"]["oos"]["mean"] * 100, result["population"]["dev"]["mean"] * 100))


if __name__ == "__main__":
    main()
