#!/usr/bin/env -S uv run --quiet --python 3.11 --with pandas --with pyarrow --with numpy
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow", "numpy"]
# ///
"""PRE-REG-TOXICITY-03: range5 live-feed deployability check (measurement only).

Q1: does range5 recomputed from the IEX-only tape still separate fills?
Q2: is range5 just an AM/PM proxy?  No adoption, no bot change.
Out: factory/artifacts/lb18_range5_live.json + .parquet
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import toxicity_state as ts  # noqa: E402

IEX = ROOT / "data" / "iex_tape"
OUT = ROOT / "factory" / "artifacts"
THR = 0.13214
COVER_MIN = 0.70
RETAIN_FRAC = 0.50


def iex_frames(day):
    p = IEX / f"path_{day}.parquet"
    if not p.exists():
        return {}
    df = pd.read_parquet(p, columns=["ticker", "t", "h", "l", "c", "v"])
    out = {}
    for tk, g in df.sort_values(["ticker", "t"]).groupby("ticker", sort=False):
        g = g.set_index("t")
        out[str(tk)] = g[~g.index.duplicated(keep="last")]
    return out


def attach_iex(d):
    rows = []
    for day, sub in d.groupby("date", sort=False):
        frames = iex_frames(day)
        for r in sub.itertuples():
            f = ts.feats(frames.get(r.ticker), r.t0)
            rows.append({"date": r.date, "ticker": r.ticker, "tf": r.tf,
                         "iex_range5": (f or {}).get("range5", np.nan)})
    return pd.DataFrame(rows)


def split_stats(d, col, thr):
    hi = d[d[col] >= thr]
    lo = d[d[col] < thr]
    mh = round(float(hi["ret"].mean()), 4) if len(hi) else None
    ml = round(float(lo["ret"].mean()), 4) if len(lo) else None
    return {"n_hi": int(len(hi)), "n_lo": int(len(lo)), "mean_hi": mh, "mean_lo": ml,
            "gap": (round(mh - ml, 4) if mh is not None and ml is not None else None)}


def stratum_ok(d, col, thr):
    s = split_stats(d, col, thr)
    ok = s["mean_hi"] is not None and s["mean_lo"] is not None and s["mean_hi"] > s["mean_lo"]
    return ok, s


def self_test():
    d = pd.DataFrame({"ret": [0.05, -0.05, 0.10, -0.10], "x": [0.2, 0.1, 0.3, 0.05]})
    s = split_stats(d, "x", 0.15)
    assert s["n_hi"] == 2 and abs(s["gap"] - 0.15) < 1e-9, s
    m = pd.DataFrame({"full": [0.2, 0.30, 0.05], "iex": [0.2, 0.31, np.nan]})
    cov = m[np.isfinite(m["iex"])]
    assert len(cov) == 2
    assert float(((cov["full"] >= THR) == (cov["iex"] >= THR)).mean()) == 1.0
    m2 = pd.DataFrame({"full": [0.2, 0.30], "iex": [0.2, 0.05]})
    assert float(((m2["full"] >= THR) == (m2["iex"] >= THR)).mean()) == 0.5
    print("SELF-TEST PASS")


def run():
    d = ts.load_fills()
    full = ts.attach(d)
    full["session"] = np.where(full["t0"] < 720, "AM", "PM")

    sp = ts._spearman(full["range5"], full["t0"])
    am_ok, am_s = stratum_ok(full[full["session"] == "AM"], "range5", THR)
    pm_ok, pm_s = stratum_ok(full[full["session"] == "PM"], "range5", THR)
    q2 = {
        "AM_range5_mean_median": [round(float(full.loc[full.session == "AM", "range5"].mean()), 4),
                                  round(float(full.loc[full.session == "AM", "range5"].median()), 4)],
        "PM_range5_mean_median": [round(float(full.loc[full.session == "PM", "range5"].mean()), 4),
                                  round(float(full.loc[full.session == "PM", "range5"].median()), 4)],
        "spearman_range5_t0": (round(sp, 4) if sp is not None else None),
        "AM_split": am_s, "PM_split": pm_s, "not_confounded": bool(am_ok and pm_ok),
    }

    oos = full[full["era"] == "2024-2025"].copy()
    iex = attach_iex(oos)
    m = oos.merge(iex, on=["date", "ticker", "tf"], how="left")
    cov = m[np.isfinite(m["iex_range5"])].copy()
    coverage = round(len(cov) / len(m), 4) if len(m) else 0.0
    agree = (round(float(((cov["range5"] >= THR) == (cov["iex_range5"] >= THR)).mean()), 4)
             if len(cov) else None)
    iex_med = round(float(cov["iex_range5"].median()), 5) if len(cov) else None

    q1 = {"population": "OOS 2024-01..2025-02",
          "n_all": int(len(m)), "coverage": coverage, "agreement_at_thr": agree,
          "iex_range5_median": iex_med, "full": {}, "iex_thr": {}, "iex_median": {}, "pf2": {}}
    for name, sub in (("all", cov), ("pf2", cov[cov["prior_flush"] >= 2])):
        sf = split_stats(sub, "range5", THR)
        si = split_stats(sub, "iex_range5", THR)
        sm = split_stats(sub, "iex_range5", iex_med) if iex_med is not None else {}
        key = "all" if name == "all" else "pf2"
        q1["full"][key] = sf
        q1["iex_thr"][key] = si
        q1["iex_median"][key] = sm
    a = q1["iex_thr"]["pf2"]
    f = q1["full"]["pf2"]
    q1_ok = (coverage >= COVER_MIN and a.get("gap") is not None and f.get("gap") is not None
             and np.sign(a["gap"]) == np.sign(f["gap"]) and a["gap"] >= RETAIN_FRAC * f["gap"])
    q1["pass"] = bool(q1_ok)

    if q1_ok and q2["not_confounded"]:
        verdict = "DEPLOYABLE-PROXY"
    elif q1_ok:
        verdict = "PROXY-WEAK (AM-only)"
    else:
        verdict = "NOT-DEPLOYABLE-AS-IS"

    out = {"study": "PRE-REG-TOXICITY-03 range5 live-feed deployability",
           "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True,
                     "no_adoption": True},
           "threshold": THR, "q1_iex_proxy": q1, "q2_session_confound": q2,
           "verdict": verdict}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "lb18_range5_live.json").write_text(json.dumps(out, indent=1, default=str))
    m.to_parquet(OUT / "lb18_range5_live.parquet", index=False)
    print(json.dumps({"q1_pass": q1_ok, "coverage": coverage, "agreement": agree,
                      "pf2_full": f, "pf2_iex": a, "q2": {"AM": am_s, "PM": pm_s,
                      "not_confounded": q2["not_confounded"], "spearman": q2["spearman_range5_t0"],
                      "AM_stats": q2["AM_range5_mean_median"], "PM_stats": q2["PM_range5_mean_median"]},
                      "verdict": verdict}, indent=1))
    print("artifact ->", OUT / "lb18_range5_live.json")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        run()
