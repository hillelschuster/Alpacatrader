#!/usr/bin/env python3
"""PRE-REG-TOXICITY-01 producer: can causal pre-anchor state identify toxic
flush fills? Features from grid bars <= t0 (anchor minute); toxicity = net ret,
fc<0 (gap-through-like), ret<=-0.10 (stop-like). Measurement only.
Artifacts: factory/artifacts/lb18_toxicity.json + .parquet
"""
# /// script
# dependencies = ["pandas", "numpy", "pyarrow"]
# ///
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LB = ROOT / "data" / "leaderboard"
OUT = ROOT / "factory" / "artifacts"
WIN = 5
EXTREME_N = 80
DIFF_PP = 0.005
RETAIN = 0.60
ERA = "era"


def load_fills() -> pd.DataFrame:
    oos = pd.read_parquet(ROOT / "factory" / "artifacts" / "lb18_oos_oos.parquet")
    oos["era"] = "2024-2025"
    bb = pd.read_parquet(ROOT / "factory" / "artifacts" / "lb18_backbone_fills.parquet")
    bb["era"] = "2021-2023"
    cols = ["date", "ticker", "t0", "tf", "ret", "fc", "rank", "prior_flush", "era"]
    d = pd.concat([oos[cols], bb[cols]], ignore_index=True)
    d["date"] = d["date"].astype(str)
    d["t0"] = d["t0"].astype(int)
    d["tf"] = d["tf"].astype(int)
    d["month"] = d["date"].str[:7]
    return d.drop_duplicates(["date", "ticker", "tf"], keep="first")


def feats(g: pd.DataFrame | None, t0: int) -> dict | None:
    if g is None or t0 not in g.index:
        return None
    idx = g.index
    w = g.loc[(idx >= t0 - (WIN - 1)) & (idx <= t0)]
    if len(w) < 3:
        return None
    c0 = float(g.loc[t0, "c"])
    if c0 <= 0:
        return None
    vol5 = float(w["v"].sum())
    base = np.nan
    vals = g.loc[idx <= t0 - WIN, "v"]
    if len(vals) >= WIN:
        sums = np.convolve(vals.to_numpy(dtype=float), np.ones(WIN), mode="valid")
        sums = sums[sums > 0]
        if len(sums):
            base = float(np.median(sums))
    hi, lo = float(w["h"].max()), float(w["l"].min())
    cmax = float(g.loc[idx <= t0, "c"].max())
    return {
        "c0": c0,
        "vol5_ratio": (vol5 / base) if base and base > 0 else np.nan,
        "range5": (hi - lo) / c0,
        "pullback": c0 / cmax - 1,
    }


def day_frames(day: str) -> dict:
    p = LB / f"path_{day}.parquet"
    if not p.exists():
        return {}
    df = pd.read_parquet(p, columns=["ticker", "t", "h", "l", "c", "v"])
    out = {}
    for tk, g in df.sort_values(["ticker", "t"]).groupby("ticker", sort=False):
        g = g.set_index("t")
        out[str(tk)] = g[~g.index.duplicated(keep="last")]
    return out


