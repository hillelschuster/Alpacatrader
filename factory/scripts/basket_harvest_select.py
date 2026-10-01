#!/usr/bin/env python3
"""HARVEST01 — causal top-gainer selection at fixed PM and RTH clocks.

For each development day and each frozen clock, produce the top-4 rank lists the market
showed at that clock under ONE anchor: gain versus the immediately previous session's
RTH close, split-normalized (the displayed-change convention a market screener shows):

    gain_adj = decision_px / (prev_close * split_factor) - 1
    split_factor = prod(old_rate / new_rate) over splits with prev_session < ex_date <= day

Sources and causal reads:
  * RTH clocks:  data/atlas/observation/v0/race.minute_full (full broad 1-min board).
    Decision row t = C (the panel's px at row t is the close of the last completed bar
    with et <= t-1, so row C carries exactly the information complete at the clock),
    known_by_t true.
    prev_close/flags come from the panel itself (it carries prev_close_day/source and
    quality flags; its raw denominator is split-unadjusted, which we normalize).
  * PM clocks:   data/sip/pm_snapshots (this lane's acquisition). Decision px = close of
    the last completed premarket bar et <= clock-1 (px_{clock}); prev_close from the
    panel's day universe when present, else the rth compact's previous-session c_last.

Ranking variants (all stored; the choice stays visible, never silent):
  raw      : every row with a decision price and prev_close > 0
  primary  : raw minus provably-broken rows (flag_prevclose_discrepancy, nonpositive px,
             gain_adj > 10x) and px >= $0.05
  listed   : primary requiring the PIT listed-common tag where the source provides it

Outputs per day (atomic, resumable):
    <data>/harvest01/base/selected/<day>.parquet
    <data>/harvest01/base/selected/<day>.manifest.json

Usage:
    .venv/bin/python factory/scripts/basket_harvest_select.py --self-check
    .venv/bin/python factory/scripts/basket_harvest_select.py --days 2021-02-01 2022-05-09
    .venv/bin/python factory/scripts/basket_harvest_select.py --dev-days --workers 3
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
import basket_pm_snapshots as bps  # noqa: E402  (pit_elig + data-root resolver reuse)

PM_CLOCKS = [510, 540, 550, 555, 560, 565, 569]
RTH_CLOCKS = [571, 575, 580, 585, 590, 595, 600, 615, 630, 660, 690, 720]
KEEP_RANKS = 4
GAIN_CEIL = 10.0     # >10x overnight is broken data, not a move; flagged, not silently kept
PX_FLOOR = 0.05      # sub-nickel prints are not a sane top-gainer basket slot
VARIANTS = ["raw", "primary", "listed"]

PANEL_COLS = ["t", "ticker", "px", "px_et", "known_by_t", "fresh_2m", "age_min",
              "rank_known", "rank_fresh_2m", "rank_unfiltered",
              "n_known", "n_eligible", "n_unfiltered",
              "prev_close", "prev_close_day", "prev_close_source",
              "flag_prevclose_discrepancy", "flag_nonpositive_px", "flag_extreme_gain",
              "pit_listed", "session_end", "quality_flags", "source"]


def prev_session_map(data_root: Path) -> dict:
    days = [json.loads(ln)["day"]
            for ln in (data_root / "sip" / "universe" / "index_rth.jsonl").read_text().strip().split("\n")]
    days = sorted(set(days))
    return {days[i]: days[i - 1] for i in range(1, len(days))}


def split_factor_frame(data_root: Path) -> pl.DataFrame:
    p = data_root / "harvest01" / "base" / "splits.parquet"
    df = pl.read_parquet(p, columns=["symbol", "action_type", "new_rate", "old_rate", "ex_date"])
    return df.with_columns((pl.col("old_rate") / pl.col("new_rate")).alias("factor"))


def split_map_for_day(day: str, prev_day: str, splits: pl.DataFrame) -> pl.DataFrame:
    sf = splits.filter((pl.col("ex_date") <= day) & (pl.col("ex_date") > prev_day))
    return sf.group_by("symbol").agg(pl.col("factor").product().alias("split_factor"),
                                     pl.len().alias("split_events"))


def panel_universe(day: str, data_root: Path) -> pl.DataFrame | None:
    """One row per ticker from the day's race panel (prev_close + quality + pit tags)."""
    pf = (data_root / "atlas" / "observation" / "v0" / "race.minute_full"
          / f"month={day[:7]}" / f"{day}.parquet")
    if not pf.exists():
        return None
    d = pl.scan_parquet(pf).select(PANEL_COLS).collect()
    u = d.group_by("ticker").agg([
        pl.col("prev_close").drop_nulls().last().alias("prev_close_panel"),
        pl.col("prev_close_day").drop_nulls().last().alias("prev_close_day"),
        pl.col("prev_close_source").drop_nulls().last().alias("prev_close_source"),
        pl.col("flag_prevclose_discrepancy").any().alias("flag_discrepancy"),
        pl.col("flag_nonpositive_px").any().alias("flag_nonpos"),
        pl.col("pit_listed").last().alias("pit_listed"),
        pl.col("session_end").drop_nulls().last().alias("session_end"),
    ])
    return u


