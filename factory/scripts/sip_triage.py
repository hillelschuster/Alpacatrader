#!/usr/bin/env python3
"""BASKET-01 SIP panel triage — classify certification tail revisions & coverage.

The certification note lists raw revisions; this layer records WHY each revision
happened where it can be seen from the data:
  coverage_low   : symbol has < MIN_RTH_TRADES RTH trades in the SIP archive
                   (auction-print-only or sparsely archived) -> SIP cannot be
                   treated as comparison truth for that symbol-day;
  large_revision : healthy coverage AND |SIP/stored - 1| > 0.5 -> either the
                   legacy high was not reproducible (bad print) or a scale/
                   adjustment offset (ratio cluster);
  minor          : everything else.

It also carries the three hand-verified case studies (BRP bad prints; BKKT SIP
archive gap; BNY scale offset) so the decision note cites checked facts, not
heuristics. Measurement-only; no strategy logic.

Outputs: factory/artifacts/basket/sip/sip_triage.json + SIP_TRIAGE.md
Usage:   --self-test | --dir factory/artifacts/basket/sip
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SIPD = ROOT / "factory" / "artifacts" / "basket" / "sip"
TRADES = ROOT / "data" / "sip" / "trades"
MIN_RTH_TRADES = 200
SCALE_MIN = 1.5  # |ratio-1| flag threshold (symmetric)

CASE_STUDIES = [
    {
        "day": "2022-03-10", "symbol": "BRP",
        "verdict": "legacy bad print (confirmed)",
        "facts": [
            "legacy clean tape contained bars at 1092.71 / 785.47 forming a "
            "corrupted spike; glitch-free member MFE +3.7%",
            "SIP trades max 26.35/26.47 all day; the corrupted prints are ABSENT from SIP",
            "absolute stored high 1092.71 is invalid; ranking and MFE both contaminated "
            "in the legacy record on that day",
        ],
    },
    {
        "day": "2021-10-25", "symbol": "BKKT",
        "verdict": "SIP archive gap -> comparison void (SIP is incomplete, legacy looks right)",
        "facts": [
            "SIP trades for BKKT that day: 10 rows, auction/cross conditions only "
            "(09:30 Q/O at ~13.8; 16:00 M/6 at 30.5-30.6)",
            "direct live Alpaca probe 09:35-09:40 ET returns 0 trades for BKKT, BE, RDW "
            "(AAPL/SPY return 17k/10k on the same date + window; BE next day returns 6.3k)",
            "legacy RTH bars show 29.6-31.57 late day with ~1.3M volume; SIP closing print "
            "30.6 is consistent with the legacy scale",
            "therefore the -56% 'revision' is an instrument artifact, not a legacy error",
        ],
    },
    {
        "day": "2025-10-30", "symbol": "BNY",
        "verdict": "scale/adjustment offset x~10.4 (percent-safe, absolute-price unsafe)",
        "facts": [
            "SIP trades all day span 106.03-108.78 (42,328 prints, tight spread)",
            "legacy highs ~10.26-10.34 across 2025-10-29/30/31 -> consistent scale factor ~10.4",
            "pattern matches a subsequent 10:1 split adjustment in the legacy tape; "
            "percent moves are scale-invariant, absolute levels are not comparable",
        ],
    },
]


def symbol_counts(trades: pl.DataFrame) -> dict:
    ts = trades["ts_utc"].dt.convert_time_zone("America/New_York")
    d = trades.with_columns(
        et=(ts.dt.hour().cast(pl.Int32) * 60 + ts.dt.minute().cast(pl.Int32))
    )
    g = d.group_by("symbol").agg(
        pl.len().alias("n_total"),
        (pl.col("et") >= 570).cast(pl.Int32).sum().alias("n_rth"),
        pl.col("ts_utc").min().alias("t0"), pl.col("ts_utc").max().alias("t1"),
    )
    return {r["symbol"]: r for r in g.iter_rows(named=True)}


def classify(delta: float, sip_high: float, stored_high: float, n_rth: int) -> dict:
    out: dict = {"n_rth": n_rth}
    if n_rth < MIN_RTH_TRADES:
        out["class"] = "coverage_low"
        out["ratio"] = None
        return out
    if stored_high and stored_high > 0 and sip_high > 0:
        ratio = round(sip_high / stored_high, 4)
        out["ratio"] = ratio
        out["class"] = "large_revision" if abs(ratio - 1.0) > (SCALE_MIN - 1.0) else "minor"
    else:
        out["ratio"] = None
        out["class"] = "minor"
    return out


def triage(sip_dir: Path, trades_dir: Path) -> dict:
    certs = sorted(p for p in glob.glob(str(sip_dir / "certification_*.json"))
                   if "panel" not in p)
    per_day, all_rows = [], []
    cls_counter = Counter()
    for cpath in certs:
        c = json.load(open(cpath))
        day = c["day"]
        tp = trades_dir / f"{day}.parquet"
        counts = symbol_counts(pl.read_parquet(tp, columns=["symbol", "ts_utc"])) if tp.exists() else {}
        rows = []
        for t in c.get("tail", {}).get("diffs_vs_stored_high", []):
            cc = counts.get(t["ticker"], {})
            n_rth = int(cc.get("n_rth", 0)) if cc else 0
            extra = classify(t["delta"], t["sip_rth_po_max"], t["stored_high"], n_rth)
            cls_counter[extra["class"]] += 1
            rows.append({"day": day, **t, "sip_high": t["sip_rth_po_max"], **extra})
        all_rows.extend(rows)
        per_day.append({"day": day, "diffs": len(rows),
                        "coverage_low": sum(1 for r in rows if r["class"] == "coverage_low"),
                        "large_revision": sum(1 for r in rows if r["class"] == "large_revision"),
                        "minor": sum(1 for r in rows if r["class"] == "minor")})
    big = sorted([r for r in all_rows if r["class"] == "large_revision"],
                 key=lambda x: abs(x["delta"]), reverse=True)
    scale_cluster = [r for r in big if r["ratio"] and (r["ratio"] >= SCALE_MIN or r["ratio"] <= 1 / SCALE_MIN)]
    return {
        "days": len(certs),
        "diffs_total": len(all_rows),
        "classes": dict(cls_counter),
        "scale_cluster": [r for r in scale_cluster][:20],
        "largest_large_revision": big[:20],
        "per_day": per_day,
        "case_studies": CASE_STUDIES,
        "thresholds": {"min_rth_trades": MIN_RTH_TRADES, "scale_ratio_flag": SCALE_MIN},
    }


def render_md(t: dict) -> str:
    L = ["# SIP panel triage — why revisions happened (facts + coverage)", ""]
    L.append(f"Panel days: **{t['days']}**; tail diffs classified: **{t['diffs_total']}** "
             f"-> {t['classes']}.")
    L.append("")
    L.append("## Case studies (hand-verified)")
    for cs in t["case_studies"]:
        L.append(f"### {cs['day']} {cs['symbol']} — {cs['verdict']}")
        for f in cs["facts"]:
            L.append(f"- {f}")
        L.append("")
    L.append("## Largest large-revision rows (healthy SIP coverage)")
    L.append("| day | ticker | stored | SIP | delta | ratio | n_rth |")
    L.append("|---|---|---|---|---|---|---|")
    for r in t["largest_large_revision"][:15]:
        L.append(f"| {r['day']} | {r['ticker']} | {r['stored_high']} | {r['sip_high']} | "
                 f"{r['delta']:.4f} | {r['ratio']} | {r['n_rth']} |")
    L.append("")
    L.append("## Coverage-low revisions (SIP incomplete — comparison void)")
    L.append("See sip_triage.json `per_day` + certification rows with class=coverage_low; "
             "these are excluded from 'SIP is truth' claims.")
    L.append("")
    L.append("Thresholds: " + json.dumps(t["thresholds"]))
    return "\n".join(L) + "\n"


def selftest():
    # symbol_counts needs real datetimes; build a deterministic fixture
    import datetime as dt
    base = dt.datetime(2021, 10, 25, 13, 30, tzinfo=dt.timezone.utc)
    syms, times = [], []
    for s, n in (("AAA", 300), ("BBB", 10), ("CCC", 500)):
        for i in range(n):
            syms.append(s)
            times.append(base + dt.timedelta(minutes=(i % 200)))
    df = pl.DataFrame({"symbol": syms, "ts_utc": times}).with_columns(
        pl.col("ts_utc").dt.replace_time_zone("UTC").dt.cast_time_unit("us"))
    cnt = symbol_counts(df)
    assert cnt["AAA"]["n_rth"] == 300 and cnt["BBB"]["n_total"] == 10
    a = classify(0.5, 11.0, 10.0, 300)
    assert a["class"] == "minor" and a["ratio"] == 1.1, a
    b = classify(-0.9, 2.6, 26.0, 500)
    assert b["class"] == "large_revision" and b["ratio"] == 0.1, b
    c = classify(-0.5, 13.9, 31.6, 10)
    assert c["class"] == "coverage_low", c
    d = classify(0.9, 108.78, 10.26, 42328)
    assert d["class"] == "large_revision" and abs(d["ratio"] - 10.6) < 0.01, d
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(SIPD))
    ap.add_argument("--trades", default=str(TRADES))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    t = triage(Path(args.dir), Path(args.trades))
    with open(Path(args.dir) / "sip_triage.json", "w") as fh:
        json.dump(t, fh, indent=1, default=str)
    with open(Path(args.dir) / "SIP_TRIAGE.md", "w") as fh:
        fh.write(render_md(t))
    print("triage:", json.dumps(t["classes"]), "->", Path(args.dir) / "SIP_TRIAGE.md")


if __name__ == "__main__":
    main()
