#!/usr/bin/env python3
"""HARVEST01 — basket-level handling: release a dead member and move the money.

For every (day, primary variant, clock, N) simulate equal-dollar entry into the top-N
selection, then apply one policy from the frozen grid:

  trigger x redeploy mode, where trigger is a causal release on the member's own bars
  (gb10 / failrec_a5 / decay_r50 / nohigh20d10), and mode decides where the released
  cash goes:
     cash             released money stays cash
     equal_survivors  buy the still-held members equally at that bar's open
     best_survivor    buy the held member with the best current gain from fill
     market_leader    buy the current rank-1 leader (leaders lane) at its next
                      executable bar; skipped if it is the sold member itself

All buys/sells execute at bar opens; decided on completed bars only; sells fire like the
member engine (decision bar -> next bar open). Endpoint exits follow the sim convention
(first bar with et >= max(E, last_buy_et+1); E==session_end requires a bar at/after
session_end, else UNKNOWN). A "base_hold" row is the unmanaged baseline of the same cell.

Outputs per day: <data>/harvest01/basket/<day>.parquet
Usage:
    .venv/bin/python factory/scripts/basket_harvest_basket.py --days 2021-02-01
    .venv/bin/python factory/scripts/basket_harvest_basket.py --dev-days --workers 3
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402
from basket_harvest_policies import load_bars, trigger_fire  # noqa: E402

ENDPOINTS = [600, 630, 660, 690, 720]
SIDE = 0.005
TRIGGERS = {
    "gb10": {"type": "gb", "g": 0.10},
    "failrec_a5": {"type": "failrec", "L": 0.10, "A": 0.05},
    "decay_r50": {"type": "decay", "D": 0.10, "r": 0.5},
    "nohigh20d10": {"type": "nohigh", "T": 20, "D": 0.10},
}
MODES = ["cash", "equal_survivors", "best_survivor", "market_leader"]
GAP_BLOCK = 5


def simulate(members, bars, leaders, se, clock):
    """members: list of dicts (rank, ticker, status, fill_et, fill_px). Returns dict of
    policy -> (fires, cash_end, value_at(E) dict)."""
    # initial positions: ticker -> list of [buy_et, buy_px, shares]
    init = {}
    base_blocked = 0.0
    for m in members:
        if m["status"] != "filled":
            base_blocked += 1.0 / len(members)
        else:
            sh = (1.0 / len(members)) / (m["fill_px"] * (1 + SIDE))
            init.setdefault(m["ticker"], []).append([m["fill_et"], m["fill_px"], sh])
    all_ends = sorted({e for e in ENDPOINTS if e > clock and e < se} | {se})

    def _state_of(E, log, side=SIDE, init_state=None, cash0=None):
        """(value, cash) at endpoint E: replay only events with et <= E, then mark open
        positions at their first bar open >= max(E, last_buy+1). No post-endpoint pricing."""
        cash = base_blocked if cash0 is None else cash0
        positions = {t: [list(p) for p in ps]
                     for t, ps in (init if init_state is None else init_state).items()}
        for ev in log:
            if ev[1] > E:
                break
            kind, et, tk, px, shares = ev
            if kind == "sell":
                ps = positions.get(tk) or []
                cash += sum(p[2] for p in ps) * px * (1 - side)
                positions[tk] = []
            else:
                cash -= shares * px * (1 + side)
                positions.setdefault(tk, []).append([et, px, shares])
        tot = cash
        for t, ps in positions.items():
            if not ps:
                continue
            b = bars.get(t)
            if not b:
                return None, cash
            last_buy = max(p[0] for p in ps)
            j = bisect.bisect_left(b["et"], se if E == se else max(E, last_buy + 1))
            if j >= len(b["et"]):
                return None, cash
            tot += sum(p[2] for p in ps) * b["open"][j] * (1 - side)
        return tot - 1, cash

    def value_of(E, log):
        return _state_of(E, log)[0]

    def member_px(tk, et):
        b = bars.get(tk)
        if not b:
            return None
        j = bisect.bisect_left(b["et"], et)
        return None if j >= len(b["et"]) else (b["open"][j], b["et"][j])

    # member fires: decision bar -> sell at next bar open
    fires = {}
    for m in members:
        if m["status"] != "filled":
            continue
        b = bars.get(m["ticker"])
        if not b:
            continue
        fi = bisect.bisect_left(b["et"], m["fill_et"])
        if fi >= len(b["et"]):
            continue
        for tname, trig in TRIGGERS.items():
            f_i = trigger_fire(trig, b, fi, m["fill_px"])
            if f_i >= 0 and f_i + 1 < len(b["et"]):
                fires.setdefault(tname, []).append((int(b["et"][f_i + 1]), m["ticker"], float(b["open"][f_i + 1])))

    def run(policy):
        """Execute the policy over the whole session; return (event_log, n_fires)."""
        trig, mode = policy
        ev = sorted(fires.get(trig, []))
        positions = {t: [list(p) for p in ps] for t, ps in init.items()}
        log = []
        cash_s = 0.0
        n_fires = 0
        for (xet, tk, xpx) in ev:
            ps = positions.get(tk) or []
            if not ps:
                continue
            proceeds = sum(p[2] * xpx * (1 - SIDE) for p in ps)
            positions[tk] = []
            log.append(("sell", xet, tk, xpx, 0.0))
            cash_s += proceeds
            n_fires += 1
            # redeploy
            if mode == "cash" or cash_s <= 0:
                continue
            held = [t for t, q in positions.items() if q]
            if mode == "market_leader":
                clks = [c for c in leaders if c >= xet]
                target = None
                if clks:
                    lt = leaders[min(clks)]
                    if lt["ticker"] != tk:
                        target = lt["ticker"]
                if target and bars.get(target):
                    bp = member_px(target, max(min(clks), xet))
                    if bp:
                        px, bet = bp
                        sh = cash_s / (px * (1 + SIDE))
                        positions.setdefault(target, []).append([bet, px, sh])
                        log.append(("buy", bet, target, px, sh))
                        cash_s = 0.0
                continue
            if not held:
                continue
            if mode == "best_survivor":
                best, bestg = None, None
                for t in held:
                    b = bars.get(t)
                    if not b:
                        continue
                    j = bisect.bisect_left(b["et"], xet - 1)
                    if j >= len(b["et"]):
                        j = len(b["et"]) - 1
                    g = b["close"][j] / positions[t][0][1] - 1
                    if best is None or g > bestg:
                        best, bestg = t, g
                targets = [best] if best else []
            else:
                targets = held
            if not targets:
                continue
            share_cash = cash_s / len(targets)
            for t in targets:
                b = bars.get(t)
                if not b:
                    continue
                j = bisect.bisect_left(b["et"], max(xet, positions[t][-1][0] + 1))
                if j >= len(b["et"]):
                    continue
                px, bet = b["open"][j], b["et"][j]
                sh = share_cash / (px * (1 + SIDE))
                positions[t].append([bet, px, sh])
                log.append(("buy", bet, t, px, sh))
                cash_s -= share_cash
        return log, n_fires

    outs = {"base_hold": (0, 0.0, {E: value_of(E, []) for E in all_ends})}
    for trig in TRIGGERS:
        for mode in MODES:
            log, nf = run((trig, mode))
            log = sorted(log, key=lambda e: (e[1], 0 if e[0] == "sell" else 1))
            outs[f"{trig}|{mode}"] = (nf, _state_of(se, log)[1],
                                      {E: value_of(E, log) for E in all_ends})

    # ---- scale-in / reserve family: deploy only f0 at entry, hold the rest back ----
    f0 = 2 / 3
    pos_r = {}
    n_filled = sum(1 for m in members if m["status"] == "filled")
    for m in members:
        if m["status"] != "filled":
            pos_r.setdefault(m["ticker"], [])
        else:
            sh = (f0 / len(members)) / (m["fill_px"] * (1 + SIDE))
            pos_r[m["ticker"]] = [[m["fill_et"], m["fill_px"], sh]]
    reserve = (1 - f0) * n_filled / len(members)

    def first_trigger_bar(H):
        best = None
        for m in members:
            if m["status"] != "filled":
                continue
            b = bars.get(m["ticker"])
            if not b:
                continue
            fi = bisect.bisect_left(b["et"], m["fill_et"])
            for j in range(fi, len(b["et"])):
                if b["high"][j] >= m["fill_px"] * (1 + H):
                    if best is None or (b["et"][j], m["rank"]) < (best[0], best[3]):
                        best = (int(b["et"][j]), m["ticker"], float(m["fill_px"]), m["rank"])
                    break
        return best

    def deploy_single(H):
        tg = first_trigger_bar(H)
        log = []
        if tg is not None:
            _, tk, _, _ = tg
            b = bars.get(tk)
            j = bisect.bisect_left(b["et"], tg[0] + 1)
            if j < len(b["et"]):
                px = b["open"][j]
                sh = reserve / (px * (1 + SIDE))
                log.append(("buy", int(b["et"][j]), tk, float(px), sh))
        return log

    def deploy_split630():
        log = []
        targets = []
        for m in members:
            if m["status"] != "filled":
                continue
            b = bars.get(m["ticker"])
            if not b:
                continue
            fi = bisect.bisect_left(b["et"], m["fill_et"])
            hi = 0.0
            for j in range(fi, len(b["et"])):
                if b["et"][j] >= 630:
                    break
                hi = max(hi, b["high"][j])
            if hi >= m["fill_px"] * 1.20:
                targets.append(m["ticker"])
        if targets:
            share = reserve / len(targets)
            for tk in targets:
                b = bars.get(tk)
                j = bisect.bisect_left(b["et"], 631)
                if j < len(b["et"]):
                    px = b["open"][j]
                    sh = share / (px * (1 + SIDE))
                    log.append(("buy", int(b["et"][j]), tk, float(px), sh))
        return log

    def deploy_dip(D):
        """Deploy the reserve into the FIRST member that dips >= D from its running high
        after its fill (buying weakness), then hold to the endpoint."""
        best = None
        for m in members:
            if m["status"] != "filled":
                continue
            b = bars.get(m["ticker"])
            if not b:
                continue
            fi = bisect.bisect_left(b["et"], m["fill_et"])
            peak = -1.0
            for j in range(fi, len(b["et"])):
                peak = max(peak, b["high"][j])
                if b["low"][j] <= peak * (1 - D):
                    if best is None or (b["et"][j], m["rank"]) < (best[0], best[3]):
                        best = (int(b["et"][j]), m["ticker"], float(m["fill_px"]), m["rank"])
                    break
        log = []
        if best is not None:
            _, tk, _, _ = best
            b = bars.get(tk)
            j = bisect.bisect_left(b["et"], best[0] + 1)
            if j < len(b["et"]):
                px = b["open"][j]
                sh = reserve / (px * (1 + SIDE))
                log.append(("buy", int(b["et"][j]), tk, float(px), sh))
        return log

    for name, fn in (("scale|reserve_cash", None), ("scale|single20", lambda: deploy_single(0.20)),
                     ("scale|single50", lambda: deploy_single(0.50)), ("scale|split630", deploy_split630),
                     ("scale|dip10", lambda: deploy_dip(0.10)), ("scale|dip15", lambda: deploy_dip(0.15))):
        log = [] if fn is None else fn()
        log = sorted(log, key=lambda e: (e[1], 1))
        cash0 = base_blocked + reserve
        vals = {E: _state_of(E, log, init_state=pos_r, cash0=cash0)[0] for E in all_ends}
        outs[name] = (0, _state_of(se, log, init_state=pos_r, cash0=cash0)[1], vals)
    return outs


def process_day(day: str, data_root: Path, se: int, force: bool) -> str:
    outd = data_root / "harvest01" / "basket"
    outd.mkdir(parents=True, exist_ok=True)
    mp_ = outd / f"{day}.manifest.json"
    if not force and mp_.exists():
        try:
            if json.loads(mp_.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    fl_p = data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet"
    sel_p = data_root / "harvest01" / "base" / "selected" / f"{day}.parquet"
    if not (fl_p.exists() and sel_p.exists()):
        return f"{day}: missing inputs"
    fills = pl.read_parquet(fl_p).filter(pl.col("variant") == "primary")
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
    for clock in sorted(fills["clock"].unique().to_list()):
        fv = fills.filter(pl.col("clock") == clock).sort("rank")
        allm = fv.to_dicts()
        for N in (1, 2, 3, 4):
            members = allm[:N]
            outs = simulate(members, bars, leaders, se, int(clock))
            for pol, (nf, cash_s, vals) in outs.items():
                for E, v in vals.items():
                    rows.append({"day": day, "clock": int(clock), "N": N, "policy": pol,
                                 "end": int(E), "ret": v, "n_fires": nf,
                                 "cash_end": cash_s})
    df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame(schema={"day": pl.Utf8, "policy": pl.Utf8})
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    mp_.write_text(json.dumps({"day": day, "status": "ok", "rows": int(df.height),
                               "elapsed_s": round(time.time() - t0, 1)}, indent=1))
    return f"{day}: ok rows={df.height} el={round(time.time()-t0,1)}s"


def _worker(a):
    day, data_root, se, force = a
    return process_day(day, Path(data_root), se, force)


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
    t0 = time.time()
    if args.workers <= 1:
        for d in days:
            print(process_day(d, data_root, int(sends.get(d, 959)), args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), int(sends.get(d, 959)), args.force) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
