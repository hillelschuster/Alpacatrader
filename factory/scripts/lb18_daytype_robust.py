#!/usr/bin/env python3
"""PRE-REG-DAYTYPE-01 Amendment A1: robustness of the H029 day-breadth candidate.

R1 LOMO sign, R2 day-clustered bootstrap, R3 A3b direction, R4 alternative
breadth, R5 concentration, R6 permutation placebo. No new cuts, no adoption.
Producer: factory/scripts/lb18_daytype_robust.py
Artifact: factory/artifacts/lb18_daytype_robust.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lb18_daytype as dt  # noqa: E402

ROOT = dt.ROOT
ART = dt.ART
RNG = np.random.default_rng(20260913)


def period(d, pop):
    return d[d["pop"] == pop].copy()


def add_ns(d):
    d = d.copy()
    d["ns"] = np.where(d["n_strict"] <= 1, "0-1", "2+")
    return d


def diff_of(x):
    a = x[x["ns"] == "0-1"]["ret"]
    b = x[x["ns"] == "2+"]["ret"]
    if len(a) == 0 or len(b) == 0:
        return float("nan")
    return float(a.mean() - b.mean())


def r1_lomo(x):
    folds = []
    for m in sorted(x["month"].unique()):
        d = diff_of(x[x["month"] != m])
        folds.append({"dropped": m, "diff": d})
    return {"folds": folds, "sign_positive": int(sum(1 for f in folds if f["diff"] > 0)),
            "n_folds": len(folds),
            "pass": int(sum(1 for f in folds if f["diff"] > 0)) >= 12}


def r2_bootstrap(x, n=5000):
    day = x.groupby("date").apply(
        lambda g: pd.Series({
            "s01": g.loc[g["ns"] == "0-1", "ret"].sum(),
            "c01": int((g["ns"] == "0-1").sum()),
            "s2": g.loc[g["ns"] == "2+", "ret"].sum(),
            "c2": int((g["ns"] == "2+").sum())}), include_groups=False)
    s01 = day["s01"].to_numpy(); c01 = day["c01"].to_numpy()
    s2 = day["s2"].to_numpy(); c2 = day["c2"].to_numpy()
    nd = len(day)
    draw = RNG.integers(0, nd, size=(n, nd))
    m01 = s01[draw].sum(axis=1) / np.maximum(c01[draw].sum(axis=1), 1)
    m2 = s2[draw].sum(axis=1) / np.maximum(c2[draw].sum(axis=1), 1)
    arr = m01 - m2
    lo, hi = np.percentile(arr, [2.5, 97.5])
    return {"n": int(n), "mean": float(arr.mean()), "p2_5": float(lo),
            "p97_5": float(hi), "pass": bool(lo > 0), "observed": diff_of(x)}


def r3_a3b():
    p = ART / "lb18_iex_hybrid.parquet"
    if not p.exists():
        return {"skipped": "artifact missing"}
    h = pd.read_parquet(p)
    v = h[h["variant"] == "A3b"].copy() if "variant" in h.columns else h.copy()
    v["month"] = v["date"].astype(str).str[:7]
    v["pop"] = "a3b"
    if "exit_t" not in v.columns:
        v["exit_t"] = np.nan
    if "prior_flush" not in v.columns:
        v["prior_flush"] = np.nan
    feats = dt.attach_features(v)
    feats["ns"] = np.where(feats["n_strict"] <= 1, "0-1", "2+")
    a = feats[feats["ns"] == "0-1"]["ret"]
    b = feats[feats["ns"] == "2+"]["ret"]
    return {"n": int(len(feats)), "quiet_n": int(len(a)), "busy_n": int(len(b)),
            "quiet_mean": float(a.mean()), "busy_mean": float(b.mean()),
            "direction_ok": bool(a.mean() > b.mean())}


def breadth_day(date):
    """count of tickers with gain_c >= 1.0 at each minute (causal grid value)."""
    p = pd.read_parquet(dt.LBDIR / f"path_{date}.parquet")
    a = np.zeros(dt.NMIN, dtype=np.int64)
    hot = p[p["gain_c"] >= 1.0]
    for t0 in hot["t"].values:
        idx = int(t0) - 570
        if 0 <= idx < dt.NMIN:
            a[idx:] += 1
    return a


def r4_alt(x, label):
    dates = sorted(x["date"].unique())
    rows = []
    for d in dates:
        try:
            rows.append(pd.DataFrame({"date": d, "t": np.arange(570, 960),
                                      "gb": breadth_day(d)}))
        except FileNotFoundError:
            continue
    tab = pd.concat(rows, ignore_index=True)
    x = x.copy()
    m = x.merge(tab, left_on=["date", x["tf"] - 1], right_on=["date", "t"],
                how="left", suffixes=("", "_d"))
    m["gb"] = m["gb"].fillna(0)
    a = m[m["gb"] <= 1]["ret"]
    b = m[m["gb"] >= 2]["ret"]
    return {"label": label, "n": int(len(m)), "quiet_n": int(len(a)),
            "busy_n": int(len(b)), "quiet_mean": float(a.mean()),
            "busy_mean": float(b.mean()),
            "direction_ok": bool(len(a) and len(b) and a.mean() > b.mean())}


def r5_concentration(x):
    b = x[x["ns"] == "2+"].copy()
    b["contrib"] = b["ret"]
    top = b.reindex(b["contrib"].abs().sort_values(ascending=False).index).head(5)
    top3 = set(zip(top["date"].head(3), top["ticker"].head(3)))
    keep = x[~x.apply(lambda r: (r["date"], r["ticker"]) in top3, axis=1)]
    return {"top5": [{"date": str(r["date"]), "ticker": str(r["ticker"]),
                      "ret": float(r["ret"])} for _, r in top.iterrows()],
            "diff_excl_top3": diff_of(add_ns(keep)),
            "pass": bool(diff_of(add_ns(keep)) > 0)}


def r6_placebo(x, n=5000):
    obs = diff_of(x)
    labels = (x["ns"] == "2+").to_numpy()
    k = labels.sum()
    cnt = 0
    rets = x["ret"].to_numpy()
    for _ in range(n):
        idx = RNG.permutation(len(rets))
        lab = np.zeros(len(rets), dtype=bool)
        lab[idx[:k]] = True
        d = rets[~lab].mean() - rets[lab].mean()
        if d >= obs:
            cnt += 1
    return {"p_ge_observed": cnt / n, "observed": obs}


def main():
    d = add_ns(dt.attach_features(dt.load_fills()))
    oos, dev = period(d, "oos"), period(d, "dev")
    out = {}
    for label, x in [("oos", oos), ("oos_rank1", oos[oos["rank"] == 1]),
                     ("dev", dev), ("dev_rank1", dev[dev["rank"] == 1])]:
        out[label] = {"observed_diff": diff_of(x), "n": int(len(x))}
    out["R1_lomo"] = {"oos": r1_lomo(oos), "oos_rank1": r1_lomo(oos[oos["rank"] == 1])}
    out["R2_bootstrap"] = {"oos": r2_bootstrap(oos),
                           "oos_rank1": r2_bootstrap(oos[oos["rank"] == 1])}
    out["R3_a3b"] = r3_a3b()
    out["R4_alt_breadth"] = {"oos": r4_alt(oos, "oos"), "dev": r4_alt(dev, "dev")}
    out["R5_concentration"] = r5_concentration(oos)
    out["R6_placebo"] = {"oos": r6_placebo(oos),
                         "oos_rank1": r6_placebo(oos[oos["rank"] == 1])}
    (ART / "lb18_daytype_robust.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if k.startswith("R")}, indent=1, default=str)[:2600])
    print("VERDICT:", "CANDIDATE" if (out["R1_lomo"]["oos"]["pass"]
          and out["R2_bootstrap"]["oos"]["pass"] and out["R5_concentration"]["pass"]
          and out["R4_alt_breadth"]["oos"]["direction_ok"]) else "DOWNGRADE")


if __name__ == "__main__":
    main()
