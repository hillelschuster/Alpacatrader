#!/usr/bin/env python3
"""LIFECYCLE-01 v2 — study loader + shared behavior-first statistics (no models).

Reads the corrected v2 substrate only:
  <data>/harvest01/lifecycle/v2/{universe,roster,panel}/<day>.parquet
  <data>/harvest01/lifecycle/v2/manifest.json       (global schema/source/counts)
  <data>/harvest01/lifecycle/v2/_done/<day>.json    (per-day atomic marker; status ok|empty)
  <data>/harvest01/lifecycle/v2/split.json          (READ-ONLY immutable split)

Split discipline
----------------
  * v2/split.json is the immutable split; calendar_sha256 verified against the worktree
    session calendar. discovery = first 533 dev days, validation = remaining 533.
  * validation outcomes are NOT loadable until v2/FREEZE.json exists.
  * coverage is strict: a requested day with no _done marker (status ok|empty) is a hard
    error; a status=ok day with no panel file is a hard error. Never silently analyze a
    subset of what happens to exist.

Frozen panel schema (one row per fixed-roster member per minute t in [clock..session_end],
including blocked/missing cash rows with null features):
  key      day, clock, rank, ticker, t
  exec     entry_et, entry_px, fill_et, fill_px, status, session_end, tenure(=t-clock),
           px, px_et, px_age(=t-px_et), filled
  state    ret_fill, peak_gain, dd_from_high(<=0), mae_sofar, bars_since_high, nh5, nh15,
           nh30, streak_up, ret1, ret3, ret5, ret10, ret15, v5, v15, v30, v60, dvol5,
           dvol15, vol_med_ratio, range5
  pm state pm_n_bars, pm_cum_vol, pm_high, pm_low, pm_last_px, pm_last_et (as-of t-1)
  race     race_rank, race_rank_fresh, race_gain, race_n_known, race_n_eligible, race_age_min,
           race_fresh2, drank5, drank15  (RTH t>=570 only)
  peers    n_peers, sib_ret_mean, sib_above_fill, peer_ret_p25, peer_ret_p50, peer_ret_p75,
           basket_ret, peer_dvol5_mean, rel_dvol5
  labels   sell_px, sell_et, sell_volume, V1,V2,V3,V5,V10,V15,V30,V60,V120, fmfe30/60/120,
           fmae30/60/120, mfe_close, mae_close, Vclose, t_peak, censored   (NEVER predictors)

Execution fields (sell_px/sell_et/sell_volume) and every label are excluded from predictors
by construction. OHLC extrema are taken from the supplied peak_gain/dd_from_high/mae_sofar
and fmfe/fmae; they are NEVER re-derived from closes.

CLI (schema/coverage print only):
    python factory/scripts/lifecycle_study.py --which discovery --clocks 540,560,569,571 --limit 3
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
from lifecycle_features import augment, ROUTE_FEATURES  # noqa: E402

V2_SUB = ("harvest01", "lifecycle", "v2")
HORIZONS = (1, 2, 3, 5, 10, 15, 30, 60, 120)
TAIL_H = (30, 60, 120)
CLOCKS = (540, 560, 569, 571)          # PM 540/560/569 + near-open 571
PM_CLOCKS = (540, 560, 569)
NEAR_OPEN_CLOCK = 571
N_DISCOVERY = 533
N_VALIDATION = 533

# label / execution columns that must never be used as predictors
LABEL_COLS = (["sell_px", "sell_et", "sell_volume", "exit_px", "exit_et", "mark_px", "mark_et",
               "fill_volume", "mfe_close", "mae_close", "Vclose", "t_peak", "censored",
               "mfe_day", "mae_day", "t_peak_day", "censored_day", "Rclose", "action",
               "raw_top5", "exclude_reason"]
              + [f"V{h}" for h in HORIZONS] + [f"R{h}" for h in HORIZONS]
              + [f"fmfe{h}" for h in TAIL_H] + [f"fmae{h}" for h in TAIL_H])
EXEC_COLS = {"sell_px", "sell_et", "sell_volume", "exit_px", "exit_et", "mark_px", "mark_et",
             "fill_volume"}

# explicit causal predictor allowlist
PANEL_FEATS = ["ret_fill", "peak_gain", "dd_from_high", "mae_sofar", "bars_since_high", "nh5",
               "nh15", "nh30", "streak_up", "ret1", "ret3", "ret5", "ret10", "ret15",
               "v5", "v15", "v30", "v60", "dvol5", "dvol15", "vol_med_ratio", "range5",
               "px_age", "tenure", "own_decision_gain"]
PANEL_FEATS += ROUTE_FEATURES
PM_FEATS = ["pm_n_bars", "pm_cum_vol", "pm_high", "pm_low", "pm_last_px", "pm_last_et"]
RACE_FEATS = ["race_rank", "race_rank_fresh", "race_gain", "race_n_known", "race_n_eligible",
              "race_age_min", "race_fresh2", "drank5", "drank15"]
PEER_FEATS = ["n_peers", "sib_ret_mean", "sib_above_fill", "peer_ret_p25", "peer_ret_p50",
              "peer_ret_p75", "basket_ret", "peer_dvol5_mean", "rel_dvol5"]
# Minute tape canonical predictor names (producer lifecycle_tape.py 2.0.1, schema v2).
# tr_n_all/tr_dvol_all = ALL prints; tr_n/tr_dvol = volume-counting prints only. Chosen
# deliberately: keep both, models can pick. q_*_shares_* are SHARES (never re-multiply).
TAPE_FEATS = ["tr_n_all", "tr_dvol_all", "tr_n", "tr_dvol", "tr_avg_sz", "tr_n_5m", "tr_dvol_5m",
              "tr_n_15m", "tr_dvol_15m", "tr_odd_share", "tr_sweep_share", "tr_intensity_5m",
              "tr_intensity_15m", "tr_max_interarrival_s", "q_spread_bps_med",
              "q_spread_bps_last", "q_spread_bps_min", "q_spread_bps_max", "q_imb_med",
              "q_imb_last", "q_bid_shares_last", "q_ask_shares_last", "q_bid_shares_med",
              "q_ask_shares_med", "q_spread_bps_mean_minmed_5m", "q_imb_mean_minmed_5m"]
TAPE_REQUIRED = ["tr_n_all", "tr_dvol_all", "q_spread_bps_med"]
TAPE_LAYER_DIRS = {"tape": "tape", "tape5": "tape5", "tape10": "tape10", "entry": "entry",
                   "coverage": "coverage"}

ROSTER_ANCHOR = ["decision_px", "decision_et", "gain_adj", "prev_close_adj", "prev_close_raw",
                 "split_factor", "prev_used_src", "source", "quality_flags", "flag_discrepancy",
                 "flag_nonpos", "flag_extreme_gain", "pit_listed", "rank_all", "n_universe"]
ROSTER_EXEC = ["fill_et", "fill_px", "status", "entry_gap_min", "bars_source", "session_end"]
ROSTER_LABELS = ["mfe_day", "mae_day", "t_peak_day", "censored_day", "Rclose"] + \
                [f"R{h}" for h in (1, 2, 3, 5, 10, 15, 30, 60, 120)]


# --------------------------------------------------------------------- split / coverage
def v2_dir(data_root: Path) -> Path:
    return data_root.joinpath(*V2_SUB)


def _calendar_sha() -> str:
    return hashlib.sha256(bps.CALENDAR.read_bytes()).hexdigest()


def load_split(data_root: Path, verify: bool = True) -> dict:
    p = v2_dir(data_root) / "split.json"
    if not p.exists():
        raise SystemExit(f"immutable split missing: {p} (do not fabricate a split)")
    d = json.loads(p.read_text())
    if verify and d.get("calendar_sha256") != _calendar_sha():
        raise SystemExit("split calendar_sha256 does not match the worktree session calendar; "
                         "refusing a stale/foreign split")
    disc, val = list(d.get("discovery_days", [])), list(d.get("validation_days", []))
    if len(disc) != N_DISCOVERY or len(val) != N_VALIDATION:
        raise SystemExit(f"split sizes wrong: discovery={len(disc)} validation={len(val)}")
    cal = set(json.loads(bps.CALENDAR.read_text())["evidence"])
    both = disc + val
    if not set(both) <= cal:
        raise SystemExit(f"split days absent from calendar: {sorted(set(both) - cal)[:5]}")
    if len(set(both)) != len(both):
        raise SystemExit("split has duplicate days")
    for day in both:
        m = day[:7]
        if day[:4] == "2024" or m == "2025-01" or "2026-06" <= m <= "2026-08":
            raise SystemExit(f"forbidden excluded day present in split: {day}")
    if max(disc) >= min(val):
        raise SystemExit("split boundary violation: discovery overlaps validation")
    return d


def discovery_days(data_root: Path) -> list[str]:
    return load_split(data_root)["discovery_days"]


def frozen(data_root: Path) -> bool:
    return (v2_dir(data_root) / "FREEZE.json").exists()


def validation_days(data_root: Path, allow: bool = False) -> list[str]:
    if not allow or not frozen(data_root):
        raise SystemExit("validation outcomes are locked: no freeze marker at "
                         f"{v2_dir(data_root) / 'FREEZE.json'} (do not inspect validation)")
    return load_split(data_root)["validation_days"]


def load_manifest(data_root: Path) -> dict | None:
    p = Path(os.environ.get("LIFECYCLE_V2_MANIFEST", v2_dir(data_root) / "manifest.json"))
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    if not m.get("schema_version"):
        raise SystemExit("v2 manifest missing schema_version; stale results possible")
    return m


def day_status(data_root: Path, day: str) -> str | None:
    p = v2_dir(data_root) / "_done" / f"{day}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("status")
    except Exception:
        return None


def assert_coverage(days: list[str], data_root: Path, kind: str = "panel") -> None:
    """Strict coverage via per-day atomic markers; absent marker = not built = hard error.
    A marker whose schema_version differs from the manifest is stale and refused."""
    man = load_manifest(data_root)
    want_sv = man.get("schema_version") if man else None
    missing = [d for d in days if day_status(data_root, d) is None]
    if missing:
        raise SystemExit(f"partial v2 {kind} input: {len(missing)} split days have no _done "
                         f"marker (first: {missing[:8]}). Refusing to analyze a silent subset.")
    if want_sv:
        stale = []
        for d in days:
            p = v2_dir(data_root) / "_done" / f"{d}.json"
            try:
                sv = json.loads(p.read_text()).get("schema_version")
            except Exception:
                sv = None
            if sv != want_sv:
                stale.append(d)
        if stale:
            raise SystemExit(f"stale v2 {kind} markers: {len(stale)} days built under a different "
                             f"schema_version than manifest {want_sv} (first: {stale[:8]}). Rebuild.")
    bad = [d for d in days if day_status(data_root, d) == "ok"
           and not (v2_dir(data_root) / kind / f"{d}.parquet").exists()]
    if bad:
        raise SystemExit(f"partial v2 {kind} input: {len(bad)} status=ok days lack "
                         f"{kind}/<day>.parquet (first: {bad[:8]})")


TAPE_PRODUCER = "2.0.1"
TAPE_SCHEMA = "v2"


def tape_manifest_day(data_root: Path, day: str) -> dict | None:
    p = v2_dir(data_root) / "manifest" / f"{day}.json"
    return json.loads(p.read_text()) if p.exists() else None


def assert_tape_coverage(days: list[str], data_root: Path, layer: str = "tape") -> None:
    """Hard-fail unless every requested day has a valid tape manifest and the layer file
    exists with the recorded size. Days with status no_roster/no_sources are NOT ok."""
    bad: list[tuple[str, str]] = []
    for d in days:
        m = tape_manifest_day(data_root, d)
        if m is None:
            bad.append((d, "no_manifest"))
            continue
        if (m.get("status") != "ok" or m.get("producer_version") != TAPE_PRODUCER
                or m.get("schema_version") != TAPE_SCHEMA):
            bad.append((d, f"status={m.get('status')} v={m.get('producer_version')}"))
            continue
        L = (m.get("layers") or {}).get(layer)
        if not L:
            bad.append((d, f"no_{layer}_layer"))
            continue
        p = Path(L.get("path", ""))
        if not p.exists() or (L.get("bytes") is not None and p.stat().st_size != L["bytes"]):
            bad.append((d, f"{layer}_missing_or_size"))
    if bad:
        raise SystemExit(f"partial tape coverage ({layer}): {len(bad)} days failed "
                         f"(first: {bad[:5]}). Refusing to silently drop tape.")


# --------------------------------------------------------------------- canonicalization
def _alias(df: pl.DataFrame, canon: str, cands: list[str]) -> pl.DataFrame:
    if canon in df.columns:
        return df
    for c in cands:
        if c in df.columns:
            return df.rename({c: canon})
    return df


def _horizon(df: pl.DataFrame, h: int) -> pl.DataFrame:
    return _alias(df, f"V{h}", [f"R{h}", f"hold{h}", f"V_{h}"])


def canonicalize_panel(df: pl.DataFrame) -> pl.DataFrame:
    for canon, cands in {"px": ["close_px"], "tenure": []}.items():
        if cands:
            df = _alias(df, canon, cands)
    for h in HORIZONS:
        df = _horizon(df, h)
    for h in TAIL_H:
        df = _alias(df, f"fmfe{h}", [f"mfe{h}"])
        df = _alias(df, f"fmae{h}", [f"mae{h}"])
    df = _alias(df, "Vclose", ["holdclose"])
    req = ["day", "clock", "rank", "ticker", "t", "entry_px", "px", "peak_gain", "dd_from_high",
           "mae_sofar", "ret_fill", "session_end"]
    miss = [c for c in req if c not in df.columns]
    if miss:
        raise SystemExit(f"v2 panel missing frozen columns: {miss} (will not re-derive OHLC "
                         f"extrema from closes)")
    if "entry_et" not in df.columns:
        df = df.with_columns(pl.col("t").alias("entry_et"))
    if "tenure" not in df.columns:
        df = df.with_columns((pl.col("t") - pl.col("clock")).alias("tenure"))
    if "filled" not in df.columns:
        df = df.with_columns((pl.col("status") == "filled").alias("filled"))
    if "filled_asof" not in df.columns:
        df = df.with_columns(pl.col("filled").alias("filled_asof"))
    return df


def canonicalize_roster(df: pl.DataFrame) -> pl.DataFrame:
    for canon, cands in {"decision_et": ["entry_et"], "decision_px": ["entry_px"],
                         "fill_et": [], "fill_px": []}.items():
        if cands:
            df = _alias(df, canon, cands)
    if "status" not in df.columns:
        df = df.with_columns(pl.when(pl.col("fill_px").is_not_null())
                             .then(pl.lit("filled")).otherwise(pl.lit("blocked")).alias("status"))
    return df


# --------------------------------------------------------------------- loaders
def _read(kind: str, day: str, data_root: Path) -> pl.DataFrame | None:
    p = v2_dir(data_root) / kind / f"{day}.parquet"
    return pl.read_parquet(p) if p.exists() else None


def load_roster(days: list[str], data_root: Path, clocks=CLOCKS, strict: bool = True) -> pl.DataFrame:
    if strict:
        assert_coverage(days, data_root, "roster")
    frames = [f for d in days if (f := _read("roster", d, data_root)) is not None]
    if not frames:
        return pl.DataFrame()
    df = canonicalize_roster(pl.concat(frames, how="vertical_relaxed"))
    if "clock" in df.columns:
        df = df.filter(pl.col("clock").is_in(list(clocks)))
    return df


def load_panel(days: list[str], data_root: Path, clocks=CLOCKS, with_roster: bool = True,
               with_tape: bool = True, strict: bool = True) -> pl.DataFrame:
    if strict:
        assert_coverage(days, data_root, "panel")
    frames = [f for d in days if (f := _read("panel", d, data_root)) is not None]
    if not frames:
        raise SystemExit(f"no v2 panel rows for {len(days)} requested days")
    df = canonicalize_panel(pl.concat(frames, how="vertical_relaxed"))
    df = df.filter(pl.col("clock").is_in(list(clocks)))
    if with_roster:
        r = load_roster(days, data_root, clocks, strict=False)
        if r.height:
            keep = ["day", "clock", "rank", "ticker"] + [
                c for c in ROSTER_ANCHOR + ROSTER_EXEC + ROSTER_LABELS if c in r.columns]
            df = df.join(r.select(keep), on=["day", "clock", "rank", "ticker"], how="left")
    if with_tape:
        assert_tape_coverage(days, data_root, "tape")
        frames = [f for d in days if (f := _read("tape", d, data_root)) is not None]
        if not frames:
            raise SystemExit("tape manifest ok but no tape files found")
        tp = _by_name(frames)
        miss = [c for c in TAPE_REQUIRED if c not in tp.columns]
        if miss:
            raise SystemExit(f"tape schema missing required columns {miss}; refusing to "
                             f"silently drop tape features")
        cols = [c for c in TAPE_FEATS if c in tp.columns]
        # minute tape row t already holds completed minute [t-1,t): DIRECT join, no +1 shift
        tp = (tp.select(["day", "ticker", "t"] + cols)
              .with_columns(pl.col("t").cast(pl.Int64), pl.col("day").cast(pl.String),
                            pl.col("ticker").cast(pl.String)))
        df = df.join(tp, on=["day", "ticker", "t"], how="left")
    return augment(df)


def _by_name(frames: list[pl.DataFrame]) -> pl.DataFrame:
    """Day files may differ in column ORDER; union by NAME, never by position."""
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def load_tape(days: list[str], data_root: Path) -> pl.DataFrame:
    frames = [f for d in days if (f := _read("tape", d, data_root)) is not None]
    return _by_name(frames)


def load_tape5(days: list[str], data_root: Path) -> pl.DataFrame:
    frames = [f for d in days if (f := _read("tape5", d, data_root)) is not None]
    return _by_name(frames)


def load_tape10(days: list[str], data_root: Path) -> pl.DataFrame:
    frames = [f for d in days if (f := _read("tape10", d, data_root)) is not None]
    return _by_name(frames)


def load_coverage(days: list[str], data_root: Path) -> pl.DataFrame:
    frames = [f for d in days if (f := _read("coverage", d, data_root)) is not None]
    return _by_name(frames)


def load_universe(days: list[str], data_root: Path, clocks=CLOCKS) -> pl.DataFrame:
    frames = [f for d in days if (f := _read("universe", d, data_root)) is not None]
    if not frames:
        return pl.DataFrame()
    df = pl.concat(frames, how="vertical_relaxed")
    if "clock" in df.columns:
        df = df.filter(pl.col("clock").is_in(list(clocks)))
    return df


# --------------------------------------------------------------------- statistics
def fin(name: str) -> pl.Expr:
    """Finite-only expression (NaN is non-null in Polars; the contract requires is_finite)."""
    c = pl.col(name)
    return c.filter(c.is_finite())


def day_block_ci(df: pl.DataFrame, valcol: str, n_boot: int = 2000, seed: int = 0) -> dict:
    """Equal day weighting with a day-block bootstrap CI (overlapping rows are not
    independent; uncertainty is over days, not member-minutes)."""
    per = (df.select(["day", valcol]).filter(pl.col(valcol).is_finite())
           .group_by("day").agg(pl.col(valcol).mean().alias("v")))
    v = per["v"].to_numpy().astype(float)
    if v.size == 0:
        return {"n_days": 0, "mean": None, "lo": None, "hi": None}
    rng = np.random.default_rng(seed)
    bs = np.array([rng.choice(v, size=v.size, replace=True).mean() for _ in range(n_boot)])
    return {"n_days": int(v.size), "mean": float(v.mean()),
            "lo": float(np.percentile(bs, 2.5)), "hi": float(np.percentile(bs, 97.5))}


def member_balanced_mean(df: pl.DataFrame, valcol: str) -> float | None:
    per = (df.select(["day", "clock", "rank", "ticker", valcol])
           .filter(pl.col(valcol).is_finite())
           .group_by(["day", "clock", "rank", "ticker"]).agg(pl.col(valcol).mean().alias("v")))
    return None if per.height == 0 else float(per["v"].mean())


def auc_safe(y, x) -> float | None:
    """AUC with sign; null-target handling: needs both classes and enough finite rows
    (masks NaN in BOTH target and feature)."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    y, x = y[ok].astype(int), x[ok]
    if y.size < 30 or np.unique(y).size < 2:
        return None
    from sklearn.metrics import roc_auc_score
    try:
        return float(roc_auc_score(y, x))
    except Exception:
        return None