def compact_prev(data_root: Path, prev_day: str) -> pl.DataFrame | None:
    p = data_root / "sip" / "universe" / "rth" / f"{prev_day}.parquet"
    if not p.exists():
        return None
    return pl.read_parquet(p, columns=["symbol", "c_last"]).rename(
        {"symbol": "ticker", "c_last": "prev_close_compact"})


def build_universe(day: str, prev_day: str, splits: pl.DataFrame, data_root: Path) -> pl.DataFrame:
    u = panel_universe(day, data_root)
    cp = compact_prev(data_root, prev_day)
    if u is None:
        u = pl.DataFrame(schema={"ticker": pl.Utf8, "prev_close_panel": pl.Float64,
                                 "prev_close_day": pl.Utf8, "prev_close_source": pl.Utf8,
                                 "flag_discrepancy": pl.Boolean, "flag_nonpos": pl.Boolean,
                                 "pit_listed": pl.Boolean, "session_end": pl.Int32})
    if cp is not None:
        u = u.join(cp, on="ticker", how="full", coalesce=True)
        u = u.with_columns(
            pl.when(pl.col("prev_close_panel").is_not_null()).then(pl.lit("panel"))
              .otherwise(pl.lit("compact")).alias("prev_used_src"))
        u = u.with_columns(pl.col("prev_close_panel").fill_null(pl.col("prev_close_compact")))
    else:
        u = u.with_columns(pl.lit("panel").alias("prev_used_src"))
    u = u.with_columns(pl.col("prev_close_panel").alias("prev_close_raw"))
    sf = split_map_for_day(day, prev_day, splits).rename({"symbol": "ticker"})
    u = u.join(sf, on="ticker", how="left").with_columns(
        pl.col("split_factor").fill_null(1.0),
        pl.col("split_events").fill_null(0).cast(pl.Int32))
    u = u.with_columns((pl.col("prev_close_raw") * pl.col("split_factor")).alias("prev_close_adj"))
    for c in ("flag_discrepancy", "flag_nonpos"):
        if c in u.columns:
            u = u.with_columns(pl.col(c).fill_null(False))
    if "pit_listed" in u.columns:
        u = u.with_columns(pl.col("pit_listed").fill_null(False))
    return u.drop_nulls("prev_close_raw")


def _variants(df: pl.DataFrame, has_first: bool = True) -> dict[str, pl.DataFrame]:
    base = df.filter(pl.col("prev_close_adj") > 0)
    out = {"raw": base}
    pri = base.filter(
        (~pl.col("flag_discrepancy")) & (~pl.col("flag_nonpos"))
        & (pl.col("gain_adj") <= GAIN_CEIL) & (pl.col("decision_px") >= PX_FLOOR))
    out["primary"] = pri
    out["listed"] = pri.filter(pl.col("pit_listed")) if "pit_listed" in pri.columns else pri
    return out


