"""Independent verification of load-bearing claims (ET clocks, causal gating).

Tasks (each prints verdict-ready numbers with exact n):
  A  hindsight-leader minutes: eventual-#1 (n>=100 label) next-min mean vs tail.
  B  causal rank-1 chasing: per-minute rank-1 next-min returns, pooled by month.
  C  PM retests via harness path: echo-narrow extension, PM-high-dist corr,
     PM-$-vol-leadership hit rate (top-5 finish).

Usage:
  python factory/scripts/verify_core.py --task A --months 2025-03 --day-idx 0
  python factory/scripts/verify_core.py --task B --months 2025-03 2025-04
  python factory/scripts/verify_core.py --task C --months 2025-05 --max-days 4
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_watchlist import load_day, et_minute  # noqa: E402

T_OPEN = 570  # 09:30 ET


def day_frame(month: str, day):
    import pandas as pd
    sess = load_day(month, day)
    return sess


def task_A(sess, label=""):
    """Hindsight: eventual-#1 minute next-min mean vs tail (rank 11+ by day gain)."""
    import pandas as pd
    eves = sess.groupby("ticker").agg(fo=("open", "first"), lc=("close", "last"),
                                      n=("close", "size"))
    eves = eves[(eves["n"] >= 100) & (eves["fo"] >= 1) & (eves["fo"] <= 50)]
    if len(eves) < 12:
        return None
    eves["gain"] = eves["lc"] / eves["fo"] - 1
    rk = eves["gain"].rank(ascending=False, method="first")
    ev = rk.idxmin()
    tail = set(rk[rk >= 11].index)
    g = sess[sess["ticker"].isin(set([ev]) | tail)].sort_values(["ticker", "timestamp"])
    g["nxt"] = g.groupby("ticker")["close"].shift(-1)
    same = g.groupby("ticker")["timestamp"].shift(-1).dt.date == g["timestamp"].dt.date
    g = g[same.fillna(False)]
    g["ret"] = (g["nxt"] / g["close"] - 1) * 10000
    a = g[g["ticker"] == ev]["ret"]
    b = g[g["ticker"] != ev]["ret"]
    import statistics as st
    return {"ev": ev, "ev_n": len(a), "ev_mean": round(float(a.mean()), 2),
            "tail_n": len(b), "tail_mean": round(float(b.mean()), 3)}


def task_B(sess, horizon_min: int = 1):
    """Causal minute rank-1 chasing: rank by gain-so-far each minute, forward ret.
    horizon_min=1 -> next bar; =60 -> close ~60 min later (panel-claim match)."""
    import pandas as pd
    import numpy as np
    el = sess[sess["et"] >= T_OPEN].copy()
    first = el.sort_values("timestamp").groupby("ticker").first()[["open"]]
    el = el.join(first.rename(columns={"open": "fo"}), on="ticker")
    el = el[(el["fo"] >= 1) & (el["fo"] <= 50)]
    el["gso"] = el["close"] / el["fo"] - 1
    el["rk"] = el.groupby("timestamp")["gso"].rank(ascending=False, method="first")
    el = el.sort_values(["ticker", "timestamp"])
    el["nxt"] = el.groupby("ticker")["close"].shift(-1)
    same = el.groupby("ticker")["timestamp"].shift(-1).dt.date == el["timestamp"].dt.date
    el = el[same.fillna(False)]
    r1 = el[el["rk"] == 1.0].copy()
    if horizon_min == 1:
        r1["ret"] = (r1["nxt"] / r1["close"] - 1) * 10000
    else:
        import pandas as pd
        # per (ticker, timestamp) -> forward close via merge_asof on target time
        bars = el[["ticker", "timestamp", "close"]].sort_values(["ticker", "timestamp"])
        tg = r1[["ticker", "timestamp", "close"]].copy()
        tg["target"] = tg["timestamp"] + pd.Timedelta(minutes=horizon_min)
        ret_by_key = {}
        for t, grp in tg.groupby("ticker"):
            b = bars[bars["ticker"] == t][["timestamp", "close"]]
            # no tolerance: first print >= +60min (a holder exits at next print;
            # tolerance would drop halt-affected rows = survivorship tilt)
            m = pd.merge_asof(grp.sort_values("target"), b, left_on="target",
                              right_on="timestamp", direction="forward",
                              suffixes=("", "_f"))
            for _, row in m.iterrows():
                cf = row.get("close_f")
                ret_by_key[(t, row["timestamp"])] = (
                    (cf / row["close"] - 1) * 10000 if pd.notna(cf) else None)
        r1["ret"] = [ret_by_key.get((t, ts)) for t, ts in zip(r1["ticker"], r1["timestamp"])]
        r1 = r1.dropna(subset=["ret"])
    import statistics as st
    return {"n": len(r1), "mean": round(float(r1["ret"].mean()), 2) if len(r1) else None,
            "med": round(float(r1["ret"].median()), 2) if len(r1) else None}


