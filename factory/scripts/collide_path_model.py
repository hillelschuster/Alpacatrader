"""Collision test: apply FROZEN path model to unseen month(s). No refit.

For each day: top-3 (per arm) -> medoid assignment (frozen DTW medoids) +
ridge score (frozen scaler+weights) -> rank-IC vs realized mb200,
top-1/day capture vs baseline (top-1 by gain), FP-per-capture.
Writes data/_scratch/path_collide_<month>.json + prints verdict table.

Usage: python factory/scripts/collide_path_model.py --months 2025-08
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.path_features import dtw  # noqa: E402

HOLD = {"2025-08": "data/_scratch/p2_hold_2025-08.json",
        "2025-10": None}  # October built only after August reports


def downsample(z, m: int = 10):
    import numpy as np
    z = np.asarray(z, dtype=float)
    idx = (np.linspace(0, len(z) - 1, min(m, len(z)))).astype(int)
    return z[idx].tolist()


def main():
    import argparse
    import numpy as np
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--freeze", default="data/_scratch/path_freeze_v1.json")
    args = ap.parse_args()
    F = json.load(open(args.freeze))
    R15 = F["meta"]["features_R15"]
    out_all = {}
    for month in args.months:
        rows = json.load(open(HOLD[month]))
        by_day = {}
        for r in rows:
            by_day.setdefault(r["day"], []).append(r)
        res = {"month": month, "days": [], "arms": {}}
        for arm in ("A", "B"):
            FA = F["arms"][arm]
            mi = np.array(FA["scaler_R15"]["median"])
            ii = np.array(FA["scaler_R15"]["iqr"])
            w = np.array(FA["ridge"]["weights"])
            meds = FA["medoids"]
            pred_all, real_all, top1_m, top1_b, fp, cap = [], [], [], [], 0, 0
            for day in sorted(by_day):
                cand = [r for r in by_day[day]
                        if (r["rank"] <= 3) and (arm == "A" or r["in_B"])]
                if not cand:
                    continue
                for r in cand:
                    z = downsample(r["zseries"])
                    c = int(np.argmin([dtw(z, m) for m in meds]))
                    r["_cluster"] = c
                    x = (np.array([r["features"][f] for f in R15]) - mi) / ii
                    r["_score"] = float(x @ w)
                    pred_all.append(r["_score"])
                    real_all.append(r["mb200"])
                top = sorted(cand, key=lambda r: -r["_score"])
                base = sorted(cand, key=lambda r: -(r["features"]["ret_open_T"]))
                top1_m.append(top[0]["mb200"])
                top1_b.append(base[0]["mb200"])
                for r in cand:
                    if r["mb200"] > 200:
                        cap += 1
                    else:
                        fp += 1
            pred_all = np.array(pred_all)
            real_all = np.array(real_all)
            ric = float(np.corrcoef(np.argsort(np.argsort(pred_all)),
                                    np.argsort(np.argsort(real_all)))[0, 1])
            res["arms"][arm] = {
                "n": len(pred_all), "rankIC": round(ric, 3),
                "top1_model_mean": round(float(np.mean(top1_m)), 1),
                "top1_base_mean": round(float(np.mean(top1_b)), 1),
                "fp_per_cap": round(fp / max(cap, 1), 2)}
        out_all[month] = res
        print(month, json.dumps(res["arms"]), flush=True)
    Path(f"data/_scratch/path_collide_{args.months[0]}.json").write_text(json.dumps(out_all))


if __name__ == "__main__":
    main()