def _rows_for(day, clock, source, df, variant, extra):
    top = (df.sort(["gain_adj", "ticker"], descending=[True, False])
             .head(KEEP_RANKS).with_row_index("rank", offset=1))
    rows = []
    for r in top.to_dicts():
        row = {"day": day, "clock": clock, "source": source, "variant": variant,
               "rank": int(r["rank"]), "ticker": r["ticker"],
               "decision_px": float(r["decision_px"]), "decision_et": int(r["decision_et"]),
               "age_min": r.get("age_min"), "gain_adj": float(r["gain_adj"]),
               "gain_raw": float(r["gain_raw"]), "prev_close_raw": float(r["prev_close_raw"]),
               "prev_close_adj": float(r["prev_close_adj"]),
               "prev_used_src": r.get("prev_used_src"),
               "split_factor": float(r["split_factor"]),
               "split_event": bool((r.get("split_events") or 0) > 0),
               "flag_discrepancy": bool(r.get("flag_discrepancy")),
               "n_eligible": int(df.height),
               "rank_known": r.get("rank_known"), "rank_fresh": r.get("rank_fresh_2m"),
               "rank_unfiltered": r.get("rank_unfiltered"),
               "known_by_t": r.get("known_by_t"), "pit_listed": r.get("pit_listed")}
        row.update(extra)
        rows.append(row)
    return rows


def select_pm(day: str, snap: pl.DataFrame, u: pl.DataFrame) -> list[dict]:
    rows = []
    j = (snap.rename({"symbol": "ticker"})
         .join(u.select(["ticker", "prev_close_raw", "prev_close_adj", "prev_used_src",
                         "split_factor", "split_events", "flag_discrepancy", "flag_nonpos",
                         "pit_listed"]), on="ticker", how="inner"))
    for C in PM_CLOCKS:
        sub = j.filter(pl.col(f"px_{C}").is_not_null()).with_columns(
            pl.col(f"px_{C}").alias("decision_px"),
            pl.col(f"et_{C}").alias("decision_et"),
            (C - 1 - pl.col(f"et_{C}")).alias("age_min"),
            (pl.col(f"px_{C}") / pl.col("prev_close_adj") - 1).alias("gain_adj"),
            (pl.col(f"px_{C}") / pl.col("prev_close_raw") - 1).alias("gain_raw"))
        for variant, frame in _variants(sub).items():
            if frame.height:
                rows += _rows_for(day, C, "pm_snapshot", frame, variant, {})
    return rows


def select_rth(day: str, panel: pl.DataFrame, u: pl.DataFrame) -> tuple[list[dict], dict]:
    """Decision at clock C reads panel row t=C: px there is the close of the last
    completed bar with et <= C-1 (known by C), the panel's own as-of convention."""
    rows = []
    stats = {"n_panel_rows": int(panel.height)}
    at = panel.filter(pl.col("t").is_in(list(RTH_CLOCKS)) & pl.col("known_by_t")
                      & pl.col("px").is_not_null())
    base = at.join(u.select(["ticker", "prev_close_raw", "prev_close_adj", "prev_used_src",
                             "split_factor", "split_events", "flag_discrepancy",
                             "flag_nonpos", "pit_listed"]),
                   on="ticker", how="inner")
    for C in RTH_CLOCKS:
        sub = base.filter(pl.col("t") == C).with_columns(
            pl.col("px").alias("decision_px"),
            pl.col("px_et").alias("decision_et"),
            (pl.col("px") / pl.col("prev_close_adj") - 1).alias("gain_adj"),
            (pl.col("px") / pl.col("prev_close_raw") - 1).alias("gain_raw"))
        for variant, frame in _variants(sub).items():
            if frame.height:
                rows += _rows_for(day, C, "minute_full", frame, variant, {})
    return rows, stats


