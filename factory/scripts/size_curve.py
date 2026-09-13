#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow", "numpy"]
# ///
"""PRE-REG-SIZE-01: fill-size response + adverse selection at size.

Reads factory/artifacts/lb18_fills_micro.parquet (one row per frozen OOS fill)
and writes factory/artifacts/lb18_size.json + lb18_size.parquet.
Measurement only: no rule, no bot change.
Usage: uv run size_curve.py [--self-test]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MICRO = ROOT / "factory" / "artifacts" / "lb18_fills_micro.parquet"
OUT = ROOT / "factory" / "artifacts"
NOTIONALS = (2000, 5000, 10000, 25000, 50000, 100000, 200000)
PF2_BASELINE = 0.0114
BREACH = -0.105


def months_from(dates):
    return pd.Series(dates).str[:7]


def subset_stats(d, mask):
    s = d[mask]
    if len(s) == 0:
        return {"n": 0}
    months = np.array([str(x)[:7] for x in s["date"].to_numpy()])
    mg = pd.DataFrame({"m": months, "ret": s["ret"].to_numpy()}).groupby("m")["ret"].mean()
    return {
        "n": int(len(s)),
        "mean": round(float(s["ret"].mean()), 4),
        "median": round(float(s["ret"].median()), 4),
        "pos": round(float((s["ret"] > 0).mean()), 3),
        "breach": round(float((s["ret"] <= BREACH).mean()), 3),
        "months_pos": int((mg > 0).sum()),
        "n_months": int(len(mg)),
    }


def curve(d, notionals=NOTIONALS):
    rows = []
    for n in notionals:
        shares = n / d["B"]
        mask_opt = d["flush_vol_at"] >= shares
        mask_pes = d["flush_vol_at"] >= 2 * shares
        rows.append({
            "notional": n,
            "opt": subset_stats(d, mask_opt),
            "pes": subset_stats(d, mask_pes),
            "opt_share": round(float(mask_opt.mean()), 3),
            "pes_share": round(float(mask_pes.mean()), 3),
            "exit_proxy_share": round(float((d["next_vol"] >= shares).mean()), 3),
        })
    return rows


def quartiles(d):
    q = pd.qcut(d["flush_vol_at"], 4, labels=["Q1", "Q2", "Q3", "Q4"],
                duplicates="drop")
    out = {}
    for name, g in d.assign(_q=q).groupby("_q", observed=True):
        out[str(name)] = subset_stats(g, pd.Series(True, index=g.index))
    rho = d[["flush_vol_at", "ret"]].corr(method="spearman").iloc[0, 1]
    return out, round(float(rho), 4)


def verdict(d, table):
    pf2 = d[d["prior_flush"] >= 2]
    if len(pf2) < 100:
        return {"size_safe_to": None, "reason": "pf2 subset too small"}
    best, binding = None, []
    for row in table["pf2"]:
        st = row["opt"]
        ok_fill = row["opt_share"] >= 0.90
        ok_ev = st.get("mean") is not None and st["mean"] >= PF2_BASELINE - 0.001
        ok_mo = st.get("months_pos", 0) >= 10
        q4, q1 = table["pf2_quartiles"].get("Q4"), table["pf2_quartiles"].get("Q1")
        ok_adv = (q4 and q1 and q4.get("mean") is not None and q1.get("mean") is not None
                  and q4["mean"] >= q1["mean"] - 0.005)
        if ok_fill and ok_ev and ok_mo and ok_adv:
            best = row["notional"]
        else:
            binding.append((row["notional"], ok_fill, ok_ev, ok_mo, ok_adv))
    return {"size_safe_to": best, "binding_failures": binding,
            "baseline_pf2": PF2_BASELINE}


def run():
    d = pd.read_parquet(MICRO)
    if "month" not in d.columns:
        d["month"] = months_from(d["date"].to_numpy())
    all_q, all_rho = quartiles(d)
    pf2 = d[d["prior_flush"] >= 2].copy()
    pf2_q, pf2_rho = quartiles(pf2)
    table = {
        "n_all": int(len(d)),
        "n_pf2": int(len(pf2)),
        "baseline": {"all": subset_stats(d, pd.Series(True, index=d.index)),
                     "pf2": subset_stats(pf2, pd.Series(True, index=pf2.index))},
        "all": curve(d),
        "pf2": curve(pf2),
        "all_quartiles": all_q,
        "pf2_quartiles": pf2_q,
        "spearman_vol_ret": {"all": all_rho, "pf2": pf2_rho},
    }
    table["verdict"] = verdict(d, {**table, "pf2_quartiles": pf2_q})
    table["flags"] = {"measurement": True, "seen_data": True,
                      "not_an_alpha_claim": True,
                      "queue_ignored": True,
                      "population": "frozen OOS 2024-01..2025-02"}
    (OUT / "lb18_size.json").write_text(json.dumps(table, indent=1, default=str))
    per = d[["date", "ticker", "tf", "B", "ret", "prior_flush", "rank",
             "flush_vol_at", "next_vol"]].copy()
    per["frac_vol_at_of_flush"] = (d["flush_vol_at"] / d["flush_vol"]).round(4)
    for n in NOTIONALS:
        per[f"pass_{n}"] = (d["flush_vol_at"] >= n / d["B"]).astype(int)
    per.to_parquet(OUT / "lb18_size.parquet")
    print("VERDICT:", json.dumps(table["verdict"]))
    print("\nbaseline all:", table["baseline"]["all"])
    print("baseline pf2:", table["baseline"]["pf2"])
    print("\npf2 by notional:")
    for row in table["pf2"]:
        st = row["opt"]
        print(f"  N=${row['notional']:>7,} share={row['opt_share']:.3f} "
              f"n={st.get('n')} mean={st.get('mean')} months+="
              f"{st.get('months_pos')}/{st.get('n_months')} "
              f"exit_proxy={row['exit_proxy_share']:.3f}")
    print("\npf2 vol quartiles:")
    for k, v in pf2_q.items():
        print(f"  {k}: n={v.get('n')} mean={v.get('mean')} "
              f"median={v.get('median')} breach={v.get('breach')}")
    print(f"spearman(vol_at, ret): all={all_rho} pf2={pf2_rho}")
    return table


def self_test():
    d = pd.DataFrame({
        "date": ["2024-01-02"] * 4,
        "ticker": ["A", "A", "B", "B"],
        "tf": [600, 610, 620, 630],
        "B": [10.0, 10.0, 10.0, 10.0],
        "ret": [0.10, -0.11, 0.05, -0.11],
        "prior_flush": [2, 2, 0, 3],
        "rank": [1, 1, 2, 3],
        "flush_vol_at": [1000.0, 100.0, 5000.0, 50.0],
        "flush_vol": [2000.0, 200.0, 9000.0, 80.0],
        "next_vol": [1500.0, 120.0, 8000.0, 60.0],
    })
    t = curve(d, notionals=(1000, 10000))
    small, big = t
    assert small["opt_share"] == 0.75, small
    assert big["opt_share"] == 0.5, big
    q, rho = quartiles(d[d["prior_flush"] >= 2])
    assert isinstance(rho, float) and -1.0 <= rho <= 1.0, rho
    assert q, q
    print("SELF-TEST PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
    else:
        run()
