#!/usr/bin/env python3
"""HARVEST01 — policy grid: many causal handling rules over the actual member paths.

One pass per member-day evaluates a frozen grid of ~50 policies (trigger x sell
fraction x re-entry/rotation) and records per-endpoint deltas vs holding the same
member to the same endpoint. All triggers are causal on the member's own completed
bars from its actual fill; execution is at the next bar open; partial sells split the
position; rotation buys the current rank-1 leader (leaders lane) at the first 5-minute
clock at/after the sell.

Frozen grid (see build_grid): giveback {10,15,20,25}% x sell {100,50,33}%;
damage+wait (10,5) (15,5); failed-recovery (10,A=3/5/8); damage+volume-decay
(10, r=0.5/0.3); no-new-high 20 bars while damaged; time-stop (10% for 30min without
reclaim); scale-out into strength touch {30,50,100} x sell 33%; trail-after-strength
(touch 50 then giveback 10); and re-entry/rotation variants of gb10, failrec_a5, decay.

Outputs per day: <data>/harvest01/policies/<day>.parquet
Usage:
    .venv/bin/python factory/scripts/basket_harvest_policies.py --days 2021-02-01
    .venv/bin/python factory/scripts/basket_harvest_policies.py --dev-days --workers 3
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

ENDPOINTS = [600, 630, 660, 690, 720]
SIDE = 0.005


def build_grid() -> list[dict]:
    grid = []

    def add(pid, trig, sell, reentry="none"):
        grid.append({"id": pid, "trigger": trig, "sell": sell, "reentry": reentry})

    for g in (0.10, 0.15, 0.20, 0.25):
        for f in (1.0, 0.5, 1 / 3):
            add(f"gb{int(g*100)}_{int(round(f*100))}", {"type": "gb", "g": g}, f)
    for L, w in ((0.10, 5), (0.15, 5)):
        for f in (1.0, 0.5):
            add(f"dmgw{int(L*100)}w{w}_{int(round(f*100))}", {"type": "dmgw", "L": L, "w": w}, f)
    for A in (0.03, 0.05, 0.08):
        for f in (1.0, 0.5):
            add(f"failrec_a{int(A*100)}_{int(round(f*100))}", {"type": "failrec", "L": 0.10, "A": A}, f)
    for r in (0.5, 0.3):
        add(f"decay_r{int(r*100)}_100", {"type": "decay", "D": 0.10, "r": r}, 1.0)
    add("nohigh20d10_100", {"type": "nohigh", "T": 20, "D": 0.10}, 1.0)
    add("tstop10_30_5_100", {"type": "tstop", "L": 0.10, "T": 30, "Lr": 0.05}, 1.0)
    for H in (0.30, 0.50, 1.00):
        add(f"touch{int(H*100)}_33", {"type": "touch", "H": H}, 1 / 3)
    add("touch50g10_100", {"type": "touch_gb", "H": 0.50, "g": 0.10}, 1.0)
    for base in ("gb10", "failrec_a5", "decay_r50"):
        for re_ in ("reclaim_same", "rotate_leader"):
            trig = {"gb10": {"type": "gb", "g": 0.10},
                    "failrec_a5": {"type": "failrec", "L": 0.10, "A": 0.05},
                    "decay_r50": {"type": "decay", "D": 0.10, "r": 0.5}}[base]
            add(f"{base}_{re_}", trig, 1.0, re_)
    return grid


def first_true(mask) -> int:
    w = np.flatnonzero(mask)
    return int(w[0]) if w.size else -1


def trigger_fire(trig: dict, b: dict, fi: int, fill_px: float) -> int:
    """First bar index (>= fi) at which the trigger fires, or -1. Causal: bar i only."""
    et = b["et"]
    h = b["high"]
    lo = b["low"]
    c = b["close"]
    v = b["volume"]
    ets = np.asarray(et[fi:], dtype=np.int64)
    hs = np.asarray(h[fi:], dtype=np.float64)
    cs = np.asarray(c[fi:], dtype=np.float64)
    ls = np.asarray(lo[fi:], dtype=np.float64)
    vs = np.asarray(v[fi:], dtype=np.float64)
    n = ets.size
    if n == 0:
        return -1
    t = trig["type"]
    if t == "gb":
        peak = np.maximum.accumulate(hs)
        j = first_true(cs <= (1 - trig["g"]) * peak)
        return fi + j if j >= 0 else -1
    if t == "dmgw":
        below = cs < fill_px * (1 - trig["L"])
        idx = np.flatnonzero(below)
        if idx.size == 0:
            return -1
        j = idx[0]
        while True:
            target = ets[j] + trig["w"]
            k = int(np.searchsorted(ets, target, side="left"))
            if k >= n:
                return -1
            if below[k]:
                return fi + k
            nxt = np.flatnonzero(below[k + 1:])
            if nxt.size == 0:
                return -1
            j = k + 1 + int(nxt[0])
    if t == "failrec":
        below = cs <= fill_px * (1 - trig["L"])
        j0 = first_true(below)
        if j0 < 0:
            return -1
        ep_low = ls[j0]
        bounced = False
        anchor = None
        for j in range(j0, n):
            if ls[j] < ep_low:
                ep_low = ls[j]
                if not bounced:
                    continue
            if not bounced:
                if hs[j] >= ep_low * (1 + trig["A"]):
                    bounced = True
                    anchor = ep_low
            else:
                if cs[j] < anchor:
                    return fi + j
        return -1
    if t == "decay":
        v5 = np.convolve(vs, np.ones(5) / 5, mode="valid")
        v20 = np.convolve(vs, np.ones(20) / 20, mode="valid")
        ratio = np.full(n, np.inf)
        m = min(v5.size, v20.size)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio[19:19 + (m - 19)] = np.where(v20[19:m] > 0, v5[19:m] / v20[19:m], np.inf)
        cond = (cs <= fill_px * (1 - trig["D"])) & (ratio < trig["r"])
        j = first_true(cond)
        return fi + j if j >= 0 else -1
    if t == "nohigh":
        last = 0
        ts = np.zeros(n, dtype=np.int64)
        peak = -1.0
        for j in range(n):
            if hs[j] >= peak:
                peak = hs[j]
                last = j
            ts[j] = j - last
        cond = (ts >= trig["T"]) & (cs <= fill_px * (1 - trig["D"]))
        j = first_true(cond)
        return fi + j if j >= 0 else -1
    if t == "tstop":
        start = -1
        for j in range(n):
            if start < 0:
                if cs[j] <= fill_px * (1 - trig["L"]):
                    start = ets[j]
            else:
                if hs[j] >= fill_px * (1 - trig["Lr"]):
                    start = -1
                elif cs[j] <= fill_px * (1 - trig["L"]) and ets[j] - start >= trig["T"]:
                    return fi + j
        return -1
    if t == "touch":
        j = first_true(hs >= fill_px * (1 + trig["H"]))
        return fi + j if j >= 0 else -1
    if t == "touch_gb":
        j0 = first_true(hs >= fill_px * (1 + trig["H"]))
        if j0 < 0:
            return -1
        peak = np.maximum.accumulate(hs[j0:])
        j = first_true(cs[j0:] <= (1 - trig["g"]) * peak)
        return fi + j0 + j if j >= 0 else -1
    raise ValueError(t)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()

    data_root = bps.resolve_data_root(args.data_root)
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    sends = {d: int(v["session_end"]) for d, v in cal.items()}
    days = list(args.days)
    if args.day:
        days.append(args.day)
    if args.dev_days:
        days = sorted(sends.keys())
    if args.limit:
        days = days[:args.limit]
    if not days:
        ap.print_help()
        return 2
    grid = build_grid()
    out_root = data_root / "harvest01" / "policies"
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "_grid.json").write_text(json.dumps(grid, indent=1))
    t0 = time.time()
    if args.workers <= 1:
        for d in days:
            print(process_day(d, data_root, int(sends.get(d, 959)), grid, args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), int(sends.get(d, 959)), grid, args.force) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


def _null_schema() -> dict:
    sch = {"day": pl.Utf8, "variant": pl.Utf8, "clock": pl.Int32, "rank": pl.Int32,
           "ticker": pl.Utf8, "policy": pl.Utf8, "fired": pl.Boolean,
           "sell_frac": pl.Float64, "reentry": pl.Utf8, "fire_et": pl.Int32,
           "sell_et": pl.Int32, "sell_px": pl.Float64, "mfe_after": pl.Float64,
           "monster_after": pl.Boolean, "rebuy_et": pl.Int32, "rebuy_px": pl.Float64,
           "cand_ticker": pl.Utf8}
    for E in sorted(set(ENDPOINTS + [959, 779])):
        sch[f"d100_{E}"] = pl.Float64
        sch[f"d150_{E}"] = pl.Float64
    return sch


def normalize(df: pl.DataFrame) -> pl.DataFrame:
    sch = _null_schema()
    for c, t in sch.items():
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=t).alias(c))
        else:
            df = df.with_columns(pl.col(c).cast(t))
    return df.select(list(sch.keys()))


def process_day(day: str, data_root: Path, se: int, grid: list[dict], force: bool) -> str:
    outd = data_root / "harvest01" / "policies"
    mp_ = outd / f"{day}.manifest.json"
    if not force and mp_.exists():
        try:
            if json.loads(mp_.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    fl_p = data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet"
    if not fl_p.exists():
        return f"{day}: no fills"
    fills = pl.read_parquet(fl_p)
    bars = load_bars(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    leaders = {}
    lead_p = data_root / "harvest01" / "base" / "leaders" / f"{day}.parquet"
    if lead_p.exists():
        ld = pl.read_parquet(lead_p)
        for c in ld["clock"].unique().to_list():
            sub = ld.filter((pl.col("clock") == c) & (pl.col("rank") == 1))
            if sub.height:
                leaders[int(c)] = sub.to_dicts()[0]
    t0 = time.time()
    rows = []
    for f in fills.filter(pl.col("status") == "filled").iter_rows(named=True):
        ends = sorted(e for e in ENDPOINTS if e < se and e > f["clock"]) + [se]
        b = bars.get(f["ticker"])
        if not b:
            continue
        ets = b["et"]
        o = b["open"]
        n = len(ets)
        fi = bisect.bisect_left(ets, f["fill_et"])
        if fi >= n:
            continue
        fpx = f["fill_px"]

        def hold_value(E, side):
            if E == se:
                j = bisect.bisect_left(ets, se, fi + 1)
            else:
                j = bisect.bisect_left(ets, max(E, ets[fi] + 1), fi + 1)
            return None if j >= n else (o[j] * (1 - side)) / (fpx * (1 + side))

        for pol in grid:
            fire_i = trigger_fire(pol["trigger"], b, fi, fpx)
            row = {"day": day, "variant": f["variant"], "clock": f["clock"], "rank": f["rank"],
                   "ticker": f["ticker"], "policy": pol["id"], "fired": fire_i >= 0,
                   "sell_frac": pol["sell"], "reentry": pol["reentry"]}
            sell = None
            if fire_i >= 0 and fire_i + 1 < n:
                xi = fire_i + 1
                sell = (xi, o[xi], ets[xi])
                row["fire_et"], row["sell_et"], row["sell_px"] = int(ets[fire_i]), int(ets[xi]), float(o[xi])
                post = b["high"][xi:]
                row["mfe_after"] = (max(post) / o[xi] - 1) if post else None
                row["monster_after"] = bool(row["mfe_after"] is not None and row["mfe_after"] >= 0.30)
            else:
                fire_i = -1
                row["fire_et"] = row["sell_et"] = row["sell_px"] = None
                row["mfe_after"] = row["monster_after"] = None
            # second leg: re-entry / rotation
            rebuy = None
            cand = None
            if sell is not None and pol["reentry"] != "none":
                si, spx, set_ = sell
                if pol["reentry"] == "reclaim_same":
                    j = si + 1
                    while j < n and b["close"][j] < spx:
                        j += 1
                    if j < n and j + 1 < n:
                        rebuy = (j + 1, o[j + 1], ets[j + 1])
                else:  # rotate_leader
                    clks = [c for c in leaders if c >= set_]
                    if clks:
                        L = min(clks)
                        lt = leaders[L]
                        if lt["ticker"] != f["ticker"]:
                            cb = bars.get(lt["ticker"])
                            if cb:
                                ci = bisect.bisect_left(cb["et"], L)
                                if ci < len(cb["et"]):
                                    cand = (lt["ticker"], cb["et"][ci], cb["open"][ci])
            row["rebuy_et"] = None if rebuy is None else int(rebuy[2])
            row["rebuy_px"] = None if rebuy is None else float(rebuy[1])
            row["cand_ticker"] = None if cand is None else cand[0]
            for E in ends:
                for side, tag in ((SIDE, "100"), (0.0075, "150")):
                    if tag == "150" and E != ends[-1] and E != 720:
                        continue
                    hE = hold_value(E, side)
                    if hE is None:
                        row[f"d{tag}_{E}"] = None
                        continue
                    if sell is None or sell[2] > E:
                        row[f"d{tag}_{E}"] = 0.0
                        continue
                    w_sell = (sell[1] * (1 - side)) / (fpx * (1 + side))
                    if pol["sell"] < 1.0:
                        # partial: remainder held to E
                        v = pol["sell"] * w_sell + (1 - pol["sell"]) * hE
                        row[f"d{tag}_{E}"] = v - hE
                        continue
                    if rebuy is None and cand is None:
                        row[f"d{tag}_{E}"] = w_sell - hE
                        continue
                    if rebuy is not None:
                        _, bpx, bet = rebuy
                        if bet > E:
                            row[f"d{tag}_{E}"] = w_sell - hE
                            continue
                        jE = (bisect.bisect_left(ets, se, fi + 1) if E == se
                              else bisect.bisect_left(ets, max(E, ets[fi] + 1), fi + 1))
                        if jE >= n:
                            row[f"d{tag}_{E}"] = None
                            continue
                        v = w_sell * ((o[jE] * (1 - side)) / (bpx * (1 + side)))
                        row[f"d{tag}_{E}"] = v - hE
                        continue
                    # rotation
                    ctk, cet, cpx = cand
                    cb = bars[ctk]
                    if cet > E:
                        row[f"d{tag}_{E}"] = w_sell - hE
                        continue
                    jE = (bisect.bisect_left(cb["et"], se) if E == se
                          else bisect.bisect_left(cb["et"], max(E, cet + 1)))
                    if jE >= len(cb["et"]):
                        row[f"d{tag}_{E}"] = None
                        continue
                    v = w_sell * ((cb["open"][jE] * (1 - side)) / (cpx * (1 + side)))
                    row[f"d{tag}_{E}"] = v - hE
            rows.append(row)
    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={"day": pl.Utf8, "policy": pl.Utf8})
    df = normalize(df)
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    mp_.write_text(json.dumps({"day": day, "status": "ok", "rows": int(df.height),
                               "policies": len(grid),
                               "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok rows={df.height} el={round(time.time()-t0,1)}s"


def load_bars(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close", "volume"])
    out = {}
    for tk, sub in d.group_by("ticker"):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        out[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close", "volume")}
    return out


def _worker(a):
    day, data_root, se, grid, force = a
    return process_day(day, Path(data_root), se, grid, force)


if __name__ == "__main__":
    raise SystemExit(main())
