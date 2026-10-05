#!/usr/bin/env python3
"""H042/EXP-87 four-cost verification: fold persistency, internal-forward selection,
and family-wise Reality Check over the 144 active cells per cost rung.

Parent verification 2026-10-06. Read-only over the frozen replay artifact:
  <data_root>/owned_claim/replay_cash_first_four_costs/daily.parquet
where data_root defaults to
  /home/hillel/algo projects/Alpacatrader/data/harvest01/lifecycle/v2
(override with H042_DATA_ROOT or argv[1]).

Output: JSON to stdout (also --out PATH). No repo writes beyond the requested file.

Definitions (match the repo's readers):
- cell = (clock, n, side, policy, cycles); active = policy != 'cash0:cash'.
- book-date ret = normalized return of one cash-first book (cash 1.0 -> settled close);
  UNKNOWN dates leave both numerator and denominator (set NaN here).
- folds = the replay's own `fold` column (0/1/2; test windows 150/150/83 days).
- Internal-forward: select cells with mean(ret | folds 0+1) > 0, evaluate equal-weight
  across selected cells on fold-2 days (day = cluster; per-day equal-weight, nan-aware).
- Reality Check (White 2000, simplified): statistic = max_c mean_c; null centers each
  cell's full-sample mean, resamples days with replacement (shared across cells, which
  preserves the cross-section), recomputes the max; p = share(null >= observed).
  UNKNOWN cells are treated as 0 in the centered null only (1.3% of rows).
"""
import json
import sys

import numpy as np
import pandas as pd
from scipy import stats


def load(data_root: str) -> pd.DataFrame:
    p = f"{data_root}/owned_claim/replay_cash_first_four_costs/daily.parquet"
    df = pd.read_parquet(p)
    df = df[df["policy"] != "cash0:cash"].copy()
    df["cell"] = (
        df["clock"].astype(str) + "|" + df["n"].astype(str) + "|" + df["side"].astype(str)
        + "|" + df["policy"] + "|" + df["cycles"]
    )
    return df


def pivot(df: pd.DataFrame, side: float) -> pd.DataFrame:
    sub = df[df["side"] == side]
    p = sub.pivot_table(index="day", columns="cell", values="ret", aggfunc="first")
    u = sub.pivot_table(index="day", columns="cell", values="unknown", aggfunc="first")
    return p.where(~u.fillna(True))


def reality_check(P: pd.DataFrame, draws: int = 10000, seed: int = 11) -> dict:
    X = P.to_numpy()
    n_known = np.sum(~np.isnan(X), axis=0)
    good = n_known >= 300
    Xg = X[:, good]
    obs = np.nanmean(Xg, axis=0)
    Z = np.where(np.isnan(Xg), 0.0, Xg - obs[None, :])
    rng = np.random.default_rng(seed)
    n = Xg.shape[0]
    maxnull = np.empty(draws)
    for t in range(draws):
        idx = rng.integers(0, n, size=n)
        maxnull[t] = Z[idx].mean(axis=0).max()
    return {
        "cells": int(good.sum()),
        "observed_best_cell_mean": float(obs.max()),
        "rc_p": float((maxnull >= obs.max()).mean()),
        "null_q95": float(np.quantile(maxnull, 0.95)),
    }


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/home/hillel/algo projects/Alpacatrader/data/harvest01/lifecycle/v2"
    df = load(root)
    days = np.sort(df["day"].unique())
    folds = {int(f): np.sort(df.loc[df["fold"] == f, "day"].unique()) for f in (0, 1, 2)}
    f01 = np.sort(np.concatenate([folds[0], folds[1]]))
    out = {"data_root": root, "days": len(days),
           "folds": {str(k): [len(v), str(v[0]), str(v[-1])] for k, v in folds.items()}, "by_rung": {}}
    for bps in (25, 50, 100, 150):
        P = pivot(df, bps / 20000)
        m0 = P.loc[[d for d in folds[0] if d in P.index]].mean()
        m1 = P.loc[[d for d in folds[1] if d in P.index]].mean()
        m2 = P.loc[[d for d in folds[2] if d in P.index]].mean()
        m01 = P.loc[[d for d in f01 if d in P.index]].mean()
        ok = m01.notna() & m2.notna() & m0.notna() & m1.notna()
        cols = P.columns
        f2days = [d for d in folds[2] if d in P.index]
        sub = P.loc[f2days]
        res = {
            "cells_ok": int(ok.sum()),
            "pos_f01": int((m01[ok] > 0).sum()),
            "pos_f2": int((m2[ok] > 0).sum()),
            "spearman": {
                "f0_f1": float(stats.spearmanr(m0[ok], m1[ok]).statistic),
                "f1_f2": float(stats.spearmanr(m1[ok], m2[ok]).statistic),
                "f01_f2": float(stats.spearmanr(m01[ok], m2[ok]).statistic),
            },
        }
        # internal-forward: cells selected on folds0+1 (>0) -> fold2 equal-weight per day
        sel = cols[(ok & (m01 > 0)).to_numpy()]
        pm = sub[sel].mean(axis=1, skipna=True)
        allm = sub.mean(axis=1, skipna=True)
        res["internal_forward"] = {
            "cells_selected": int(len(sel)),
            "f2_mean_per_day": float(pm.mean()), "f2_se": float(pm.std(ddof=1) / np.sqrt(pm.notna().sum())),
            "f2_all_cells_mean_per_day": float(allm.mean()),
        }
        # random-subset null for the selected set size
        rng = np.random.default_rng(7)
        arr = sub.to_numpy()
        k = int(len(sel))
        null = np.array([np.nanmean(arr[:, rng.choice(arr.shape[1], size=k, replace=False)]) for _ in range(2000)])
        res["internal_forward"]["random_subset_p"] = float((null >= pm.mean()).mean())
        # top-k by f01
        res["topk_by_f01_to_f2"] = {}
        for kk in (5, 10, 20, 40):
            top = m01[ok].sort_values(ascending=False).head(kk).index
            pmk = sub[top].mean(axis=1, skipna=True)
            res["topk_by_f01_to_f2"][str(kk)] = {
                "mean": float(pmk.mean()), "se": float(pmk.std(ddof=1) / np.sqrt(pmk.notna().sum()))}
        once = [c for c in m01[ok].index if c.endswith("|once")]
        topo = m01[once].sort_values(ascending=False).head(max(5, len(once) // 4)).index
        res["once_top_quartile_to_f2"] = {"k": int(len(topo)), "mean": float(sub[topo].mean(axis=1, skipna=True).mean())}
        # family aggregate + reality check
        fam = np.nanmean(P.to_numpy(), axis=1)
        fam = fam[~np.isnan(fam)]
        res["family_mean_per_day"] = {"mean": float(fam.mean()), "se": float(fam.std(ddof=1) / np.sqrt(len(fam)))}
        fonce_cols = [c for c in cols if c.endswith("|once")]
        res["family_once_mean_per_day"] = float(np.nanmean(P[fonce_cols].to_numpy()))
        res["reality_check"] = reality_check(P)
        out["by_rung"][str(bps)] = res
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
