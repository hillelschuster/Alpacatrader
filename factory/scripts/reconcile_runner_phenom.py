#!/usr/bin/env python
"""H019 reconciliation: split runner_phenom_2025.json into genuine >=60% open->close
runners vs fallback day-#1 rows; recompute key phenomenology stats per subset;
spot-check vs raw clean bars (polars read; pandas has no parquet engine in this venv)."""
import json, sys, statistics as st
from pathlib import Path
import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "factory/artifacts/runner_phenom_2025.json"
sys.path.insert(0, str(ROOT / "factory"))
from scripts.replay_watchlist import et_minute  # noqa: E402

rows = json.loads(ART.read_text())
genuine = [r for r in rows if r["gain"] >= 0.60]
fallback = [r for r in rows if r["gain"] < 0.60]
days_all = sorted({r["day"] for r in rows})
days_g = sorted({r["day"] for r in genuine})
days_f = sorted({r["day"] for r in fallback})

both = sorted(set(days_g) & set(days_f))
dup_sym = [d for d in days_all if len({r["sym"] for r in rows if r["day"] == d}) != len([r for r in rows if r["day"] == d])]
fb_multi = [d for d in days_f if len([r for r in fallback if r["day"] == d]) > 1]

def stats(rs, label):
    if not rs:
        return {"label": label, "n": 0}
    t50 = [r["t_50pct"] for r in rs if r["t_50pct"] is not None]
    t25 = [r["t_25pct"] for r in rs if r["t_25pct"] is not None]
    ret30 = [r for r in rs if r["max_retrace_pre_hi"] >= 0.30]
    def bucket(lo, hi):
        b = [r for r in rs if lo <= r["n_halt_gaps"] <= hi]
        return {"n": len(b), "mean_gain": round(st.mean([r["gain"] for r in b]), 3) if b else None}
    return {
        "label": label, "n": len(rs), "n_days": len({r["day"] for r in rs}),
        "median_gain": round(st.median([r["gain"] for r in rs]), 3),
        "mean_gain": round(st.mean([r["gain"] for r in rs]), 3),
        "median_t_open2hi_min": st.median([r["t_open2hi"] for r in rs]),
        "median_t_25pct_min": st.median(t25) if t25 else None,
        "median_t_50pct_min": st.median(t50) if t50 else None,
        "frac_half_by_1000": round(sum(1 for r in rs if r["t_50pct"] is not None and r["t_50pct"] <= 30) / len(rs), 3),
        "frac_half_by_1125": round(sum(1 for r in rs if r["t_50pct"] is not None and r["t_50pct"] <= 115) / len(rs), 3),
        "median_pm_frac_of_move": round(st.median([r["pm_frac_of_move"] for r in rs]), 3),
        "frac_any_halt_ge5min": round(sum(1 for r in rs if r["n_halt_gaps"] >= 1) / len(rs), 3),
        "halt_buckets": {"0": bucket(0, 0), "1-2": bucket(1, 2), "3-5": bucket(3, 5), "6-10": bucket(6, 10), "11+": bucket(11, 999)},
        "retrace30_n": len(ret30),
        "retrace30_mean_gain": round(st.mean([r["gain"] for r in ret30]), 3) if ret30 else None,
    }

per_month = {}
for r in rows:
    m = r["day"][:7]
    v = per_month.setdefault(m, {"rows": 0, "genuine": 0, "fallback": 0, "days": set(), "days_g": set()})
    v["rows"] += 1
    if r["gain"] >= 0.60:
        v["genuine"] += 1; v["days_g"].add(r["day"])
    else:
        v["fallback"] += 1
    v["days"].add(r["day"])
pm = {m: {"rows": v["rows"], "genuine": v["genuine"], "fallback": v["fallback"],
          "days_in_artifact": len(v["days"]), "days_with_genuine": len(v["days_g"]),
          "genuine_per_day": round(v["genuine"] / len(v["days"]), 3) if v["days"] else None}
      for m, v in sorted(per_month.items())}