def md_table(rows: list[list[str]], header: list[str], min_rows: int = 1) -> list[str]:
    """Markdown table that never emits an empty header (old-script bug)."""
    if not rows or len(rows) < min_rows:
        return ["_no rows meeting the minimum day/event threshold_"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return lines


def wall_clock(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


def outcome_class(mfe: float | None) -> str:
    if mfe is None or not np.isfinite(mfe):
        return "unknown"
    if mfe >= 1.0:
        return "monster"
    if mfe >= 0.30:
        return "runner"
    if mfe >= 0.10:
        return "mid"
    return "dud"


def add_forward_path(df: pl.DataFrame, horizons=TAIL_H, leg: float = 0.30) -> pl.DataFrame:
    """Forward-only at-risk reclaim/death from the SUPPLIED OHLC-derived fmfe/fmae.

    reclaim_h(t) = the future high within (t,t+h] exceeds the running high at t
    death_h(t)   = the future low  within (t,t+h] undercuts the running low at t
                   (never pre-entry MAE)
    leg_h(t)     = a FUTURE leg: fmfe_h >= leg (px reaches +30% from sell-now within h)
    """
    for h in horizons:
        if f"fmfe{h}" not in df.columns or f"fmae{h}" not in df.columns:
            raise SystemExit(f"panel missing fmfe{h}/fmae{h}; refusing to re-derive from closes")
    if "sell_px" not in df.columns:
        raise SystemExit("panel missing sell_px; forward reclaim/death need the executable anchor")
    hi = pl.col("entry_px") * (1 + pl.col("peak_gain"))
    lo = pl.col("entry_px") * (1 + pl.col("mae_sofar"))
    exprs = []
    for h in horizons:
        fhi = pl.col("sell_px") * (1 + pl.col(f"fmfe{h}"))
        flo = pl.col("sell_px") * (1 + pl.col(f"fmae{h}"))
        exprs += [pl.when(fhi.is_finite() & hi.is_finite())
                  .then((fhi > hi).cast(pl.Int8)).otherwise(None).alias(f"reclaim{h}"),
                  pl.when(flo.is_finite() & lo.is_finite())
                  .then((flo < lo).cast(pl.Int8)).otherwise(None).alias(f"death{h}")]
        if h in (60, 120):
            exprs.append(pl.when(pl.col(f"fmfe{h}").is_finite())
                         .then((pl.col(f"fmfe{h}") >= leg).cast(pl.Int8))
                         .otherwise(None).alias(f"leg{h}"))
    return df.with_columns(exprs)


def descriptor_table(df: pl.DataFrame) -> pl.DataFrame:
    """One row per fixed-roster member with transparent outcome descriptors (RULERS only,
    never predictors). Distinguishes MFE from the executable captured return. NaN-safe."""
    base = (df.group_by(["day", "clock", "rank", "ticker"])
            .agg(pl.col("entry_px").first().alias("entry_px"),
                 fin("peak_gain").max().alias("mfe_path"),
                 fin("mae_sofar").min().alias("mae_path"),
                 fin("dd_from_high").min().alias("max_dd"),
                 fin("ret_fill").last().alias("ret_last"),
                 pl.col("session_end").first().alias("session_end")))
    if "mfe_day" in df.columns:
        base = base.join(df.group_by(["day", "clock", "rank", "ticker"]).agg(
            fin("mfe_day").first().alias("mfe_day"),
            fin("Rclose").first().alias("Rclose")),
            on=["day", "clock", "rank", "ticker"], how="left")
    else:
        base = base.with_columns(pl.lit(None, dtype=pl.Float64).alias("mfe_day"),
                                 pl.lit(None, dtype=pl.Float64).alias("Rclose"))
    base = base.with_columns([
        pl.coalesce([pl.when(pl.col("mfe_day").is_finite()).then(pl.col("mfe_day")),
                     pl.col("mfe_path")]).alias("mfe"),
        pl.when(pl.col("Rclose").is_finite()).then(pl.col("Rclose")).otherwise(None)
        .alias("captured"),
        pl.when(pl.col("ret_last").is_finite()).then(pl.col("ret_last")).otherwise(None)
        .alias("mark_ret"),
    ])
    return base.with_columns(
        pl.when(pl.col("mfe").is_null() | ~pl.col("mfe").is_finite()).then(pl.lit("unknown"))
        .when(pl.col("mfe") < 0.10).then(pl.lit("dud"))
        .when(pl.col("captured").is_null()).then(pl.lit("unknown"))
        .when((pl.col("mfe") >= 1.0) & (pl.col("captured") >= 0.5)).then(pl.lit("monster"))
        .when((pl.col("mfe") >= 0.30) & (pl.col("captured") >= 0.10)).then(pl.lit("runner"))
        .when((pl.col("mfe") >= 0.50) & (pl.col("captured") < 0.10)).then(pl.lit("transient_spike"))
        .when((pl.col("max_dd") <= -0.30) & (pl.col("captured") >= 0.10)).then(pl.lit("recovered_flush"))
        .when((pl.col("mfe") >= 0.20) & (pl.col("captured") < 0.0)).then(pl.lit("exhaustion"))
        .otherwise(pl.lit("mid")).alias("descriptor"))


DESCRIPTORS = ("monster", "runner", "transient_spike", "recovered_flush", "exhaustion", "mid",
               "dud", "unknown")


# --------------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", default="discovery")
    ap.add_argument("--clocks", default=",".join(str(c) for c in CLOCKS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    split = load_split(data_root)
    days = validation_days(data_root, allow=True) if args.which == "validation" else split["discovery_days"]
    if args.limit:
        days = days[:args.limit]
    clocks = tuple(int(x) for x in args.clocks.split(","))
    df = load_panel(days, data_root, clocks=clocks)
    print(f"split discovery={len(split['discovery_days'])} validation={len(split['validation_days'])} "
          f"frozen={frozen(data_root)} manifest={'yes' if load_manifest(data_root) else 'no'}")
    print(f"panel days={len(days)} rows={df.height} clocks={clocks}")
    print("columns:", df.columns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
