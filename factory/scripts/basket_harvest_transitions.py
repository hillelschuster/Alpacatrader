#!/usr/bin/env python3
"""HARVEST01 — transition anatomy: what actually happens after a member enters a state.

For a declared event predicate (first crossing into a state), extract one episode per
member-day: the event minute, the state at entry, and the forward path from the next
executable open — forward V_h (from the minute panel), time to the next running high,
time back to fill, forward MFE/MAE, and the close vs fill. Every episode is reported
against a matched control set (members in the same clock bucket and tenure bucket that
did NOT enter the state), because a state's raw forward mean is a weighting/selection
artefact until the matched comparison says otherwise (advisor guardrail 1).

Events (--event):
    deep20 : first minute with dist_high <= -0.20
    dmg10  : first minute with dist_high <= -0.10
    stall10: first minute with bars_since_high >= 10 (after tenure >= 5)
    nohigh15: first minute with nh15 == 0 and tenure >= 15
    rankworse10: first minute with drank5 >= 10

Outputs: harvest01/report/transitions_<event>.parquet + transitions.md
Usage:
    .venv/bin/python factory/scripts/basket_harvest_transitions.py --event deep20
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

EVENTS = {
    "deep20": lambda d: (pl.col("dist_high") <= -0.20),
    "dmg10": lambda d: (pl.col("dist_high") <= -0.10),
    "stall10": lambda d: (pl.col("bars_since_high") >= 10) & (pl.col("tenure") >= 5),
    "nohigh15": lambda d: (pl.col("nh15") == 0) & (pl.col("tenure") >= 15),
    "rankworse10": lambda d: (pl.col("drank5") >= 10),
    "basket5": lambda d: (pl.col("basket_ret") <= -0.05),
    "rank101": lambda d: (pl.col("rank_known") >= 101),
}
HORIZONS = (5, 15, 30, 60, 120)


def load_bars(path: Path) -> dict:
    if not path.exists():
        return {}
    d = pl.read_parquet(path, columns=["ticker", "et", "open", "high", "low", "close"])
    out = {}
    for tk, sub in d.group_by("ticker"):
        t = tk[0] if isinstance(tk, tuple) else tk
        s = sub.sort("et")
        out[t] = {c: s[c].to_list() for c in ("et", "open", "high", "low", "close")}
    return out


def process_day(day: str, data_root: Path, event: str, se: int) -> list[dict]:
    fp = data_root / "harvest01" / "minute" / f"{day}.parquet"
    if not fp.exists():
        return []
    d = pl.read_parquet(fp, columns=["variant", "clock", "rank", "ticker", "t", "tenure",
                                     "dist_high", "ret_fill", "bars_since_high", "nh15", "drank5",
                                     "rank_known"] + [f"v{h}" for h in HORIZONS] + ["fmfe120"])
    if d.height == 0:
        return []
    pred = EVENTS[event](d)
    d = d.with_columns(pred.alias("_ev"))
    bars = load_bars(data_root / "harvest01" / "base" / "bars" / f"{day}.parquet")
    fills = pl.read_parquet(data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet").filter(
        pl.col("status") == "filled")
    fillmap = {(r["variant"], r["clock"], r["rank"], r["ticker"]):
               (int(r["fill_et"]), float(r["fill_px"])) for r in fills.iter_rows(named=True)}
    rows = []

    def path_stats(tk: str, t_ev: int, fill_px: float) -> dict:
        b = bars.get(tk)
        if b is None:
            return {}
        ets = b["et"]
        i = bisect.bisect_left(ets, t_ev)
        if i >= len(ets):
            return {}
        entry_px = b["open"][i]
        seg_h, seg_l, seg_c = b["high"][i:], b["low"][i:], b["close"][i:]
        peak_pre = max(b["high"][:i]) if i > 0 else None
        out = {"entry_exec_px": entry_px,
               "fwd_mfe": max(seg_h) / entry_px - 1 if seg_h else None,
               "fwd_mae": min(seg_l) / entry_px - 1 if seg_l else None,
               "min_to_new_high": next((ets[i + j] - t_ev for j, hh in enumerate(seg_h)
                                        if peak_pre is not None and hh > peak_pre), None),
               "min_to_fill": next((ets[i + j] - t_ev for j, cc in enumerate(seg_c)
                                    if cc >= fill_px), None),
               "close_vs_fill": seg_c[-1] / fill_px - 1 if seg_c else None}
        return out

    for (variant, clock, rank, ticker), grp in d.group_by(["variant", "clock", "rank", "ticker"]):
        g = grp.sort("t")
        ev = g.filter(pl.col("_ev"))
        if ev.height == 0:
            continue
        e = ev.row(0, named=True)
        fm = fillmap.get((variant, clock, rank, ticker))
        if fm is None:
            continue
        fill_et, fill_px = fm
        row = {"day": day, "variant": variant, "clock": int(clock), "rank": int(rank),
               "ticker": ticker, "event": event, "ev_t": int(e["t"]),
               "tenure_at_ev": int(e["tenure"]), "dist_high_at_ev": e["dist_high"],
               "ret_fill_at_ev": e["ret_fill"], "rank_known_at_ev": e["rank_known"],
               "fill_et": fill_et, "fill_px": fill_px}
        for h in HORIZONS:
            row[f"v{h}"] = e[f"v{h}"]
        row.update(path_stats(ticker, int(e["t"]), fill_px))
        rows.append(row)
        # matched control: another member of the same day+clock, tenure within +/-5 minutes,
        # not in the event state at that minute; fall back to the same member's non-event
        # minutes (closest tenure). Cross-member first: member identity is what the
        # matched comparison must control for.
        t0 = int(e["tenure"])
        others = d.filter((pl.col("clock") == clock) & (pl.col("ticker") != ticker))
        ctrl = others.filter((~pl.col("_ev")) & (pl.col("tenure").is_between(t0 - 5, t0 + 5)))
        if ctrl.height == 0:
            ctrl = g.filter(~pl.col("_ev"))
        if ctrl.height:
            c = (ctrl.with_columns((pl.col("tenure") - t0).abs().alias("_d"))
                 .sort("_d").row(0, named=True))
            crow = {"day": day, "variant": variant, "clock": int(clock), "rank": int(rank),
                    "ticker": ticker, "event": event + "_control", "ev_t": int(c["t"]),
                    "tenure_at_ev": int(c["tenure"]), "dist_high_at_ev": c["dist_high"],
                    "ret_fill_at_ev": c["ret_fill"], "rank_known_at_ev": c["rank_known"],
                    "fill_et": fill_et, "fill_px": fill_px}
            for h in HORIZONS:
                crow[f"v{h}"] = c[f"v{h}"]
            rows.append(crow)
    return rows


def _day_worker(a):
    day, data_root, event, se = a
    return process_day(day, Path(data_root), event, se)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="deep20", choices=sorted(EVENTS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    days = sorted(cal.keys())
    if args.limit:
        days = days[:args.limit]
    rows = []
    if args.workers > 1:
        import multiprocessing as mp
        tasks = [(d, str(data_root), args.event, int(cal[d]["session_end"])) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for res in pool.imap_unordered(_day_worker, tasks):
                rows += res
    else:
        for day in days:
            rows += process_day(day, data_root, args.event, int(cal[day]["session_end"]))
    df = pl.DataFrame(rows, infer_schema_length=None)
    out = rep / f"transitions_{args.event}.parquet"
    df.write_parquet(out)
    # summary
    ev = df.filter(pl.col("event") == args.event)
    ct = df.filter(pl.col("event") == args.event + "_control")
    lines = [f"# HARVEST01 — transition anatomy: `{args.event}` (dev)\n",
             f"episodes: {ev.height}; matched controls: {ct.height}\n"]
    lines.append("| group | n | V5 | V15 | V30 | V60 | V120 | fwd MFE | fwd MAE | min to new high | min to fill | close vs fill |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, s in (("event", ev), ("control", ct)):
        if s.height == 0:
            continue

        def m(c, ss=None):
            v = (ss if ss is not None else s)[c].drop_nulls()
            return f"{v.median()*100:.2f}" if v.len() else ""

        def mins(c):
            v = s[c].drop_nulls()
            return f"{v.median():.0f}" if v.len() else ""
        b1 = s.filter(pl.col("day") <= "2023-12-31")
        b2 = s.filter(pl.col("day") >= "2025-02-01")
        lines.append(f"| {name} | {s.height} | {m('v5')} | {m('v15')} | {m('v30')} | {m('v60')} | {m('v120')} | "
                     f"{m('fwd_mfe')} | {m('fwd_mae')} | {mins('min_to_new_high')} | {mins('min_to_fill')} | "
                     f"{m('close_vs_fill')} | {m('v30', b1)} | {m('v30', b2)} | {m('v120', b1)} | {m('v120', b2)} |")
    lines.insert(len(lines) - 1, "")  # spacer before the note
    hdr = ("| group | n | V5 | V15 | V30 | V60 | V120 | fwd MFE | fwd MAE | min to new high | min to fill | "
           "close vs fill | V30 B1 | V30 B2 | V120 B1 | V120 B2 |")
    for i, ln in enumerate(lines):
        if ln.startswith("| group |"):
            lines[i] = hdr
            break
    lines.append("\nMedians in %; min-to-* in minutes. Controls = same clock, tenure ±5, no event in the "
                 "preceding 5 minutes. A state is only a candidate if the event/control gap is material "
                 "AND positive in both blocks; then and only then does it earn a rule test.\n")
    (rep / "transitions.md").write_text("\n".join(lines) + "\n")
    print(f"transitions_{args.event}: episodes={ev.height} controls={ct.height} -> {rep/'transitions.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
