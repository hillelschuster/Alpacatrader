"""Offline replay harness: selection alpha vs timing alpha, historical + forward.

Pipeline per day:
  snapshot state at t  ->  watchlist rule  ->  selected names
      -> E0 buy-at-t+1 outcome (SELECTION alpha: fwd/MFE/MAE)
      -> E1/E2 entry-event outcomes (TIMING alpha: same watchlist, realistic entries)

Runs on historical parquet (session + optional premarket files). Forward observer
days are replayed the same way once their full-market bars are backfilled
(next-day historical pull — identical SIP data), so selection rules and entries
are evaluated with one code path on both.

Usage:
  python factory/scripts/replay_watchlist.py --months 2025-03 --max-days 4
  python factory/scripts/replay_watchlist.py --months 2025-03 --max-days 4 --rules top4_gain,seplist --entries E0,E1
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

COST_BPS = 20.0
SNAP_LAG = 1  # decision at t uses bars with ET-minute <= t-1 (start-stamped bars)


def _dst_bounds(year: int):
    """US DST: second Sunday March -> first Sunday November (no tzdata needed)."""
    import datetime as dt
    def nth_sunday(y, m, n):
        d = dt.date(y, m, 1)
        first = d + dt.timedelta(days=(6 - d.weekday()) % 7)
        return first + dt.timedelta(weeks=n - 1)
    return nth_sunday(year, 3, 2), nth_sunday(year, 11, 1)


def et_minute(ts):
    """Vectorized UTC->ET minute-of-day (Series in, Series out)."""
    import pandas as pd
    ts = pd.to_datetime(ts, utc=True)
    off = pd.Series(300, index=ts.index)
    for year in ts.dt.year.dropna().unique():
        s, e = _dst_bounds(int(year))
        d = ts.dt.date
        off = off.mask((d >= s) & (d < e), 240)
    return (ts.dt.hour * 60 + ts.dt.minute - off) % 1440


# ── data ───────────────────────────────────────────────────────────────────
def load_day(month: str, day, premarket: bool = False):
    import pandas as pd
    base = Path("data/backfill") if month >= "2026-03" else Path("data")
    pre = f"premarket_ohlcv_{month}.parquet" if month < "2026-03" else None
    frames = []
    if premarket and pre and (Path("data/backfill") / pre).exists():
        frames.append(pd.read_parquet(Path("data/backfill") / pre,
                                      columns=["timestamp", "ticker", "open", "high", "low", "close", "volume"]))
    frames.append(pd.read_parquet(base / f"clean_ohlcv_{month}.parquet",
                                  columns=["timestamp", "ticker", "open", "high", "low", "close", "volume"]))
    df = pd.concat(frames)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.floor("D")
    g = df[df["date"] == day].copy()
    g["et"] = et_minute(g["timestamp"])
    # session in ET (matches clean_month RTH 9:30-16:00 ET); NO full-day gating here —
    # eligibility is decided causally inside snapshot() from bars <= t only.
    sess = g[(g["et"] >= 570) & (g["et"] < 960)].sort_values(["ticker", "timestamp"])
    return sess


def snapshot(sess, t_et: int, lag: int = SNAP_LAG, min_bars: int = 10):
    """Causal snapshot at ET-minute t_et using bars with et <= t_et - lag.
    Eligibility (causal-only): >= min_bars bars by t AND first session open $1-50.
    min_bars=10 (not 20): halt-type bar holes are part of small-cap momentum;
    RAPP-2025-03-04 ran +49% on the day with only 12 bars by 10:00 ET.
    Returns per-ticker rows sorted by open-anchored gain (pc/po - 1)."""
    early = sess[sess["et"] <= t_et - lag].sort_values(["ticker", "timestamp"])
    if len(early) == 0:
        import pandas as pd
        return pd.DataFrame()
    agg = early.groupby("ticker").agg(
        po=("open", "first"), pc=("close", "last"), ph=("high", "max"),
        cv=("volume", "sum"), n=("close", "size"),
        cdv=("close", lambda s: float((s * early.loc[s.index, "volume"]).sum())))
    agg = agg[(agg["n"] >= min_bars) & (agg["po"] >= 1) & (agg["po"] <= 50)]
    agg["gain"] = agg["pc"] / agg["po"] - 1
    agg["vwap"] = agg["cdv"] / agg["cv"]
    return agg.sort_values("gain", ascending=False)


# ── watchlist rules (pure; same semantics as forward observer) ─────────────
def r_top_gain(e, k=4):
    return list(e.head(k).index)


def r_gain_x_vol(e, k=4):
    s = e["gain"] * (e["cdv"].apply(lambda v: math.log1p(v)))
    return list(s.sort_values(ascending=False).head(k).index)


def r_sep_list(e, k=5):
    return list(e.head(k).index)


def r_vwap_hold(e, k=4):
    held = e[e["pc"] > e["vwap"]]
    return list(held.head(k).index)


RULES = {"top4_gain": r_top_gain, "gain_x_vol": r_gain_x_vol,
         "seplist5": r_sep_list, "vwap_hold": r_vwap_hold}


# ── outcomes ───────────────────────────────────────────────────────────────
def outcome_E0(sess, ticker, t_et: int):
    """Buy next bar after ET-minute t, hold to close. Pure selection read."""
    late = sess[(sess["ticker"] == ticker) & (sess["et"] > t_et)]
    late = late.sort_values("timestamp")
    if len(late) < 2:
        return None
    ref = late["open"].iloc[0]
    fwd = (late["close"].iloc[-1] / ref - 1) * 10000 - COST_BPS
    mfe = (late["high"].max() / ref - 1) * 10000
    mae = (late["low"].min() / ref - 1) * 10000
    up = late[late["high"] / ref - 1 >= 0.01]
    dn = late[late["low"] / ref - 1 <= -0.01]
    iu = up["timestamp"].iloc[0] if len(up) else None
    idn = dn["timestamp"].iloc[0] if len(dn) else None
    return {"fwd": round(float(fwd), 1), "mfe": round(float(mfe), 1),
            "mae": round(float(mae), 1),
            "obp": bool(iu is not None and (idn is None or iu <= idn))}


def entries_E1(sess, ticker, t_et: int):
    """First-pullback entry: opening drive then <=3-bar pause, enter new high.
    Stop = pause low, time-stop 15 min, flatten at 15:30 ET. Returns trade or None."""
    late = sess[(sess["ticker"] == ticker) & (sess["et"] > t_et)]
    late = late.sort_values("timestamp").reset_index(drop=True)
    if len(late) < 20:
        return None
    drive_hi = late["high"].iloc[:10].max()
    if drive_hi / late["open"].iloc[0] - 1 < 0.03:
        return None
    for i in range(10, len(late) - 6):
        window = late.iloc[i - 3:i]
        pause = (window["close"] <= window["open"]).sum() >= 2
        tight = (window["high"].max() / window["low"].min() - 1) < 0.02
        brk = late["close"].iloc[i] > window["high"].max()
        if pause and tight and brk:
            entry = late["close"].iloc[i]
            stop = window["low"].min()
            if entry / stop - 1 > 0.05 or entry / stop - 1 <= 0:
                return None
            for j in range(i + 1, min(i + 16, len(late))):
                hh = late["high"].iloc[j]
                ll = late["low"].iloc[j]
                end = late["et"].iloc[j] >= 930  # 15:30 ET flatten
                if ll <= stop or end:
                    px = stop if ll <= stop else late["close"].iloc[j]
                    ret = (px / entry - 1) * 10000 - COST_BPS
                    return {"entry_i": i, "ret": round(float(ret), 1),
                            "stop_hit": bool(ll <= stop and not end)}
                if hh / entry - 1 >= 2 * (entry / stop - 1):
                    px = entry * (1 + 2 * (entry / stop - 1))
                    ret = (px / entry - 1) * 10000 - COST_BPS
                    return {"entry_i": i, "ret": round(float(ret), 1), "stop_hit": False}
            px = late["close"].iloc[min(i + 15, len(late) - 1)]
            return {"entry_i": i, "ret": round(float((px / entry - 1) * 10000 - COST_BPS), 1),
                    "stop_hit": False}
    return None


def replay_day(month: str, day, t_list, rules, entries):
    sess = load_day(month, day)
    eves = sess.sort_values("timestamp").groupby("ticker").agg(
        lc=("close", "last"), fo=("open", "first"), n=("close", "size"))
    eves = eves[eves["n"] >= 100]  # label-side presence filter (label only, not selection)
    eventual = (eves["lc"] / eves["fo"] - 1).idxmax()
    out = {"day": str(day.date()), "eventual": eventual, "snaps": {}}
    for t in t_list:
        e = snapshot(sess, t)
        if len(e) < 5:
            continue
        top = e.head(5)
        sep = {"diff_pp": round(float((top["gain"].iloc[0] - top["gain"].iloc[1]) * 100), 2),
               "ratio": round(float(top["gain"].iloc[0] / top["gain"].iloc[1]), 2)
               if top["gain"].iloc[1] > 0 else None}
        sres = {"sep": sep, "rules": {}}
        for rn in rules:
            picks = RULES[rn](e)
            det = []
            for p in picks:
                d = {"ticker": p}
                if "E0" in entries:
                    d["E0"] = outcome_E0(sess, p, t)
                if "E1" in entries:
                    d["E1"] = entries_E1(sess, p, t)
                det.append(d)
            sres["rules"][rn] = det
        out["snaps"][t] = sres
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--max-days", type=int, default=4)
    ap.add_argument("--times", default="585,600,630,660")  # ET minutes
    ap.add_argument("--rules", default=",".join(RULES))
    ap.add_argument("--entries", default="E0,E1")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    import pandas as pd
    t_list = [int(x) for x in args.times.split(",")]
    rules = args.rules.split(",")
    entries = args.entries.split(",")
    results = []
    for month in args.months:
        base = Path("data/backfill") if month >= "2026-03" else Path("data")
        probe = pd.read_parquet(base / f"clean_ohlcv_{month}.parquet", columns=["timestamp"])
        probe["timestamp"] = pd.to_datetime(probe["timestamp"], utc=True)
        dates = sorted(probe["timestamp"].dt.floor("D").unique())[:args.max_days]
        for d in dates:
            try:
                results.append(replay_day(month, d, t_list, rules, entries))
                print(f"  {month} {str(d.date())} ok", flush=True)
            except Exception as ex:
                print(f"  {month} {str(d.date())} SKIP {str(ex)[:100]}", flush=True)
    # selection-vs-timing summary
    for rn in rules:
        e0 = [d["E0"]["fwd"] for r in results for s in r["snaps"].values()
              for d in s["rules"][rn] if d.get("E0")]
        e1 = [d["E1"]["ret"] for r in results for s in r["snaps"].values()
              for d in s["rules"][rn] if d.get("E1")]
        import statistics as st
        print(f"{rn}: E0 n={len(e0)} mean={st.mean(e0) if e0 else None} "
              f"med={st.median(e0) if e0 else None} | "
              f"E1 n={len(e1)} mean={st.mean(e1) if e1 else None} "
              f"hit={(sum(1 for x in e1 if x > 0) / len(e1)) if e1 else None}")
    if args.out:
        Path(args.out).write_text(json.dumps(results))
    return results


if __name__ == "__main__":
    main()
