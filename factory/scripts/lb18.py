#!/usr/bin/env python3
"""Vectorized 18-month causal top-3 leaderboard + path preservation at 1-minute
native resolution. Semantics identical to the verified per-ticker build:
  decision at t in 571..959 uses close of last bar et<=t-1;
  top-3 by causal gain vs immediately-prior session close (PIT universe);
  paths for union-of-top3 names with last-completed-bar OHLCV + n_bars.
Writes data/leaderboard/{lb,path}_YYYY-MM-DD.parquet, resumable per day.
LB_OUT env var overrides output dir (used for equality verification).
"""
import os
import sys
from bisect import bisect_right
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tv_leaderboard import month_path  # noqa

LB = Path(os.environ.get("LB_OUT", str(ROOT / "data" / "leaderboard")))
T_START, T_END = 571, 959
MIN_GRID = np.arange(570, 960)

_pit_cache: dict = {}


def elig_for(day_str: str) -> set:
    if not _pit_cache:
        df = pd.read_parquet(ROOT / "data" / "pit" / "pit_symbols.parquet")
        _pit_cache["by_vintage"] = {v: set(g["symbol"])
                                    for v, g in df.groupby("vintage")}
        _pit_cache["vintages"] = sorted(_pit_cache["by_vintage"])
    vs = _pit_cache["vintages"]
    i = bisect_right(vs, day_str) - 1
    if i < 0:
        sys.exit(f"no PIT vintage on/before {day_str}")
    return _pit_cache["by_vintage"][vs[i]]


def month_frames(month: str):
    df = pd.read_parquet(month_path(month),
                         columns=["timestamp", "ticker", "open", "high",
                                  "low", "close", "volume"])
    ts = pd.to_datetime(df["timestamp"], utc=True)
    et = ts.dt.tz_convert("America/New_York")
    df["et"] = et.dt.hour * 60 + et.dt.minute
    df["d"] = et.dt.date
    df = df[(df["et"] >= 570) & (df["et"] <= 959)]
    df = df.sort_values(["d", "ticker", "et"])
    sess_last = (df.sort_values("timestamp").groupby(["d", "ticker"], sort=False)
                   ["close"].last())
    sessions = sorted(df["d"].unique())

    def prev_close_for(day):
        prior = [s for s in sessions if s < day]
        if not prior:
            y, m = int(str(day)[:4]), int(str(day)[5:7])
            pm = f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"
            try:
                pdf = pd.read_parquet(month_path(pm),
                                      columns=["timestamp", "ticker", "close"])
                pts = pd.to_datetime(pdf["timestamp"], utc=True)
                pet = pts.dt.tz_convert("America/New_York")
                pmin = pet.dt.hour * 60 + pet.dt.minute
                pdf = pdf[(pmin >= 570) & (pmin < 960)]
                pdf["d"] = pet.dt.date
                pdays = sorted(pdf["d"].unique())
                if pdays:
                    return dict(pdf[pdf["d"] == pdays[-1]]
                                 .groupby("ticker")["close"].last())
            except Exception:
                pass
            return {}
        return dict(sess_last.loc[prior[-1]])

    return df, prev_close_for


def build_day(g_day: pd.DataFrame, day, prev: dict, elig: set) -> None:
    key = LB / f"lb_{day.isoformat()}.parquet"
    pkey = LB / f"path_{day.isoformat()}.parquet"
    if key.exists() and pkey.exists():
        return
    if len(g_day) == 0 or not prev:
        pd.DataFrame().to_parquet(key)
        pd.DataFrame().to_parquet(pkey)
        return
    ge = g_day[g_day["ticker"].isin(elig) & g_day["ticker"].isin(prev)]
    if len(ge) == 0:
        pd.DataFrame().to_parquet(key)
        pd.DataFrame().to_parquet(pkey)
        return
    ge = ge.drop_duplicates(["ticker", "et"], keep="last")

    wide = {}
    raw = {}
    for f in ("open", "high", "low", "close", "volume"):
        w = (ge.pivot(index="et", columns="ticker", values=f)
               .reindex(MIN_GRID))
        raw[f] = w
        wide[f] = w.ffill()
    # decision t -> positional row of minute t-1 in MIN_GRID
    dec_rows = np.arange(T_START, T_END + 1)
    idx = dec_rows - 570 - 1
    causal_close = wide["close"].iloc[idx]
    nbars = raw["close"].notna().cumsum(axis=0).iloc[idx]
    prev_s = pd.Series(prev).reindex(causal_close.columns).to_numpy()
    gain = causal_close.to_numpy() / prev_s[None, :] - 1

    gm = np.where(np.isnan(gain), -np.inf, gain)
    order = np.argsort(-gm, axis=1, kind="stable")
    n_t = gm.shape[0]
    valid_n = (~np.isnan(gain)).sum(axis=1)

    cols = np.array(causal_close.columns)
    lb_rows = []
    union = set()
    for i in range(n_t):
        k = min(3, int(valid_n[i]))
        if k == 0:
            continue
        t = int(dec_rows[i])
        for rank in range(k):
            cix = order[i, rank]
            tk = cols[cix]
            lb_rows.append((t, rank + 1, tk, round(float(gain[i, cix]), 4),
                            round(float(causal_close.iat[i, cix]), 4)))
            union.add(tk)
    lb = pd.DataFrame(lb_rows, columns=["t", "rank", "ticker", "gain", "px"])
    lb.insert(0, "date", day.isoformat())
    lb.to_parquet(key)

    if not union:
        pd.DataFrame().to_parquet(pkey)
        return
    ucols = [c for c in cols if c in union]
    pc = causal_close[ucols]
    po = wide["open"].iloc[idx][ucols]
    ph = wide["high"].iloc[idx][ucols]
    pl = wide["low"].iloc[idx][ucols]
    pv = wide["volume"].iloc[idx][ucols]
    pnb = nbars[ucols]
    pgain = pc.to_numpy() / np.array([prev[c] for c in ucols])[None, :] - 1
    parts = []
    for c in ucols:
        nbc = pnb[c].to_numpy()
        vc = pv[c].to_numpy().copy()
        vc[nbc == 0] = 0.0
        gc = pgain[:, ucols.index(c)].copy()
        gc[nbc == 0] = np.nan
        parts.append(pd.DataFrame({
            "t": dec_rows, "ticker": c,
            "o": po[c].to_numpy(), "h": ph[c].to_numpy(),
            "l": pl[c].to_numpy(), "c": pc[c].to_numpy(),
            "v": vc, "gain_c": gc, "n_bars": nbc.astype(int),
        }))
    path = pd.concat(parts, ignore_index=True)
    path.insert(0, "date", day.isoformat())
    path.to_parquet(pkey)


def main():
    months = sys.argv[1:]
    LB.mkdir(parents=True, exist_ok=True)
    for month in months:
        df, prev_close_for = month_frames(month)
        for day in sorted(df["d"].unique()):
            g_day = df[df["d"] == day]
            prev = prev_close_for(day)
            elig = elig_for(day.isoformat())
            build_day(g_day, day, prev, elig)
            print(f"{month} {day}: ok", flush=True)


if __name__ == "__main__":
    main()
