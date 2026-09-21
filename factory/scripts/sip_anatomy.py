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


_LB = None


def calendar_days() -> list:
    global _LB
    if _LB is None:
        _LB = sorted(Path(p).name[3:13]
                     for p in glob.glob(str(ROOT / "data" / "leaderboard" / "lb_*.parquet")))
    return _LB


def prev_check(day: str, prev_day: str):
    """None when the previous-session close is calendar-adjacent (or this is the span's
    first calendar day); otherwise a reason string. Guards against a stale prev close
    silently crossing a raw-data gap (e.g. the 2025-01-31 seed resolving to 2023-12-29)."""
    cal = calendar_days()
    i = cal.index(day) if day in cal else None
    if i is None or i == 0:
        return None  # first calendar day: seed-allowed
    cp = cal[i - 1]
    uni_days = set(Path(p).name[:10] for p in glob.glob(str(SIU / "rth" / "*.parquet")))
    if cp not in uni_days:
        return "prev_session_outside_coverage"
    if prev_day != cp:
        return "prev_not_adjacent"
    return None


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
    """Session-max / terminal leaders from the full PIT SIP table.

    Returns (winners_open, winners_prev, winners_close_open, winners_close_prev).
    winners_open/winners_close_open are RTH-open anchored and do NOT require a
    previous-session close (2026-09-21 repair). The compact table only carries o570,
    so names whose 09:30 print is missing are unrankable here; build_day() unions the
    merged-frame (first-open anchored) lists back in and tags their anchor source."""
    df = uni.filter(pl.col("o570") > 0)
    if not df.height:
        return [], [], [], [], [], []
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
        ((pl.col("c_last") / pl.col("pclose") - 1)).alias("eod_prev"),
    )
    wo = df.sort(["gain_open", "symbol"], descending=[True, False]).head(10)
    winners_open = [{"ticker": r["symbol"], "gain_open": ba.R(r["gain_open"]),
                     "gain_prev": ba.R(r["gain_prev"]), "eod_open": ba.R(r["eod_open"]),
                     "anchor_src": "o570",
                     "split_flag": r["symbol"] in split_set} for r in wo.iter_rows(named=True)]
    dfp = df.filter(pl.col("pclose") > 0)
    wp = dfp.sort(["gain_prev", "symbol"], descending=[True, False]).head(10)
    winners_prev = [{"ticker": r["symbol"], "gain_prev": ba.R(r["gain_prev"]),
                     "gain_open": ba.R(r["gain_open"]), "anchor_src": "prev_close",
                     "split_flag": r["symbol"] in split_set}
                    for r in wp.iter_rows(named=True)]
    wco = df.sort(["eod_open", "symbol"], descending=[True, False]).head(10)
    winners_close_open = [{"ticker": r["symbol"], "eod_open": ba.R(r["eod_open"]),
                           "gain_open": ba.R(r["gain_open"]), "anchor_src": "o570",
                           "split_flag": r["symbol"] in split_set}
                          for r in wco.iter_rows(named=True)]
    wcp = dfp.sort(["eod_prev", "symbol"], descending=[True, False]).head(10)
    winners_close_prev = [{"ticker": r["symbol"], "eod_prev": ba.R(r["eod_prev"]),
                           "eod_open": ba.R(r["eod_open"]), "anchor_src": "prev_close",
                           "split_flag": r["symbol"] in split_set}
                          for r in wcp.iter_rows(named=True)]
    # The same open-anchored leader objects restricted to the basket's own tradeability
    # floor (o570 >= $1). Containment is reported against BOTH universes: the unfloored
    # leader set is the conservative object (a sub-$1 penny leader is a leader we could
    # never have bought), the floored set is the comparable one for capture questions.
    dff = df.filter(pl.col("o570") >= ba.MIN_PRICE)
    wof = dff.sort(["gain_open", "symbol"], descending=[True, False]).head(10)
    winners_open_floored = [{"ticker": r["symbol"], "gain_open": ba.R(r["gain_open"]),
                             "anchor_src": "o570_floored",
                             "split_flag": r["symbol"] in split_set}
                            for r in wof.iter_rows(named=True)]
    wcof = dff.sort(["eod_open", "symbol"], descending=[True, False]).head(10)
    winners_close_open_floored = [{"ticker": r["symbol"], "eod_open": ba.R(r["eod_open"]),
                                   "anchor_src": "o570_floored",
                                   "split_flag": r["symbol"] in split_set}
                                  for r in wcof.iter_rows(named=True)]
    return (winners_open, winners_prev, winners_close_open, winners_close_prev,
            winners_open_floored, winners_close_open_floored)