def process_day(day: str, data_root: Path, sessions: dict, splits: pl.DataFrame,
                force: bool) -> str:
    outd = data_root / "harvest01" / "base" / "selected"
    outd.mkdir(parents=True, exist_ok=True)
    mp = outd / f"{day}.manifest.json"
    if not force and mp.exists():
        try:
            if json.loads(mp.read_text()).get("status") == "ok":
                return f"{day}: skip"
        except Exception:
            pass
    t0 = time.time()
    prev_day = sessions.get(day)
    if not prev_day:
        return f"{day}: no previous session"
    u = build_universe(day, prev_day, splits, data_root)
    if u.height == 0:
        return f"{day}: empty universe"
    rows = []
    stats = {"prev_day": prev_day, "n_universe": int(u.height),
             "n_prev_panel": int(u.filter(pl.col("prev_used_src") == "panel").height)}

    snap_p = data_root / "sip" / "pm_snapshots" / f"{day}.parquet"
    if snap_p.exists():
        snap = pl.read_parquet(snap_p)
        rows += select_pm(day, snap, u)
        stats["pm_symbols"] = int(snap.height)
    else:
        stats["pm_symbols"] = None

    pf = (data_root / "atlas" / "observation" / "v0" / "race.minute_full"
          / f"month={day[:7]}" / f"{day}.parquet")
    champs_rows = 0
    if pf.exists():
        panel = pl.scan_parquet(pf).select(PANEL_COLS).collect()
        rrows, rst = select_rth(day, panel, u)
        rows += rrows
        stats.update(rst)
        # ---- day-level per-ticker outcome maxima (retrospective; anatomy only) ----
        key = panel.filter(pl.col("known_by_t") & pl.col("px").is_not_null())
        agg = key.group_by("ticker").agg(
            pl.col("px").max().alias("px_max"),
            pl.col("px_et").get(pl.col("px").arg_max()).alias("px_max_et"),
            pl.col("px").sort_by("t").last().alias("px_last"),
            pl.col("px_et").sort_by("t").last().alias("px_last_et"),
            pl.len().alias("n_known_min"),
            pl.col("t").min().alias("first_known_et"),
            pl.col("session_end").max().alias("session_end"),
        )
        agg = agg.join(u.select(["ticker", "prev_close_raw", "prev_close_adj", "split_factor",
                                 "split_events", "flag_discrepancy", "flag_nonpos", "pit_listed"]),
                       on="ticker", how="left")
        agg = agg.with_columns(
            (pl.col("px_max") / pl.col("prev_close_adj") - 1).alias("gain_max_adj"),
            (pl.col("px_last") / pl.col("prev_close_adj") - 1).alias("gain_last_adj"),
            (pl.col("px_max") / pl.col("prev_close_raw") - 1).alias("gain_max_raw"),
            pl.lit(day).alias("day"))
        champs_rows = int(agg.height)
        outd2 = data_root / "harvest01" / "base" / "champs"
        outd2.mkdir(parents=True, exist_ok=True)
        fp2, tmp2 = outd2 / f"{day}.parquet", outd2 / f"{day}.parquet.tmp"
        agg.write_parquet(tmp2)
        os.replace(tmp2, fp2)
    else:
        stats["n_panel_rows"] = None
    stats["champs_rows"] = champs_rows

    df = pl.DataFrame(rows) if rows else pl.DataFrame(
        schema={"day": pl.Utf8, "clock": pl.Int32, "source": pl.Utf8, "variant": pl.Utf8,
                "rank": pl.Int32, "ticker": pl.Utf8})
    fp, tmp = outd / f"{day}.parquet", outd / f"{day}.parquet.tmp"
    df.write_parquet(tmp)
    os.replace(tmp, fp)
    man = {"day": day, "status": "ok", "rows": int(df.height),
           "stats": stats, "elapsed_s": round(time.time() - t0, 1), "file": fp.name}
    mp.write_text(json.dumps(man, indent=1, default=str))
    return (f"{day}: ok sel={int(df.height)} univ={stats['n_universe']} "
            f"pm_syms={stats.get('pm_symbols')} el={man['elapsed_s']}s")


def _worker(a):
    day, data_root, prev_day, splits_path, force = a
    splits = split_factor_frame(Path(data_root))
    return process_day(day, Path(data_root), {day: prev_day}, splits, force)


