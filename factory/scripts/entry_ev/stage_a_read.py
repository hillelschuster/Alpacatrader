#!/usr/bin/env python3
"""ENTRY-EV-01 Stage-A readout (pre-reg researches/PRE-REG-ENTRY-EV-01.md §2, §8).

Sections:
  1. pooled gross per horizon + DISCOVER/CONFIRM split;
  2. 1-D conditional surfaces (8 coordinates x h in {5,15,30,60}) with breadth/tails;
  3. hindsight-vs-causal promotion anatomy (the one large positive region, its causal
     proxies, and the gate evaluation);
  4. escalation-gate check (§6): no Stage-A region meets the Stage-B criterion if empty.

Read-only over data/entry_ev/stage_a/<day>.parquet. Prints JSON (or --out PATH).
"""
import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path("/home/hillel/projects/Alpacatrader/data/entry_ev/stage_a")
DISC_END = "2022-04-07"
HS = [1, 3, 5, 10, 15, 30, 60]


def split_stats(s: pl.Series, days: pl.Series) -> dict:
    n = len(s)
    if n == 0:
        return {"n": 0}
    sv = s.sort(descending=True)
    tot = float(s.sum())
    top5 = float(sv.head(5).sum())
    return {"n": n, "mean": float(s.mean()), "median": float(s.median()),
            "top5_share": (top5 / tot) if tot != 0 else None,
            "mean_excl_top5": float((tot - top5) / (n - min(5, n))) if n > 5 else None}


def main():
    lf = pl.scan_parquet(str(ROOT / "*.parquet"))
    df = lf.select(["day", "t", "ticker", "rank_known", "gain", "px", "ret1", "ret3", "ret5",
                    "ret15", "dd_from_high", "promo_age", "vol30_ratio"] + [f"fwd_ret_{h}" for h in HS]).collect()
    out = {"days": int(df["day"].n_unique()), "rows": len(df), "pooled": {}, "causal": {}, "hindsight": {}, "gate": {}}

    for h in HS:
        v = df[f"fwd_ret_{h}"].drop_nulls()
        d = df.filter(pl.col(f"fwd_ret_{h}").is_not_null())
        disc = d.filter(pl.col("day") <= DISC_END)[f"fwd_ret_{h}"]
        conf = d.filter(pl.col("day") > DISC_END)[f"fwd_ret_{h}"]
        out["pooled"][str(h)] = {"n": len(v), "days": int(d["day"].n_unique()),
                                 "mean": float(v.mean()), "median": float(v.median()),
                                 "disc": {"n": len(disc), "mean": float(disc.mean())},
                                 "confirm": {"n": len(conf), "mean": float(conf.mean())}}

    # ---- promotion anatomy: hindsight bucket vs causal proxies (rank 6-10) ----
    r5 = df.filter(pl.col("rank_known") == 5).group_by(["day", "t"]).agg(pl.col("gain").first().alias("gain5"))
    dfj = df.join(r5, on=["day", "t"], how="left")
    mid = dfj.filter((pl.col("rank_known") >= 6) & (pl.col("rank_known") <= 10))
    hind = dfj.filter((pl.col("promo_age") >= -5) & (pl.col("promo_age") < 0))
    post = dfj.filter((pl.col("promo_age") >= 0) & (pl.col("promo_age") < 5))

    def table(sub: pl.DataFrame, hs=(1, 3, 5, 15, 30)) -> dict:
        r = {"n_rows": len(sub), "days": int(sub["day"].n_unique())}
        for h in hs:
            r[h] = split_stats(sub[f"fwd_ret_{h}"].drop_nulls(), sub["day"])
        return r

    out["hindsight"] = {
        "promo_age_-5..0": table(hind),
        "promo_age_0..5_post": table(post),
        "note": "promo_age<0 conditions on the ticker's first top-5 entry occurring 1-5 minutes AFTER t: future information; not tradable as conditioned.",
    }
    causal = {
        "rank6-10_all": table(mid),
        "rank6-10_ret5>0": table(mid.filter(pl.col("ret5") > 0)),
        "rank6-10_ret5>1pct": table(mid.filter(pl.col("ret5") > 0.01)),
        "rank6-10_ret1>2pct": table(mid.filter(pl.col("ret1") > 0.02)),
        "rank6-10_gap5>-1pct": table(mid.filter((pl.col("gain") >= pl.col("gain5") - 0.01) & (pl.col("gain") != pl.col("gain5")))),
        "rank6-10_vol30ratio>1.5": table(mid.filter(pl.col("vol30_ratio") > 1.5)),
        "rank6-10_vol30ratio<0.5": table(mid.filter(pl.col("vol30_ratio") < 0.5)),
        "rank6-10_at_dayhigh": table(mid.filter(pl.col("dd_from_high") > -0.005)),
    }
    out["causal"] = causal

    # ---- gate check (§6/§2): best Stage-A eligible region gross stats ----
    best = None
    for name, r in causal.items():
        for h in (1, 3, 5, 15, 30):
            e = r.get(h)
            if not e or not e.get("n"):
                continue
            if best is None or e["mean"] > best["mean"]:
                best = {"region": name, "h": h, **e}
    out["gate"] = {
        "criterion": "Stage-B runs only where Stage-A gross mean AND median exceed a stated margin over the measured mechanism cost floor (~92-116bps).",
        "best_eligible_region": best,
        "stage_b_eligible": bool(best and best["mean"] > 0.0120 and best["median"] > 0.0120),
        "hindsight_not_eligible": "promo_age<0 is future-conditioned; excluded from Stage-B candidacy by construction.",
    }
    txt = json.dumps(out, indent=1)
    if "--out" in sys.argv:
        Path(sys.argv[sys.argv.index("--out") + 1]).write_text(txt)
    else:
        print(txt)


if __name__ == "__main__":
    main()
