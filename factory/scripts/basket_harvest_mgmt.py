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
DELTA_ENDS_OFFSET = {600, 630, 660, 690, 720}  # absolute et endpoints for delta scoring
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
              "decay_v", "decay_nh", "tstop30", "gb10_half", "failrec_a5_half",
              "reentry_gb10", "replace_gb10", "gb10_wait5_fix", "dmg_wait_fix"]


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
    # combined rules: standing trigger -> 5-minute wait -> if a -20% flush develops during
    # the wait, defer the sale to the first +3% bounce off the panic low (cap flush+30);
    # otherwise sell at the end of the wait.
    pend = {"gb10_wait5_fix": None, "dmg_wait_fix": None}
    fl_state = {"gb10_wait5_fix": None, "dmg_wait_fix": None}  # (flush_et, flush_low)
    for i in range(fill_idx, n):
        px_h, px_l, px_c = h[i], lo[i], c[i]
        if px_h >= peak:
            peak = px_h
            peak_i = i
        # gb10: close <= (1-gb) * running high (running high includes this bar)
        if not res["gb10"]["fired"] and px_c <= peak * (1 - gb):
            res["gb10"]["fired"] = True
            res["gb10"]["fire_i"] = i
        # combined: gb10 trigger -> wait -> flushfix
        if not res["gb10_wait5_fix"]["fired"]:
            st = fl_state["gb10_wait5_fix"]
            if pend["gb10_wait5_fix"] is None:
                if px_c <= peak * (1 - gb):
                    pend["gb10_wait5_fix"] = ets[i]
            elif st is not None:
                f_et, f_low = st
                if px_h >= f_low * 1.03 or ets[i] >= f_et + 30:
                    res["gb10_wait5_fix"]["fired"] = True
                    res["gb10_wait5_fix"]["fire_i"] = i
            else:
                if px_c <= peak * 0.80:
                    fl_state["gb10_wait5_fix"] = (ets[i], min(lo[i:min(i + 3, n)]))
                elif ets[i] >= pend["gb10_wait5_fix"] + 5:
                    res["gb10_wait5_fix"]["fired"] = True
                    res["gb10_wait5_fix"]["fire_i"] = i
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
        # combined: dmg_wait trigger -> wait -> flushfix
        if not res["dmg_wait_fix"]["fired"]:
            st = fl_state["dmg_wait_fix"]
            if pend["dmg_wait_fix"] is None:
                if breach_et is not None and ets[i] >= breach_et + cfg["dmg_wait_min"] \
                        and px_c < fill_px * (1 - dmg):
                    pend["dmg_wait_fix"] = ets[i]
                    breach_et = None  # consumed by the combined rule
            elif st is not None:
                f_et, f_low = st
                if px_h >= f_low * 1.03 or ets[i] >= f_et + 30:
                    res["dmg_wait_fix"]["fired"] = True
                    res["dmg_wait_fix"]["fire_i"] = i
            else:
                if px_c <= peak * 0.80:
                    fl_state["dmg_wait_fix"] = (ets[i], min(lo[i:min(i + 3, n)]))
                elif ets[i] >= pend["dmg_wait_fix"] + 5:
                    res["dmg_wait_fix"]["fired"] = True
                    res["dmg_wait_fix"]["fire_i"] = i
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


def _null_schema() -> dict:
    sch = {"day": pl.Utf8, "variant": pl.Utf8, "clock": pl.Int32, "rank": pl.Int32,
           "ticker": pl.Utf8, "rule": pl.Utf8, "fired": pl.Boolean, "session_end": pl.Int32,
           "fire_et": pl.Int32, "exec_et": pl.Int32, "exec_px": pl.Float64,
           "mfe_after": pl.Float64, "monster_after": pl.Boolean,
           "rebuy_et": pl.Int32, "rebuy_px": pl.Float64,
           "cand_ticker": pl.Utf8, "cand_fill_et": pl.Int32, "cand_fill_px": pl.Float64}
    for E in sorted(DELTA_ENDS_OFFSET | {959, 779}):
        sch[f"delta100_{E}"] = pl.Float64
        sch[f"delta150_{E}"] = pl.Float64
    return sch


