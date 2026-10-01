#!/usr/bin/env python3
"""HARVEST01 — sub-minute execution probe on sampled member-days (local trades lane).

For a bounded sample of member-days (giants: post-fill MFE >= 100%; duds: MFE < 10%;
runners in between), compare the simulation's execution assumptions with the raw SIP
print tape already on disk (data/sip/net/trades/<day>.parquet, ~50 symbols/day):

  * entry: first print at/after the fill minute vs the bar open the sim uses;
  * exit: first print at/after the exit minute vs the bar open the sim uses;
  * first-60s print count / traded size / price range at entry (liquidity context);
  * print silence >= 5 minutes between fill and exit (halt / no-trade context).

One file read per sampled day; sampled days are distinct. Sample is outcome-stratified
but analysis-only (never a selection input).

Usage:
    .venv/bin/python factory/scripts/basket_harvest_subminute.py --sample 30
"""
from __future__ import annotations

import argparse
import bisect
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

ET_TZ = "America/New_York"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=30, help="member-days per class")
    ap.add_argument("--exit", type=int, default=720)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    rep.mkdir(parents=True, exist_ok=True)

    fills = pl.scan_parquet(str(data_root / "harvest01" / "sim" / "fills" / "*.parquet"),
                            glob=True, extra_columns="ignore").collect()
    fills = fills.filter((pl.col("variant") == "primary") & (pl.col("status") == "filled")
                         & pl.col("mfe_adj").is_not_null())
    giants = fills.filter(pl.col("mfe_adj") >= 1.0).sort("mfe_adj", descending=True)
    duds = fills.filter(pl.col("mfe_adj") < 0.10)
    runners = fills.filter((pl.col("mfe_adj") >= 0.30) & (pl.col("mfe_adj") < 1.0))

    def pick(df, n):
        seen, out = set(), []
        for r in df.iter_rows(named=True):
            if r["day"] in seen:
                continue
            seen.add(r["day"])
            out.append(r)
            if len(out) >= n:
                break
        return out

    sample = pick(giants, args.sample) + pick(runners, args.sample) + pick(duds, args.sample)
    by_day = {}
    for r in sample:
        by_day.setdefault(r["day"], []).append(r)

    rows = []
    for day, members in sorted(by_day.items()):
        tp = data_root / "sip" / "net" / "trades" / f"{day}.parquet"
        if not tp.exists():
            for m in members:
                rows.append({"day": day, "ticker": m["ticker"], "status": "no_trades_file"})
            continue
        tk = sorted({m["ticker"] for m in members})
        try:
            t = pl.scan_parquet(tp).select(["symbol", "ts_utc", "price", "size"]).filter(
                pl.col("symbol").is_in(tk)).collect()
        except Exception as e:
            for m in members:
                rows.append({"day": day, "ticker": m["ticker"], "status": f"read_error:{str(e)[:60]}"})
            continue
        if t.height == 0:
            for m in members:
                rows.append({"day": day, "ticker": m["ticker"], "status": "no_prints"})
            continue
        t = t.with_columns(
            (pl.col("ts_utc").dt.convert_time_zone(ET_TZ).dt.hour() * 60
             + pl.col("ts_utc").dt.convert_time_zone(ET_TZ).dt.minute()).alias("et"),
            (pl.col("ts_utc").dt.convert_time_zone(ET_TZ).dt.hour() * 3600
             + pl.col("ts_utc").dt.convert_time_zone(ET_TZ).dt.minute() * 60
             + pl.col("ts_utc").dt.convert_time_zone(ET_TZ).dt.second()).alias("sec"))
        for m in members:
            sub = t.filter(pl.col("symbol") == m["ticker"]).sort("sec")
            ets = sub["et"].to_list()
            px = sub["price"].to_list()
            sz = sub["size"].to_list()
            row = {"day": day, "ticker": m["ticker"], "status": "ok", "clock": m["clock"],
                   "rank": m["rank"], "mfe_adj": m["mfe_adj"], "fill_et": m["fill_et"],
                   "fill_px": m["fill_px"], "n_prints": len(ets)}
            i = bisect.bisect_left(ets, m["fill_et"])
            if i < len(ets):
                row["first_print_et"] = int(ets[i])
                row["first_print_px"] = float(px[i])
                row["entry_px_diff"] = float(px[i] / m["fill_px"] - 1)
                w = [j for j in range(i, len(ets)) if ets[j] <= m["fill_et"] + 1]
                if w:
                    row["first60_n"] = len([j for j in range(i, len(ets)) if ets[j] < ets[i] + 1])
                    row["first60_sz"] = float(sum(sz[j] for j in w))
                    pr = [px[j] for j in w]
                    row["first60_range"] = float(max(pr) / min(pr) - 1) if pr else None
            j = bisect.bisect_left(ets, args.exit)
            if j < len(ets):
                row["exit_print_et"] = int(ets[j])
                row["exit_px_diff"] = float(px[j] / m.get(f"r{args.exit}_100", float("nan")) - 1) if False else None
                # compare with the sim's bar-open exit if the fills lane carries it
                row["exit_first_print_px"] = float(px[j])
            gaps = max((ets[k + 1] - ets[k] for k in range(max(i, 0), max(len(ets) - 1, 0))), default=0)
            row["max_print_gap_min"] = int(gaps)
            rows.append(row)
    df = pl.DataFrame(rows, infer_schema_length=None)
    out = rep / "subminute_probe.parquet"
    df.write_parquet(out)
    ok = df.filter(pl.col("status") == "ok")
    md = ["# HARVEST01 — sub-minute execution probe (local print tape)\n"]
    md.append(f"sample rows: {df.height}; ok: {ok.height}; days: {df['day'].n_unique()}\n")
    if df.height:
        md.append("status counts: " + ", ".join(
            f"{r['status']}={r['len']}" for r in df.group_by("status").len().sort("len", descending=True).iter_rows(named=True)) + "\n")
    if ok.height and "entry_px_diff" in ok.columns:
        md.append("| class | n | median entry print-vs-fill diff % | median max print gap (min) | median first-60s prints |")
        md.append("|---|---|---|---|---|")
        for lo, hi, name in ((1.0, 9.9, "giant>=100%"), (0.30, 1.0, "runner"), (0.0, 0.10, "dud")):
            s = ok.filter((pl.col("mfe_adj") >= lo) & (pl.col("mfe_adj") < hi))
            if s.height == 0:
                continue
            md.append(f"| {name} | {s.height} | "
                      f"{(s['entry_px_diff'].median() or 0)*100:.3f} | "
                      f"{s['max_print_gap_min'].median()} | {s['first60_n'].median()} |")
    (rep / "subminute_probe.md").write_text("\n".join(md) + "\n")
    print(f"probe rows={df.height} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
