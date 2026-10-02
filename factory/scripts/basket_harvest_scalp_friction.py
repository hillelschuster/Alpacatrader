#!/usr/bin/env python3
"""HARVEST01 — measured friction for the PM scalp (v2: proper ET conversion, pushdown).

For the sim's own primary fills: find the exact prints on the SIP tape (validates the
fill convention), the scalp's print-to-print gross, and the REAL quoted spread at the
fill minute from the SIP quotes (bid/ask), plus inside-size vs our order size.

Usage:
    .venv/bin/python factory/scripts/basket_harvest_scalp_friction.py --workers 3 --limit 60
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

ET = ZoneInfo("America/New_York")
NOTIONAL = 10_000.0


def et_minutes(ts: datetime) -> int:
    t = ts.astimezone(ET)
    return t.hour * 60 + t.minute


def process_day(day: str, data_root: Path, sip_root: Path) -> list[dict]:
    fl_p = data_root / "harvest01" / "sim" / "fills" / f"{day}.parquet"
    tr_p = sip_root / "trades" / f"{day}.parquet"
    q_p = sip_root / "quotes" / f"{day}.parquet"
    if not (fl_p.exists() and tr_p.exists()):
        return []
    fills = pl.read_parquet(fl_p).filter(
        (pl.col("variant") == "primary") & (pl.col("status") == "filled"))
    if fills.height == 0:
        return []
    syms = sorted(set(fills["ticker"].to_list()))
    trades = (pl.scan_parquet(tr_p).filter(pl.col("symbol").is_in(syms))
              .select(["symbol", "ts_utc", "price", "size"]).collect())
    tmap: dict[str, tuple[list, list, list]] = {}
    for (sym,), sub in trades.group_by(["symbol"]):
        sub = sub.sort("ts_utc")
        ets = [et_minutes(x) for x in sub["ts_utc"].to_list()]
        tmap[sym] = (ets, sub["price"].to_list(), sub["size"].to_list())
    qmap: dict[str, tuple[list, list, list, list, list]] = {}
    if q_p.exists():
        q = (pl.scan_parquet(q_p).filter(pl.col("symbol").is_in(syms))
             .select(["symbol", "ts_utc", "bid_price", "ask_price", "bid_size", "ask_size"]).collect())
        for (sym,), sub in q.group_by(["symbol"]):
            sub = sub.sort("ts_utc")
            qmap[sym] = (sub["ts_utc"].to_list(), sub["bid_price"].to_list(), sub["ask_price"].to_list(),
                         sub["bid_size"].to_list(), sub["ask_size"].to_list())
    rows = []
    for f in fills.iter_rows(named=True):
        sym = f["ticker"]
        if sym not in tmap:
            continue
        ets, px, sz = tmap[sym]
        base_px = float(f["fill_px"])
        i = bisect.bisect_left(ets, int(f["clock"]))
        if i >= len(ets):
            continue
        rec = {"day": day, "ticker": sym, "clock": int(f["clock"]), "rank": int(f["rank"]),
               "fill_px": base_px, "print_px": float(px[i]),
               "px_match": abs(float(px[i]) - base_px) / base_px < 1e-6,
               "fill_et": ets[i], "print_sz": float(sz[i])}
        for ex, name in ((571, "s571"), (575, "s575"), (585, "s585")):
            j = bisect.bisect_left(ets, ex)
            if j < len(ets) and px[j] > 0:
                rec[f"gross_{name}"] = float(px[j]) / base_px - 1
                rec[f"sz_{name}"] = float(sz[j])
        if sym in qmap:
            qts, qb, qa, qbs, qas = qmap[sym]
            k = bisect.bisect_right(qts, trades.filter(pl.col("symbol") == sym)["ts_utc"][i]) - 1
            if 0 <= k < len(qts) and qa[k] and qb[k] and qa[k] > qb[k] > 0:
                mid = (qa[k] + qb[k]) / 2
                rec["spread_bps"] = (qa[k] - qb[k]) / mid * 1e4
                rec["pos_in_q"] = (base_px - qb[k]) / (qa[k] - qb[k])
                rec["quote_age_s"] = (trades.filter(pl.col("symbol") == sym)["ts_utc"][i] - qts[k]).total_seconds()
                rec["ask_notional"] = float(qas[k] or 0) * qa[k]
                rec["bid_notional"] = float(qbs[k] or 0) * qb[k]
        rows.append(rec)
    return rows


def _w(a):
    day, data_root, sip_root = a
    return process_day(day, Path(data_root), Path(sip_root))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--days", default="")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    sip_root = data_root / "sip"
    rep = data_root / "harvest01" / "report"
    cal = json.loads(bps.CALENDAR.read_text())["evidence"]
    days = sorted(cal.keys())
    if args.days:
        days = [d for d in days if d in set(args.days.split(","))]
    if args.limit:
        days = days[:: max(1, len(days) // args.limit)][:args.limit]
    rows = []
    if args.workers > 1:
        import multiprocessing as mp
        tasks = [(d, str(data_root), str(sip_root)) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for res in pool.imap_unordered(_w, tasks):
                rows += res
    else:
        for d in days:
            rows += process_day(d, data_root, sip_root)
    df = pl.DataFrame(rows, infer_schema_length=None)
    df.write_parquet(rep / "scalp_friction.parquet")
    lines = ["# HARVEST01 — measured scalp friction (SIP prints + quotes)\n",
             f"days sampled: {len(days)}; fills: {df.height}; print match: "
             f"{df['px_match'].mean() if df.height else float('nan'):.4f}; quote coverage: "
             f"{df['spread_bps'].is_not_null().mean() if df.height else float('nan'):.4f}\n"]
    for clock in (569, 571, 575):
        s = df.filter(pl.col("clock") == clock)
        if s.height == 0:
            continue
        q = s.drop_nulls("spread_bps")
        lines.append(f"\n## clock {clock}: n={s.height}, quoted n={q.height}\n")
        if q.height:
            sp = q["spread_bps"]
            lines.append(f"- quoted spread bps: p25 {sp.quantile(0.25):.0f}, median {sp.median():.0f}, "
                         f"p75 {sp.quantile(0.75):.0f}, p90 {sp.quantile(0.90):.0f}")
            lines.append(f"- fill position in quote (0=bid,1=ask): mean {q['pos_in_q'].mean():.2f}, "
                         f"median {q['pos_in_q'].median():.2f}; quote age s: median {q['quote_age_s'].median():.1f}")
            lines.append(f"- inside ask notional vs $10k: median ratio "
                         f"{(q['ask_notional']/NOTIONAL).median():.2f}; bid median ratio "
                         f"{(q['bid_notional']/NOTIONAL).median():.2f}")
        for name in ("s571", "s575", "s585"):
            c = f"gross_{name}"
            if c in s.columns:
                v = s[c].drop_nulls()
                if v.len():
                    lines.append(f"- gross {name}: mean {v.mean()*100:+.3f}%, median {v.median()*100:+.3f}% (n={v.len()})")
                    net = s.drop_nulls([c, "spread_bps"]).with_columns(
                        (pl.col(c) - pl.col("spread_bps") / 1e4 - 0.0002).alias("net"))
                    if net.height:
                        lines.append(f"- net {name} (gross - quoted spread - 2bps fees): mean "
                                     f"{net['net'].mean()*100:+.3f}%, median {net['net'].median()*100:+.3f}% (n={net.height})")
    (rep / "scalp_friction.md").write_text("\n".join(lines) + "\n")
    print(f"scalp_friction fills={df.height} -> {rep/'scalp_friction.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