def normalize(df: pl.DataFrame) -> pl.DataFrame:
    sch = _null_schema()
    for c, t in sch.items():
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=t).alias(c))
        else:
            df = df.with_columns(pl.col(c).cast(t))
    return df.select(list(sch.keys()))


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
    import bisect
    for f in fills.filter(pl.col("status") == "filled").iter_rows(named=True):
        ends = sorted(e for e in DELTA_ENDS_OFFSET if e < se and e > f["clock"]) + [se]
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
        res, ets2, o2, _ = eval_rules(b, fi, fpx, se, cfg)
        # endpoint exit bars (hold convention; must match the sim exactly)
        exitb = {}
        for E in ends:
            if E == se:
                j = bisect.bisect_left(ets, se, fi + 1)
            else:
                j = bisect.bisect_left(ets, max(E, ets[fi] + 1), fi + 1)
            exitb[E] = j if j < n else -1

        def hold_value(E, side):
            j = exitb[E]
            return None if j < 0 else (o[j] * (1 - side)) / (fpx * (1 + side))

        exec_of = {}
        for rule, r in res.items():
            if r["fired"] and r["fire_i"] is not None and r["fire_i"] + 1 < n:
                xi = r["fire_i"] + 1
                exec_of[rule] = (xi, o[xi], ets[xi])
        rebuy = None
        if "gb10" in exec_of:
            xi, xpx, _ = exec_of["gb10"]
            j = xi + 1
            c = b["close"]
            while j < n and c[j] < xpx:
                j += 1
            if j < n and j + 1 < n:
                rebuy = (j + 1, o[j + 1], ets[j + 1])

        def value_sell(rule, E, side):
            if rule not in exec_of:
                return hold_value(E, side)
            _, xpx, xet = exec_of[rule]
            if xet > E:
                return hold_value(E, side)
            return (xpx * (1 - side)) / (fpx * (1 + side))

        def value_half(rule, E, side):
            if rule not in exec_of:
                return hold_value(E, side)
            hv = hold_value(E, side)
            if hv is None:
                return None
            _, xpx, xet = exec_of[rule]
            if xet > E:
                return hv
            return 0.5 * ((xpx * (1 - side)) / (fpx * (1 + side))) + 0.5 * hv

        def value_reentry(E, side):
            if "gb10" not in exec_of:
                return hold_value(E, side)
            _, xpx, xet = exec_of["gb10"]
            hv = hold_value(E, side)
            if hv is None:
                return None
            if xet > E:
                return hv
            w_sell = (xpx * (1 - side)) / (fpx * (1 + side))
            if rebuy is None:
                return w_sell
            _, bpx, bet = rebuy
            if bet > E:
                return w_sell
            jE = exitb[E]
            if jE < 0:
                return None
            return w_sell * ((o[jE] * (1 - side)) / (bpx * (1 + side)))

        # replacement candidate: current rank-1 leader at the first 5-min clock >= sell exec
        cand = None
        if "gb10" in exec_of and leaders:
            _, _, xet = exec_of["gb10"]
            clks = [c for c in leaders if c >= xet]
            if clks:
                L = min(clks)
                lt = leaders[L]
                if lt["ticker"] != f["ticker"]:
                    cb = bars.get(lt["ticker"])
                    if cb:
                        ci = bisect.bisect_left(cb["et"], L)
                        if ci < len(cb["et"]):
                            cand = (lt["ticker"], cb["et"][ci], cb["open"][ci], L)
        if cand is not None:
            ctk, cet, cpx, L = cand
            cb = bars[ctk]
            cexitb = {}
            for E in ends:
                if E == se:
                    j = bisect.bisect_left(cb["et"], se, bisect.bisect_left(cb["et"], cet) + 1)
                else:
                    j = bisect.bisect_left(cb["et"], max(E, cet + 1), bisect.bisect_left(cb["et"], cet) + 1)
                cexitb[E] = j if j < len(cb["et"]) else -1

        def value_replace(E, side):
            if "gb10" not in exec_of:
                return hold_value(E, side)
            _, xpx, xet = exec_of["gb10"]
            hv = hold_value(E, side)
            if hv is None:
                return None
            if xet > E:
                return hv
            w_sell = (xpx * (1 - side)) / (fpx * (1 + side))
            if cand is None:
                return w_sell
            ctk, cet, cpx, _ = cand
            if cet > E:
                return w_sell
            jE = cexitb[E]
            if jE < 0:
                return None
            return w_sell * ((bars[ctk]["open"][jE] * (1 - side)) / (cpx * (1 + side)))

        for rule in RULE_NAMES:
            base_rule = "gb10" if rule in ("gb10_half", "reentry_gb10", "replace_gb10") else (
                "failrec_a5" if rule == "failrec_a5_half" else rule)
            r = res.get(base_rule, {"fired": False, "fire_i": None})
            row = {"day": day, "variant": f["variant"], "clock": f["clock"], "rank": f["rank"],
                   "ticker": f["ticker"], "rule": rule, "fired": bool(r["fired"]),
                   "session_end": se}
            if base_rule in exec_of:
                xi, xpx, xet = exec_of[base_rule]
                row["fire_et"] = int(ets2[xi - 1]) if xi - 1 >= 0 else None
                row["exec_et"] = int(xet)
                row["exec_px"] = float(xpx)
                post = b["high"][xi:]
                row["mfe_after"] = (max(post) / xpx - 1) if post else None
                row["monster_after"] = bool(row["mfe_after"] is not None and row["mfe_after"] >= 0.30)
            else:
                row["fire_et"] = row["exec_et"] = row["exec_px"] = None
                row["mfe_after"] = row["monster_after"] = None
            if rule == "reentry_gb10" and rebuy is not None:
                row["rebuy_et"], row["rebuy_px"] = int(rebuy[2]), float(rebuy[1])
            else:
                row["rebuy_et"], row["rebuy_px"] = None, None
            if rule == "replace_gb10" and cand is not None:
                row["cand_ticker"], row["cand_fill_et"], row["cand_fill_px"] = (
                    cand[0], int(cand[1]), float(cand[2]))
            else:
                row["cand_ticker"], row["cand_fill_et"], row["cand_fill_px"] = None, None, None
            for E in ends:
                hv = hold_value(E, 0.005)
                if rule in ("gb10", "dmg_wait", "failrec_a3", "failrec_a5", "failrec_a8",
                            "decay_v", "decay_nh", "tstop30", "hold",
                            "gb10_wait5_fix", "dmg_wait_fix"):
                    v = hv if rule == "hold" else value_sell(rule, E, 0.005)
                    v150 = (hold_value(E, 0.0075) if rule == "hold"
                            else value_sell(rule, E, 0.0075))
                elif rule in ("gb10_half", "failrec_a5_half"):
                    v = value_half(base_rule, E, 0.005)
                    v150 = value_half(base_rule, E, 0.0075)
                elif rule == "replace_gb10":
                    v = value_replace(E, 0.005)
                    v150 = value_replace(E, 0.0075)
                else:  # reentry_gb10
                    v = value_reentry(E, 0.005)
                    v150 = value_reentry(E, 0.0075)
                hv150 = hold_value(E, 0.0075)
                row[f"delta100_{E}"] = None if (v is None or hv is None) else v - hv
                row[f"delta150_{E}"] = None if (v150 is None or hv150 is None) else v150 - hv150
            rows.append(row)
    df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame(schema={"day": pl.Utf8, "rule": pl.Utf8})
    df = normalize(df)
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
    out_root = data_root / "harvest01" / "mgmt"
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "_config.json").write_text(json.dumps(cfg, indent=1))
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
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
