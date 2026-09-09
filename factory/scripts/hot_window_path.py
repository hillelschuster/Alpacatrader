#!/usr/bin/env python3
"""H12 path-representation probe: can ML read the recent raw path itself?

cache MONTHS... : for each cached scalar sample (data/cache_h12/), extract the
last-30-bar path (4 channels x 30 positions, right-aligned by recency) from the
raw month file and write data/cache_h12_path/ with ALL original columns + path.
eval --dev M... [--collision M...] : LightGBM (same fixed params) on
scalars-only vs path-only vs scalars+path; T1 AUC + volrank control + per-t-bucket
AUC + corrected bracket economics. Inspection mode — no gates (discovery).

Channels per bar (all scale-free): r = bar return vs prior session-bar close,
g = high/low - 1 range, v = volume / mean(window volumes), q = (c-l)/(h-l)
close position in bar (NaN on zero-range bars)."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hot_window_ml import (CACHE, FEATURES, FRICTION, ROOT, UP, DN,  # noqa: E402
                           bracket_ret, month_path)

PATH_CACHE = ROOT / "data" / "cache_h12_path"
ART = ROOT / "factory" / "artifacts"
NBARS = 30
PATH_COLS = [f"{ch}_{i}" for ch in "rgvq" for i in range(NBARS)]


def path_cols(g: pd.DataFrame, t: int) -> dict | None:
    """Last NBARS bars with et <= t-1, right-aligned (index NBARS-1 = bar t-1).
    Left positions NaN when history is shorter — LightGBM reads the NaN pattern
    as 'age of information', not as a value."""
    gg = g[g["et"] <= t - 1].tail(NBARS)
    if len(gg) < 2:
        return None
    o = gg["open"].to_numpy(float); h = gg["high"].to_numpy(float)
    l = gg["low"].to_numpy(float);  c = gg["close"].to_numpy(float)
    v = gg["volume"].to_numpy(float)
    with np.errstate(all="ignore"):
        prev_c = np.concatenate([[np.nan], c[:-1]])
        r = c / prev_c - 1
        rng = h / l - 1
        vmean = v.mean()
        vr = v / vmean if vmean > 0 else np.full(len(v), np.nan)
        q = np.where(h > l, (c - l) / (h - l), np.nan)
    out = {}
    for j, arr in enumerate([r, rng, vr, q]):
        ch = "rgvq"[j]
        pad = NBARS - len(arr)
        for i in range(NBARS):
            out[f"{ch}_{i}"] = float(arr[i - pad]) if i >= pad else np.nan
    return out


def cmd_cache(months):
    for month in months:
        df = pd.read_parquet(month_path(month))
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        et = df["timestamp"].dt.tz_convert("America/New_York")
        df["et"] = et.dt.hour * 60 + et.dt.minute
        del et
        df = df[(df["et"] >= 570) & (df["et"] <= 660)]
        df["date"] = df["timestamp"].dt.floor("D")
        for day, g in df.groupby("date", sort=True):
            key = PATH_CACHE / f"h12_{str(day)[:10]}.parquet"
            src = CACHE / f"h12_{str(day)[:10]}.parquet"
            if key.exists() or not src.exists():
                continue
            cached = pd.read_parquet(src)
            if len(cached) == 0:
                cached.to_parquet(key)
                continue
            groups = {tk: x.sort_values("timestamp")
                      for tk, x in g.groupby("ticker", sort=False)}
            rows = []
            for _, r in cached.iterrows():
                gg = groups.get(r["ticker"])
                pc = path_cols(gg, int(r["t"])) if gg is not None else None
                rows.append(pc if pc is not None else
                            {c: np.nan for c in PATH_COLS})
            out = pd.concat(
                [cached.reset_index(drop=True),
                 pd.DataFrame(rows, columns=PATH_COLS)], axis=1)
            out.to_parquet(key)
            print(f"{month} {str(day)[:10]}: {len(out)} path rows", flush=True)


def load_path_cache(months):
    dfs = []
    for p in sorted(PATH_CACHE.glob("h12_*.parquet")):
        d = pd.read_parquet(p)
        if len(d) and d["month"].iloc[0] in months:
            dfs.append(d)
    if not dfs:
        sys.exit(f"no path-cached samples for {months}")
    return pd.concat(dfs, ignore_index=True)


def fit_predict(cols, train, test):
    import lightgbm as lgb
    params = dict(objective="binary", num_leaves=31, learning_rate=0.05,
                  n_estimators=300, min_child_samples=50, feature_fraction=0.9,
                  bagging_fraction=0.8, bagging_freq=1, seed=7, verbose=-1,
                  force_col_wise=True)
    m = lgb.LGBMClassifier(**params)
    m.fit(train[cols], train["T1"])
    return m.predict_proba(test[cols])[:, 1]


def auc_of(o, lbl):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(o[lbl], o["score"]))


def economics(o):
    o = o.copy()
    o["bracket"] = o.apply(bracket_ret, axis=1)
    o["dec"] = np.minimum((o["score"].rank(pct=True) * 10).astype(int), 9)
    g = o.groupby("dec").agg(n=("bracket", "size"), t1=("T1", "mean"),
                             bracket=("bracket", "mean"), t2=("T2", "mean"))
    g["br_net"] = g["bracket"] - FRICTION
    top = o[o["dec"] >= 8].sort_values("t").drop_duplicates(["date", "ticker"])
    tm = o[o["dec"] >= 8].groupby("month").agg(
        n=("bracket", "size"), br_net=("bracket", lambda x: x.mean() - FRICTION))
    return g, top["bracket"].mean() - FRICTION, len(top), tm


def cmd_eval(dev, collision):
    devdf = load_path_cache(dev)
    sets = {"scalars": FEATURES, "path": PATH_COLS,
            "scalars+path": FEATURES + PATH_COLS}
    out = {"probe": "path representation vs scalars (order/shape preserved)",
           "friction_bps": FRICTION, "n_dev": len(devdf)}
    coldf = load_path_cache(collision) if collision else None
    for name, cols in sets.items():
        # LOMO dev
        parts = []
        for m in sorted(devdf["month"].unique()):
            tr, te = devdf[devdf["month"] != m], devdf[devdf["month"] == m]
            o = te[["month", "date", "t", "ticker", "T1", "T2", "rng10",
                    "mfe30", "mae30"]].copy()
            o["score"] = fit_predict(cols, tr, te)
            parts.append(o)
        lomo = pd.concat(parts, ignore_index=True)
        d_auc = auc_of(lomo, "T1")
        vr_auc = auc_of(lomo.assign(volrank=lomo.groupby("date")["rng10"]
                                    .rank(pct=True)), "T1")
        dec, dedup_net, dedup_n, tmonth = economics(lomo)
        early = lomo[lomo["t"] <= 600]
        late = lomo[lomo["t"] > 600]
        e_auc = auc_of(early, "T1") if early["T1"].nunique() == 2 else float("nan")
        l_auc = auc_of(late, "T1") if late["T1"].nunique() == 2 else float("nan")
        r = {"lomo_auc": round(d_auc, 4), "volrank_auc": round(vr_auc, 4),
             "auc_t<=600": round(e_auc, 4), "auc_t>600": round(l_auc, 4),
             "decile9_br_net": round(float(dec.loc[9, "br_net"]), 1),
             "dec8plus_dedup_br_net": round(float(dedup_net), 1),
             "dec8plus_dedup_n": int(dedup_n),
             "deciles": dec.round(2).to_dict("index"),
             "dec8_month_net": tmonth.round(1).to_dict("index")}
        print(f"\n== {name} (dev LOMO): AUC {d_auc:.4f} (volrank {vr_auc:.4f}) "
              f"early {e_auc:.3f} late {l_auc:.3f} | dec9 br_net "
              f"{dec.loc[9, 'br_net']:.1f} dedup8+ {dedup_net:.1f} (n={dedup_n})")
        print(dec.round(2).to_string())
        if coldf is not None:
            co = coldf[["month", "date", "t", "ticker", "T1", "T2", "rng10",
                        "mfe30", "mae30"]].copy()
            co["score"] = fit_predict(cols, devdf, coldf)
            c_auc = auc_of(co, "T1")
            cvr = auc_of(co.assign(volrank=co.groupby("date")["rng10"]
                                   .rank(pct=True)), "T1")
            cdec, cnet, cn, ctm = economics(co)
            r["collision"] = {"auc": round(c_auc, 4),
                              "volrank_auc": round(cvr, 4),
                              "decile9_br_net": round(float(cdec.loc[9, "br_net"]), 1),
                              "dec8plus_dedup_br_net": round(float(cnet), 1),
                              "dec8plus_dedup_n": int(cn),
                              "deciles": cdec.round(2).to_dict("index"),
                              "dec8_month_net": ctm.round(1).to_dict("index")}
            print(f"   collision: AUC {c_auc:.4f} (volrank {cvr:.4f}) | dec9 "
                  f"br_net {cdec.loc[9, 'br_net']:.1f} dedup8+ {cnet:.1f} (n={cn})")
        out[name] = r
    p = ART / "hot_window_ml_H12_path.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"\n-> {p}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["cache", "eval"])
    p.add_argument("months", nargs="*")
    p.add_argument("--dev", nargs="*")
    p.add_argument("--collision", nargs="*")
    a = p.parse_args()
    if a.cmd == "cache":
        cmd_cache(a.months)
    else:
        if not a.dev:
            sys.exit("eval needs --dev MONTHS")
        cmd_eval(a.dev, a.collision)


if __name__ == "__main__":
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    main()
