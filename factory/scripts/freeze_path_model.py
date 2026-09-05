"""Freeze step: fit normalizations + DTW medoids + Ridge on DEV months only.

Reads p2_dev_*.json, writes data/_scratch/path_freeze_v1.json:
  - robust scaler stats (median/IQR per feature, dev-only)
  - DTW medoids (K=4) per arm on downsampled z-series + cluster outcome stats
  - Ridge coefs (<=15 pre-registered features, alpha by leave-one-month-out CV)
  - full metadata (dev months, n, arms, K, feature list, alpha, support gates)

Pre-registered (2026-09-06, before August collision):
  K=4; Ridge features R15 (below); target mb200_capped; alpha grid [0.1,1,10,100];
  support gate >=15 obs & >=3 dev months per cluster; hit = mb200>300.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.path_features import dtw, FEATURES  # noqa: E402

R15 = ["pm_range_pct", "gap_pct", "ret_open_T", "ret_last15", "max_1m_gain",
       "accel", "path_conc", "time_of_high", "reversal_from_high", "n_pullbacks",
       "dist_HOD", "dist_VWAP", "rank_now", "dollar_vol_to_T", "separation"]
K = 4
ALPHAS = [0.1, 1.0, 10.0, 100.0]


def downsample(z, m: int = 10):
    import numpy as np
    z = np.asarray(z, dtype=float)
    idx = (np.linspace(0, len(z) - 1, min(m, len(z)))).astype(int)
    return z[idx].tolist()


def pam_medoids(D, k: int, seed: int = 0):
    """Partitioning Around Medoids on precomputed condensed list-of-lists dist."""
    import random
    n = len(D)
    rng = random.Random(seed)
    meds = rng.sample(range(n), k)
    assign = [min(range(k), key=lambda c: D[i][meds[c]]) for i in range(n)]
    improved = True
    while improved:
        improved = False
        best = (sum(D[i][meds[assign[i]]] for i in range(n)), None)
        for m in range(k):
            for cand in range(n):
                if cand in meds:
                    continue
                trial = meds.copy()
                trial[m] = cand
                cost = 0.0
                for i in range(n):
                    cost += min(D[i][t] for t in trial)
                if cost < best[0] - 1e-9:
                    best = (cost, trial)
        if best[1] is not None:
            meds, improved = best[1], True
            assign = [min(range(k), key=lambda c: D[i][meds[c]]) for i in range(n)]
    return meds, assign


def ridge_fit(X, y, alpha: float):
    import numpy as np
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, p = X.shape
    A = X.T @ X + alpha * np.eye(p + 1 if False else p)
    # standardize inside fit; caller passes standardized X (dev stats)
    w = __import__("numpy").linalg.solve(A, X.T @ y)
    return w


def main():
    import numpy as np
    dev_files = ["data/_scratch/p2_dev_2025-05.json", "data/_scratch/p2_dev_2025-06.json",
                 "data/_scratch/p2_dev_2025-07.json"]
    rows = []
    for f in dev_files:
        rows += json.load(open(f))
    print(f"dev rows: {len(rows)}", flush=True)
    freeze = {"meta": {"dev_months": ["2025-05", "2025-06", "2025-07"], "K": K,
                       "features_R15": R15, "target": "mb200_capped",
                       "support_gate": {"min_obs": 15, "min_months": 3}}, "arms": {}}
    for arm, sel in (("A", lambda r: r["rank"] <= 3),
                     ("B", lambda r: r["rank"] <= 3 and r["in_B"])):
        sub = [r for r in rows if sel(r)]
        months = sorted(set(r["day"][:7] for r in sub))
        print(f"arm {arm}: n={len(sub)}", flush=True)
        X = np.array([[r["features"][f] for f in FEATURES] for r in sub])
        med = np.median(X, axis=0)
        iqr = np.subtract(*np.percentile(X, [75, 25], axis=0))
        iqr[iqr == 0] = 1.0
        Xs = (X - med) / iqr
        # leave-one-month-out alpha selection on R15
        Xi = np.array([[r["features"][f] for f in R15] for r in sub])
        mi = np.median(Xi, axis=0)
        ii = np.subtract(*np.percentile(Xi, [75, 25], axis=0))
        ii[ii == 0] = 1.0
        Xs15 = (Xi - mi) / ii
        y = np.array([r["mb200"] for r in sub])
        mo = np.array([r["day"][:7] for r in sub])
        best_a, best_s = ALPHAS[0], -1e18
        for a in ALPHAS:
            preds = np.zeros_like(y)
            for m in months:
                tr, te = mo != m, mo == m
                if tr.sum() < 20 or te.sum() == 0:
                    continue
                w = ridge_fit(Xs15[tr], y[tr], a)
                preds[te] = Xs15[te] @ w
            ok = ~np.isnan(preds)
            s = float(np.corrcoef(np.argsort(np.argsort(preds[ok])),
                                  np.argsort(np.argsort(y[ok])))[0, 1]) if ok.sum() > 10 else -9
            if s > best_s:
                best_s, best_a = s, a
        w = ridge_fit(Xs15, y, best_a)
        # DTW medoids on downsampled z-series
        Z = [downsample(r["zseries"]) for r in sub]
        n = len(Z)
        D = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = dtw(Z[i], Z[j])
                D[i][j] = D[j][i] = d
        meds, assign = pam_medoids(D, K)
        clusters = []
        for c in range(K):
            idx = [i for i in range(n) if assign[i] == c]
            yy = y[idx]
            ms = sorted(set(mo[i] for i in idx))
            clusters.append({"medoid_row": int(meds[c]), "n": len(idx),
                             "months": ms, "mean_mb200": round(float(np.mean(yy)), 1),
                             "med_mb200": round(float(np.median(yy)), 1),
                             "p_gt300": round(float(np.mean(yy > 300)), 3)})
        freeze["arms"][arm] = {
            "n": n, "months": months,
            "scaler_all": {"median": med.tolist(), "iqr": iqr.tolist()},
            "scaler_R15": {"median": mi.tolist(), "iqr": ii.tolist()},
            "ridge": {"alpha": best_a, "lomo_rankIC": round(best_s, 3),
                      "weights": w.tolist()},
            "medoids": [Z[m] for m in meds],
            "medoid_full": [sub[m]["zseries"] for m in meds],
            "clusters": clusters}
        print(f"arm {arm}: ridge alpha={best_a} lomoIC={best_s:.3f} " +
              " ".join(f"C{c}:{cl['n']}/{cl['med_mb200']}" for c, cl in enumerate(clusters)),
              flush=True)
    Path("data/_scratch/path_freeze_v1.json").write_text(json.dumps(freeze))
    print("wrote path_freeze_v1.json", flush=True)


if __name__ == "__main__":
    main()