base_result = {
    "artifact": str(ART.relative_to(ROOT)),
    "total_rows": len(rows), "genuine_rows": len(genuine), "fallback_rows": len(fallback),
    "days_total": len(days_all), "days_with_genuine": len(days_g), "days_fallback_only": len(days_f),
    "genuine_per_day": round(len(genuine) / len(days_all), 3),
    "mixed_per_day": round(len(rows) / len(days_all), 3),
    "share_days_with_genuine": round(len(days_g) / len(days_all), 3),
    "semantics_checks": {"days_with_both_genuine_and_fallback": both, "duplicate_sym_in_day": dup_sym, "fallback_days_with_gt1_row": fb_multi},
    "stats_genuine": stats(genuine, "genuine>=60%"),
    "stats_fallback": stats(fallback, "fallback day-#1"),
    "stats_mixed": stats(rows, "mixed (as-reported artifact)"),
    "per_month": pm,
}
print("=== ARTIFACT-ONLY RECONCILIATION ===")
print(json.dumps(base_result, indent=1, default=str))

# ── spot-check vs raw clean bars (polars-only; no pyarrow in venv) ────────
def recompute_day(day: str):
    import datetime as dt
    d = pd.Timestamp(day, tz="UTC"); nxt = d + pd.Timedelta(days=1)
    month = day[:7]
    base = ROOT / ("data/backfill" if month >= "2026-03" else "data")
    df = pl.read_parquet(str(base / f"clean_ohlcv_{month}.parquet"),
                         columns=["timestamp", "ticker", "open", "high", "low", "close", "volume"])
    if df["timestamp"].dtype.time_zone is None:
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("ts_et"))
    df = df.filter(pl.col("ts_et").dt.date() == d.date())
    df = df.with_columns((pl.col("ts_et").dt.hour().cast(pl.Int32) * 60 + pl.col("ts_et").dt.minute().cast(pl.Int32)).alias("et"))
    sess = df.filter((pl.col("et") >= 570) & (pl.col("et") < 960)).sort(["ticker", "timestamp"])
    if sess.is_empty():
        return {"error": "no bars"}
    g = sess.group_by("ticker", maintain_order=True).agg(
        pl.col("open").first().alias("fo"), pl.col("close").last().alias("lc"),
        pl.len().alias("n"), pl.col("high").max().alias("ph"))
    g = g.filter((pl.col("n") >= 100) & (pl.col("fo") >= 1) & (pl.col("fo") <= 50))
    g = g.with_columns((pl.col("lc") / pl.col("fo") - 1).alias("gain"))
    picks = g.filter(pl.col("gain") >= 0.60).sort("gain", descending=True)
    top1 = g.sort("gain", descending=True).head(1)
    return {"recomputed_genuine": int(picks.height),
            "recomputed_genuine_syms": picks["ticker"].to_list(),
            "recomputed_top1_sym": top1["ticker"][0], "recomputed_top1": round(float(top1["gain"][0]), 3)}


multi_g = [d for d in days_g if len([r for r in genuine if r["day"] == d]) > 1]
sample = days_g[:1] + multi_g[:1] + days_f[:1]
spot = {}
for day in sample:
    art_day = [r for r in rows if r["day"] == day]
    spot[day] = {"artifact_rows": len(art_day),
                 "artifact_genuine_syms": sorted(r["sym"] for r in art_day if r["gain"] >= 0.60),
                 "artifact_fallback_syms": sorted(r["sym"] for r in art_day if r["gain"] < 0.60),
                 **recompute_day(day)}
print("\n=== SPOT-CHECK vs RAW ===")
print(json.dumps(spot, indent=1, default=str))

base_result["spot_check_vs_raw"] = spot
OUT = ROOT / "factory/artifacts/runner_phenom_reconcile.json"
OUT.write_text(json.dumps(base_result, indent=1, default=str))
print(f"\nwrote {OUT}")
