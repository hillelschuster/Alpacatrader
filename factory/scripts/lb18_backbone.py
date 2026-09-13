#!/usr/bin/env python3
# /// script
# dependencies = ["pandas", "pyarrow", "numpy"]
# ///
"""PRE-REG-BACKBONE-01: frozen-mechanism regime replication on 2021-2023.

Runs the frozen H025 engine (lb18_oos.run_engine, unchanged) per year on the
PIT-built 2021-2023 tape, with split-clean variants, plus per-year census
aggregates, then applies the pre-registered REPLICATES/FAILS/MIXED rule.
No adoption path; replication evidence only.

Usage: python factory/scripts/lb18_backbone.py [--self-test]
Artifacts: factory/artifacts/lb18_backbone.json / lb18_backbone_fills.parquet
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lb18_oos import load_months, run_engine, stats  # noqa: E402
from event_census import events_for_day, usable_days  # noqa: E402

ROOT = HERE.parents[1]
LB = ROOT / "data" / "leaderboard"
ART = ROOT / "factory" / "artifacts"
YEARS = ["2021", "2022", "2023"]
REF_MONTHS = [f"2024-{m:02d}" for m in range(1, 13)] + ["2025-01", "2025-02"]


def months_for(year):
    ms = {f.name[5:12] for f in LB.glob("path_*.parquet")}
    return sorted(m for m in ms if m.startswith(year))


def load_flags():
    f = pd.read_parquet(ART / "audit_splits.parquet")
    return f[["date", "ticker"]].drop_duplicates()


def split_clean(fills, flags):
    f = fills.merge(flags.assign(_flag=True), on=["date", "ticker"], how="left")
    return f[f["_flag"].isna()].drop(columns="_flag")


def engine_period(months, label, flags):
    paths, lb = load_months(months)
    fills = run_engine(paths, lb)
    days = int(paths["date"].nunique())
    out = {"months": len(months), "days": days, "fills": int(len(fills))}
    for tag, df in (("all", fills),
                    ("pf2", fills[fills["prior_flush"] >= 2]),
                    ("rank1", fills[fills["rank"] == 1])):
        d = stats(df, f"{label} {tag}")
        d["fills_per_day"] = round(len(df) / days, 3)
        out[tag] = d
    cl = split_clean(fills, flags)
    out["clean_all"] = stats(cl, f"{label} clean all")
    out["clean_pf2"] = stats(cl[cl["prior_flush"] >= 2], f"{label} clean pf2")
    out["flagged_fills"] = int(len(fills) - len(cl))
    return out, fills


def census_year(year):
    days = [d for d in usable_days() if d[:4] == year]
    rows = []
    for d in days:
        try:
            rows.extend(events_for_day(d))
        except Exception as e:
            print(f"  census skip {d}: {type(e).__name__}: {e}")
    df = pd.DataFrame(rows)
    if df.empty:
        return {"days": len(days), "n_events": 0}
    rec = df["recovered_30m"].mean()

    def bucket(sub):
        if len(sub) == 0:
            return {"n": 0, "rec": None}
        return {"n": int(len(sub)), "rec": round(float(sub["recovered_30m"].mean()), 4),
                "t_rec_med": float(sub["t_recover_min"].median()),
                "adv_p05": round(float(sub["adverse_from_low"].quantile(.05)), 4)}

    ranks = {"1": bucket(df[df["rank"] == 1]),
             "2": bucket(df[df["rank"] == 2]),
             "3": bucket(df[df["rank"] == 3]),
             "off": bucket(df[df["rank"].isna()])}
    pf = {k: bucket(df[df["prior_flush"] == int(k)]) for k in ("0", "1")}
    pf["2plus"] = bucket(df[df["prior_flush"] >= 2])
    halt = df[df["pre_gap_min"] >= 5]
    return {"days": len(days), "n_events": int(len(df)),
            "events_per_day": round(len(df) / len(days), 3),
            "recovered_30m": round(float(rec), 4),
            "rank": ranks, "prior_flush": pf,
            "am_rec": round(float(df[df["t"] < 720]["recovered_30m"].mean()), 4),
            "pm_rec": round(float(df[df["t"] >= 720]["recovered_30m"].mean()), 4),
            "halt_share": round(len(halt) / len(df), 4),
            "halt": bucket(halt)}


def verdict(per_year):
    fails, ok = [], []
    for y, r in per_year.items():
        pf2 = r["engine"]["pf2"]
        frac = pf2["months_pos"] / max(pf2["n_months"], 1)
        if pf2["mean"] <= 0 and pf2["n"] >= 100:
            fails.append(y)
        if pf2["mean"] > 0 and frac >= 0.60:
            ok.append(y)
    grad = []
    for y, r in per_year.items():
        c = r["census"]
        r1 = c.get("rank", {}).get("1", {}).get("rec")
        r3 = c.get("rank", {}).get("3", {}).get("rec")
        grad.append(r1 is not None and r3 is not None and r1 > r3)
    if fails:
        return "FAILS", {"failing_years": fails, "gradient": grad}
    if len(ok) == len(per_year) and all(grad):
        return "REPLICATES", {"gradient": grad}
    return "MIXED", {"ok_years": ok, "gradient": grad}


def selftest():
    flags = pd.DataFrame([{"date": "2021-02-09", "ticker": "KALV"}])
    fills = pd.DataFrame([{"date": "2021-02-09", "ticker": "KALV", "ret": 0.1},
                          {"date": "2021-02-10", "ticker": "XYZ", "ret": 0.1}])
    cl = split_clean(fills, flags)
    assert len(cl) == 1 and cl.iloc[0]["ticker"] == "XYZ"
    v, _ = verdict({"2022": {"engine": {"pf2": {"mean": 0.01, "n": 200, "months_pos": 8, "n_months": 12}},
                             "census": {"rank": {"1": {"rec": .4}, "3": {"rec": .2}}}}})
    assert v == "REPLICATES", v
    print("SELF-TEST PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        selftest()
        return

    flags = load_flags()
    print(f"split flags: {len(flags)}")
    flags_by_year = (flags.assign(y=flags["date"].str[:4]).groupby("y").size()
                     .to_dict())

    ref, ref_fills = engine_period(REF_MONTHS, "REF2024", flags)
    if ref["all"]["n"] != 541 or ref["pf2"]["n"] != 381:
        sys.exit(f"PARITY FAIL: ref all={ref['all']['n']} pf2={ref['pf2']['n']}")

    per_year, all_fills = {}, []
    for y in YEARS:
        months = months_for(y)
        print(f"=== {y}: {len(months)} months ===")
        eng, fills = engine_period(months, y, flags)
        cen = census_year(y)
        per_year[y] = {"engine": eng, "census": cen}
        fills = fills.assign(year=y)
        all_fills.append(fills)

    v, detail = verdict(per_year)
    out = {"study": "PRE-REG-BACKBONE-01",
           "rule": "frozen H025 engine (lb18_oos.run_engine) on PIT-built tape",
           "reference": ref, "split_flags_by_year": flags_by_year,
           "years": per_year, "verdict": v, "verdict_detail": detail,
           "flags": {"measurement": True, "seen_data": False,
                     "not_an_alpha_claim": True,
                     "note": "2021-2023 were unexamined before this run; "
                             "replication of a frozen mechanism, not OOS"}}
    ART.mkdir(exist_ok=True)
    (ART / "lb18_backbone.json").write_text(json.dumps(out, indent=1, default=str))
    pd.concat(all_fills, ignore_index=True).to_parquet(
        ART / "lb18_backbone_fills.parquet", index=False)
    print(f"\nVERDICT: {v}  {json.dumps(detail)}")
    print("artifacts: lb18_backbone.json / lb18_backbone_fills.parquet")


if __name__ == "__main__":
    main()
