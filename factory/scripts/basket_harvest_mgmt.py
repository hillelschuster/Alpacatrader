#!/usr/bin/env python3
"""HARVEST01 — causal member-management rules on the actual member paths.

For every filled member (day, variant, clock, rank) evaluate a small set of named,
past-only release rules on the member's own minute bars after its fill, execute at the
next bar open, and compare with holding to the same fixed endpoint. The question the
rules exist to answer: can a deteriorating member be released without repeatedly
selling a later giant?

Rules (defaults; frozen per run in mgmt_config.json before any policy PnL is viewed):
  hold              no-op benchmark
  gb10              close <= 0.90 * running high  (the known peak-relative ruler)
  dmg_wait          close < fill*0.90, still below 5 minutes later (R2-style reference)
  failrec_a3/a5/a8  damage > 10%, a bounce >= A%, then close < the episode low
  decay_v           damaged 10% AND 5-bar volume mean < 0.5 * prior 20-bar mean
  decay_nh          decay_v AND no new high in the last 20 bars
  tstop30           below -10% from fill for 30+ minutes without reclaiming -5%

Member outputs: day, variant, clock, rank, ticker, rule, fired, fire_et, exec_et, exec_px,
mfe_after (max high after execution vs exec_px), mfe_member (post-fill), and per-endpoint
deltas vs hold (per-dollar). Losers are kept.

Usage:
    .venv/bin/python factory/scripts/basket_harvest_mgmt.py --days 2021-02-01 --smoke
    .venv/bin/python factory/scripts/basket_harvest_mgmt.py --dev-days --workers 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

SIDE = {100: 0.005, 150: 0.0075}
DELTA_ENDS_OFFSET = {600, 630, 660, 720}  # absolute et endpoints for delta scoring
DEFAULTS = {
    "gb10": 0.10,
    "dmg": 0.10,
    "dmg_wait_min": 5,
    "failrec_L": 0.10,
    "failrec_A": [0.03, 0.05, 0.08],
    "decay_ratio": 0.5,
    "decay_w1": 5,
    "decay_w2": 20,
    "decay_no_high_bars": 20,
    "tstop_min": 30,
    "tstop_reclaim": 0.05,
}
RULE_NAMES = ["hold", "gb10", "dmg_wait", "failrec_a3", "failrec_a5", "failrec_a8",
              "decay_v", "decay_nh", "tstop30"]


def load_bars_cols(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close", "volume"])
    out = {}
    for tk, sub in d.group_by("ticker"):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        out[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close", "volume")}
    return out


def eval_rules(bars: dict, fill_idx: int, fill_px: float, session_end: int, cfg: dict):
    """Run every rule on one member path. Returns {rule: dict(fired, fire_i)}."""
    ets = bars["et"]
    o = bars["open"]
    h = bars["high"]
    lo = bars["low"]
    c = bars["close"]
    v = bars["volume"]
    n = len(ets)
    res = {name: {"fired": False, "fire_i": None} for name in RULE_NAMES}
    gb = cfg["gb10"]
    dmg = cfg["dmg"]
    peak = -1.0
    peak_i = fill_idx
    breach_et = None
    fr = {A: {"armed": False, "ep_low": None, "bounced": False, "anchor": None}
          for A in cfg["failrec_A"]}
    tst_start_et = None
    for i in range(fill_idx, n):
        px_h, px_l, px_c = h[i], lo[i], c[i]
        if px_h >= peak:
            peak = px_h
            peak_i = i
        # gb10: close <= (1-gb) * running high (running high includes this bar)
        if not res["gb10"]["fired"] and px_c <= peak * (1 - gb):
            res["gb10"]["fired"] = True
            res["gb10"]["fire_i"] = i
        # dmg_wait: first close < fill*(1-dmg); if still below w minutes later -> fire (re-arms)
        if not res["dmg_wait"]["fired"]:
            if breach_et is None and px_c < fill_px * (1 - dmg):
                breach_et = ets[i]
            elif breach_et is not None and ets[i] >= breach_et + cfg["dmg_wait_min"]:
                if px_c < fill_px * (1 - dmg):
                    res["dmg_wait"]["fired"] = True
                    res["dmg_wait"]["fire_i"] = i
                else:
                    breach_et = None
        # failrec: damage -> bounce >= A% off the episode low -> close below episode low
        for A, st in fr.items():
            name = f"failrec_a{int(A*100)}"
            if res[name]["fired"]:
                continue
            if not st["armed"]:
                if px_c <= fill_px * (1 - cfg["failrec_L"]):
                    st["armed"] = True
                    st["ep_low"] = px_l
                    st["bounced"] = False
                continue
            st["ep_low"] = min(st["ep_low"], px_l)
            if not st["bounced"]:
                if px_h >= st["ep_low"] * (1 + A):
                    st["bounced"] = True
                    st["anchor"] = st["ep_low"]
            elif px_c < st["anchor"]:
                res[name]["fired"] = True
                res[name]["fire_i"] = i
        # decay_v: damaged AND recent volume drying up
        if not res["decay_v"]["fired"] and i >= fill_idx + cfg["decay_w1"] + cfg["decay_w2"] - 1:
            if px_c <= fill_px * (1 - dmg):
                recent = sum(v[i - cfg["decay_w1"] + 1:i + 1]) / cfg["decay_w1"]
                prior = sum(v[i - cfg["decay_w1"] - cfg["decay_w2"] + 1:i - cfg["decay_w1"] + 1]) / cfg["decay_w2"]
                if prior > 0 and recent < cfg["decay_ratio"] * prior:
                    res["decay_v"]["fired"] = True
                    res["decay_v"]["fire_i"] = i
        # decay_nh: same damage/volume condition while no new high for N bars
        if not res["decay_nh"]["fired"] and i >= fill_idx + cfg["decay_w1"] + cfg["decay_w2"] - 1:
            if (px_c <= fill_px * (1 - dmg) and (i - peak_i) >= cfg["decay_no_high_bars"]):
                recent = sum(v[i - cfg["decay_w1"] + 1:i + 1]) / cfg["decay_w1"]
                prior = sum(v[i - cfg["decay_w1"] - cfg["decay_w2"] + 1:i - cfg["decay_w1"] + 1]) / cfg["decay_w2"]
                if prior > 0 and recent < cfg["decay_ratio"] * prior:
                    res["decay_nh"]["fired"] = True
                    res["decay_nh"]["fire_i"] = i
        # tstop30: below -10% for >= tstop_min without a +5% reclaim print
        if not res["tstop30"]["fired"]:
            if tst_start_et is None:
                if px_c <= fill_px * (1 - dmg):
                    tst_start_et = ets[i]
            else:
                if px_h >= fill_px * (1 - cfg["tstop_reclaim"]):
                    tst_start_et = None  # reclaimed; re-arm
                elif px_c <= fill_px * (1 - dmg) and ets[i] - tst_start_et >= cfg["tstop_min"]:
                    res["tstop30"]["fired"] = True
                    res["tstop30"]["fire_i"] = i
    return res, ets, o, fill_px


def process_day(day: str, data_root: Path, se: int, cfg: dict, force: bool) -> str:
    outd = data_root / "harvest01" / "mgmt"
    outd.mkdir(parents=True, exist_ok=True)
    mp = outd / f"{day}.manifest.json"
    if not force and mp.exists():
        try:
            if json.loads(mp.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    fl_p = data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet"
    if not fl_p.exists():
        return f"{day}: no fills"
    fills = pl.read_parquet(fl_p)
    bars = load_bars_cols(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    t0 = time.time()
    rows = []
    ends = sorted(e for e in DELTA_ENDS_OFFSET if e < se) + [se]
    for f in fills.filter(pl.col("status") == "filled").iter_rows(named=True):
        b = bars.get(f["ticker"])
        if not b:
            continue
        ets = b["et"]
        import bisect
        fi = bisect.bisect_left(ets, f["fill_et"])
        if fi >= len(ets):
            continue
        fpx = f["fill_px"]
        res, ets2, o2, fpx2 = eval_rules(b, fi, fpx, se, cfg)
        hold = {}
        for E in ends:
            key = f"r{E}_100"
            hold[E] = f.get(key)
        for rule, r in res.items():
            row = {"day": day, "variant": f["variant"], "clock": f["clock"], "rank": f["rank"],
                   "ticker": f["ticker"], "rule": rule, "fired": bool(r["fired"]),
                   "session_end": se}
            if r["fired"]:
                f_i = r["fire_i"]
                row["fire_et"] = int(ets2[f_i])
                xi = f_i + 1
                if xi < len(ets2):
                    row["exec_et"] = int(ets2[xi])
                    row["exec_px"] = float(o2[xi])
                    post = b["high"][xi:]
                    row["mfe_after"] = (max(post) / row["exec_px"] - 1) if post else None
                    row["monster_after"] = bool(row["mfe_after"] is not None and row["mfe_after"] >= 0.30)
                    w_r_100 = (row["exec_px"] * (1 - SIDE[100])) / (fpx * (1 + SIDE[100]))
                    w_r_150 = (row["exec_px"] * (1 - SIDE[150])) / (fpx * (1 + SIDE[150]))
                else:
                    row["exec_et"] = None
                    row["exec_px"] = None
                    row["mfe_after"] = None
                    row["monster_after"] = None
                    w_r_100 = w_r_150 = None
            else:
                row["fire_et"] = None
                row["exec_et"] = None
                row["exec_px"] = None
                row["mfe_after"] = None
                row["monster_after"] = None
                w_r_100 = w_r_150 = None
            for E in ends:
                h100 = hold.get(E)
                if h100 is None:
                    row[f"delta100_{E}"] = None
                    row[f"delta150_{E}"] = None
                    continue
                w_hold_100 = 1 + h100
                if w_r_100 is None or (row.get("exec_et") or 0) > E:
                    w_use = w_hold_100
                else:
                    w_use = w_r_100
                row[f"delta100_{E}"] = w_use - w_hold_100
                # 150 bps: recompute proportionally from the same prices when available
                if w_r_150 is None or (row.get("exec_et") or 0) > E:
                    row[f"delta150_{E}"] = None
                else:
                    h150 = f.get(f"r{E}_150")
                    row[f"delta150_{E}"] = None if h150 is None else w_r_150 - (1 + h150)
            rows.append(row)
    df = pl.DataFrame(rows) if rows else pl.DataFrame(schema={"day": pl.Utf8, "rule": pl.Utf8})
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    mp.write_text(json.dumps({"day": day, "status": "ok", "rows": int(df.height),
                              "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok rows={df.height} el={round(time.time()-t0,1)}s"


def _worker(a):
    day, data_root, se, cfg, force = a
    return process_day(day, Path(data_root), se, cfg, force)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()

    data_root = bps.resolve_data_root(args.data_root)
    cfg = dict(DEFAULTS)
    if args.config:
        cfg.update(json.loads(Path(args.config).read_text()))
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
    t0 = time.time()
    if args.workers <= 1:
        for d in days:
            print(process_day(d, data_root, int(sends.get(d, 959)), cfg, args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), int(sends.get(d, 959)), cfg, args.force) for d in days]
        with mp.Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
