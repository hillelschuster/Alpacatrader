# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow", "numpy"]
# ///
"""PRE-REG-LEADER-01: rank/time conditioning of the frozen flush mechanics.

Baseline = frozen H025 engine (unfiltered lb) and must reproduce the frozen
numbers exactly (parity gate) before any variant is read.

V1-V3 restrict the anchor set by filtering `lb` (anchors are its rows) and call
the frozen engine unchanged:
  V1 rank==1 anchors; V2 anchors with t<720 ET; V3 rank==1 AND t<720.

V4 is the census-native frequency variant: while a name is rank 1 and t<720,
maintain a resting bid at B = 0.9 * running session-max close (running max of
completed bars before the current bar; re-armed at each qualifying minute and
persisting under the same 120-min expiry as H025), fill on the first new bar
with low <= B, exit exactly as H025 (stop 0.9B, target c0 = running max, tl30).

Exit: if V4 win < 49% or mean net <= 0 -> RETIRED (no tweaking).
Artifacts: factory/artifacts/lb18_leader.json + .parquet
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import build  # noqa: E402
from lb18_oos import (FR, STOP_L, TL, WIN, load_months, run_engine,  # noqa: E402
                      sim_tl30, stats)

ART = ROOT / "factory" / "artifacts"
AM_CUT = 12 * 60  # 720 ET


def month_range(a, b):
    return [str(p) for p in pd.period_range(a, b, freq="M")]


def _replay_v4(d, mins, pfmap, key):
    """Same mechanics as H025 but bid anchored on the running session max."""
    t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
    npx = d["newpos"]
    date, ticker = key
    rows = []
    cm = -1.0
    flat = True
    B = c0 = None
    b_t = None
    exit_t = -(10 ** 9)
    for jj in range(len(npx)):
        j = int(npx[jj])
        tj = int(t[j])
        if jj > 0:
            cm = max(cm, float(c[int(npx[jj - 1])]))
        if not flat and tj > exit_t:
            flat, B, c0 = True, None, None
        if flat and tj in mins and cm > 0:
            B = cm * (1 - STOP_L)
            c0 = cm
            b_t = tj
        if not flat or B is None:
            continue
        if b_t is not None and tj - b_t > WIN:
            B = None
            continue
        if l[j] <= B:
            ret, ex = sim_tl30(npx[jj:], o, h, l, c, t, B, c0)
            if ret is None:
                continue
            rows.append({
                "date": date, "month": str(date)[:7], "ticker": ticker,
                "t0": int(b_t), "tf": tj, "rank": 1,
                "prior_flush": int(pfmap.get((date, ticker, int(b_t)), -1)),
                "fc": float(c[j]) / B - 1, "ret": ret - FR, "exit_t": int(ex)})
            flat, exit_t, B, c0 = False, int(ex), None, None
    return rows


def run_v4(paths, lb, am_cut=AM_CUT):
    A = build(paths)
    pfmap = {(r.date, r.ticker, int(r.t)): int(r.prior_flush)
             for r in paths[["date", "ticker", "t", "prior_flush"]].itertuples()}
    sel = lb[(lb["rank"] == 1) & (lb["t"] < am_cut)]
    q = {k: set(g["t"].astype(int)) for k, g in sel.groupby(["date", "ticker"], sort=False)}
    rows = []
    for key, mins in q.items():
        d = A.get(key)
        if d is None:
            continue
        rows.extend(_replay_v4(d, mins, pfmap, key))
    return pd.DataFrame(rows)


def fills_per_day(df, paths):
    days = paths["date"].nunique()
    return round(len(df) / max(days, 1), 3), int(days)


def summarize(df, paths, tag):
    s = stats(df, tag)
    fpd, days = fills_per_day(df, paths)
    s["fills_per_day"] = fpd
    s["days"] = days
    if len(df):
        s["worst5"] = df.nsmallest(5, "ret")[["date", "ticker", "tf", "ret"]].to_dict("records")
    return s


def variant_stats(df, paths, tag):
    out = {"all": summarize(df, paths, tag)}
    if len(df):
        out["pf2"] = summarize(df[df["prior_flush"] >= 2], paths, tag + " pf2")
        mo = df[df["prior_flush"] >= 2].groupby("month")["ret"].agg(["count", "mean"]).round(4)
        out["monthly_pf2"] = {k: {"n": int(v["count"]), "mean": float(v["mean"])}
                              for k, v in mo.iterrows()}
    return out


def gate(v, base, dev):
    a = v["all"]
    cond = {
        "freq_ge_1.5x_baseline": a["fills_per_day"] >= 1.5 * base["all"]["fills_per_day"],
        "mean_ge_0.005": a.get("mean", -9) >= 0.005,
        "win_ge_0.50": a.get("pos", 0) >= 0.50,
        "months_pos_ge_10": a.get("months_pos", 0) >= 10,
        "worst_month_ok": a.get("worst_month", -9) >= -0.0251,
        "dev_direction_ok": bool(dev) and dev["all"].get("mean", -9) > 0
                            and dev["all"].get("months_pos", 0) >= 11,
    }
    return {"conditions": cond, "candidate": all(cond.values())}


def self_test():
    d = {"t": np.array([575, 576, 577, 578, 579, 580]),
         "o": np.array([100, 100, 95, 100, 100, 100]),
         "h": np.array([101, 101, 101, 120, 120, 120]),
         "l": np.array([101, 100, 89, 99, 99, 99]),
         "c": np.array([100, 100, 100, 100, 100, 100]),
         "newpos": np.array([0, 1, 2, 3, 4, 5])}
    rows = _replay_v4(d, {576}, {}, ("2024-01-02", "TEST"))
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["t0"] == 576 and r["tf"] == 577, r
    assert abs(r["ret"] - (100 / 90 - 1 - FR)) < 1e-9, r
    print("SELF-TEST PASS: v4 fills at bar 577, target hit, ret=%.5f" % r["ret"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return

    oos_m = month_range("2024-01", "2025-02")
    dev_m = month_range("2025-03", "2026-08")
    out = {"study": "PRE-REG-LEADER-01", "am_cut": AM_CUT,
           "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True,
                     "h025_untouched": True},
           "parity": {}, "periods": {}}

    for name, months in (("oos", oos_m), ("dev", dev_m)):
        paths, lb = load_months(months)
        base = run_engine(paths, lb)
        b = variant_stats(base, paths, f"{name} baseline")
        v1 = run_engine(paths, lb[lb["rank"] == 1])
        v2 = run_engine(paths, lb[lb["t"] < AM_CUT])
        v3 = run_engine(paths, lb[(lb["rank"] == 1) & (lb["t"] < AM_CUT)])
        v4 = run_v4(paths, lb)
        out["periods"][name] = {
            "baseline": b,
            "V1_rank1": variant_stats(v1, paths, f"{name} V1 rank1"),
            "V2_am": variant_stats(v2, paths, f"{name} V2 am"),
            "V3_rank1_am": variant_stats(v3, paths, f"{name} V3 rank1+am"),
            "V4_cont_bid": variant_stats(v4, paths, f"{name} V4 cont"),
        }
        if name == "oos":
            out["parity"]["oos"] = {
                "all_n": b["all"]["n"], "all_mean": b["all"].get("mean"),
                "pf2_n": b["pf2"]["n"], "pf2_mean": b["pf2"].get("mean")}
        frames = []
        for tag, df in (("baseline", base), ("V1_rank1", v1), ("V2_am", v2),
                        ("V3_rank1_am", v3), ("V4_cont_bid", v4)):
            if len(df):
                df = df.copy()
                df["variant"] = tag
                frames.append(df)
        if frames:
            pd.concat(frames, ignore_index=True).to_parquet(
                ART / f"lb18_leader_{name}.parquet")

    p = out["parity"]["oos"]
    assert p["all_n"] == 541 and abs(p["all_mean"] - 0.0091) < 1e-4, p
    assert p["pf2_n"] == 381 and abs(p["pf2_mean"] - 0.0114) < 1e-4, p
    print("PARITY OK (frozen reproduced exactly)")

    base_oos = out["periods"]["oos"]["baseline"]
    out["gates"] = {}
    for k in ("V1_rank1", "V2_am", "V3_rank1_am", "V4_cont_bid"):
        g = gate(out["periods"]["oos"][k], base_oos, out["periods"]["dev"][k])
        gg = g["conditions"]
        if k == "V4_cont_bid" and (out["periods"]["oos"][k]["all"].get("pos", 0) < 0.49
                                   or out["periods"]["oos"][k]["all"].get("mean", -9) <= 0):
            g["retired"] = True
        out["gates"][k] = g
        print(f"\n{k}: {json.dumps(gg, indent=1)}")
        print(f"  candidate={g['candidate']} retired={g.get('retired', False)}")

    (ART / "lb18_leader.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\nartifact -> {ART / 'lb18_leader.json'}")


if __name__ == "__main__":
    main()
