"""Dev/collision dataset builder for the top-3 path program.

One row per top-3 name per day: 28 path features + z-series + mb targets.
Frozen stats (medians/IQR, tercile cuts, DTW medoids) fit on DEV ONLY and stored
alongside; collision applies them without refit.

Usage:
  python factory/scripts/build_path_panel.py --months 2025-05 --day-offset 0 --max-days 2 --out data/_scratch/path_dev_test.json
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.replay_watchlist import load_day, snapshot, prior_closes, et_minute  # noqa: E402
from scripts.path_features import build_vector, zseries_30m, is_common_stock  # noqa: E402
from scripts.stagea_eval import outcome_MB  # noqa: E402

T_SNAP = 600


def day_rows(month: str, day, arms=("A", "B")) -> list:
    import pandas as pd
    sess, pm = load_day(month, day, return_pm=True)
    prev = prior_closes(month, day)
    e = snapshot(sess, T_SNAP, prev=prev)
    if len(e) < 5:
        return []
    e = e[~e.index.map(lambda s: False)]  # keep order; universe filter below
    # rank context needs full table: compute progression ranks cheaply
    e45 = snapshot(sess, 585, prev=prev)
    r45 = {s: i + 1 for i, s in enumerate(e45.index)}
    r60 = {s: i + 1 for i, s in enumerate(e.index)}
    top20 = e.head(20)
    sep = float((top20["gain"].iloc[0] - top20["gain"].iloc[3]) * 100) if len(top20) > 3 else 0.0
    med20 = float(top20["gain"].median())
    rows = []
    for rank, sym in enumerate(e.head(10).index, start=1):
        if rank > 3:
            # still record ranks 4-10 lightly for arm-B universe + dropped comparisons
            pass
        sb = sess[sess["ticker"] == sym]
        pb = pm[pm["ticker"] == sym] if pm is not None and len(pm) else None
        if pb is not None and len(pb) == 0:
            pb = None
        dvol_rank = float((e["cdv"] <= e.loc[sym]["cdv"]).mean())
        ctx = {"rank_now": rank, "rank_first": r45.get(sym, 99),
               "rank_switches": abs(r45.get(sym, 99) - rank),
               "dvol_rank_pct": dvol_rank, "separation": sep,
               "n_big": int((e["gain"] > 0.20).sum()),
               "median_top20_gain": med20}
        v = build_vector(sb, pb, prev.get(sym), T_SNAP, rank_ctx=ctx)
        if v is None:
            continue
        z = zseries_30m(sb, T_SNAP)
        # forward targets from first open at/after snapshot
        late = sb[sb["et"] >= T_SNAP].sort_values("timestamp")
        if len(late) < 2:
            continue
        ref = late["open"].iloc[0]
        fwd = (late["close"].iloc[-1] / ref - 1) * 10000 - 20.0
        import numpy as np
        lo = late["low"].to_numpy() / ref - 1
        hi = late["high"].to_numpy() / ref - 1
        mb = {}
        for dd in (100, 200):
            idx = np.nonzero(lo <= -dd / 10000)[0]
            end = idx[0] if len(idx) else len(late)
            best = hi[:end].max(initial=0.0)
            mb[dd] = round(float(min(best * 10000, 1000)), 1)
        rows.append({"day": str(day.date()), "symbol": sym, "rank": rank,
                     "common": bool(is_common_stock(sym)),
                     "in_B": bool(is_common_stock(sym) and 2 <= sb["close"].iloc[-1] <= 20),
                     "features": v, "zseries": z,
                     "fwd": round(float(fwd), 1), "mb100": mb[100], "mb200": mb[200]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--day-offset", type=int, default=0)
    ap.add_argument("--max-days", type=int, default=2)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import pandas as pd
    import json
    allrows = []
    for month in args.months:
        base = Path("data/backfill") if month >= "2026-03" else Path("data")
        probe = pd.read_parquet(base / f"clean_ohlcv_{month}.parquet", columns=["timestamp"])
        probe["timestamp"] = pd.to_datetime(probe["timestamp"], utc=True)
        dates = sorted(probe["timestamp"].dt.floor("D").unique())
        for d in dates[args.day_offset:args.day_offset + args.max_days]:
            try:
                rs = day_rows(month, d)
                allrows += rs
                print(f"  {month} {str(d.date())} rows={len(rs)}", flush=True)
            except Exception as ex:
                print(f"  {month} {str(d.date())} SKIP {str(ex)[:120]}", flush=True)
    Path(args.out).write_text(json.dumps(allrows))
    print(f"wrote {len(allrows)} rows -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