def union_leaders(univ: list, merged: list | None, key: str, cap: int = 10) -> list:
    """Universe list first, plus merged-frame names it cannot rank (no o570), tagged."""
    seen = {w["ticker"] for w in univ}
    extra = []
    for w in merged or []:
        if w["ticker"] in seen:
            continue
        w = dict(w)
        w["anchor_src"] = "first_open"
        extra.append(w)
    both = list(univ) + extra
    both.sort(key=lambda w: w["ticker"])
    both.sort(key=lambda w: (w.get(key) is not None, w.get(key) if w.get(key) is not None else 0.0),
              reverse=True)
    return both[:cap]


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
    why = prev_check(day, prev_day)
    if why:
        return {"day": day, "status": why, "prev_day": prev_day}
    prev_map = prev_map_from(prev_day)
    pm_day = pm_day_from(day)
    rec = ba.process_day(frame, day, prev_map, pm_day, max_days_flag=False)
    if not rec:
        return {"day": day, "status": "empty", "prev_day": prev_day}

    # --- winners + audit patched from the FULL PIT universe table
    uni = pl.read_parquet(SIU / "rth" / f"{day}.parquet")
    split_set = ba.split_excl(day)
    wo, wp, wco, wcp, wof, wcof = winners_from_universe(uni, prev_map, split_set)
    if wo:
        rec["winners_open"] = union_leaders(wo, rec.get("winners_open"), "gain_open")
        rec["winners_prev"] = union_leaders(wp, rec.get("winners_prev"), "gain_prev")
        rec["winners_close_open"] = union_leaders(wco, rec.get("winners_close_open"), "eod_open")
        rec["winners_close_prev"] = union_leaders(wcp, rec.get("winners_close_prev"), "eod_prev")
        rec["winners_open_floored"] = wof
        rec["winners_close_open_floored"] = wcof
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
    wo, wp, wco, wcp, wof, wcof = winners_from_universe(uni, prev, {"BBB"})
    # gain_open: AAA 2.0, BBB .5, CCC .2, EEE .1 ; DDD excluded (o570 null)
    assert [w["ticker"] for w in wo] == ["AAA", "BBB", "CCC", "EEE"], wo
    assert wo[0]["gain_open"] == 2.0 and wo[0]["eod_open"] == 1.0
    assert wo[1]["split_flag"] is True and wo[1]["gain_prev"] == 0.5
    # floored variants: a sub-$1 leader (FFF, o570 0.5, +900% from open) is excluded by
    # the tradeability floor; the EOD/open tie BBB==CCC resolves by ticker ascending
    sub = pl.DataFrame({"symbol": ["AAA", "BBB", "CCC", "EEE", "FFF"],
                        "o570": [1.0, 2.0, 10.0, 3.0, 0.5],
                        "hi": [3.0, 3.0, 12.0, 3.3, 5.0],
                        "c_last": [2.0, 2.2, 11.0, 3.1, 4.0]})
    _, _, _, _, sf, scf = winners_from_universe(sub, {}, set())
    assert [w["ticker"] for w in sf] == ["AAA", "BBB", "CCC", "EEE"], sf
    assert [w["ticker"] for w in scf] == ["AAA", "BBB", "CCC", "EEE"], scf
    # exact-tie determinism: equal gain_open resolves by ticker ascending
    tie = pl.DataFrame({"symbol": ["ZZZ", "AAA"], "o570": [1.0, 1.0],
                        "hi": [2.0, 2.0], "c_last": [1.5, 1.5]})
    t_open, _, _, _, _, _ = winners_from_universe(tie, {}, set())
    assert [w["ticker"] for w in t_open] == ["AAA", "ZZZ"], t_open
    # gain_prev: AAA 2.0, CCC 1.4, BBB .5, EEE .65 -> order AAA, CCC, EEE, BBB
    assert [w["ticker"] for w in wp] == ["AAA", "CCC", "EEE", "BBB"], wp
    assert wp[1]["gain_prev"] == 1.4
    # terminal (EOD) leaders are their own lists
    assert wco[0]["ticker"] == "AAA" and wco[0]["eod_open"] == 1.0, wco
    assert wcp[0]["ticker"] == "CCC" and wcp[0]["eod_prev"] == 1.2, wcp
    # union keeps a merged-frame-only (first-open anchored) leader, tagged
    u = union_leaders(wo, [{"ticker": "ZZZ", "gain_open": 9.9}], "gain_open")
    assert u[0]["ticker"] == "ZZZ" and u[0]["anchor_src"] == "first_open"
    assert len(union_leaders(wo, [{"ticker": "AAA", "gain_open": 99.0}], "gain_open")) == 4
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