def self_check(data_root: Path) -> int:
    snaps = sorted((data_root / "sip" / "pm_snapshots").glob("*.parquet"),
                   key=lambda p: p.stat().st_size, reverse=True)
    day = snaps[0].stem if snaps else "2021-02-01"
    sessions = prev_session_map(data_root)
    splits = split_factor_frame(data_root)
    process_day(day, data_root, sessions, splits, force=True)
    sel = pl.read_parquet(data_root / "harvest01" / "base" / "selected" / f"{day}.parquet")
    u = build_universe(day, sessions[day], splits, data_root).to_dicts()
    um = {r["ticker"]: r for r in u}
    snap = pl.read_parquet(data_root / "sip" / "pm_snapshots" / f"{day}.parquet").to_dicts()
    cand = []
    for r in snap:
        t = r["symbol"]
        if t in um and r["px_560"] is not None and um[t]["prev_close_adj"] > 0:
            g = r["px_560"] / um[t]["prev_close_adj"] - 1
            cand.append((g, t))
    best = sorted(cand, key=lambda x: (-x[0], x[1]))[:4]
    got = [(r["gain_adj"], r["ticker"]) for r in sel.filter(
        (pl.col("clock") == 560) & (pl.col("source") == "pm_snapshot")
        & (pl.col("variant") == "raw")).sort("rank").to_dicts()]
    same = len(best) == len(got) and all(
        abs(a[0] - b[0]) < 1e-9 and a[1] == b[1] for a, b in zip(best, got))
    print(f"self-check {day} pm560 raw naive==engine: {same}")
    if not same:
        print(" naive:", [(round(a, 6), b) for a, b in best])
        print(" got  :", [(round(a, 6), b) for a, b in got])
        return 1
    pf = (data_root / "atlas" / "observation" / "v0" / "race.minute_full"
          / f"month={day[:7]}" / f"{day}.parquet")
    if pf.exists():
        panel = pl.scan_parquet(pf).select(PANEL_COLS).collect()
        rows = panel.filter((pl.col("t") == 585) & pl.col("known_by_t")
                            & pl.col("px").is_not_null()).to_dicts()
        cand = [(r["px"] / um[r["ticker"]]["prev_close_adj"] - 1, r["ticker"])
                for r in rows if r["ticker"] in um and um[r["ticker"]]["prev_close_adj"] > 0]
        best = sorted(cand, key=lambda x: (-x[0], x[1]))[:4]
        got = [(r["gain_adj"], r["ticker"]) for r in sel.filter(
            (pl.col("clock") == 585) & (pl.col("source") == "minute_full")
            & (pl.col("variant") == "raw")).sort("rank").to_dicts()]
        same = len(best) == len(got) and all(
            abs(a[0] - b[0]) < 1e-9 and a[1] == b[1] for a, b in zip(best, got))
        print(f"self-check {day} rth585 raw naive==engine: {same}")
        if not same:
            print(" naive:", [(round(a, 6), b) for a, b in best])
            print(" got  :", [(round(a, 6), b) for a, b in got])
            return 1
    print("self-check OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day")
    ap.add_argument("--days", nargs="+", default=[])
    ap.add_argument("--dev-days", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--force-missing-pm", action="store_true",
                    help="re-run only days whose manifest has no PM snapshot yet")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()

    data_root = bps.resolve_data_root(args.data_root)
    if args.self_check:
        return self_check(data_root)

    sessions = prev_session_map(data_root)
    splits_path = data_root / "harvest01" / "base" / "splits.parquet"
    splits = split_factor_frame(data_root)
    days = list(args.days)
    if args.day:
        days.append(args.day)
    if args.dev_days:
        days = sorted(json.loads(bps.CALENDAR.read_text())["evidence"].keys())
    if args.limit:
        days = days[:args.limit]
    if args.force_missing_pm:
        outd = data_root / "harvest01" / "base" / "selected"
        keep = []
        for d in days:
            mp = outd / f"{d}.manifest.json"
            need = True
            if mp.exists():
                try:
                    stats = json.loads(mp.read_text()).get("stats") or {}
                    need = stats.get("pm_symbols") is None
                except Exception:
                    need = True
            if need:
                keep.append(d)
        days = keep
        args.force = True
    if not days:
        if args.force_missing_pm:
            print("nothing missing")
            return 0
        ap.print_help()
        return 2
    t0 = time.time()
    if args.workers <= 1:
        for d in days:
            print(process_day(d, data_root, sessions, splits, args.force), flush=True)
    else:
        import multiprocessing as mp
        tasks = [(d, str(data_root), sessions.get(d), str(splits_path), args.force) for d in days]
        with mp.get_context("spawn").Pool(processes=args.workers) as pool:
            for line in pool.imap_unordered(_worker, tasks):
                print(line, flush=True)
    print(f"done {len(days)} days in {round(time.time() - t0, 1)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