def attach(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, sub in d.groupby("date", sort=False):
        frames = day_frames(day)
        for r in sub.itertuples():
            f = feats(frames.get(r.ticker), r.t0)
            if f is None:
                continue
            rows.append({
                "date": r.date, "month": r.month, "ticker": r.ticker,
                "t0": r.t0, "tf": r.tf, "ret": r.ret, "fc": r.fc,
                "rank": r.rank, "prior_flush": r.prior_flush, "era": r.era,
                **f,
            })
    out = pd.DataFrame(rows)
    out["price_band"] = pd.cut(out["c0"], [0, 4, 10, 1e9], labels=["<$4", "$4-10", ">=$10"])
    out["session"] = np.where(out["t0"] < 720, "AM", "PM")
    return out


def bin_stats(d: pd.DataFrame, by: pd.Series) -> list[dict]:
    rows = []
    for name, sub in d.groupby(by, observed=True):
        if len(sub) == 0:
            continue
        m = sub.groupby("month")["ret"].mean()
        rows.append({
            "bin": str(name), "n": int(len(sub)),
            "mean": round(float(sub["ret"].mean()), 4),
            "median": round(float(sub["ret"].median()), 4),
            "fc_neg": round(float((sub["fc"] < 0).mean()), 3),
            "stop_like": round(float((sub["ret"] <= -0.10).mean()), 3),
            "months_pos": int((m > 0).sum()), "n_months": int(len(m)),
        })
    return rows


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    if len(a) < 30:
        return None
    return round(float(a.rank().corr(b.rank())), 4)


def feature_table(d: pd.DataFrame, col: str) -> dict:
    x = d[np.isfinite(d[col])].copy()
    if len(x) < 200:
        return {"feature": col, "insufficient": True, "n": len(x)}
    x["_q"] = pd.qcut(x[col], 4, labels=False, duplicates="drop")
    pooled = bin_stats(x, x["_q"])
    eras = {e: bin_stats(g, g["_q"]) for e, g in x.groupby(ERA, observed=True)}
    rho = {}
    for e, g in x.groupby(ERA, observed=True):
        rho[e] = _spearman(g[col], g["ret"])
    rho["pooled"] = _spearman(x[col], x["ret"])
    return {"feature": col, "n": int(len(x)), "bins": pooled, "eras": eras, "spearman": rho}


def apply_gate(ft: dict, pop_mean: float, pop_n: int) -> dict:
    if ft.get("insufficient"):
        return {"pass": False, "why": "insufficient n"}
    bins = ft["bins"]
    best = max(bins, key=lambda b: b["mean"])
    worst = min(bins, key=lambda b: b["mean"])
    why = []
    if not (worst["mean"] <= best["mean"] - DIFF_PP):
        why.append("diff<0.50pp")
    if min(worst["n"], best["n"]) < EXTREME_N:
        why.append("extreme bin n<80")
    dirs = []
    for e, bs in ft["eras"].items():
        if len(bs) >= 2:
            b = max(bs, key=lambda z: z["mean"])
            w = min(bs, key=lambda z: z["mean"])
            dirs.append(w["mean"] <= b["mean"] - DIFF_PP)
    if len(dirs) < 2 or not all(dirs):
        why.append("direction not in both eras")
    kept = [b for b in bins if b is not worst]
    kept_n = sum(b["n"] for b in kept)
    kept_mean = sum(b["mean"] * b["n"] for b in kept) / kept_n if kept_n else np.nan
    if kept_n / pop_n < RETAIN:
        why.append("retention<60%")
    if not (kept_mean >= pop_mean):
        why.append("retained mean below population")
    return {"pass": not why, "why": why, "best": best["bin"], "worst": worst["bin"],
            "kept_n": kept_n, "kept_mean": round(float(kept_mean), 4)}


SEED = 20260913
N_BOOT = 2000


def _diff_extreme(ret: np.ndarray, q: np.ndarray) -> float:
    best, worst = -1e9, 1e9
    for b in np.unique(q):
        mu = ret[q == b].mean()
        best, worst = max(best, mu), min(worst, mu)
    return best - worst


def robust(d: pd.DataFrame) -> dict:
    rng = np.random.default_rng(SEED)
    out = {}
    for col in ("vol5_ratio", "range5"):
        x = d[np.isfinite(d[col])].reset_index(drop=True)
        x["_q"] = pd.qcut(x[col], 4, labels=False, duplicates="drop")
        ret = x["ret"].to_numpy()
        q = x["_q"].to_numpy()
        obs = _diff_extreme(ret, q)
        dayv = x["date"].to_numpy()
        uniq = np.unique(dayv)
        pos = {dt: np.where(dayv == dt)[0] for dt in uniq}
        boots = []
        for _ in range(N_BOOT):
            picks = rng.choice(len(uniq), size=len(uniq), replace=True)
            idx = np.concatenate([pos[uniq[p]] for p in picks])
            boots.append(_diff_extreme(ret[idx], q[idx]))
        ci = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)))
        perms = [_diff_extreme(ret[rng.permutation(len(ret))], q) for _ in range(N_BOOT)]
        p = float(np.mean(np.abs(perms) >= abs(obs)))
        eras = {}
        for e in x["era"].unique():
            m = (x["era"] == e).to_numpy()
            eras[str(e)] = round(float(_diff_extreme(ret[m], q[m])), 4)
        rank_c = x["rank"].where(x["rank"].isin([1, 2, 3]), 0)
        rank_strata = {}
        for v in (1, 2, 3, 0):
            m = (rank_c == v).to_numpy()
            rank_strata[str(v)] = (round(float(_diff_extreme(ret[m], q[m])), 4)
                                   if m.sum() >= 150 else None)
        price_strata = {}
        for v in x["price_band"].cat.categories:
            m = (x["price_band"] == v).to_numpy()
            price_strata[str(v)] = (round(float(_diff_extreme(ret[m], q[m])), 4)
                                    if m.sum() >= 150 else None)
        rk_ok = sum(1 for v in rank_strata.values() if v is not None and v > 0)
        rk_n = sum(1 for v in rank_strata.values() if v is not None)
        pb_ok = all(v is None or v > 0 for v in price_strata.values())
        survives = (ci[0] > 0) and (p < 0.05) and all(v > 0 for v in eras.values()) \
            and (rk_ok >= 2) and pb_ok
        out[col] = {"obs_diff": round(float(obs), 4), "ci95": [round(v, 4) for v in ci],
                    "p_perm": round(p, 4), "eras": eras, "rank_strata": rank_strata,
                    "n_rank_positive": f"{rk_ok}/{rk_n}", "price_strata": price_strata,
                    "survives": bool(survives)}
        print(f"{col}: diff {obs:+.4f} ci95 [{ci[0]:+.4f},{ci[1]:+.4f}] p {p:.4f} "
              f"eras {eras} survives={survives}")
    return out


