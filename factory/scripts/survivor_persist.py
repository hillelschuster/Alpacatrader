#!/usr/bin/env python3
"""H034 probe 2: cross-day survivor persistence (measurement only).
Usage: python factory/scripts/survivor_persist.py [--self-test]
Artifacts: factory/artifacts/lb18_persist.json / lb18_persist_fills.parquet
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "factory" / "scripts"))
import event_census as ec  # noqa: E402

ART = ROOT / "factory" / "artifacts"
FILL_FILES = [ART / "lb18_backbone_fills.parquet", ART / "lb18_oos_oos.parquet"]
FRICTION = 0.01


def load_survivors():
    days = sorted(ec.usable_days())
    ev = ec.load_all(days)
    g = (ev.groupby(["date", "ticker"], as_index=False)["seq"].max()
           .rename(columns={"seq": "n_events"}))
    g["survivor"] = (g["n_events"] >= 2).astype(int)
    return days, g


def persistence(surv, days):
    fwd = {a: b for a, b in zip(days, days[1:])}
    sv = surv[["date", "ticker", "survivor"]].copy()
    sv["next_date"] = sv["date"].map(fwd)
    sv = sv.dropna(subset=["next_date"])
    m = sv.merge(surv[["date", "ticker", "survivor"]], left_on=["next_date", "ticker"],
                 right_on=["date", "ticker"], suffixes=("_prev", "_today"))
    m["era"] = np.where(m["date_today"] < "2024-01-01", "2021-2023", "2024+")
    out = {"n_pairs": int(len(m)), "base_rate": round(float(m["survivor_today"].mean()), 4)}
    a = m[m["survivor_prev"] == 1]["survivor_today"]
    b = m[m["survivor_prev"] == 0]["survivor_today"]
    out.update({"p_surv_given_prev1": round(float(a.mean()), 4), "n_prev1": int(len(a)),
                "p_surv_given_prev0": round(float(b.mean()), 4), "n_prev0": int(len(b)),
                "lift": round(float(a.mean() - b.mean()), 4)})
    for era, sub in m.groupby("era"):
        aa = sub[sub["survivor_prev"] == 1]["survivor_today"]
        bb = sub[sub["survivor_prev"] == 0]["survivor_today"]
        out[f"era_{era}"] = {"n": int(len(sub)), "base": round(float(sub["survivor_today"].mean()), 4),
                             "p1": round(float(aa.mean()), 4), "p0": round(float(bb.mean()), 4)}
    return out


def load_fills():
    fr = []
    for p in FILL_FILES:
        d = pd.read_parquet(p)
        fr.append(d[["date", "ticker", "tf", "rank", "prior_flush", "ret"]])
    f = pd.concat(fr, ignore_index=True)
    return f.drop_duplicates(subset=["date", "ticker", "tf"]).sort_values(["date", "ticker", "tf"])


def stats(df, tag):
    if len(df) == 0:
        return {"tag": tag, "n": 0}
    r = df["ret"].to_numpy()
    mon = df.assign(m=df["date"].str[:7]).groupby("m")["ret"].mean()
    return {"tag": tag, "n": int(len(r)), "mean": round(float(r.mean()), 4),
            "median": round(float(np.median(r)), 4), "pos": round(float((r > 0).mean()), 3),
            "months_pos": int((mon > 0).sum()), "n_months": int(len(mon)),
            "worst_month": round(float(mon.min()), 4)}


def verdict(rows, base_pf2):
    row = next((x for x in rows if x["tag"] == "pf2:prev_survivor"), None)
    if not row or row["n"] < 200:
        return "NEGATIVE", "insufficient prev-survivor pf2 fills"
    ok = (row["mean"] >= base_pf2 + 0.0030 and row["months_pos"] / row["n_months"] >= 0.6
          and (row.get("era_2021_2023") or -1) > 0 and (row.get("era_2024_2025") or -1) > 0)
    return ("CANDIDATE" if ok else "NEGATIVE"), "frozen four-part gate"


def attach(f, days, surv):
    prev = {b: a for a, b in zip(days, days[1:])}
    idx = surv.set_index(["date", "ticker"])["survivor"]
    f = f.copy()
    f["prev_date"] = f["date"].map(prev)
    f["today_survivor"] = [int(idx.get((d, t), -1)) for d, t in zip(f["date"], f["ticker"])]
    f["prev_survivor"] = [int(idx.get((d, t), -1)) for d, t in zip(f["prev_date"], f["ticker"])]
    return f


def self_test():
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    surv = pd.DataFrame({"date": ["2024-01-02", "2024-01-03", "2024-01-03", "2024-01-03",
                                  "2024-01-04", "2024-01-04"],
                         "ticker": ["A", "A", "B", "C", "A", "C"],
                         "survivor": [1, 1, 0, 0, 1, 0]})
    p = persistence(surv, days)
    assert p["n_pairs"] == 3, p          # day1->2: A; day2->3: A, C
    assert p["p_surv_given_prev1"] == 1.0 and p["n_prev1"] == 2, p
    assert p["p_surv_given_prev0"] == 0.0 and p["n_prev0"] == 1, p
    f = pd.DataFrame({"date": ["2024-01-03"], "ticker": ["A"], "tf": [600],
                      "rank": [1], "prior_flush": [2], "ret": [0.01]})
    a = attach(f, days, surv)
    assert a["prev_survivor"].iloc[0] == 1 and a["today_survivor"].iloc[0] == 1
    print("SELF-TEST PASS: persistence + causal prev-day attach")


def run():
    days, surv = load_survivors()
    f = load_fills()
    a = attach(f, days, surv)
    pers = persistence(surv, days)
    rows = []
    for scope, sub in (("all", a), ("pf2", a[a["prior_flush"] >= 2])):
        for label, ss in (("prev_survivor", sub[sub["prev_survivor"] == 1]),
                          ("prev_nonsurvivor", sub[sub["prev_survivor"] == 0]),
                          ("prev_absent", sub[sub["prev_survivor"] == -1])):
            r = stats(ss, f"{scope}:{label}")
            if r["n"]:
                r["era_2021_2023"] = round(float(ss[ss["date"] < "2024-01-01"]["ret"].mean()), 4) \
                    if (ss["date"] < "2024-01-01").any() else None
                r["era_2024_2025"] = round(float(ss[ss["date"] >= "2024-01-01"]["ret"].mean()), 4) \
                    if (ss["date"] >= "2024-01-01").any() else None
            rows.append(r)
    base = stats(a[a["prior_flush"] >= 2], "pf2:baseline")
    v, why = verdict(rows, base["mean"])
    out = {"study": "H034 probe 2 - cross-day survivor persistence",
           "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True},
           "disclaimer": "post-hoc split of seen fills; candidate only, forward test required",
           "n_days": len(days), "persistence": pers, "pf2_baseline": base,
           "fill_splits": rows, "verdict": v, "verdict_basis": why}
    (ART / "lb18_persist.json").write_text(json.dumps(out, indent=1))
    a.to_parquet(ART / "lb18_persist_fills.parquet")
    print(f"days={len(days)} fills={len(a)}")
    print("persistence:", json.dumps(pers))
    for r in rows:
        print(" ", json.dumps(r))
    print("VERDICT:", v, "-", why)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test()
        return
    run()


if __name__ == "__main__":
    main()
