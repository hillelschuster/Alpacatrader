#!/usr/bin/env python3
"""Split-artifact audit of the leaderboard tape (data/leaderboard/path_*).

Motivation: the OHLCV source is raw/unadjusted and the leaderboard pipeline has
no split filter in evidence. A reverse split manufactures a fake "+900% overnight
leader" that would enter the top-3 tape as a candidate; a forward split
manufactures a fake -50% name (harmless for top-gainer work but still an
artifact). This audit flags symbol-days whose entire day-1 gain is an overnight
jump with no intraday follow-through.

Signature: gain_c at the first real bar implies an overnight ratio
  overnight_ratio = o0 / (c0 / (1 + gain_c0))
and the intraday move (last close / first close - 1) is small. Real news gaps
(e.g. FDA) can also gap overnight, so flags are CANDIDATES; the ratio
distribution (split factors cluster at exact multiples) and the overlap with the
frozen fills tell us whether any of them matter to the strategy.

Artifacts: factory/artifacts/audit_splits.json + .parquet
Usage: python factory/scripts/audit_splits.py
"""
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "factory" / "artifacts"
RATIO_HI = 2.5   # reverse-split candidates (>= 2.5x overnight)
RATIO_LO = 0.40  # forward-split candidates
FLAT_INTRADAY = 0.30  # |last/first - 1| below this = "the move was the gap"


def audit_day(path):
    d = pd.read_parquet(path, columns=["date", "t", "ticker", "o", "c", "gain_c"])
    d = d.sort_values(["date", "ticker", "t"])
    g = d.groupby(["date", "ticker"], sort=False).agg(
        t0=("t", "first"), o0=("o", "first"), c0=("c", "first"),
        g0=("gain_c", "first"), cmax=("c", "max"), clast=("c", "last"),
        n=("t", "size"))
    g = g.reset_index()
    g["prev_close"] = g["c0"] / (1 + g["g0"])
    g["ratio"] = g["o0"] / g["prev_close"]
    g["intraday"] = g["clast"] / g["c0"] - 1
    return g


def main():
    rows = []
    files = sorted(glob.glob(str(ROOT / "data" / "leaderboard" / "path_*.parquet")))
    skipped = []
    for f in files:
        try:
            rows.append(audit_day(f))
        except Exception as e:  # the one schema-less/empty day file
            skipped.append((Path(f).name, str(e)[:60]))
    print(f"files={len(files)} skipped={len(skipped)} {skipped}")
    d = pd.concat(rows, ignore_index=True)
    d["year"] = d["date"].str[:4]
    hi = d[(d["ratio"] >= RATIO_HI) & (d["intraday"].abs() <= FLAT_INTRADAY)].copy()
    lo = d[(d["ratio"] <= RATIO_LO) & (d["intraday"].abs() <= FLAT_INTRADAY)].copy()
    hi["kind"], lo["kind"] = "reverse_like", "forward_like"
    flags = pd.concat([hi, lo], ignore_index=True)

    # exact multiple of a plausible split factor?
    factors = np.array([2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50, 100])
    def near_factor(r):
        if r <= 0:
            return None
        a = np.abs(factors - r) / factors
        if a.min() <= 0.05:
            return int(factors[a.argmin()])
        inv = np.abs(factors - 1 / r) / factors
        if inv.min() <= 0.05:
            return f"1/{int(factors[inv.argmin()])}"
        return None
    flags["factor"] = flags["ratio"].map(near_factor)
    rev_like = flags[flags["kind"] == "reverse_like"]

    fills = pd.read_parquet(OUT / "lb18_oos_oos.parquet")
    fkeys = set(zip(fills["date"].astype(str), fills["ticker"]))
    flags["in_frozen_fills"] = [tuple(x) in fkeys for x in zip(flags["date"], flags["ticker"])]
    try:
        hy = pd.read_parquet(OUT / "lb18_iex_hybrid.parquet")
        a3b = hy[hy.get("variant") == "A3b"] if "variant" in hy else hy
        akeys = set(zip(a3b["date"].astype(str), a3b["ticker"]))
        flags["in_a3b_fills"] = [tuple(x) in akeys for x in zip(flags["date"], flags["ticker"])]
    except Exception:
        flags["in_a3b_fills"] = False

    per_year = {}
    for y, grp in d.groupby("year"):
        f = flags[flags["year"] == y]
        per_year[y] = {
            "pairs": int(len(grp)),
            "flagged": int(len(f)),
            "flagged_share": round(len(f) / len(grp), 5) if len(grp) else None,
        }
    report = {
        "study": "split-artifact audit of data/leaderboard",
        "flags": {"ratio_hi": RATIO_HI, "ratio_lo": RATIO_LO, "flat_intraday": FLAT_INTRADAY},
        "pairs": int(len(d)),
        "flagged_reverse_like": int(len(hi)),
        "flagged_forward_like": int(len(lo)),
        "exact_factor_share_reverse": round(rev_like["factor"].notna().mean(), 4) if len(rev_like) else None,
        "flagged_in_frozen_fills": int(flags["in_frozen_fills"].sum()),
        "flagged_in_a3b_fills": int(flags["in_a3b_fills"].sum()),
        "per_year": per_year,
        "reverse_like_top": rev_like.sort_values("ratio", ascending=False).head(25)[
            ["date", "ticker", "g0", "ratio", "intraday", "factor"]].round(4).to_dict("records"),
        "note": "reverse_like = huge overnight ratio with no intraday follow-through; "
                "real news gaps look similar, so treat as candidates, not proof",
        "flags_seen_data": True, "not_an_alpha_claim": True,
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "audit_splits.json").write_text(json.dumps(report, indent=1, default=str))
    flags.to_parquet(OUT / "audit_splits.parquet", index=False)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("reverse_like_top",)}, indent=1, default=str))
    print("top reverse-like:", json.dumps(report["reverse_like_top"][:8], default=str))


if __name__ == "__main__":
    main()