def run() -> None:
    d = attach(load_fills())
    pop_mean = float(d["ret"].mean())
    print(f"fills with features: {len(d)}  pop mean {pop_mean:.4f}")
    tables = {"vol5_ratio": feature_table(d, "vol5_ratio"),
              "range5": feature_table(d, "range5"),
              "pullback": feature_table(d, "pullback"),
              "price_band": {"feature": "price_band", "n": len(d),
                             "bins": bin_stats(d, d["price_band"]),
                             "eras": {e: bin_stats(g, g["price_band"]) for e, g in d.groupby(ERA, observed=True)},
                             "spearman": {}}}
    verdicts = {k: apply_gate(v, pop_mean, len(d)) for k, v in tables.items()}
    out = {"study": "PRE-REG-TOXICITY-01", "n_fills": len(d), "pop_mean": round(pop_mean, 4),
           "features": tables, "gate": verdicts,
           "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True}}
    (OUT / "lb18_toxicity.json").write_text(json.dumps(out, indent=1, default=str))
    d.to_parquet(OUT / "lb18_toxicity.parquet")
    for k, v in verdicts.items():
        print(f"{k:12s} pass={v['pass']}  {v.get('why')}")
    for k in ("vol5_ratio", "range5", "pullback", "price_band"):
        print(f"\n== {k} ==")
        for b in tables[k]["bins"]:
            print("  ", b)
    print("artifact ->", OUT / "lb18_toxicity.json")


def self_test() -> None:
    idx = list(range(10, 30))
    g = pd.DataFrame({
        "h": [10.0] * 20, "l": [9.0] * 20, "c": [9.5] * 20,
        "v": [100.0] * 10 + [500.0] * 5 + [100.0] * 5,
    }, index=idx)
    f = feats(g, 24)
    assert f and f["vol5_ratio"] > 4.0, f
    assert abs(f["range5"] - 1.0 / 9.5) < 1e-9, f
    assert f["pullback"] == 0.0, f
    assert feats(g, 99) is None
    assert feats(g, 10) is None
    assert feats(None, 24) is None
    row = {"mean": 0.02, "n": 300, "bin": "b"}
    bad = {"mean": -0.02, "n": 300, "bin": "w"}
    ft = {"n": 600, "bins": [row, bad],
          "eras": {"x": [row, bad], "y": [row, bad]}}
    v = apply_gate(ft, pop_mean=0.0, pop_n=400)
    assert v["pass"] and v["kept_n"] == 300, v
    print("SELF-TEST PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--robust", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
    elif a.robust:
        d = pd.read_parquet(OUT / "lb18_toxicity.parquet")
        res = robust(d)
        (OUT / "lb18_toxicity_robust.json").write_text(
            json.dumps({"study": "PRE-REG-TOXICITY-01 Amendment A1",
                        "results": res, "flags": {"measurement": True,
                        "seen_data": True, "not_an_alpha_claim": True}},
                       indent=1, default=str))
        print("artifact ->", OUT / "lb18_toxicity_robust.json")
    else:
        run()
