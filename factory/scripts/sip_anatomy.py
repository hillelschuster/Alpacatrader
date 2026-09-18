#!/usr/bin/env python3
"""BASKET-01 SIP anatomy driver — regenerated Phase-1 day records from SIP substrate.

Per day, reuses basket_anatomy.process_day on the Layer-2 merged SIP frame
(data/sip/net/bars/<day>.parquet, built by sip_netbars.py), with:
  * prev_map from the previous available SIP universe day's c_last (SIP previous-session
    close; the stored legacy prev_close is never used for ranking);
  * pm_day synthesized from the SIP premarket compact table (A_pm), when available;
  * winners (session-max leaders) and audit counts patched from the FULL PIT-universe SIP
    compact table so containment uses all PIT symbols, not just the fetched net;
  * split flags kept as audit-only metadata (future-dependent, never causal admission).

Outputs (same shape as the legacy anatomy, separate tree):
  factory/artifacts/basket/sip/anatomy/<day>.jsonl
  factory/artifacts/basket/sip/bars/<day>.parquet

Usage:
  .venv/bin/python factory/scripts/sip_anatomy.py --self-test
  .venv/bin/python factory/scripts/sip_anatomy.py --day 2021-02-01
  .venv/bin/python factory/scripts/sip_anatomy.py --days 2021-02-01 2025-09-09 --force
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_anatomy as ba  # noqa: E402

SIU = ROOT / "data" / "sip" / "universe"
NETBARS = ROOT / "data" / "sip" / "net" / "bars"
OUT = ROOT / "factory" / "artifacts" / "basket" / "sip"


def prev_universe_day(day: str, max_back: int = 10):
    """Most recent universe table strictly before `day`."""
    days = sorted(Path(p).name[:10] for p in glob.glob(str(SIU / "rth" / "*.parquet")))
    prior = [d for d in days if d < day]
    return prior[-1] if prior else None


def prev_map_from(prev_day: str) -> dict:
    if not prev_day:
        return {}
    df = pl.read_parquet(SIU / "rth" / f"{prev_day}.parquet", columns=["symbol", "c_last"])
    return dict(zip(df["symbol"].to_list(), df["c_last"].to_list()))


def pm_day_from(day: str):
    p = SIU / "premarket" / f"{day}.parquet"
    if not p.exists():
        return None
    df = pl.read_parquet(p, columns=["symbol", "pm_last_et", "pm_last_px", "pm_n_bars"])
    df = df.filter((pl.col("pm_last_px") > 0) & pl.col("pm_last_et").is_not_null())
    if not df.height:
        return None
    return df.select([pl.col("symbol").alias("ticker"), pl.col("pm_last_et").alias("et"),
                      pl.col("pm_last_px").alias("close")])


def winners_from_universe(uni: pl.DataFrame, prev_map: dict, split_set: set):
    """Session-max leaders from the full PIT SIP table (top-10 by open anchor and prev anchor)."""
    df = uni.filter(pl.col("o570") > 0)
    if not df.height:
        return [], []
    pcm = pl.DataFrame({"symbol": list(prev_map.keys()),
                        "pclose": [float(v) for v in prev_map.values()]}) if prev_map else None
    if pcm is not None:
        df = df.join(pcm, on="symbol", how="left")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("pclose"))
    df = df.with_columns(
        (pl.col("hi") / pl.col("o570") - 1).alias("gain_open"),
        (pl.col("c_last") / pl.col("o570") - 1).alias("eod_open"),
        ((pl.col("hi") / pl.col("pclose") - 1)).alias("gain_prev"),
        pl.col("o570").is_null().alias("delayed_open"),
    )
    wo = df.sort("gain_open", descending=True).head(10)
    winners_open = [{"ticker": r["symbol"], "gain_open": ba.R(r["gain_open"]),
                     "gain_prev": ba.R(r["gain_prev"]), "eod_open": ba.R(r["eod_open"]),
                     "et_hi": None, "delayed_open": bool(r["delayed_open"]),
                     "split_flag": r["symbol"] in split_set} for r in wo.iter_rows(named=True)]
    wp = df.filter(pl.col("pclose") > 0).sort("gain_prev", descending=True).head(10)
    winners_prev = [{"ticker": r["symbol"], "gain_prev": ba.R(r["gain_prev"]),
                     "gain_open": ba.R(r["gain_open"]), "split_flag": r["symbol"] in split_set}
                    for r in wp.iter_rows(named=True)]
    return winners_open, winners_prev


def build_day(day: str, force: bool = False) -> dict:
    jl = OUT / "anatomy" / f"{day}.jsonl"
    bp = OUT / "bars" / f"{day}.parquet"
    frame_p = NETBARS / f"{day}.parquet"
    if not frame_p.exists():
        return {"day": day, "status": "no_frame"}
    if jl.exists() and not force:
        return {"day": day, "status": "skip"}
    frame = pl.read_parquet(frame_p)
    prev_day = prev_universe_day(day)
    prev_map = prev_map_from(prev_day)
    pm_day = pm_day_from(day)
    rec = ba.process_day(frame, day, prev_map, pm_day, max_days_flag=False)
    if not rec:
        return {"day": day, "status": "empty", "prev_day": prev_day}

    # --- winners + audit patched from the FULL PIT universe table
    uni = pl.read_parquet(SIU / "rth" / f"{day}.parquet")
    split_set = ba.split_excl(day)
    wo, wp = winners_from_universe(uni, prev_map, split_set)
    if wo:
        rec["winners_open"] = wo
        rec["winners_prev"] = wp
    audit = rec.get("audit", {})
    audit.update({
        "rows": int(frame.height),
        "n_elig": int(uni.height),
        "n_open0930": int(uni["o570"].is_not_null().sum()),
        "missing_0930": int(uni.height - int(uni["o570"].is_not_null().sum())),
        "source": "sip_net",
        "prev_day": prev_day,
        "pm_available": bool(pm_day is not None),
    })
    cov_p = ROOT / "data" / "sip" / "net" / "coverage" / f"{day}.json"
    if cov_p.exists():
        audit["frame_classes"] = json.load(open(cov_p)).get("classes", {})
    rec["audit"] = audit

    (OUT / "anatomy").mkdir(parents=True, exist_ok=True)
    (OUT / "bars").mkdir(parents=True, exist_ok=True)
    bars = rec.pop("_bars")
    tmp = jl.with_suffix(".jsonl.tmp")
    with open(tmp, "w") as fh:
        fh.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
    tmp.replace(jl)
    tmpb = bp.with_suffix(".parquet.tmp")
    bars.write_parquet(tmpb)
    tmpb.replace(bp)
    return {"day": day, "status": "ok", "prev_day": prev_day, "pm": pm_day is not None,
            "snaps": len(rec["snapshots"]), "audit": {k: audit[k] for k in ("rows", "n_elig", "n_open0930")}}


def selftest():
    uni = pl.DataFrame({
        "symbol": ["AAA", "BBB", "CCC", "DDD", "EEE"],
        "o570": [1.0, 2.0, 10.0, None, 3.0],
        "hi": [3.0, 3.0, 12.0, 5.0, 3.3],
        "c_last": [2.0, 2.2, 11.0, 4.0, 3.1],
    })
    prev = {"AAA": 1.0, "BBB": 2.0, "CCC": 5.0, "EEE": 2.0}
    wo, wp = winners_from_universe(uni, prev, {"BBB"})
    # gain_open: AAA 2.0, BBB .5, CCC .2, EEE .1 ; DDD excluded (o570 null)
    assert [w["ticker"] for w in wo] == ["AAA", "BBB", "CCC", "EEE"], wo
    assert wo[0]["gain_open"] == 2.0 and wo[0]["eod_open"] == 1.0
    assert wo[1]["split_flag"] is True and wo[1]["gain_prev"] == 0.5
    # gain_prev: AAA 2.0, CCC 1.4, BBB .5, EEE .65 -> order AAA, CCC, EEE, BBB
    assert [w["ticker"] for w in wp] == ["AAA", "CCC", "EEE", "BBB"], wp
    assert wp[1]["gain_prev"] == 1.4
    print("self-test OK")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    days = []
    if args.day:
        days.append(args.day)
    if args.days:
        days.extend(args.days)
    if not days:
        ap.error("--day or --days required")
    for d in days:
        print(build_day(d, force=args.force))


if __name__ == "__main__":
    main()