def task_C_day(month, day):
    """PM retests, harness path: echo-narrow ext rate, PM-high-dist corr, PMlead hit."""
    import pandas as pd
    import numpy as np
    sess = load_day(month, day)
    pm_path = Path(f"data/backfill/premarket_ohlcv_{month}.parquet")
    if not pm_path.exists():
        return {"skip": "no-premarket-file"}
    pm = pd.read_parquet(pm_path, columns=["timestamp", "ticker", "open", "high", "low", "close", "volume"])
    pm["timestamp"] = pd.to_datetime(pm["timestamp"], utc=True)
    pm["date"] = pm["timestamp"].dt.floor("D")
    pm = pm[pm["date"] == day]
    if len(pm) == 0:
        return {"skip": "no-pm-day"}
    pa = pm.sort_values("timestamp").groupby("ticker").agg(
        pfo=("open", "first"), phi=("high", "max"), plo=("low", "min"),
        pvol=("volume", "sum"), n=("close", "size"))
    pa = pa[pa["n"] >= 10]
    pa["prange"] = (pa["phi"] - pa["plo"]) / pa["pfo"]
    op = sess[sess["et"] == T_OPEN].set_index("ticker")["open"]
    common = pa.join(op.rename("sopen"), how="inner")
    common = common[(common["sopen"] >= 1) & (common["sopen"] <= 50)]
    fh = sess[(sess["et"] >= 570) & (sess["et"] < 630)].groupby("ticker").agg(
        fhh=("high", "max"), fhl=("low", "min"))
    m = common.join(fh, how="inner")
    m["fh_range"] = (m["fhh"] - m["fhl"]) / m["sopen"]
    m["ext"] = (m["fh_range"] > m["prange"]).astype(int)
    narrow = m[m["prange"] <= m["prange"].quantile(1 / 3)]
    wide = m[m["prange"] >= m["prange"].quantile(2 / 3)]
    dist = (m["phi"] - m["sopen"]) / m["phi"]
    lc = sess.sort_values("timestamp").groupby("ticker").last()[["close"]]
    m2 = m.join(lc, how="inner")
    m2["rest"] = m2["close"] / m2["sopen"] - 1
    corr = float(np.corrcoef(dist.fillna(0), m2["rest"].fillna(0))[0, 1])
    day_gain = m2["rest"].sort_values(ascending=False)
    pmlead = (m2["close"] * 0 + m2["pvol"]).idxmax()  # PM $-vol approx via vol; $-vol below
    pmlead = ((m2["close"] * m2["pvol"]).idxmax())
    top5 = set(day_gain.head(5).index)
    return {"n": len(m), "ext_all": round(float(m["ext"].mean()), 3),
            "ext_narrow": round(float(narrow["ext"].mean()), 3),
            "ext_wide": round(float(wide["ext"].mean()), 3),
            "pmcorr": round(corr, 4), "pmlead": pmlead,
            "pmlead_top5": bool(pmlead in top5)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["A", "B", "C"], required=True)
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--day-idx", type=int, default=0)
    ap.add_argument("--max-days", type=int, default=2)
    ap.add_argument("--horizon", type=int, default=1)
    args = ap.parse_args()
    import pandas as pd
    for month in args.months:
        base = Path("data/backfill") if month >= "2026-03" else Path("data")
        probe = pd.read_parquet(base / f"clean_ohlcv_{month}.parquet", columns=["timestamp"])
        probe["timestamp"] = pd.to_datetime(probe["timestamp"], utc=True)
        dates = sorted(probe["timestamp"].dt.floor("D").unique())
        sel = [dates[args.day_idx]] if args.task in ("A",) else dates[:args.max_days]
        for d in sel:
            try:
                sess = day_frame(month, d)
                if args.task == "A":
                    print(month, str(d.date()), task_A(sess), flush=True)
                elif args.task == "B":
                    print(month, str(d.date()), task_B(sess, args.horizon), flush=True)
                else:
                    print(month, str(d.date()), task_C_day(month, d), flush=True)
            except Exception as ex:
                print(month, str(d.date()), "SKIP", str(ex)[:100], flush=True)


if __name__ == "__main__":
    main()
