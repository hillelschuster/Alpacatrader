#!/usr/bin/env python3
"""LIFECYCLE-01 Stage G — the premarket ramp: when does the pop actually accumulate?

REANCHOR: the 08:30→09:31 ladder is unprofitable at every clock, which locates the pop
BEFORE 08:30. This probe selects the causal top-5 gainers at early premarket clocks
(04:30, 05:30, 06:30, 07:30, 08:00, 08:30) from the full-market premarket minute bars and
measures the EXECUTABLE forward value of a claim bought at each clock.

Selection: gain vs the previous actual session close (split-normalized), the same anchor as
the rest of the study. Entry: the first observed bar open at/after the clock. Forward values:
executable opens at 09:30 (the RTH open), +120 m, the window end (13:00), and the session
close, plus the MFE/MAE to the window end from the same bars.

Local data only (`ohlcv_<month>.parquet` = full-market PM+RTH minute bars; prev_close from
the race board). Discovery half only; no validation read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

CLOCKS = (270, 300, 330, 360, 390, 420, 450, 480, 510)
KEEP = 5
GAP_MAX = 5


def monthly(data_root: Path, month: str) -> Path | None:
    for p in (data_root / f"ohlcv_{month}.parquet", data_root / "backfill" / f"ohlcv_{month}.parquet"):
        if p.exists():
            return p
    return None


def process_month(month: str, days: list[str], data_root: Path) -> tuple[list[dict], list[dict]]:
    p = monthly(data_root, month)
    if p is None:
        return [], []
    lf = pl.scan_parquet(p)
    et = (pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.hour().cast(pl.Int32) * 60
          + pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.minute().cast(pl.Int32))
    bars = (lf.select(["ticker", "timestamp", "open", "high", "low", "close", "volume"])
            .with_columns(day=pl.col("timestamp").dt.convert_time_zone("America/New_York")
                          .dt.strftime("%Y-%m-%d"), et=et)
            .filter(pl.col("day").is_in(days)).collect())
    prev = (pl.scan_parquet(data_root / "atlas" / "observation" / "v0" / "race.minute_full"
                            / f"month={month}")
            .select(["day", "ticker", "prev_close"]).unique().collect())
    splits = pl.read_parquet(data_root / "harvest01" / "base" / "splits.parquet")
    sf = splits.with_columns((pl.col("old_rate") / pl.col("new_rate")).alias("factor"))
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    roster_rows, minute_rows = [], []
    for day in days:
        prev_day = max((d for d in cal if d < day), default=None)
        sfm = (sf.filter((pl.col("ex_date") <= day) & (pl.col("ex_date") > prev_day))
               .group_by("symbol").agg(pl.col("factor").product().alias("split_factor"))
               if prev_day else pl.DataFrame(schema={"symbol": pl.String, "split_factor": pl.Float64}))
        d = bars.filter(pl.col("day") == day)
        if d.height == 0:
            continue
        prevd = prev.filter(pl.col("day") == day)
        pm = (d.filter((pl.col("et") >= 240) & (pl.col("et") <= 959))
              .join(prevd, on="ticker", how="inner")
              .join(sfm.rename({"symbol": "ticker"}), on="ticker", how="left")
              .with_columns(pl.col("split_factor").fill_null(1.0))
              .with_columns(((pl.col("prev_close") * pl.col("split_factor"))).alias("prev_adj")))
        pm = pm.filter((pl.col("prev_adj") > 0) & (pl.col("close") > 0))
        if pm.height == 0:
            continue
        se = int(cal[day]["session_end"])
        for C in CLOCKS:
            sub = pm.filter(pl.col("et") <= C - 1).sort("et")
            if sub.height == 0:
                continue
            last = sub.group_by("ticker").agg(
                pl.col("close").last().alias("px_last"), pl.col("et").last().alias("et_last"),
                pl.col("prev_adj").last().alias("prev_adj"))
            last = last.filter(pl.col("et_last") >= C - 30)  # a stale name is not a candidate
            last = last.with_columns((pl.col("px_last") / pl.col("prev_adj") - 1).alias("gain"))
            top = last.sort("gain", descending=True).head(KEEP)
            for rank, r in enumerate(top.iter_rows(named=True), 1):
                tk = r["ticker"]
                b = pm.filter(pl.col("ticker") == tk).sort("et")
                ets = b["et"].to_numpy()
                j = int(np.searchsorted(ets, C))
                if j >= len(ets) or int(ets[j]) - C >= GAP_MAX:
                    roster_rows.append({"day": day, "clock": C, "rank": rank, "ticker": tk,
                                        "gain_adj": float(r["gain"]), "status": "blocked"})
                    continue
                entry_et, entry_px = int(ets[j]), float(b["open"][j])
                row = {"day": day, "clock": C, "rank": rank, "ticker": tk,
                       "gain_adj": float(r["gain"]), "status": "filled",
                       "entry_et": entry_et, "entry_px": entry_px,
                       "prev_adj": float(r["prev_adj"]), "session_end": se}
                def open_ge(target: int):
                    k = int(np.searchsorted(ets, target))
                    return float(b["open"][k]) if k < len(ets) and b["open"][k] and b["open"][k] > 0 else None
                for label, target in (("r570", 570), ("r660", 660), ("r780", 780), ("rclose", se)):
                    px = open_ge(target)
                    row[label] = (px / entry_px - 1) if px else None
                seg = b.filter(pl.col("et") <= 780)
                if seg.height:
                    row["mfe_780"] = float(seg["high"].max() / entry_px - 1)
                    row["mae_780"] = float(seg["low"].min() / entry_px - 1)
                    row["mfe_day"] = float(b["high"].max() / entry_px - 1)
                    row["captured"] = row["rclose"]
                roster_rows.append(row)
                if C <= 510:
                    for rr in b.filter((pl.col("et") >= entry_et) & (pl.col("et") <= 780)).iter_rows(named=True):
                        minute_rows.append({"day": day, "clock": C, "rank": rank, "ticker": tk,
                                            "t": int(rr["et"]), "px": float(rr["close"]),
                                            "high": float(rr["high"]), "low": float(rr["low"]),
                                            "volume": float(rr["volume"] or 0.0),
                                            "entry_px": entry_px})
    return roster_rows, minute_rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--clocks", default="")
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    out = root / "report"
    days = json.loads((root / "split.json").read_text())["discovery_days"]
    months: dict[str, list[str]] = {}
    for d in days:
        months.setdefault(d[:7], []).append(d)
    all_roster = []
    for month, mdays in sorted(months.items()):
        r, _ = process_month(month, mdays, a.data_root)
        all_roster += r
        print(f"  {month}: {len(r)} roster rows", flush=True)
    ros = pl.DataFrame(all_roster, infer_schema_length=None)
    ros.write_parquet(out / "preramp_roster.parquet")
    filled = ros.filter(pl.col("status") == "filled")
    rows = []
    for clock, g in filled.group_by("clock"):
        cap = g["captured"].cast(pl.Float64)
        rows.append({"clock": int(clock[0]), "n": g.height,
                     "captured_mean": round(float(cap.mean()), 4),
                     "captured_median": round(float(cap.median()), 4),
                     "to_r570_mean": round(float(g["r570"].cast(pl.Float64).mean()), 4),
                     "mfe_780_mean": round(float(g["mfe_780"].cast(pl.Float64).mean()), 4),
                     "monster_share": round(float((g["mfe_day"].cast(pl.Float64) >= 1).mean()), 4)})
    tab = pl.DataFrame(rows).sort("clock")
    lines = ["# LIFECYCLE-01 Stage G — the premarket ramp (discovery half)", "",
             "Causal top-5 at early PM clocks; entry = first observed open at/after the clock;",
             "values are executable opens. captured = to the session close; r570 = to the RTH open.", "",
             "| clock | n | mean captured | median | mean to 09:30 | mean MFE to 13:00 | monster share |",
             "|---|---|---|---|---|---|---|"]
    for r in tab.iter_rows(named=True):
        lines.append(f"| {r['clock']} | {r['n']} | {100*r['captured_mean']:+.2f}% | "
                     f"{100*r['captured_median']:+.2f}% | {100*r['to_r570_mean']:+.2f}% | "
                     f"{100*r['mfe_780_mean']:+.2f}% | {100*r['monster_share']:.1f}% |")
    (out / "preramp.md").write_text("\n".join(lines) + "\n")
    print(f"preramp rows={ros.height} -> {out/'preramp.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
