#!/usr/bin/env python3
"""BASKET-01 Phase-2 F8 — joint basket economics (multi-survivor lens).

PRE-REG-BASKET-02 §3.7. Reads ONLY canonical Phase-1 artifacts (fills are never
recomputed):
  - anatomy day files: factory/artifacts/basket/sip/anatomy/YYYY-MM-DD.jsonl
  - SIP bars:          factory/artifacts/basket/sip/bars/YYYY-MM-DD.parquet
  - session ends:      factory/artifacts/basket/sip/phase2_session_calendar.json
                       (absent -> TEMPORARY map: 779 for the 7 known early closes
                        in the dev span, else 959; flagged SESSION_MAP=TEMP_MAP)

Populations (entry families): A_pm/570, A_pm31/570, A_open/570, B/585, B/600.
Baskets: N in {2,3,4} slots, equal capital.
  sub[N] = first N FILLED names by rank over the snapshot's candidate list
           (blocked/unfilled names skipped; if fewer than N filled exist,
            remaining slots = cash, return 0; all days kept in denominators).
  top[N] = names[:N] filtered to filled (Phase-1 T7b semantics; cash for
           blocked/unfilled slots, never substituted) -> reconciliation only.

Outputs (resumable, month-sharded then merged):
  <OUT>/panels/YYYY-MM.jsonl (+ .done)   daily per-family panels
  <OUT>/panels_merged.jsonl              concatenated panels
  <OUT>/joint_pooled.json                pooled + dual-block joint metrics
  <OUT>/joint_monthly.json               month-blocked joint metrics
  <OUT>/joint_distributions.json         joint MFE/EOD order stats, pairwise corr,
                                         day-shape tables, golden-window conditionals
  <OUT>/reconciliation_T7b.json          T7b replica vs Phase-1 reference
  <OUT>/README.md                        written separately (human report)

Usage:
  .venv/bin/python factory/scripts/basket_f8_joint.py                 # shards + merge
  .venv/bin/python factory/scripts/basket_f8_joint.py --months 2021-02,2021-03
  .venv/bin/python factory/scripts/basket_f8_joint.py --merge-only
  .venv/bin/python factory/scripts/basket_f8_joint.py --force   # recompute shards
  .venv/bin/python factory/scripts/basket_f8_joint.py --self-test
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "factory" / "artifacts" / "basket"
SIP = ART / "sip"
ANAT = SIP / "anatomy"
BARS = SIP / "bars"
OUT = ART / "phase2" / "F8_joint"
PANELS = OUT / "panels"

FAMILIES = [("A_pm", 570), ("A_pm31", 570), ("A_open", 570), ("B", 585), ("B", 600)]
N_LIST = [2, 3, 4]
REACH_HS = [5, 10, 20, 30, 50, 100]
FRICTION = 0.01  # 100 bps round trip per filled slot (net sensitivity column)

# Evidence boundary (PRE-REG-BASKET-02 §1): sealed/reserved months are refused.
FORBIDDEN_MONTHS = ({"2024-%02d" % m for m in range(1, 13)} | {"2025-01"}
                    | {"2026-06", "2026-07", "2026-08"})
DEV_START, DEV_END_A = "2021-02", "2023-12"
DEV_START_B, DEV_END_B = "2025-02", "2026-05"

EARLY_CLOSE_ETS = {  # temporary map; replaced automatically if calendar exists
    "2021-11-26": 779, "2022-11-25": 779, "2023-07-03": 779, "2023-11-24": 779,
    "2025-07-03": 779, "2025-11-28": 779, "2025-12-24": 779,
}
DEFAULT_SESS_END = 959


def month_allowed(m: str) -> bool:
    if m in FORBIDDEN_MONTHS:
        return False
    return (DEV_START <= m <= DEV_END_A) or (DEV_START_B <= m <= DEV_END_B)


def session_end_map():
    """(date->et map, label). Calendar if present, else TEMP_MAP."""
    p = SIP / "phase2_session_calendar.json"
    if p.exists():
        obj = json.loads(p.read_text())
        days = None
        if isinstance(obj, dict):
            for k in ("session_end_by_day", "days", "session_ends"):
                if isinstance(obj.get(k), dict):
                    days = obj[k]
                    break
            if days is None and all(
                    isinstance(k, str) and len(k) == 10 and k[4] == "-" for k in obj):
                days = obj
        elif isinstance(obj, list):
            days = obj
        m = {}
        if isinstance(days, dict):
            for k, v in days.items():
                et = v.get("session_end_et", v.get("et", v)) if isinstance(v, dict) else v
                m[str(k)] = int(et)
        elif isinstance(days, list):
            for d in days:
                dt = d.get("date") or d.get("day")
                et = d.get("session_end_et", d.get("et"))
                if dt is not None and et is not None:
                    m[str(dt)] = int(et)
        if m:
            return m, "CALENDAR"
        raise SystemExit("session calendar present but unparsable -> stop (no guessing)")
    return dict(EARLY_CLOSE_ETS), "TEMP_MAP"


# ---------------------------------------------------------------- bars helpers

def bars_day(date: str):
    """{ticker: {'et': [...], 'open': [...], 'rmax_hi': [...]}} or None."""
    p = BARS / f"{date}.parquet"
    if not p.exists():
        return None
    df = (pl.read_parquet(p, columns=["ticker", "et", "open", "high"])
          .sort(["ticker", "et"]))
    t = df["ticker"].to_numpy()
    et = df["et"].to_numpy()
    op = df["open"].to_numpy()
    hi = df["high"].to_numpy()
    out = {}
    if len(t) == 0:
        return out
    starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
    ends = np.r_[starts[1:], len(t)]
    for s, e in zip(starts, ends):
        sub_et, sub_op, sub_hi = et[s:e], op[s:e], hi[s:e]
        o = np.argsort(sub_et, kind="stable")
        out[str(t[s])] = {"et": sub_et[o], "open": sub_op[o],
                          "rmax_hi": np.maximum.accumulate(sub_hi[o])}
    return out


def _run_max(bi, cutoff: int):
    i = int(np.searchsorted(bi["et"], cutoff, side="right")) - 1
    return None if i < 0 else float(bi["rmax_hi"][i])


def member_bar_fields(bi, fill_px: float, fill_et: int, state: dict, sess_et: int) -> dict:
    """Forced-flat return + causal 09:45/10:00 bar features. Bars only, no lookahead."""
    out = {"flat_ret": None, "flat_et": None, "flat_fallback": False,
           "elig585": False, "elig600": False,
           "above585": None, "above600": None, "nh585": None, "nh600": None}
    if bi is None:
        return out
    i = int(np.searchsorted(bi["et"], sess_et, side="right")) - 1
    if i < 0:
        return out
    flat_et = int(bi["et"][i])
    out["flat_et"] = flat_et
    out["flat_fallback"] = flat_et != sess_et
    out["flat_ret"] = float(bi["open"][i]) / fill_px - 1.0
    prefill = _run_max(bi, fill_et)  # includes the fill bar
    for cp in (585, 600):
        st = state.get(str(cp))
        elig = st is not None
        out[f"elig{cp}"] = bool(elig)
        if not elig:
            continue
        out[f"above{cp}"] = int(float(st["ret"]) > 0)
        run = _run_max(bi, cp - 1)  # state uses bars with et <= cp-1
        if run is not None and prefill is not None:
            out[f"nh{cp}"] = int(run > prefill)
        else:
            out[f"nh{cp}"] = None
    return out


# ---------------------------------------------------------------- day panels

def build_day(date: str, rec: dict, bix: dict | None, sess_et: int,
              sess_map: str) -> list[dict]:
    snaps = {(s["pop"], s["T"]): s for s in rec.get("snapshots", [])}
    rows = []
    for pop, T in FAMILIES:
        s = snaps.get((pop, T))
        if s is None:
            continue
        names = []
        for n in s["names"]:
            f = n.get("fill")
            status = "unfilled" if f is None else ("blocked" if f.get("blocked") else "filled")
            m = {"rank": n["rank"], "ticker": n["ticker"], "fill_status": status,
                 "fill_et": None, "fill_px": None, "eod_ret": None, "eod_net": None,
                 "flat_ret": None, "flat_et": None, "flat_fallback": False,
                 "mfe": None, "reach": [], "elig585": False, "elig600": False,
                 "above585": None, "above600": None, "nh585": None, "nh600": None}
            if status == "filled":
                px = f.get("px")
                if px is None or f.get("et") is None:
                    m["fill_status"] = "unfilled"
                else:
                    m["fill_et"] = int(f["et"])
                    m["fill_px"] = float(px)
                    if n.get("eod_ret") is not None:
                        m["eod_ret"] = float(n["eod_ret"])
                        m["eod_net"] = float(n["eod_ret"]) - FRICTION
                    if n.get("mfe") is not None:
                        m["mfe"] = float(n["mfe"])
                    lad = (n.get("ladders") or {}).get("up") or {}
                    m["reach"] = [H for H in REACH_HS
                                  if lad.get(str(H)) is not None]
                    bi = (bix or {}).get(n["ticker"])
                    m.update(member_bar_fields(bi, float(px), m["fill_et"],
                                               n.get("state") or {}, sess_et))
            names.append(m)
        filled_ranks = [m["rank"] for m in names if m["fill_status"] == "filled"]
        sub = {str(N): filled_ranks[:N] for N in N_LIST}
        top = {str(N): [r for r in filled_ranks if r <= N] for N in N_LIST}
        rows.append({"date": date, "month": date[:7], "pop": pop, "T": T,
                     "family": f"{pop}/{T}", "sess_end_et": sess_et,
                     "sess_map": sess_map, "names": names, "sub": sub, "top": top})
    return rows


# ---------------------------------------------------------------- shards

def month_files(month: str) -> list[str]:
    fs = sorted(glob.glob(str(ANAT / f"{month}-*.jsonl")))
    if not fs:
        # month may straddle no files at all (should not happen in dev span)
        return []
    for f in fs:
        m = Path(f).name[:7]
        if not month_allowed(m):
            raise SystemExit(f"refusing to read forbidden month {m}: {f}")
    return fs


def run_month(month: str, sess_map: dict, sess_label: str, force: bool) -> int:
    shard = PANELS / f"{month}.jsonl"
    done = PANELS / f"{month}.done"
    if done.exists() and not force:
        return 0
    files = month_files(month)
    if not files:
        return 0
    PANELS.mkdir(parents=True, exist_ok=True)
    tmp = PANELS / f".{month}.jsonl.tmp"
    n_days = 0
    with open(tmp, "w") as fh:
        for f in files:
            rec = json.loads(Path(f).read_text())
            date = rec["date"]
            if not month_allowed(date[:7]):
                raise SystemExit(f"refusing day in forbidden month: {date}")
            sess_et = int(sess_map.get(date, DEFAULT_SESS_END))
            bix = bars_day(date)
            for row in build_day(date, rec, bix, sess_et, sess_label):
                fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
            n_days += 1
    os.replace(tmp, shard)
    done.write_text(f"{n_days}\n")
    return n_days


def load_panels() -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(PANELS / "*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------- joint metrics

def _pos(x):
    return x is not None and x > 0


def day_record(row: dict, variant: str, N: int) -> dict:
    ms = [row["names"][i - 1] for i in row[variant][str(N)]]
    n = len(ms)
    e = [m["eod_ret"] for m in ms if m["eod_ret"] is not None]
    ef = [m["flat_ret"] for m in ms if m["flat_ret"] is not None]
    mf = [m["mfe"] for m in ms if m["mfe"] is not None]
    kr = {H: sum(1 for m in ms if H in m["reach"]) for H in REACH_HS}
    pos_close = sum(1 for x in e if x > 0)
    pos_net = sum(1 for x in e if x - FRICTION > 0)
    pos_flat = sum(1 for x in ef if x > 0)
    pos_flat_net = sum(1 for x in ef if x - FRICTION > 0)
    n_flat = len(ef)
    d = {
        "n": n, "n_eod": len(e), "n_flat": n_flat, "n_mfe": len(mf),
        "full": int(n == N),
        "pos_close": pos_close, "pos_net": pos_net,
        "pos_flat": pos_flat, "pos_flat_net": pos_flat_net,
        "all_prof_close": int(n > 0 and pos_close == n),
        "all_prof_net": int(n > 0 and pos_net == n),
        "all_prof_flat": int(n > 0 and n_flat == n and pos_flat == n),
        "all_prof_flat_net": int(n > 0 and n_flat == n and pos_flat_net == n),
        "ge2_close": int(pos_close >= 2), "ge2_net": int(pos_net >= 2),
        "ge2_flat": int(pos_flat >= 2), "ge2_flat_net": int(pos_flat_net >= 2),
        "kr": kr,
        "ret_close": (sum(e) / N) if len(e) == n else None,
        "ret_close_net": ((sum(e) - FRICTION * len(e)) / N) if len(e) == n else None,
        "ret_flat": (sum(ef) / N) if (n > 0 and n_flat == n) else None,
        "ret_flat_net": ((sum(ef) - FRICTION * n_flat) / N)
                        if (n > 0 and n_flat == n) else None,
        "mfe_sorted": sorted(mf, reverse=True),
        "eod_sorted": sorted(e, reverse=True),
        "eod_flat_sorted": sorted(ef, reverse=True),
        "sign_all_pos": int(n > 0 and pos_close == n and len(e) == n),
        "sign_all_neg": int(n > 0 and sum(1 for x in e if x < 0) == n and len(e) == n),
        "empty": int(n == 0),
    }
    d["sign_mixed"] = int(n > 0 and not d["sign_all_pos"] and not d["sign_all_neg"])
    for H in REACH_HS:
        d[f"all_reach_{H}"] = int(n > 0 and kr[H] == n)
        d[f"all_reach_{H}_full"] = int(n == N and kr[H] == n)
        d[f"ge2_reach_{H}"] = int(kr[H] >= 2)
        d[f"any_reach_{H}"] = int(kr[H] >= 1)
    for cp in (585, 600):
        elig = [m for m in ms if m.get(f"elig{cp}")]
        d[f"n_elig{cp}"] = len(elig)
        d[f"k_above{cp}"] = sum(1 for m in elig if m.get(f"above{cp}") == 1)
        d[f"k_nh{cp}"] = sum(1 for m in elig if m.get(f"nh{cp}") == 1)
    return d


def _rate(days, key):
    v = [d[key] for d in days if d.get(key) is not None]
    return round(float(np.mean(v)), 6) if v else None


def _mean(days, key):
    v = [d[key] for d in days if d.get(key) is not None]
    return round(float(np.mean(v)), 6) if v else None


def metrics_from_days(days: list[dict], flat_skipped: int = 0) -> dict:
    out = {"days": len(days), "empty_days": sum(d["empty"] for d in days),
           "full_days": sum(d["full"] for d in days),
           "mean_filled": _mean(days, "n")}
    for key in ("all_prof_close", "all_prof_net", "all_prof_flat", "all_prof_flat_net",
                "ge2_close", "ge2_net", "ge2_flat", "ge2_flat_net",
                "sign_all_pos", "sign_mixed", "sign_all_neg"):
        out[f"p_{key}"] = _rate(days, key)
    for H in REACH_HS:
        out[f"p_all_reach_{H}"] = _rate(days, f"all_reach_{H}")
        out[f"p_ge2_reach_{H}"] = _rate(days, f"ge2_reach_{H}")
        out[f"p_any_reach_{H}"] = _rate(days, f"any_reach_{H}")
    for H in (5, 10):
        out[f"p_all_reach_{H}_full"] = _rate(days, f"all_reach_{H}_full")
    for key in ("ret_close", "ret_close_net", "ret_flat", "ret_flat_net"):
        out[f"mean_basket_{key}"] = _mean(days, key)
    if flat_skipped:
        out["flat_days_skipped"] = flat_skipped
    return out


def _pctl(vals, qs=(10, 25, 50, 75, 90)):
    a = np.asarray([v for v in vals if v is not None], dtype=float)
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "mean": round(float(a.mean()), 6),
            **{f"p{q}": round(float(np.percentile(a, q)), 6) for q in qs}}


def order_stats(days: list[dict], key: str, N: int) -> dict:
    out = {}
    for pos in range(1, N + 1):
        vals = [d[key][pos - 1] for d in days if len(d[key]) >= pos]
        out[f"ord{pos}"] = _pctl(vals)
    vals = [d[key][N - 1] for d in days if len(d[key]) >= N]
    out[f"ord{N}"] = _pctl(vals)
    return out


def pairwise_corr(days: list[dict], N: int) -> dict:
    """Slot-level Pearson corr of close-based member returns (pooled + monthly)."""
    cols = {i: [] for i in range(N)}
    valid = []
    for d in days:
        r = d.get("eod_sorted")
        if r is None or len(r) < N:
            continue
        valid.append(d)
    if not valid:
        return {"n_days": 0}
    mat = [[None] * N for _ in range(N)]
    for i in range(N):
        for j in range(N):
            xi = [d["eod_sorted"][i] for d in valid]
            xj = [d["eod_sorted"][j] for d in valid]
            a, b = np.asarray(xi, float), np.asarray(xj, float)
            if a.std() > 0 and b.std() > 0:
                mat[i][j] = round(float(np.corrcoef(a, b)[0, 1]), 5)
    off = [mat[i][j] for i in range(N) for j in range(i + 1, N) if mat[i][j] is not None]
    monthly = defaultdict(list)
    for d in valid:
        monthly[d.get("month", "")].append(d)
    mcorr = []
    for m, sub in monthly.items():
        if len(sub) < 5:
            continue
        for i in range(N):
            for j in range(i + 1, N):
                a = np.asarray([x["eod_sorted"][i] for x in sub], float)
                b = np.asarray([x["eod_sorted"][j] for x in sub], float)
                if a.std() > 0 and b.std() > 0:
                    mcorr.append(float(np.corrcoef(a, b)[0, 1]))
    out = {"n_days": len(valid), "matrix": mat,
           "pooled_mean_offdiag": round(float(np.mean(off)), 5) if off else None,
           "monthly_pairs": _pctl(mcorr),
           "monthly_share_positive": round(float(np.mean([c > 0 for c in mcorr])), 4)
           if mcorr else None}
    return out


def day_shape_table(days: list[dict], N: int) -> list[dict]:
    cnt = Counter()
    for d in days:
        if d["empty"]:
            continue
        y = d.get("year", "?")
        k30 = d["kr"][30]
        kpos = d["pos_close"]
        for scope in ("all", y):
            cnt[(scope, k30, kpos)] += 1
    out = []
    for (scope, k30, kpos), n in sorted(cnt.items()):
        out.append({"scope": scope, "k_reach30": k30, "k_eod_pos": kpos, "n": n})
    return out


def conditional_tables(days: list[dict], N: int) -> list[dict]:
    out = []
    for cp in (585, 600):
        for kind, kmax in (("above_fill", f"k_above{cp}"), ("new_high", f"k_nh{cp}")):
            buckets = defaultdict(list)
            for d in days:
                if d["empty"] or d.get(f"n_elig{cp}", 0) == 0:
                    continue
                b = min(int(d[kmax]), 2)
                buckets[b].append(d)
            for b, sub in sorted(buckets.items()):
                rec = {"cp": cp, "kind": kind, "bin": f"{b}+" if b >= 2 else str(b),
                       "days": len(sub),
                       "mean_n_elig": _mean(sub, f"n_elig{cp}"),
                       "mean_filled": _mean(sub, "n"),
                       "p_ge2_reach_30": _rate(sub, "ge2_reach_30"),
                       "p_any_reach_30": _rate(sub, "any_reach_30"),
                       "p_any_reach_50": _rate(sub, "any_reach_50"),
                       "p_all_profitable": _rate(sub, "all_prof_close"),
                       "mean_basket_ret_close": _mean(sub, "ret_close"),
                       "share_all_pos": _rate(sub, "sign_all_pos")}
                out.append(rec)
    return out


# ---------------------------------------------------------------- merge

def merge(sess_label: str):
    rows = load_panels()
    if not rows:
        raise SystemExit("no panel shards found; run shard step first")
    OUT.mkdir(parents=True, exist_ok=True)
    for r in rows:
        r["year"] = r["date"][:4]
    months = sorted({r["month"] for r in rows})
    pooled = {}
    monthly = []
    distr = {}
    for fam in [f"{p}/{T}" for p, T in FAMILIES]:
        frows = [r for r in rows if r["family"] == fam]
        pooled[fam] = {}
        distr[fam] = {}
        for variant in ("sub", "top"):
            for N in N_LIST:
                days = [day_record(r, variant, N) for r in frows]
                for d, r in zip(days, frows):
                    d["month"] = r["month"]
                    d["year"] = r["year"]
                skipped = sum(1 for d in days if d["n"] > 0 and d["n_flat"] < d["n"])
                m = metrics_from_days(days, skipped)
                if variant == "sub":
                    pooled[fam][str(N)] = {
                        "pooled": m,
                        "block1": metrics_from_days([d for d in days
                                                     if DEV_START <= d["month"] <= DEV_END_A]),
                        "block2": metrics_from_days([d for d in days
                                                     if DEV_START_B <= d["month"] <= DEV_END_B]),
                    }
                else:
                    pooled[fam][f"top{N}"] = {"pooled": m}
                bym = defaultdict(list)
                for d in days:
                    bym[d["month"]].append(d)
                for mo, sub in sorted(bym.items()):
                    monthly.append({"family": fam, "variant": variant, "N": N,
                                    "month": mo, **metrics_from_days(sub)})
        for N in N_LIST:
            days = [day_record(r, "sub", N) for r in frows]
            for d, r in zip(days, frows):
                d["month"] = r["month"]
                d["year"] = r["year"]
            distr[fam][str(N)] = {
                "mfe_order": order_stats(days, "mfe_sorted", N),
                "eod_close_order": order_stats(days, "eod_sorted", N),
                "eod_flat_order": order_stats(days, "eod_flat_sorted", N),
                "corr_close": pairwise_corr(days, N),
                "day_shapes_reach30_x_eodpos": day_shape_table(days, N),
                "conditional_golden": conditional_tables(days, N),
            }
    (OUT / "joint_pooled.json").write_text(json.dumps(
        {"_producer": "basket_f8_joint.py",
         "_definitions": ("sub = first N filled by rank; top = names[:N] filled-only "
                          "(T7b semantics); gross primary; net = 100bps per filled slot; "
                          "reach = anatomy ladder touch; days retained incl. cash slots"),
         "_flags": {"session_map": sess_label,
                    "session_end_default": DEFAULT_SESS_END if sess_label == "TEMP_MAP" else None,
                    "months": months, "panel_rows": len(rows)},
         "families": pooled}, indent=1, default=float))
    (OUT / "joint_monthly.json").write_text(json.dumps(
        {"_producer": "basket_f8_joint.py", "rows": monthly}, indent=1, default=float))
    (OUT / "joint_distributions.json").write_text(json.dumps(
        {"_producer": "basket_f8_joint.py",
         "_definitions": ("order stats = filled members' MFE/EOD sorted DESCENDING "
                          "(ord1 = best); corr = slot-level Pearson of close EOD returns"),
         "families": distr}, indent=1, default=float))
    # literal merged panels
    with open(OUT / "panels_merged.jsonl", "w") as fh:
        for r in sorted(rows, key=lambda x: (x["date"], x["family"])):
            fh.write(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n")
    return rows, pooled, distr, months


def reconciliation(rows: list[dict]) -> dict:
    """Recompute Phase-1 T7b touch histograms (top variant, N=3) and diff."""
    ref_path = SIP / "agg" / "T7b.json"
    out = {"_producer": "basket_f8_joint.py",
           "_def": ("ours_top = names[:3] filled-only ladder touch (T7b semantics); "
                    "reference = factory/artifacts/basket/sip/agg/T7b.json set=main"),
           "reference_path": str(ref_path), "H": 30,
           "monthly_diffs": [], "pooled": {},
           "sub_vs_top": {"days_top_has_slot_gap": 0,
                          "day_families_with_gap": []}}
    ref = {(r["month"], f"{r['pop']}/{r['T']}", r["set"]): r for r in json.loads(ref_path.read_text())}
    fams = [f"{p}/{T}" for p, T in FAMILIES]
    our = {}
    for fam in fams:
        frows = [r for r in rows if r["family"] == fam]
        bym = defaultdict(list)
        for r in frows:
            bym[r["month"]].append(r)
        for mo, sub in sorted(bym.items()):
            hist = [0, 0, 0, 0]
            for r in sub:
                k = sum(1 for i in r["top"]["3"] if 30 in r["names"][i - 1]["reach"])
                hist[k] += 1
            our[(mo, fam)] = hist
    # pooled
    for fam in fams:
        h = [0, 0, 0, 0]
        for (mo, f), hist in our.items():
            if f == fam:
                h = [a + b for a, b in zip(h, hist)]
        days = sum(h)
        refh = [0, 0, 0, 0]
        refdays = 0
        for (mo, f, s), r in ref.items():
            if f == fam and s == "main":
                refh = [a + b for a, b in zip(refh, r["touch_k_30"])]
                refdays += r["days"]
        ge1 = (h[1] + h[2] + h[3]) / days if days else None
        ge1r = (refh[1] + refh[2] + refh[3]) / refdays if refdays else None
        ge2 = (h[2] + h[3]) / days if days else None
        ge2r = (refh[2] + refh[3]) / refdays if refdays else None
        out["pooled"][fam] = {
            "ours_top_hist_k030": h, "ours_top_days": days,
            "ref_hist_k030": refh, "ref_days": refdays,
            "ours_top_p_kge1_30": round(ge1, 6) if ge1 is not None else None,
            "ref_p_kge1_30": round(ge1r, 6) if ge1r is not None else None,
            "ours_top_p_kge2_30": round(ge2, 6) if ge2 is not None else None,
            "ref_p_kge2_30": round(ge2r, 6) if ge2r is not None else None,
            "hist_match": h == refh,
        }
        # monthly diffs where they exist
        for (mo, f), hist in sorted(our.items()):
            if f != fam:
                continue
            rr = ref.get((mo, fam, "main"))
            if rr is None:
                continue
            if hist != rr["touch_k_30"]:
                out["monthly_diffs"].append({"month": mo, "family": fam,
                                             "ours": hist, "ref": rr["touch_k_30"]})
        # sub vs top slot-gap accounting (N=3)
        gap = 0
        for r in [x for x in rows if x["family"] == fam]:
            if len(r["top"]["3"]) < 3:
                gap += 1
                if len(out["sub_vs_top"]["day_families_with_gap"]) < 40:
                    out["sub_vs_top"]["day_families_with_gap"].append(
                        {"date": r["date"], "family": fam,
                         "top3_filled": len(r["top"]["3"]),
                         "sub3": r["sub"]["3"]})
        out["sub_vs_top"]["days_top_has_slot_gap"] += gap
    # sub-variant headline shares for context
    out["sub_variant_p_kge1_30_3"] = {}
    out["sub_variant_p_kge2_30_3"] = {}
    for fam in fams:
        frows = [r for r in rows if r["family"] == fam]
        days = [day_record(r, "sub", 3) for r in frows]
        out["sub_variant_p_kge1_30_3"][fam] = _rate(days, "any_reach_30")
        out["sub_variant_p_kge2_30_3"][fam] = _rate(days, "ge2_reach_30")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default=None, help="comma list YYYY-MM")
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        selftest()
        return
    smap, slabel = session_end_map()
    if not args.merge_only:
        if args.months:
            months = args.months.split(",")
        else:
            months = sorted({Path(f).name[:7] for f in glob.glob(str(ANAT / "*.jsonl"))})
        bad = [m for m in months if not month_allowed(m)]
        if bad:
            raise SystemExit(f"refusing months outside the dev span: {bad}")
        months = sorted(months)
        for m in sorted(months):
            n = run_month(m, smap, slabel, args.force)
            if n:
                print(f"  shard {m}: {n} days")
    rows, pooled, distr, months = merge(slabel)
    rec = reconciliation(rows)
    (OUT / "reconciliation_T7b.json").write_text(json.dumps(rec, indent=1, default=float))
    print(f"F8 joint: {len(rows)} panel rows, {len(months)} months -> {OUT}")
    for fam in pooled:
        p = pooled[fam]["3"]["pooled"]
        print(f"  {fam} N=3: k>=1 +30 {p['p_any_reach_30']}, "
              f"k>=2 +30 {p['p_ge2_reach_30']}, all-prof {p['p_all_prof_close']}")


# ---------------------------------------------------------------- self-test

def selftest():
    def mk(rank, tkr, status="filled", eod=None, flat=None, mfe=None, reach=(),
           above585=None, nh585=None, elig585=False):
        return {"rank": rank, "ticker": tkr, "fill_status": status, "fill_et": 571,
                "fill_px": 10.0, "eod_ret": eod, "eod_net": None if eod is None else eod - 0.01,
                "flat_ret": flat, "flat_et": 959, "flat_fallback": False, "mfe": mfe,
                "reach": sorted(reach), "elig585": elig585, "elig600": elig585,
                "above585": above585, "above600": above585,
                "nh585": nh585, "nh600": nh585}

    def panel(date, names, sub, top):
        return {"date": date, "month": date[:7], "family": "A_pm/570", "names": names,
                "sub": sub, "top": top}

    names = [mk(1, "AAA", eod=0.40, flat=0.30, mfe=0.60, reach=(5, 10, 20, 30, 50),
                above585=1, nh585=1, elig585=True),
             mk(2, "BBB", "blocked"),
             mk(3, "CCC", eod=-0.05, flat=-0.07, mfe=0.04, reach=(5,), above585=0, nh585=0,
                elig585=True),
             mk(4, "DDD", eod=0.12, flat=0.10, mfe=0.15, reach=(5, 10, 20))]
    r0 = panel("2025-02-03", names, {"2": [1, 3], "3": [1, 3, 4], "4": [1, 3, 4]},
               {"2": [1], "3": [1, 3], "4": [1, 3, 4]})
    d2 = day_record(r0, "sub", 2)
    assert d2["n"] == 2 and d2["all_prof_close"] == 0 and d2["ge2_close"] == 0, d2
    assert d2["any_reach_30"] == 1 and d2["ret_close"] == (0.40 - 0.05) / 2, d2
    d3 = day_record(r0, "sub", 3)
    assert d3["all_reach_5"] == 1 and d3["ge2_reach_20"] == 1 and d3["any_reach_50"] == 1
    assert d3["ge2_reach_30"] == 0 and d3["all_reach_20"] == 0
    assert d3["k_above585"] == 1 and d3["n_elig585"] == 2 and d3["k_nh585"] == 1
    # top variant: names[:3] = ranks 1..3, rank2 blocked -> only 2 filled
    assert r0["top"]["3"] == [1, 3] and day_record(r0, "top", 3)["n"] == 2
    m = metrics_from_days([d3])
    assert m["p_all_reach_5"] == 1 and m["p_ge2_reach_30"] == 0 and m["p_all_prof_close"] == 0
    # order stats: descending
    o = order_stats([d3], "mfe_sorted", 3)
    assert o["ord1"]["mean"] == 0.60 and o["ord3"]["mean"] == 0.04, o
    # cash day: fewer filled than N -> days retained
    r1 = panel("2025-02-04", [mk(1, "EEE", eod=0.10, flat=0.05, mfe=0.11, reach=(5,))],
               {"2": [1], "3": [1], "4": [1]}, {"2": [1], "3": [1], "4": [1]})
    d = day_record(r1, "sub", 4)
    assert d["n"] == 1 and d["full"] == 0 and d["ret_close"] == 0.10 / 4, d
    d3["year"] = d["year"] = "2025"
    shapes = day_shape_table([d3, d], N := 3)
    assert {s["scope"] for s in shapes} >= {"all", "2025"} and sum(
        s["n"] for s in shapes if s["scope"] == "all") == 2, shapes
    print("self-test OK")


if __name__ == "__main__":
    main()
