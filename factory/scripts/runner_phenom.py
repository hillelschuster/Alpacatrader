"""Runner phenomenology: what does a big top-gainer day actually look like?

For every day, find names with session open->close gain >= 60% (or the day's #1).
Per name, compute the full-day shape: gap, time of first thrust, fraction of move
done by each hour, retrace behavior, halt gaps, afternoon legs. Writes one row per
runner. READ-ONLY; scratch out only.

Usage: python factory/scripts/runner_phenom.py --months 2025-05 --max-days 4
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.replay_watchlist import load_day, et_minute, prior_closes  # noqa: E402


def day_runners(month: str, day, min_gain: float = 0.60) -> list:
    import pandas as pd
    import numpy as np
    sess = load_day(month, day)
    if sess.empty:
        return []
    g = sess.sort_values(["ticker", "timestamp"]).groupby("ticker").agg(
        fo=("open", "first"), lc=("close", "last"), n=("close", "size"),
        ph=("high", "max"))
    g = g[(g["n"] >= 100) & (g["fo"] >= 1) & (g["fo"] <= 50)]
    g["gain"] = g["lc"] / g["fo"] - 1
    picks = g[g["gain"] >= min_gain]
    if picks.empty:
        picks = g.nlargest(1, "gain")
    prev = prior_closes(month, day)
    out = []
    for sym, row in picks.iterrows():
        b = sess[sess["ticker"] == sym].sort_values("timestamp").reset_index(drop=True)
        if len(b) < 60:
            continue
        c = b["close"].to_numpy(float)
        h = b["high"].to_numpy(float)
        et = b["et"].to_numpy(int)
        fin = row["gain"]
        # times of crossing fractions of final gain (close-anchored)
        cross = {}
        for frac in (0.10, 0.25, 0.50, 0.75):
            idx = np.argmax(c / row["fo"] - 1 >= frac * fin) if fin > 0 else 0
            cross[frac] = int(et[idx]) if (c[-1] / row["fo"] - 1) >= frac * fin else None
        hi_idx = int(np.argmax(h))
        # running max retrace before final high
        runmax = np.maximum.accumulate(h[:hi_idx + 1])
        retrace = float(np.max(1 - c[:hi_idx + 1] / runmax)) if hi_idx > 0 else 0.0
        # halt gaps: >=5-min holes in the minute sequence
        ts = et
        gaps = np.diff(ts)
        n_halts = int((gaps >= 5).sum())
        # afternoon contribution
        am = c[ts < 720][-1] if (ts < 720).any() else c[0]
        pm_frac = float((c[-1] - am) / max(c[-1] - row["fo"], 1e-9))
        out.append({
            "day": str(day.date()), "sym": sym,
            "gap": round(float(row["fo"] / prev[sym] - 1), 3) if prev.get(sym) else None,
            "gain": round(float(fin), 3),
            "t_open2hi": int(et[hi_idx]) - 570,
            "t_10pct": (cross[0.10] - 570) if cross[0.10] else None,
            "t_25pct": (cross[0.25] - 570) if cross[0.25] else None,
            "t_50pct": (cross[0.50] - 570) if cross[0.50] else None,
            "t_75pct": (cross[0.75] - 570) if cross[0.75] else None,
            "max_retrace_pre_hi": round(retrace, 3),
            "n_halt_gaps": n_halts,
            "pm_frac_of_move": round(pm_frac, 2),
            "bars": int(len(b)),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--day-offset", type=int, default=0)
    ap.add_argument("--max-days", type=int, default=4)
    ap.add_argument("--min-gain", type=float, default=0.60)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import pandas as pd
    import json
    rows = []
    outp = Path(args.out)
    done_days = set()
    if outp.exists():  # resume: skip already-processed days
        for ln in outp.read_text().splitlines():
            if ln.strip():
                r = json.loads(ln)
                rows.append(r)
                done_days.add(r["day"])
    for month in args.months:
        base = Path("data/backfill") if month >= "2026-03" else Path("data")
        probe = pd.read_parquet(base / f"clean_ohlcv_{month}.parquet", columns=["timestamp"])
        probe["timestamp"] = pd.to_datetime(probe["timestamp"], utc=True)
        dates = sorted(probe["timestamp"].dt.floor("D").unique())
        for d in dates[args.day_offset:args.day_offset + args.max_days]:
            if str(d.date()) in done_days:
                continue
            try:
                rs = day_runners(month, d, args.min_gain)
                with open(outp, "a") as f:
                    for r in rs:
                        f.write(json.dumps(r) + "\n")
                rows += rs
                if rs:
                    print(f"  {str(d.date())}: " + ", ".join(
                        f"{r['sym']}+{r['gain']*100:.0f}%" for r in rs), flush=True)
            except Exception as ex:
                print(f"  {month} {str(d.date())} SKIP {str(ex)[:80]}", flush=True)
    print(f"total runners: {len(rows)} in {args.out}", flush=True)


if __name__ == "__main__":
    main()
