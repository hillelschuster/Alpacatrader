#!/usr/bin/env python3
"""LIFECYCLE-01 Stage H — select the EARLY RAMP, not the gain level.

REANCHOR: Stage G showed that "the biggest gainers at T" is a past-the-pop selection at every
clock from 04:30 to 09:31. This probe changes the SELECTION VARIABLE, not the clock: it ranks
names by early-ramp evidence (short-horizon acceleration, volume burst, freshness of the move)
instead of by the cumulative gain, and measures the executable forward value of the top-5 at
the same clocks.

Scores (all causal at T, from the same full-market premarket+RTH minute bars):
  gain     : the cumulative gain vs prev close          (the OLD rule — the control)
  ret5     : the 5-minute return                         (pure acceleration)
  burst    : v5 / v30                                    (volume burst)
  ret5x    : ret5 x burst                                (acceleration on a burst)
  fresh    : ret5 x burst / (1 + gain)                   (early in the move, penalising the
                                                          already-extended)
  ret15x   : ret15 x (v15 / v60)

Entry = first observed open at/after the clock; values are executable opens to the close and
to the window end (13:00). Discovery half only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

CLOCKS = (300, 360, 420, 480, 510, 570)
KEEP = 5
GAP_MAX = 5
SCORES = ("gain", "ret5", "burst", "ret5x", "fresh", "ret15x")


def monthly(data_root: Path, month: str) -> Path | None:
    for p in (data_root / f"ohlcv_{month}.parquet", data_root / "backfill" / f"ohlcv_{month}.parquet"):
        if p.exists():
            return p
    return None


def process_month(month: str, days: list[str], data_root: Path) -> list[dict]:
    p = monthly(data_root, month)
    if p is None:
        return []
    et = (pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.hour().cast(pl.Int32) * 60
          + pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.minute().cast(pl.Int32))
    bars = (pl.scan_parquet(p).select(["ticker", "timestamp", "open", "high", "low", "close", "volume"])
            .with_columns(day=pl.col("timestamp").dt.convert_time_zone("America/New_York")
                          .dt.strftime("%Y-%m-%d"), et=et)
            .filter(pl.col("day").is_in(days)).collect())
    prev = (pl.scan_parquet(data_root / "atlas" / "observation" / "v0" / "race.minute_full"
                            / f"month={month}")
            .select(["day", "ticker", "prev_close"]).unique().collect())
    splits = pl.read_parquet(data_root / "harvest01" / "base" / "splits.parquet")
    sf = splits.with_columns((pl.col("old_rate") / pl.col("new_rate")).alias("factor"))
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    rows: list[dict] = []
    for day in days:
        prev_day = max((d for d in cal if d < day), default=None)
        sfm = (sf.filter((pl.col("ex_date") <= day) & (pl.col("ex_date") > prev_day))
               .group_by("symbol").agg(pl.col("factor").product().alias("split_factor"))
               if prev_day else pl.DataFrame(schema={"symbol": pl.String, "split_factor": pl.Float64}))
        d = bars.filter(pl.col("day") == day)
        if d.height == 0:
            continue
        pm = (d.join(prev.filter(pl.col("day") == day), on="ticker", how="inner")
              .join(sfm.rename({"symbol": "ticker"}), on="ticker", how="left")
              .with_columns(pl.col("split_factor").fill_null(1.0))
              .with_columns((pl.col("prev_close") * pl.col("split_factor")).alias("prev_adj"))
              .filter((pl.col("prev_adj") > 0) & (pl.col("close") > 0) & (pl.col("et") >= 240))
              .sort(["ticker", "et"]))
        if pm.height == 0:
            continue
        se = int(cal[day]["session_end"])
        base = pm
        bmap = {}
        for (tk,), g in base.group_by(["ticker"]):
            g = g.sort("et")
            bmap[tk] = (g["et"].to_numpy(), g["open"].to_numpy(),
                        g["high"].to_numpy(), g["low"].to_numpy())
        for C in CLOCKS:
            sub = base.filter(pl.col("et") <= C - 1)
            if sub.height == 0:
                continue
            agg = sub.group_by("ticker").agg(
                pl.col("close").last().alias("c_last"), pl.col("et").last().alias("et_last"),
                pl.col("prev_adj").last().alias("prev_adj"),
                pl.col("volume").filter(pl.col("et") > C - 5).sum().alias("v5"),
                pl.col("volume").filter(pl.col("et") > C - 15).sum().alias("v15"),
                pl.col("volume").filter(pl.col("et") > C - 30).sum().alias("v30"),
                pl.col("volume").filter(pl.col("et") > C - 60).sum().alias("v60"),
                pl.col("close").filter(pl.col("et") <= C - 5).last().alias("c5"),
                pl.col("close").filter(pl.col("et") <= C - 15).last().alias("c15"))
            agg = agg.filter((pl.col("prev_adj") > 0) & (pl.col("c_last") > 0)
                             & (C - 1 - pl.col("et_last") <= 30))
            agg = agg.with_columns([
                (pl.col("c_last") / pl.col("prev_adj") - 1).alias("gain"),
                pl.when(pl.col("c5") > 0).then(pl.col("c_last") / pl.col("c5") - 1).otherwise(None).alias("ret5"),
                pl.when(pl.col("c15") > 0).then(pl.col("c_last") / pl.col("c15") - 1).otherwise(None).alias("ret15"),
                pl.when(pl.col("v30") > 0).then(pl.col("v5") / pl.col("v30")).otherwise(None).alias("burst"),
                pl.when(pl.col("v60") > 0).then(pl.col("v15") / pl.col("v60")).otherwise(None).alias("burst15")])
            agg = agg.with_columns([
                (pl.col("ret5") * pl.col("burst")).alias("ret5x"),
                (pl.col("ret5") * pl.col("burst") / (1 + pl.col("gain"))).alias("fresh"),
                (pl.col("ret15") * pl.col("burst15")).alias("ret15x")])
            # only movers are candidates: the probe is about ramps, not the whole market
            agg = agg.filter((pl.col("gain") > 0.03) | (pl.col("ret15") > 0.02))
            if agg.height < KEEP:
                continue
            for score in SCORES:
                cand = agg.filter(pl.col(score).is_finite()).sort(score, descending=True).head(KEEP)
                for rank, r in enumerate(cand.iter_rows(named=True), 1):
                    arr = bmap.get(r["ticker"])
                    if arr is None:
                        continue
                    ets, op, hi_arr, _lo = arr
                    j = int(np.searchsorted(ets, C))
                    if j >= len(ets) or int(ets[j]) - C >= GAP_MAX:
                        continue
                    entry_px = float(op[j])
                    if not np.isfinite(entry_px) or entry_px <= 0:
                        continue
                    def open_ge(target):
                        k = int(np.searchsorted(ets, target))
                        o = float(op[k]) if k < len(ets) else None
                        return o if (o and o > 0) else None
                    r780 = open_ge(780)
                    rc = open_ge(se)
                    hi = hi_arr[j:]
                    rows.append({"day": day, "clock": C, "score": score, "rank": rank,
                                 "ticker": r["ticker"], "gain": float(r["gain"]),
                                 "entry_et": int(ets[j]), "entry_px": entry_px,
                                 "to780": (r780 / entry_px - 1) if r780 else None,
                                 "captured": (rc / entry_px - 1) if rc else None,
                                 "mfe_780": float(np.nanmax(hi) / entry_px - 1) if hi.size else None})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    out = root / "report"
    days = json.loads((root / "split.json").read_text())["discovery_days"]
    months: dict[str, list[str]] = {}
    for d in days:
        months.setdefault(d[:7], []).append(d)
    all_rows = []
    for month, mdays in sorted(months.items()):
        r = process_month(month, mdays, a.data_root)
        all_rows += r
        print(f"  {month}: {len(r)} rows", flush=True)
    df = pl.DataFrame(all_rows, infer_schema_length=None)
    df.write_parquet(out / "rampselect.parquet")
    lines = ["# LIFECYCLE-01 Stage H — select the early ramp, not the gain level", "",
             "Top-5 by each causal score at the clock; entry = first observed open >= clock;",
             "values are executable opens. gain = the OLD control (cumulative gain).", "",
             "| clock | score | n | mean captured | median | mean to 13:00 | mean MFE to 13:00 |",
             "|---|---|---|---|---|---|---|"]
    for clock in CLOCKS:
        for score in SCORES:
            s = df.filter((pl.col("clock") == clock) & (pl.col("score") == score))
            if s.height < 50:
                continue
            cap = s["captured"].cast(pl.Float64).drop_nulls()
            to780 = s["to780"].cast(pl.Float64).drop_nulls()
            mfe = s["mfe_780"].cast(pl.Float64).drop_nulls()
            lines.append(f"| {clock} | {score} | {cap.len()} | {100*cap.mean():+.2f}% | "
                         f"{100*cap.median():+.2f}% | {100*to780.mean():+.2f}% | {100*mfe.mean():+.2f}% |")
    (out / "rampselect.md").write_text("\n".join(lines) + "\n")
    print(f"rampselect rows={df.height} -> {out/'rampselect.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
