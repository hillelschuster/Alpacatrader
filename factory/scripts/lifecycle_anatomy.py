#!/usr/bin/env python3
"""LIFECYCLE-01 v2 — behavior-first anatomy of the top-gainer roster (no models, no policy).

Reads the corrected v2 substrate via lifecycle_study; discovery = immutable first-533 split
only. Validation is never offered before a freeze marker exists.

Per clock (540/560/569 PM, 571 near-open) it writes under
<data>/harvest01/lifecycle/v2/report/:
  anatomy_cohort_<which>_<clock>       transparent descriptors: monster / runner /
                                       transient_spike / recovered_flush / exhaustion / dud
  anatomy_route_<which>_<clock>        full minute route, tenure, wall clock, occupancy +
                                       member-balanced
  anatomy_horizons_<which>_<clock>     E[V_h] h=1..120, occupancy / member-balanced /
                                       event-first, censoring, day-block CI
  anatomy_pullback_<which>_<clock>     dd_from_high=px/runhigh-1 buckets; at-risk forward
                                       reclaim/death; event-aligned healthy vs terminal
  anatomy_separation_<which>_<clock>   timeline AUC predicting FUTURE legs/continuation
  anatomy_strongest_<which>_<clock>    exact names/days/transition minutes + $ opportunity
  anatomy_fine_<which>_<clock>         5/10-second actual-case view (when tape10 coverage exists)
Plus one cross-clock file:
  anatomy_pm_<which>                   PM vs near-open, same gain anchor, not pooled
  anatomy_evidence_<which>.json        compact machine-readable evidence

Descriptors are RULERS, never predictors. drawdown is signed negative. Reclaim/death use the
supplied OHLC-derived fmfe/fmae vs the running high/low at t — never closes and never
pre-entry MAE. Execution fields (sell_px/sell_et/sell_volume) are excluded from predictors.

Usage:
    python factory/scripts/lifecycle_anatomy.py --stage all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# bound native threads before numpy/polars import (full-run resource discipline)
for _v in ("POLARS_MAX_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402
import lifecycle_study as ls  # noqa: E402

# explicit causal predictor allowlist; execution fields and labels excluded
FEATURES = [f for f in (ls.PANEL_FEATS + ls.PM_FEATS + ls.RACE_FEATS + ls.PEER_FEATS + ls.TAPE_FEATS)
            if f not in ls.EXEC_COLS and f not in ls.LABEL_COLS]
NOTIONAL = 1000.0


def _atomic_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _atomic_parquet(path: Path, df: pl.DataFrame) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp)
    os.replace(tmp, path)


def _pct(x, digits: int = 1) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.{digits}f}%"


def _num(x, digits: int = 2) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.{digits}f}"


def _filled(df: pl.DataFrame) -> pl.DataFrame:
    for c in ("filled_asof", "filled"):
        if c in df.columns:
            return df.filter(pl.col(c))
    return df


def _fin(name: str) -> pl.Expr:
    return ls.fin(name)


# --------------------------------------------------------------------- cohort
def stage_cohort(df: pl.DataFrame, out: Path, tag: str) -> dict:
    d = ls.descriptor_table(df)
    g = (d.group_by("descriptor")
         .agg(pl.len().alias("n"), _fin("mfe").median().alias("mfe_med"),
              _fin("captured").median().alias("cap_med"),
              _fin("max_dd").median().alias("dd_med"),
              _fin("mfe").mean().alias("mfe_mean"),
              _fin("captured").mean().alias("cap_mean"))
         .sort("n", descending=True))
    lines = [f"# LIFECYCLE-01 v2 anatomy — cohort ({tag})", "",
             "Descriptors are transparent rulers over MFE and the executable captured return;",
             "they are NOT predictors.", "",
             "priority: monster -> runner -> transient_spike -> recovered_flush -> exhaustion -> dud -> mid", ""]
    rows = [[r["descriptor"], str(r["n"]), _pct(r["mfe_med"]), _pct(r["cap_med"]),
             _pct(r["dd_med"]), _pct(r["mfe_mean"]), _pct(r["cap_mean"])]
            for r in g.iter_rows(named=True)]
    lines += ls.md_table(rows, ["descriptor", "n", "MFE med", "captured med", "maxDD med",
                                "MFE mean", "captured mean"])
    ev = {}
    for name, sub in (("tail_captured", d.filter(pl.col("descriptor") == "monster")),
                      ("dud_captured", d.filter(pl.col("descriptor") == "dud"))):
        ev[name] = ls.day_block_ci(sub, "captured")
    if ev["tail_captured"]["mean"] is not None and ev["dud_captured"]["mean"] is not None:
        ev["tail_minus_dud"] = ev["tail_captured"]["mean"] - ev["dud_captured"]["mean"]
    lines += ["", f"- E[captured | monster] = {_pct(ev['tail_captured']['mean'])} "
                  f"(day-block 95% CI {_pct(ev['tail_captured']['lo'])}..{_pct(ev['tail_captured']['hi'])}, "
                  f"{ev['tail_captured']['n_days']}d)",
              f"- E[captured | dud] = {_pct(ev['dud_captured']['mean'])} "
              f"(day-block 95% CI {_pct(ev['dud_captured']['lo'])}..{_pct(ev['dud_captured']['hi'])}, "
              f"{ev['dud_captured']['n_days']}d)",
              f"- tail-minus-dud = {_pct(ev.get('tail_minus_dud'))}"]
    _atomic_parquet(out / f"anatomy_cohort_{tag}.parquet", g)
    _atomic_text(out / f"anatomy_cohort_{tag}.md", "\n".join(lines) + "\n")
    print(f"cohort -> {out / f'anatomy_cohort_{tag}.md'}")
    return ev


# --------------------------------------------------------------------- route
def stage_route(df: pl.DataFrame, out: Path, tag: str, fine_until: int = 780) -> None:
    desc = ls.descriptor_table(df).select(["day", "clock", "rank", "ticker", "descriptor"])
    j = df.join(desc, on=["day", "clock", "rank", "ticker"], how="left")
    grid = j.filter((pl.col("t") <= fine_until) | (pl.col("t") % 5 == 0))
    rt = (grid.group_by(["descriptor", "t"])
          .agg(_fin("ret_fill").median().alias("ret"),
               _fin("dd_from_high").median().alias("dd"),
               _fin("peak_gain").median().alias("pk"),
               _fin("tenure").median().alias("ten"),
               _fin("px_age").median().alias("gap"),
               pl.len().alias("occ"))
          .sort(["descriptor", "t"]))
    coarse = grid.filter(pl.col("t") % 10 == 0)
    mb = (coarse.group_by(["descriptor", "t", "day", "clock", "rank", "ticker"])
          .agg(_fin("ret_fill").median().alias("mret"))
          .group_by(["descriptor", "t"]).agg(_fin("mret").median().alias("ret_mb"),
                                             pl.len().alias("n_members"))
          .sort(["descriptor", "t"]))
    rt = rt.join(mb, on=["descriptor", "t"], how="left")
    lines = [f"# LIFECYCLE-01 v2 anatomy — route ({tag})", "",
             "Full minute route (1-min to 13:00, 5-min after). ret=px/entry-1;",
             "dd=px/running_high-1 (negative); pk=running_high/entry-1. occ=member-minutes;",
             "ret_mb=member-balanced median (each name-day equal).", ""]
    for dsc in ls.DESCRIPTORS:
        s = rt.filter(pl.col("descriptor") == dsc)
        if s.height == 0:
            continue
        lines.append(f"\n## {dsc}")
        rows = []
        tvals = list(range(int(s["t"].min()), fine_until + 1, 10))
        tvals += [t for t in range(790, 961, 30) if t <= int(s["t"].max())]
        for t in tvals:
            r = s.filter(pl.col("t") == t)
            if r.height == 0:
                continue
            rows.append([ls.wall_clock(t), str(t), _pct(r["ret"][0]), _pct(r["dd"][0]),
                         _pct(r["pk"][0]), _num(r["ten"][0], 0), _num(r["gap"][0], 0),
                         str(int(r["occ"][0])), _pct(r["ret_mb"][0])])
        lines += ls.md_table(rows, ["wall", "t", "ret", "dd", "peak_gain", "tenure", "px_age",
                                    "occ", "ret_mb"])
    _atomic_parquet(out / f"anatomy_route_{tag}.parquet", rt)
    _atomic_text(out / f"anatomy_route_{tag}.md", "\n".join(lines) + "\n")
    print(f"route -> {out / f'anatomy_route_{tag}.md'}")


# --------------------------------------------------------------------- horizons
def stage_horizons(df: pl.DataFrame, out: Path, tag: str) -> dict:
    ev: dict = {"horizons": {}}
    rows = []
    first = (df.filter(pl.col("ret_fill") >= 0.10)
             .group_by(["day", "clock", "rank", "ticker"]).agg(pl.col("t").min().alias("t_leg")))
    for h in ls.HORIZONS:
        v = f"V{h}"
        if v not in df.columns:
            continue
        allrows = df.select(["day", "clock", "rank", "ticker", "t", "session_end"])
        cens_share = float((allrows["t"] + h > allrows["session_end"]).mean())
        sub = (df.select(["day", "clock", "rank", "ticker", "t", "session_end", v])
               .filter(pl.col(v).is_finite()))
        if sub.height == 0:
            continue
        occ = float(sub[v].mean())
        ci = ls.day_block_ci(sub, v)
        mb = ls.member_balanced_mean(sub, v)
        ev_sub = (sub.join(first, on=["day", "clock", "rank", "ticker"], how="inner")
                  .filter(pl.col("t") == pl.col("t_leg")))
        ef_mean = float(ev_sub[v].mean()) if ev_sub.height else None
        ev["horizons"][h] = {"occ_mean": occ, "day_mean": ci["mean"], "day_lo": ci["lo"],
                             "day_hi": ci["hi"], "n_days": ci["n_days"], "member_balanced": mb,
                             "event_first_n": ev_sub.height, "event_first_mean": ef_mean,
                             "censored_share": cens_share, "n_rows": sub.height}
        rows.append([str(h), _pct(occ), _pct(ci["mean"]), _pct(mb), str(ev_sub.height),
                     _pct(ef_mean), _pct(cens_share), str(ci["n_days"])])
    lines = [f"# LIFECYCLE-01 v2 anatomy — continuation rulers ({tag})", "",
             "CONTINUATION RULER E[V_h] = value of NOT releasing for h more minutes vs releasing at",
             "sell-now. Holding is a measuring stick only: the thesis is a WINDOW of strength,",
             "never a session-long hold. Weightings kept separate: occupancy",
             "(member-minutes pooled), member-balanced (each name-day equal), event-first (one",
             "row per member at its first +10% leg). Day-block CI over days; overlapping rows are",
             "not independent n. censored = t+h beyond session_end (missing, never zeroed).", ""]
    lines += ls.md_table(rows, ["h", "occ E[V_h]", "day-mean", "member-balanced",
                                "event-first n", "event-first E[V_h]", "censored share", "days"])
    _atomic_parquet(out / f"anatomy_horizons_{tag}.parquet",
                    pl.DataFrame([{"h": h, **v} for h, v in ev["horizons"].items()]))
    _atomic_text(out / f"anatomy_horizons_{tag}.md", "\n".join(lines) + "\n")
    print(f"horizons -> {out / f'anatomy_horizons_{tag}.md'}")
    return ev


# --------------------------------------------------------------------- pullback / reclaim
def stage_pullback(df: pl.DataFrame, out: Path, tag: str, fine_until: int = 780) -> dict:
    d = df.filter((pl.col("t") >= 575) & (pl.col("t") <= fine_until))
    d = d.with_columns([
        pl.when(pl.col("dd_from_high") > -0.02).then(pl.lit("0-2"))
        .when(pl.col("dd_from_high") > -0.05).then(pl.lit("2-5"))
        .when(pl.col("dd_from_high") > -0.10).then(pl.lit("5-10"))
        .when(pl.col("dd_from_high") > -0.20).then(pl.lit("10-20"))
        .otherwise(pl.lit("20+")).alias("ddb"),
        pl.when(pl.col("ret_fill") > 0.10).then(pl.lit("up10+"))
        .when(pl.col("ret_fill") > 0).then(pl.lit("up0-10"))
        .otherwise(pl.lit("down")).alias("levelb")])
    aggs = [pl.len().alias("n"), _fin("reclaim60").mean().alias("rec60"),
            _fin("death60").mean().alias("dth60"), _fin("V60").mean().alias("v60"),
            _fin("V60").median().alias("v60m")]
    for h in ls.TAIL_H:
        aggs.append(_fin(f"fmfe{h}").mean().alias(f"mfe{h}"))
        aggs.append(_fin(f"fmae{h}").mean().alias(f"mae{h}"))
    g = d.group_by(["levelb", "ddb"]).agg(aggs).sort(["levelb", "ddb"])
    lines = [f"# LIFECYCLE-01 v2 anatomy — pullback health ({tag})", "",
             "dd = px/running_high - 1 (SIGNED NEGATIVE; the old scripts binned a positive",
             "difference against negative thresholds). reclaim60 = the future high within 60m",
             "exceeds the running high at t; death60 = the future low undercuts the running low",
             "at t (never pre-entry MAE).", ""]
    rows = []
    for r in g.iter_rows(named=True):
        if r["n"] < 50:
            continue
        rows.append([r["levelb"], r["ddb"], str(r["n"]), _pct(r["rec60"], 0), _pct(r["dth60"], 0),
                     _pct(r["v60"]), _pct(r["v60m"]), _pct(r["mfe60"]), _pct(r["mae60"])])
    lines += ls.md_table(rows, ["level", "drawdown", "n", "reclaim60", "death60", "E[V60]",
                                "med V60", "fMFE60", "fMAE60"])
    ev_rows, align, n_censored = _event_aligned(df, thresh=-0.15, pre=10, post=30)
    lines += ["", "## event-aligned pullback (first cross below -15%)", "",
              "k = minutes from the event; healthy = a new high within 60m, terminal = a new low",
              "or neither; censored (reclaim60 not finite) is counted separately and excluded",
              "from the aligned curves (never silently called terminal).", ""]
    arows = [[grp, str(k), _pct(v[0]), _pct(v[1]), str(v[2])]
             for (grp, k), v in sorted(align.items()) if k % 5 == 0]
    lines += ls.md_table(arows, ["group", "k", "median ret_fill", "median dd", "n"])
    fin_rec = [e["reclaim60"] for e in ev_rows if e.get("reclaim60") is not None]
    ev = {"reclaim_by_dd": {f"{r['levelb']}|{r['ddb']}": {"n": r["n"], "rec60": r["rec60"]}
                            for r in g.iter_rows(named=True) if r["n"] >= 50},
          "n_pullback_events": len(ev_rows),
          "n_censored_events": n_censored,
          "healthy_share": (float(np.mean(fin_rec)) if fin_rec else None)}
    lines += ["", f"- pullback events = {len(ev_rows)}; censored (reclaim60 not finite) = "
                  f"{n_censored}; healthy share (non-censored) = "
                  f"{_pct(ev['healthy_share']) if ev['healthy_share'] is not None else 'n/a'}"]
    _atomic_parquet(out / f"anatomy_pullback_{tag}.parquet", g)
    _atomic_text(out / f"anatomy_pullback_{tag}.md", "\n".join(lines) + "\n")
    print(f"pullback -> {out / f'anatomy_pullback_{tag}.md'}")
    return ev


def _event_aligned(df: pl.DataFrame, thresh: float, pre: int, post: int):
    align: dict = {}
    ev_rows = []
    n_censored = 0
    need = ["ret_fill", "dd_from_high", "reclaim60", "t", "day", "clock", "rank", "ticker"]
    if any(c not in df.columns for c in need):
        return ev_rows, align, n_censored
    for key, sub in df.group_by(["day", "clock", "rank", "ticker"]):
        s = sub.sort("t")
        t = s["t"].to_numpy()
        dd = s["dd_from_high"].to_numpy().astype(float)
        idx = np.where(np.isfinite(dd) & (dd <= thresh))[0]
        if idx.size == 0:
            continue
        i = int(idx[0])
        rec = s["reclaim60"][i]
        if rec is None or not np.isfinite(rec):
            # censored (reclaim60 not finite) -> NOT classified terminal, excluded from curves
            n_censored += 1
            ev_rows.append({"day": key[0], "clock": key[1], "rank": key[2], "ticker": key[3],
                            "event_t": int(t[i]), "reclaim60": None, "censored": True})
            continue
        grp = "healthy" if rec == 1 else "terminal"
        ev_rows.append({"day": key[0], "clock": key[1], "rank": key[2], "ticker": key[3],
                        "event_t": int(t[i]), "reclaim60": float(rec), "censored": False})
        for k in range(-pre, post + 1):
            j = i + k
            if 0 <= j < len(t):
                ret = s["ret_fill"][j]
                ddv = dd[j]
                if ret is None or not np.isfinite(ret) or not np.isfinite(ddv):
                    continue
                acc = align.setdefault((grp, k), [0.0, 0.0, 0])
                acc[0] += float(ret); acc[1] += float(ddv); acc[2] += 1
    for v in align.values():
        if v[2]:
            v[0] /= v[2]; v[1] /= v[2]
    return ev_rows, align, n_censored


# --------------------------------------------------------------------- separation
def stage_separation(df: pl.DataFrame, out: Path, tag: str, grid=(600, 660, 720, 780)) -> dict:
    feats = [f for f in FEATURES if f in df.columns]
    ev: dict = {"grid": list(grid), "targets": ["leg60", "cont60"], "features": feats, "cells": []}
    lines = [f"# LIFECYCLE-01 v2 anatomy — separation timeline ({tag})", "",
             "AUC of causal state at t for FUTURE targets (sign reported):",
             "  leg60 = fmfe60 >= +30% (a future leg from sell-now)",
             "  cont60 = V60 > 0 (the next 60 minutes of not-yet-released capital beat releasing now).",
             "Null targets (one class / too few finite rows) are skipped; a table only emits with",
             "enough rows. Not day-MFE (past wins would predict a past label trivially).", ""]
    best: dict = {}
    min_days = 5
    for t in grid:
        s = df.filter(pl.col("t") == t)
        if s.height < 200:
            continue
        y_leg = s["leg60"].to_numpy() if "leg60" in s.columns else None
        y_cont = None
        if "V60" in s.columns:
            y_cont = s.select(pl.when(pl.col("V60").is_finite())
                              .then((pl.col("V60") > 0).cast(pl.Int32))
                              .otherwise(None)).to_series().to_numpy()
        days_np = s["day"].to_numpy()
        for f in feats:
            x = s[f].to_numpy().astype(float)
            for tname, y in (("leg60", y_leg), ("cont60", y_cont)):
                if y is None:
                    continue
                y = np.asarray(y, dtype=float)
                mask = np.isfinite(x) & np.isfinite(y)
                n = int(mask.sum())
                n_days = int(np.unique(days_np[mask]).size)
                if n_days < min_days:
                    continue
                a = ls.auc_safe(y[mask], x[mask])
                if a is None:
                    continue
                ev["cells"].append({"t": t, "feat": f, "target": tname, "auc": a,
                                    "n": n, "n_days": n_days})
                key = (tname, f)
                if key not in best or abs(a - 0.5) > abs(best[key][0] - 0.5):
                    best[key] = (a, n_days)
    rows = []
    for (tname, f), (a, nd) in sorted(best.items(), key=lambda kv: -abs(kv[1][0] - 0.5)):
        if abs(a - 0.5) < 0.01:
            continue
        rows.append([tname, f, f"{a:.3f}", "+" if a > 0.5 else "-", str(nd)])
        if len(rows) >= 30:
            break
    lines += ls.md_table(rows, ["target", "feature", "AUC", "sign (higher feature -> target)",
                                "days"])
    _atomic_parquet(out / f"anatomy_separation_{tag}.parquet", pl.DataFrame(ev["cells"]))
    _atomic_text(out / f"anatomy_separation_{tag}.md", "\n".join(lines) + "\n")
    print(f"separation -> {out / f'anatomy_separation_{tag}.md'}")
    return ev


# --------------------------------------------------------------------- PM vs near-open
def stage_pm(roster: pl.DataFrame, out: Path, tag: str) -> dict:
    """PM vs near-open under the SAME gain anchor (gain vs prev close), computed from the
    roster label table ONLY (no full minute frames). Ranks and the control set are NOT pooled."""
    if roster.height == 0:
        _atomic_text(out / f"anatomy_pm_{tag}.md", f"# LIFECYCLE-01 v2 anatomy — PM ({tag})\n\n_no roster rows_\n")
        return {"clocks": []}
    d = roster.with_columns((pl.col("session_end") - pl.col("clock")).alias("tenure_to_close"))
    g = (d.group_by("clock")
         .agg(pl.len().alias("n_rows"), pl.col("day").n_unique().alias("n_days"),
              _fin("gain_adj").median().alias("gain_adj_med"),
              _fin("mfe_day").median().alias("peak_med"),
              _fin("mae_day").median().alias("dd_min_med"),
              _fin("Rclose").median().alias("ret_close_med"),
              _fin("tenure_to_close").median().alias("tenure_med"),
              _fin("fill_et").median().alias("fill_et_med"),
              _fin("decision_et").median().alias("decision_et_med"))
         .sort("clock"))
    lines = [f"# LIFECYCLE-01 v2 anatomy — PM vs near-open ({tag})", "",
             "Same gain anchor (gain vs previous actual close); ranks/control are NOT pooled.",
             "Computed from the roster label table only. wall = median entry wall clock;",
             "tenure = median minutes from clock to session end.", ""]
    rows = [[str(r["clock"]),
             ls.wall_clock(int(r["fill_et_med"])) if r["fill_et_med"] is not None else "n/a",
             str(r["n_rows"]), str(r["n_days"]), _pct(r["gain_adj_med"]), _pct(r["ret_close_med"]),
             _pct(r["peak_med"]), _pct(r["dd_min_med"]), _num(r["tenure_med"], 0)]
            for r in g.iter_rows(named=True)]
    lines += ls.md_table(rows, ["clock", "entry wall", "rows", "days", "gain_adj med",
                                "close ret med", "peak med", "min dd med", "tenure med"])
    ev = {"clocks": [{"clock": r["clock"], "n_rows": r["n_rows"], "n_days": r["n_days"],
                      "gain_adj_med": r["gain_adj_med"], "ret_close_med": r["ret_close_med"],
                      "peak_med": r["peak_med"], "dd_min_med": r["dd_min_med"],
                      "tenure_med": r["tenure_med"]} for r in g.iter_rows(named=True)]}
    _atomic_parquet(out / f"anatomy_pm_{tag}.parquet", g)
    _atomic_text(out / f"anatomy_pm_{tag}.md", "\n".join(lines) + "\n")
    print(f"pm -> {out / f'anatomy_pm_{tag}.md'}")
    return ev


# --------------------------------------------------------------------- strongest paths
def stage_strongest(df: pl.DataFrame, out: Path, tag: str, top: int = 25) -> dict:
    d = ls.descriptor_table(df)
    first = (df.filter(pl.col("ret_fill") >= 0.10)
             .group_by(["day", "clock", "rank", "ticker"]).agg(pl.col("t").min().alias("t_leg")))
    d = d.join(first, on=["day", "clock", "rank", "ticker"], how="left")
    if "t_peak_day" in df.columns and "fill_et" in df.columns:
        tp = (df.group_by(["day", "clock", "rank", "ticker"])
              .agg(pl.col("t_peak_day").first().alias("t_peak_day"),
                   pl.col("fill_et").first().alias("fill_et")))
        d = d.join(tp, on=["day", "clock", "rank", "ticker"], how="left")
    if "t_peak" in df.columns:
        pk = (df.group_by(["day", "clock", "rank", "ticker"])
              .agg((pl.col("t") + pl.col("t_peak")).min().alias("peak_abs")))
        d = d.join(pk, on=["day", "clock", "rank", "ticker"], how="left")
    top_rows = d.filter(pl.col("mfe") >= 0.30).sort("mfe", descending=True).head(top)
    rows = []
    for r in top_rows.iter_rows(named=True):
        t_leg = r.get("t_leg")
        peak_abs = None
        if (r.get("t_peak_day") is not None and np.isfinite(r["t_peak_day"])
                and r.get("fill_et") is not None and np.isfinite(r["fill_et"])):
            peak_abs = r["fill_et"] + r["t_peak_day"]          # roster label: entry + t_peak_day
        elif r.get("peak_abs") is not None and np.isfinite(r["peak_abs"]):
            peak_abs = r["peak_abs"]                            # panel: t + t_peak (constant)
        peak_wall = ls.wall_clock(int(peak_abs)) if peak_abs is not None else "n/a"
        cap = r.get("captured")
        rows.append([r["day"], str(r["clock"]), str(r["rank"]), r["ticker"], _pct(r["mfe"]),
                     _pct(cap),
                     ls.wall_clock(int(t_leg)) if t_leg is not None else "n/a", peak_wall,
                     f"${cap * NOTIONAL:+.0f}" if (cap is not None and np.isfinite(cap)) else "n/a"])
    lines = [f"# LIFECYCLE-01 v2 anatomy — strongest paths ({tag})", "",
             "Exact names/days; t_leg = minute the first +10% leg printed; peak = minute of the",
             "session high (roster entry_et + t_peak_day, else panel t + t_peak).",
             "$ opportunity = captured EXECUTABLE return (Rclose) per $1000; n/a when unknown.", ""]
    lines += ls.md_table(rows, ["day", "clock", "rank", "ticker", "MFE", "captured",
                                "t_leg wall", "peak wall", "$ per $1k"])
    opp = d.with_columns((pl.col("captured") * NOTIONAL).alias("dollars"))
    ev = {"dollar_opportunity": ls.day_block_ci(opp, "dollars"),
          "n_monsters": int((d["descriptor"] == "monster").sum()),
          "n_runners": int((d["descriptor"] == "runner").sum()),
          "n_duds": int((d["descriptor"] == "dud").sum())}
    do = ev["dollar_opportunity"]
    lines += ["", f"- E[$ per $1k captured] = {_num(do['mean'], 0)} "
                  f"(day-block 95% CI {_num(do['lo'], 0)}..{_num(do['hi'], 0)}, {do['n_days']}d)"]
    _atomic_parquet(out / f"anatomy_strongest_{tag}.parquet", top_rows)
    _atomic_text(out / f"anatomy_strongest_{tag}.md", "\n".join(lines) + "\n")
    print(f"strongest -> {out / f'anatomy_strongest_{tag}.md'}")
    return ev


# --------------------------------------------------------------------- fine 10s cases
def stage_fine(df: pl.DataFrame, days: list[str], data_root: Path, out: Path, tag: str,
               top: int = 3) -> dict:
    """Actual-case view at 5/10-second resolution around the transition minute, when tape
    coverage exists. Bucket b covers [b, b+div); causal at decision second t*60 iff
    avail_et_s <= t*60. no_data rows are NOT activity. Retains the full minute path too."""
    t10 = ls.load_tape10(days, data_root)
    div = 10
    if t10.height == 0:
        t10 = ls.load_tape5(days, data_root)
        div = 5
    if t10.height == 0:
        _atomic_text(out / f"anatomy_fine_{tag}.md",
                     f"# LIFECYCLE-01 v2 anatomy — fine cases ({tag})\n\n"
                     "_no 5/10-second tape coverage for these days_\n")
        print(f"fine -> {out / f'anatomy_fine_{tag}.md'} (no coverage)")
        return {"available": False, "cases": 0}
    want = ["tr_n_all", "tr_dvol_all", "tr_first_px", "tr_last_px", "tr_max_px", "tr_min_px",
            "tr_max_interarrival_s", "q_spread_bps_med", "q_imb_last", "q_bid_shares_last",
            "q_ask_shares_last", "no_data", "has_print", "has_quote"]
    cols = [c for c in want if c in t10.columns]
    d = ls.descriptor_table(df)
    first = (df.filter(pl.col("ret_fill") >= 0.10)
             .group_by(["day", "clock", "rank", "ticker"]).agg(pl.col("t").min().alias("t_leg")))
    d = d.join(first, on=["day", "clock", "rank", "ticker"], how="left").drop_nulls("t_leg")
    tops = d.sort("mfe", descending=True).head(top)
    lines = [f"# LIFECYCLE-01 v2 anatomy — fine cases ({tag}, {div}s buckets)", "",
             "Buckets around the first +10% leg (t_leg). causal@t_leg = avail_et_s <= t_leg*60.",
             "no_data rows are NOT trade activity. Real tape only; full minute path retained.", ""]
    n_cases = 0
    for r in tops.iter_rows(named=True):
        tl = int(r["t_leg"])
        sec = tl * 60
        # full minute path around t_leg
        path = (df.filter((pl.col("day") == r["day"]) & (pl.col("clock") == r["clock"]) &
                          (pl.col("rank") == r["rank"]) & (pl.col("ticker") == r["ticker"]) &
                          (pl.col("t") >= tl - 15) & (pl.col("t") <= tl + 15)).sort("t"))
        lines.append(f"\n## {r['day']} {r['ticker']} (clock {r['clock']} rank {r['rank']}) "
                     f"MFE {_pct(r['mfe'])} t_leg {ls.wall_clock(tl)}")
        prows = [[ls.wall_clock(int(x["t"])), _pct(x["ret_fill"]), _pct(x["dd_from_high"]),
                  _pct(x["peak_gain"]), _num(x["sell_px"], 3)] for x in path.iter_rows(named=True)]
        lines += ["", "minute path:"] + ls.md_table(prows, ["wall", "ret_fill", "dd", "peak_gain", "sell_px"])
        sub = (t10.filter((pl.col("day") == r["day"]) & (pl.col("ticker") == r["ticker"]) &
                          (pl.col("b") >= sec - 600) & (pl.col("b") <= sec + 600)).sort("b"))
        rows = []
        for x in sub.iter_rows(named=True):
            b = int(x["b"])
            wall = f"{b // 3600:02d}:{(b % 3600) // 60:02d}:{b % 60:02d}"
            causal = "Y" if x.get("avail_et_s", b + div) <= sec else "n"
            rows.append([wall, causal] + [
                _num(x.get(c), 3) if isinstance(x.get(c), (int, float)) else str(x.get(c))
                for c in cols])
        lines += ["", f"{div}s buckets:"] + ls.md_table(rows, ["wall", "causal@leg"] + cols)
        n_cases += 1
    _atomic_text(out / f"anatomy_fine_{tag}.md", "\n".join(lines) + "\n")
    print(f"fine -> {out / f'anatomy_fine_{tag}.md'}")
    return {"available": True, "cases": n_cases, "div": div}


# --------------------------------------------------------------------- main
def _source_sha() -> str:
    h = hashlib.sha256()
    for f in ("lifecycle_anatomy.py", "lifecycle_study.py"):
        p = Path(__file__).resolve().parent / f
        h.update(p.read_bytes())
    return h.hexdigest()


def _provenance(split: dict, all_days: list[str], days: list[str], data_root: Path,
                which: str, clocks: tuple) -> dict:
    man = ls.load_manifest(data_root)
    return {
        "which": which,
        "clocks": list(clocks),
        "requested_days_total": len(all_days),
        "requested_days": all_days,
        "analyzed_days": len(days),
        "limited": len(days) != len(all_days),
        "calendar_sha256": split.get("calendar_sha256"),
        "corpus_schema_version": (man or {}).get("schema_version"),
        "tape_producer_version": ls.TAPE_PRODUCER,
        "anatomy_source_sha256": _source_sha(),
        "label_scope": "discovery first-half only; no validation outcomes read",
        "rule_first_eligible": len(days) == len(all_days),
        "captured_basis": "executable Rclose only; ret_last kept as mark_ret (not captured)",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all")
    ap.add_argument("--clocks", default=",".join(str(c) for c in ls.CLOCKS))
    ap.add_argument("--which", default="discovery")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    split = ls.load_split(data_root)
    all_days = (ls.validation_days(data_root, allow=True) if args.which == "validation"
                else split["discovery_days"])
    days = all_days[:args.limit] if args.limit else all_days
    clocks = tuple(int(x) for x in args.clocks.split(","))
    out = ls.v2_dir(data_root) / "report"
    out.mkdir(parents=True, exist_ok=True)
    prov = _provenance(split, all_days, days, data_root, args.which, clocks)
    # PM cross-clock summary reads the roster label table only (no full minute frames)
    if args.stage == "pm":
        ros = ls.load_roster(days, data_root, clocks=clocks)
        ev = {"provenance": prov, "pm": stage_pm(ros, out, args.which)}
        _atomic_text(out / f"anatomy_evidence_pm_{args.which}.json",
                     json.dumps(ev, indent=2, default=float))
        print(f"evidence -> {out / f'anatomy_evidence_pm_{args.which}.json'}")
        return 0
    raw = ls.load_panel(days, data_root, clocks=clocks)
    census = (raw.group_by(["clock", "status"]).agg(pl.len().alias("rows")).sort(["clock", "status"])
              if "status" in raw.columns else pl.DataFrame())
    df = ls.add_forward_path(_filled(raw))
    evidence: dict = {"provenance": prov, "panel_rows_all": raw.height,
                      "panel_rows_filled": df.height, "frozen": ls.frozen(data_root),
                      "census": census.to_dicts(), "by_clock": {}}
    for c in clocks:
        dc = df.filter(pl.col("clock") == c)
        if dc.height == 0:
            continue
        tag = f"{args.which}_{c}"
        ev: dict = {"rows": dc.height, "provenance": prov}
        if args.stage in ("all", "cohort"):
            ev["cohort"] = stage_cohort(dc, out, tag)
        if args.stage in ("all", "route"):
            stage_route(dc, out, tag)
        if args.stage in ("all", "horizons"):
            ev["horizons"] = stage_horizons(dc, out, tag)
        if args.stage in ("all", "pullback"):
            ev["pullback"] = stage_pullback(dc, out, tag)
        if args.stage in ("all", "separation"):
            ev["separation"] = stage_separation(dc, out, tag)
        if args.stage in ("all", "strongest"):
            ev["strongest"] = stage_strongest(dc, out, tag)
        if args.stage in ("all", "fine"):
            ev["fine"] = stage_fine(dc, days, data_root, out, tag)
        evidence["by_clock"][str(c)] = ev
    if args.stage == "all" and len(clocks) > 1:
        ros = ls.load_roster(days, data_root, clocks=clocks)
        evidence["pm"] = stage_pm(ros, out, args.which)
    _atomic_text(out / f"anatomy_evidence_{args.which}.json",
                 json.dumps(evidence, indent=2, default=float))
    print(f"evidence -> {out / f'anatomy_evidence_{args.which}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
