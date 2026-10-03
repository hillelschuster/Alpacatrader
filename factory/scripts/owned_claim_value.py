#!/usr/bin/env python3
"""OWNED-CLAIM VALUE — predeclared train-only fitted stopping-value iteration.

Independent producer for the owned-claim decision: reads parent-owned event rows
(past-only `feature_*` states, execution columns, next-event label pointers), fits, per
fixed chronological fold and per declared feature view, a fixed number of fitted
value-iteration rounds, and writes TEST predictions only.

Economics kept exactly as contracted:
  * selling now is the reference (incremental inherited dollars 0);
  * deferred target = (next_sell_px - sell_px)/original_fill_px + max(0,q(next));
  * q is per original gross entry notional, on the SAME fixed share basis;
  * terminal next q=0; actual share count and common exit fee scale the dollars;
  * entry fee is sunk, never recharged to RETAIN.
  * no clipping / winsorization / log payoff, raw dollar tails are reported unscaled;
  * predictions are state values, NEVER portfolio P&L.

Isolation:
  * only `lifecycle_study.discovery_days` (533 days through 2023-03-14) are ever read;
    validation days, global protected panels and FREEZE markers are never opened;
  * every iteration model is fit on TRAIN days only; TEST is never fit or tuned on;
  * TEST scoring uses the explicit `feature_` allowlist only (no execution columns,
    no next_sell_* labels, no outcome/target columns);
  * no view/feature/iteration selection is performed here.

Outputs (under --out):
  scores/<day>.parquet   keys + sequence/fold/label_status + pred_state/pred_history/pred_tape
                         (test days only)
  metadata.json          explicit train/test day sets, source pins, iteration config,
                         feature allowlists and coverage
  value_diagnostics.json iteration quantiles/convergence deltas, unscaled tail exposure,
                         weight audit, view contrast
  importance_<view>_f<k>.parquet   mean LightGBM gain importance per iteration

--smoke-days N runs a tiny non-evidence probe into <out>/smoke with identical model
parameters/iterations and smoke-only rescaled fold windows (explicitly disclosed).
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
from pathlib import Path

# bound native threads before numpy/polars/lightgbm import (serial parent compute)
for _v in ("POLARS_MAX_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_study as ls  # noqa: E402  (canonical split loader / coverage rules)

import lightgbm as lgb  # noqa: E402

SCRIPT = Path(__file__).resolve()

# ---------------------------------------------------------------- predeclared config
CLOCKS = (540, 560, 569, 571)                      # pooled; per-clock census only
FOLDS = ((0, 150, 150, 300), (0, 300, 300, 450), (0, 450, 450, 533))
N_DISCOVERY = 533
ITERATIONS = 8                                     # fixed; not a CLI knob
VIEWS = ("state", "history", "tape")
CLAIM_KEY = ("day", "clock", "rank", "ticker")     # original causal claim identity
FEATURE_PREFIX = "feature_"
HIST_PREFIX = "feature_hist_"
TAPE_PREFIX = "feature_tape_"
# cumulative by construction: state subset history subset tape
VIEW_RECIPE = {
    "state": ("feature_",),
    "history": ("feature_", "feature_hist_"),
    "tape": ("feature_", "feature_hist_", "feature_tape_"),
}
# declared non-predictors (metadata / execution / labels); never enter a matrix
NON_PREDICTORS = (
    "day", "clock", "rank", "ticker", "t", "sequence", "next_sequence", "session_end",
    "event_kind", "fill_et", "fill_px", "status",
    "sell_px", "sell_et", "sell_volume",
    "next_sell_px", "next_sell_et", "V15", "V30", "V60", "V120",
)
# substrings that must never appear in an accepted predictor name (leakage tripwire)
FORBIDDEN_SUBSTRINGS = ("next_sell", "sell_px", "sell_et", "sell_volume", "_target", "outcome")
PARAMS = {
    "objective": "regression",          # mean dollar-return regression
    "metric": "l2",
    "max_depth": 4,
    "num_leaves": 15,
    "min_data_in_leaf": 100,
    "lambda_l2": 20.0,
    "learning_rate": 0.05,
    "num_threads": 2,
    "verbosity": -1,
    "seed": 17,
    "deterministic": True,
    "force_col_wise": True,
}
QUANTILES = (0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0)
STATUS_NAMES = {0: "valid", 1: "terminal", 2: "unknown", 3: "beyond_session"}


# ---------------------------------------------------------------- small helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def qstats(a: np.ndarray) -> dict | None:
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    out = {f"p{int(q * 100):02d}": float(np.quantile(a, q)) for q in QUANTILES}
    out.update({"n": int(a.size), "mean": float(a.mean()), "std": float(a.std())})
    return out


def tail_exposure(a: np.ndarray) -> dict:
    """Unscaled dollar-tail exposure of a raw target/prediction sample. No clipping."""
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    out = {"n": int(a.size), "min": float(a.min()), "max": float(a.max())}
    for thr in (0.10, 0.30, 1.0, 2.0, 5.0):
        out[f"frac_gt_{thr:g}"] = float((a > thr).mean())
    pos = a[a > 0]
    if pos.size >= 20:
        k = max(1, int(0.01 * pos.size))
        top = np.sort(pos)[-k:]
        out["top1pct_share_of_positive_mass"] = float(top.sum() / pos.sum())
    else:
        out["top1pct_share_of_positive_mass"] = None
    return out


def matrix(df: pl.DataFrame, feats: list[str], mask: np.ndarray) -> np.ndarray:
    """NaN-preserving float32 matrix for the masked rows; missing stays missing."""
    sub = df.filter(mask)
    if not feats:
        return np.zeros((sub.height, 0), dtype=np.float32)
    x = sub.select([pl.col(c).cast(pl.Float64, strict=False) for c in feats]).to_numpy()
    x = np.asarray(x, dtype=np.float32)
    x[~np.isfinite(x)] = np.nan
    return x


def observed_coverage(df: pl.DataFrame, feats: list[str], mask: np.ndarray) -> dict:
    if not feats:
        return {}
    sub = df.filter(mask)
    if sub.height == 0:
        return {c: None for c in feats}
    row = sub.select([pl.col(c).cast(pl.Float64, strict=False).is_finite().fill_null(False)
                      .mean().alias(c) for c in feats]).row(0)
    return {c: (None if v is None else round(float(v), 6)) for c, v in zip(feats, row)}


# ---------------------------------------------------------------- event input
def feature_lists(df: pl.DataFrame) -> dict[str, list[str]]:
    """Explicit predictor allowlist: `feature_`-prefixed numeric columns only.

    Tripwires: an exact non-predictor name, or a forbidden substring, hard-fails rather
    than silently leaking execution/label/target information into a matrix.
    """
    cands = [c for c in df.columns
             if c.startswith(FEATURE_PREFIX)
             and (df.schema[c].is_numeric() or df.schema[c] == pl.Boolean)]
    for c in cands:
        low = c.lower()
        if c in NON_PREDICTORS or any(s in low for s in FORBIDDEN_SUBSTRINGS):
            raise SystemExit(f"predictor allowlist violation: '{c}' looks like metadata/"
                             f"execution/label; refusing to train on it")
    state = [c for c in cands if not c.startswith(HIST_PREFIX) and not c.startswith(TAPE_PREFIX)]
    hist = state + [c for c in cands if c.startswith(HIST_PREFIX)]
    tape = hist + [c for c in cands if c.startswith(TAPE_PREFIX)]
    if len(state) < 3:
        raise SystemExit(f"insufficient state features (found {len(state)}): {state[:8]}")
    return {"state": state, "history": hist, "tape": tape}


def resolve_event_days(days: list[str], events_root: Path) -> tuple[list[str], list[dict]]:
    """Strict per-day coverage: a missing marker is a hard error (never a silent drop)."""
    ev_dir = events_root / "events"
    done_dirs = [events_root / "_done", ev_dir / "_done"]
    markers, usable = [], []
    for day in days:
        mpath = next((d / f"{day}.json" for d in done_dirs if (d / f"{day}.json").exists()), None)
        if mpath is None:
            raise SystemExit(f"owned-claim day marker missing for {day} (looked in "
                             f"{[str(d) for d in done_dirs]}); refusing to silently drop days")
        meta = json.loads(mpath.read_text())
        status = str(meta.get("status", "")).lower()
        if status not in ("ok", "empty"):
            raise SystemExit(f"owned-claim day {day} marker status={status!r}; refusing")
        markers.append({"day": day, "status": status, "marker": str(mpath),
                        "source_sha256": meta.get("source_sha256"),
                        "rows": meta.get("rows", meta.get("events"))})
        if status == "ok":
            usable.append(day)
    return usable, markers


def load_events(days: list[str], events_root: Path) -> tuple[pl.DataFrame, dict]:
    ev_dir = events_root / "events"
    frames, digests, missing = [], {}, []
    for day in days:
        p = ev_dir / f"{day}.parquet"
        if not p.exists():
            missing.append(day)
            continue
        digests[day] = sha256_file(p)
        frames.append(pl.read_parquet(p))
    if missing:
        raise SystemExit(f"events parquet missing for marked-ok days: {missing[:5]}")
    if not frames:
        raise SystemExit(f"no event rows found under {ev_dir}")
    df = pl.concat(frames, how="diagonal_relaxed")
    for col in ("day", "clock", "rank", "ticker", "t", "sequence", "next_sequence",
                "sell_px", "sell_et", "fill_px", "session_end", "event_kind"):
        if col not in df.columns:
            raise SystemExit(f"event schema missing required column '{col}': {df.columns}")
    # canonical order: claim then monotone sequence; _ri is then the POSITIONAL row index
    df = df.sort([*CLAIM_KEY, "sequence"]).with_row_index("_ri")
    return df, digests


def resolve_next(df: pl.DataFrame) -> np.ndarray:
    """Row index of the strictly-later executable next event (next_sequence pointer)."""
    key = list(CLAIM_KEY)
    dups = df.group_by([*key, "sequence"]).len().filter(pl.col("len") > 1)
    if dups.height:
        raise SystemExit(f"claim sequence not unique at {dups.height} (claim,sequence) keys")
    chk = df.group_by(key).agg(pl.col("sequence").min().alias("mn"),
                               pl.col("sequence").max().alias("mx"),
                               pl.len().alias("n"),
                               pl.col("sequence").n_unique().alias("u"))
    bad = chk.filter((pl.col("mn") != 0) | (pl.col("u") != pl.col("n")) |
                     (pl.col("mx") != pl.col("n") - 1))
    if bad.height:
        raise SystemExit(f"claim sequence not 0-based monotone on {bad.height} claims")
    df = df.with_columns(pl.col("next_sequence").cast(pl.Int64, strict=False).alias("_nseq"))
    probe = (df.select([*key, pl.col("sequence").alias("_nseq"), pl.col("_ri").alias("_nx")])
             .drop_nulls("_nseq").unique(subset=[*key, "_nseq"]))
    j = df.join(probe, on=[*key, "_nseq"], how="left").sort("_ri")
    nx = j["_nx"].fill_null(-1).to_numpy().astype(np.int64)
    ri = df["_ri"].to_numpy().astype(np.int64)
    if not np.array_equal(ri, np.arange(df.height, dtype=np.int64)):
        raise SystemExit("internal ordering error: _ri is not positional")
    want = (df["next_sequence"].cast(pl.Int64, strict=False).fill_null(-1).to_numpy() >= 0)
    unresolved = want & (nx < 0)
    if unresolved.any():
        bad_rows = df.filter(pl.Series(unresolved))["sequence"].head(3).to_list()
        raise SystemExit(f"{int(unresolved.sum())} rows point at a nonexistent next event "
                         f"(example sequences {bad_rows})")
    if (nx[want] <= ri[want]).any():
        raise SystemExit("next_sequence does not point strictly forward within the claim")
    return nx


def classify(df: pl.DataFrame, nx: np.ndarray) -> tuple[np.ndarray, dict]:
    """Per-row outcome status. valid=fit; terminal/beyond pinned to 0; unknown=UNKNOWN."""
    n = df.height
    t = df["t"].to_numpy().astype(np.float64)
    se = (df["session_end"].to_numpy().astype(np.float64) if "session_end" in df.columns
          else np.full(n, np.inf))
    kind = (df["event_kind"].cast(pl.Utf8).to_numpy() if "event_kind" in df.columns
            else np.array([""] * n, dtype=object))
    spx = pd_num(df, "sell_px")
    set_ = pd_num(df, "sell_et")
    npx = pd_num(df, "next_sell_px")
    net = pd_num(df, "next_sell_et")
    entry = pd_num(df, "fill_px")
    has = nx >= 0
    beyond = np.isfinite(se) & (t > se)
    terminal = (kind == "terminal") | (np.isfinite(se) & (t >= se))
    terminal &= ~beyond

    # prefer the declared next_sell_* labels; fall back to the next event's own execution
    derived_px = np.where(has, np.where(nx >= 0, spx[np.clip(nx, 0, n - 1)], np.nan), np.nan)
    derived_et = np.where(has, np.where(nx >= 0, set_[np.clip(nx, 0, n - 1)], np.nan), np.nan)
    npx = np.where(np.isfinite(npx), npx, derived_px)
    net = np.where(np.isfinite(net), net, derived_et)
    mismatch = has & np.isfinite(npx) & np.isfinite(derived_px) & \
        (np.abs(npx - derived_px) > 1e-6 * np.maximum(1.0, np.abs(derived_px)))

    se_nx = np.where(has, se[np.clip(nx, 0, n - 1)], np.nan)
    t_nx = t[np.clip(nx, 0, n - 1)]
    exec_ok = has & np.isfinite(npx) & np.isfinite(net) & np.isfinite(spx) & np.isfinite(set_) \
        & np.isfinite(se) & np.isfinite(se_nx) & (spx > 0) & (npx > 0) & (net > set_) \
        & (set_ <= se) & (net <= se_nx) & (t_nx >= set_) & np.isfinite(entry) & (entry > 0)
    exec_beyond = has & (np.isfinite(net) & np.isfinite(se_nx) & (net > se_nx))
    status = np.full(n, 2, dtype=np.int8)                 # unknown
    status[terminal] = 1
    status[beyond] = 3
    status[exec_ok & ~terminal & ~beyond] = 0             # valid
    # resolve_next proves nx>ri; one reverse sweep handles any legal chain length.
    v0 = np.flatnonzero(status == 0)
    direct_bad = int((~np.isin(status[nx[v0]], (0, 1))).sum())
    for i in range(n - 1, -1, -1):
        if status[i] == 0 and status[nx[i]] not in (0, 1):
            status[i] = 2

    reasons = {
        "no_next_pointer": int((~has).sum()),
        "terminal_rows": int((status == 1).sum()),
        "beyond_session_rows": int((status == 3).sum()),
        "unknown_rows": int((status == 2).sum()),
        "valid_rows": int((status == 0).sum()),
        "next_label_nonfinite_or_not_later": int((has & ~exec_ok & ~exec_beyond).sum()),
        "next_execution_beyond_session": int(exec_beyond.sum()),
        "next_status_unknown_direct": direct_bad,
        "next_status_unknown_propagated": int(v0.size - (status == 0).sum() - direct_bad),
        "next_status_unusable": int(v0.size - (status == 0).sum()),
        "declared_vs_derived_px_mismatch": int(mismatch.sum()),
    }
    if status.size and (status == 0).sum() == 0:
        raise SystemExit("no valid (strictly later executable) retention rows at all; "
                         "refusing to fit a value function with no target")
    return status, reasons


def pd_num(df: pl.DataFrame, col: str) -> np.ndarray:
    if col not in df.columns:
        return np.full(df.height, np.nan)
    return df[col].cast(pl.Float64, strict=False).to_numpy().astype(np.float64)


# ---------------------------------------------------------------- weights
def fold_weights(df: pl.DataFrame, tr_mask: np.ndarray, status: np.ndarray):
    """Inverse claim-event-count weights, then exactly one unit per day.

    Each claim's events share 1/(claim events in the train window); per-day division makes
    every train day contribute the same total weight regardless of blocked/roster counts.
    """
    key = list(CLAIM_KEY)
    sub = df.filter(tr_mask).with_columns(pl.Series("_valid", (status[tr_mask] == 0)))
    claim_n = sub.group_by(key).agg(pl.len().alias("_claim_n"))
    sub = sub.join(claim_n, on=key, how="left")
    sub = sub.with_columns((1.0 / pl.col("_claim_n")).alias("_raw"))
    day_sum = (sub.filter(pl.col("_valid")).group_by("day")
               .agg(pl.col("_raw").sum().alias("_day_raw")))
    sub = sub.join(day_sum, on="day", how="left")
    w = sub.filter(pl.col("_valid"))["_raw"].to_numpy() / \
        sub.filter(pl.col("_valid"))["_day_raw"].to_numpy()
    if w.size == 0 or not np.isfinite(w).all():
        raise SystemExit("no finite fit weights in fold; refusing to fit")
    w = w * (w.size / w.sum())
    # audit
    days = sub.filter(pl.col("_valid"))["day"].to_numpy()
    inv = np.unique(days, return_inverse=True)[1]
    day_sums = np.bincount(inv, weights=w)
    claims = sub.filter(pl.col("_valid")).select(key).unique().height
    cn = sub.filter(pl.col("_valid")).group_by(key).agg(pl.col("_claim_n").max().alias("n"))
    audit = {
        "fit_rows": int(w.size),
        "train_rows": int(tr_mask.sum()),
        "days": int(len(day_sums)),
        "claims": int(claims),
        "claim_event_count_min": int(cn["n"].min()),
        "claim_event_count_max": int(cn["n"].max()),
        "day_weight_sum_min": float(day_sums.min()),
        "day_weight_sum_max": float(day_sums.max()),
        "hint": "each train day's fit weight sums to fit_rows/days (equal day contribution)",
    }
    return w, audit


# ---------------------------------------------------------------- one view / fold
def run_view(df: pl.DataFrame, feats: list[str], tr_mask: np.ndarray, te_mask: np.ndarray,
             status: np.ndarray, nx: np.ndarray, px: np.ndarray,
             npx: np.ndarray, w: np.ndarray,
             view: str, fold: int) -> dict:
    tr_pos = np.flatnonzero(tr_mask)
    te_pos = np.flatnonzero(te_mask)
    fit_glob = np.flatnonzero(tr_mask & (status == 0))
    # global row -> local position in the train matrix (all rows of the train days)
    loc_arr = np.full(df.height, -1, dtype=np.int64)
    loc_arr[tr_pos] = np.arange(len(tr_pos), dtype=np.int64)
    fit_local = loc_arr[fit_glob]

    x_tr = matrix(df, feats, tr_mask)
    x_te = matrix(df, feats, te_mask)
    nxt_glob = nx[fit_glob]
    nxt_local = loc_arr[nxt_glob]
    if (nxt_local < 0).any():
        raise SystemExit("a valid train row points at a next event outside its train days")
    step_dollars = (npx[fit_glob] - px[fit_glob]) / pd_num(df, "fill_px")[fit_glob]
    records, gains = [], []
    p_prev_tr = np.zeros(len(tr_pos), dtype=np.float64)
    p_prev_te = np.zeros(len(te_pos), dtype=np.float64)
    p_final_te = np.zeros(len(te_pos), dtype=np.float64)
    # Feature bins and weights are invariant over value iterations. Reuse one
    # constructed Dataset; only its labels change.
    ds = lgb.Dataset(x_tr[fit_local], label=step_dollars, weight=w, feature_name=feats,
                     params=PARAMS, free_raw_data=False).construct()
    for it in range(ITERATIONS):
        # terminal next event has q = 0; otherwise the previous round's (non-negative) value
        q_next = np.where(status[nxt_glob] == 1, 0.0,
                          np.maximum(0.0, p_prev_tr[nxt_local]))
        y = step_dollars + q_next
        ds.set_label(y)
        model = lgb.train(PARAMS, ds, num_boost_round=80)
        p_tr = model.predict(x_tr, num_threads=PARAMS["num_threads"]).astype(np.float64)
        p_te = model.predict(x_te, num_threads=PARAMS["num_threads"]).astype(np.float64)
        d_tr = np.abs(p_tr - p_prev_tr)
        d_te = np.abs(p_te - p_prev_te)
        gains.append(np.asarray(model.feature_importance(importance_type="gain"),
                                dtype=np.float64))
        records.append({
            "fold": fold, "view": view, "iteration": it,
            "rows_fit": int(len(fit_local)), "features": len(feats),
            "rounds": 80,
            "target_quantiles": qstats(y),
            "target_tail": tail_exposure(y),
            "train_pred_quantiles": qstats(p_tr),
            "test_pred_quantiles": qstats(p_te),
            "delta_train_mean_abs": float(d_tr.mean()), "delta_train_max_abs": float(d_tr.max()),
            "delta_test_mean_abs": float(d_te.mean()), "delta_test_max_abs": float(d_te.max()),
        })
        p_prev_tr, p_prev_te = p_tr, p_te
        p_final_te = p_te
        del model
    # Only the calendar-known terminal state has zero continuation.
    pin = status[te_pos] == 1
    p_final_te = p_final_te.copy()
    p_final_te[pin] = 0.0
    imp = np.mean(np.stack(gains), axis=0)
    top = sorted(zip(feats, imp.tolist()), key=lambda kv: kv[1], reverse=True)
    diag = {
        "fold": fold, "view": view,
        "features": feats,
        "importance_mean_gain": {k: v for k, v in top},
        "importance_gain_by_iteration": {f"it{it}": {k: float(v) for k, v in
                                                     zip(feats, g.tolist())}
                                         for it, g in enumerate(gains)},
        "iterations": records,
        "convergence": {
            "train_mean_abs_delta_last2": float(records[-1]["delta_train_mean_abs"]),
            "test_mean_abs_delta_last2": float(records[-1]["delta_test_mean_abs"]),
            "train_mean_abs_delta_all": [r["delta_train_mean_abs"] for r in records],
            "test_mean_abs_delta_all": [r["delta_test_mean_abs"] for r in records],
        },
        "final_test_tail": tail_exposure(p_final_te),
        "final_train_tail": tail_exposure(p_prev_tr),
        "pinned_terminal_test_rows": int(pin.sum()),
    }
    del ds, x_tr, x_te
    gc.collect()
    return {"pred_te": p_final_te.astype(np.float32), "diag": diag, "imp": (feats, imp)}


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="owned-claim predeclared value iteration")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--events-root", default=None,
                    help="default <data-root>/harvest01/lifecycle/v2/owned_claim")
    ap.add_argument("--out", default=None,
                    help="default <events-root>/value")
    ap.add_argument("--views", default=",".join(VIEWS))
    ap.add_argument("--folds", default=",".join(str(i) for i in range(len(FOLDS))),
                    help="fold indices to run (subset = non-evidence diagnostic run)")
    ap.add_argument("--smoke-days", type=int, default=0,
                    help="tiny non-evidence run over the first N discovery days")
    args = ap.parse_args()

    data_root = ls.bps.resolve_data_root(args.data_root)
    split = ls.load_split(data_root)
    disc_all = list(split["discovery_days"])
    if len(disc_all) != N_DISCOVERY:
        raise SystemExit(f"discovery day count {len(disc_all)} != {N_DISCOVERY}")
    disc = list(disc_all)
    events_root = Path(args.events_root) if args.events_root else ls.v2_dir(data_root) / "owned_claim"
    out = Path(args.out) if args.out else events_root / "value"

    views = tuple(v.strip() for v in args.views.split(",") if v.strip())
    bad = [v for v in views if v not in VIEWS]
    if bad or not views:
        raise SystemExit(f"--views must be a subset of {VIEWS}; got {views}")

    fold_ids = [int(x) for x in args.folds.split(",") if x.strip() != ""]
    if not fold_ids or any(i < 0 or i >= len(FOLDS) for i in fold_ids):
        raise SystemExit(f"--folds must index {list(range(len(FOLDS)))}")

    evidence, smoke_note = True, None
    if args.smoke_days:
        n = int(args.smoke_days)
        if n < 12:
            raise SystemExit("--smoke-days must be >= 12 to form a tiny train/test split")
        disc = disc[:n]
        b1, b2 = max(2, int(np.ceil(0.281 * n))), max(4, int(np.ceil(0.563 * n)))
        folds = ((0, b1, b1, b2), (0, b2, b2, n))
        fold_ids = [i for i in fold_ids if 0 <= i < len(folds)]
        if not fold_ids:
            raise SystemExit("no smoke fold selected; use --folds 0 and/or 1")
        out = out / "smoke"
        evidence = False
        smoke_note = (f"non-evidence smoke run over first {n} discovery days; fold windows "
                      f"rescaled to the smoke day count; model parameters/iterations unchanged")
    else:
        folds = FOLDS
    if len(fold_ids) != len(folds) or tuple(fold_ids) != tuple(range(len(folds))):
        evidence = False
        smoke_note = (smoke_note + "; " if smoke_note else "") + \
            f"fold subset {fold_ids} of {list(range(len(folds)))} (diagnostic only)"
    if tuple(views) != VIEWS:
        evidence = False
        smoke_note = (smoke_note + "; " if smoke_note else "") + f"view subset {list(views)}"

    # day windows (contract indices into the canonical chronological discovery list)
    windows = []
    for i in fold_ids:
        tr0, tr1, te0, te1 = folds[i]
        tr0, tr1, te0, te1 = min(tr0, len(disc)), min(tr1, len(disc)), \
            min(te0, len(disc)), min(te1, len(disc))
        if te0 >= te1:
            raise SystemExit(f"fold {i}: empty test window after clamping ({te0},{te1})")
        windows.append({"fold": i, "train_idx": [tr0, tr1], "test_idx": [te0, te1],
                        "train_days": disc[tr0:tr1], "test_days": disc[te0:te1]})
    days_needed = sorted({d for w in windows for d in w["train_days"] + w["test_days"]})
    usable, markers = resolve_event_days(days_needed, events_root)
    df, digests = load_events(usable, events_root)
    df = df.with_columns(pl.col("day").cast(pl.Utf8))
    if not set(df["day"].unique().to_list()) <= set(disc_all):
        raise SystemExit("event rows contain days outside the discovery split; refusing")

    feats_by_view = feature_lists(df)
    nx = resolve_next(df)
    status, reasons = classify(df, nx)
    px = pd_num(df, "sell_px")
    n_days = len(usable)

    manifest_pin = None
    mpath = events_root / "manifest.json"
    if mpath.exists():
        man = json.loads(mpath.read_text())
        manifest_pin = {"path": str(mpath), "sha256": sha256_file(mpath),
                        "source_sha256": man.get("source_sha256"),
                        "n_feature_columns": len(man.get("feature_columns", []) or []),
                        "discovery_days": len(man.get("discovery_days", []) or [])}
        allowed_manifest_days = [disc_all] + ([disc] if args.smoke_days else [])
        if man.get("discovery_days") and list(man["discovery_days"]) not in allowed_manifest_days:
            raise SystemExit("event manifest days disagree with the split or explicit smoke prefix")

    n = df.height
    pred = {v: np.full(n, np.nan, dtype=np.float32) for v in views}
    fold_of = np.full(n, -1, dtype=np.int32)
    diags, weights_audit, coverage = [], {}, {}
    imp_frames = []
    out.mkdir(parents=True, exist_ok=True)
    cache_dir=out/"cache"
    progress_dir=out/"progress_scores"
    cache_dir.mkdir(exist_ok=True)
    progress_dir.mkdir(exist_ok=True)
    run_id=hashlib.sha256((sha256_file(SCRIPT)+json.dumps(digests,sort_keys=True)
                          +json.dumps(windows,sort_keys=True)+json.dumps(views)).encode()).hexdigest()
    (out/"metadata.json").write_text(json.dumps({"status":"running","run_id":run_id})+"\n")

    for w in windows:
        k = w["fold"]
        tr_mask = df["day"].is_in(w["train_days"]).to_numpy()
        te_mask = df["day"].is_in(w["test_days"]).to_numpy()
        wfit, waudit = fold_weights(df, tr_mask, status)
        waudit.update({"fold": k, "train_days": len(w["train_days"]),
                       "test_days": len(w["test_days"])})
        weights_audit[f"fold{k}"] = waudit
        for view in views:
            feats = feats_by_view[view]
            coverage[f"fold{k}_{view}"] = {
                "n_features": len(feats),
                "train_observed_fraction": observed_coverage(df, feats, tr_mask),
                "test_observed_fraction": observed_coverage(df, feats, te_mask),
            }
            cache_json=cache_dir/f"{view}_fold{k}.json"
            cache_pred=cache_dir/f"{view}_fold{k}.parquet"
            cached=json.loads(cache_json.read_text()) if cache_json.exists() else {}
            if cached.get("run_id")==run_id and cache_pred.exists():
                pp=pl.read_parquet(cache_pred)["pred"].to_numpy()
                if len(pp)!=int(te_mask.sum()):
                    raise SystemExit("cached prediction shape mismatch")
                res={"pred_te":pp,"diag":cached["diag"],
                     "imp":(feats,np.array(cached["importance"]))}
            else:
                res = run_view(df, feats, tr_mask, te_mask, status, nx, px,
                               pd_num(df, "next_sell_px"), wfit, view, k)
                pl.DataFrame({"pred":res["pred_te"]}).write_parquet(cache_pred)
                cache_json.write_text(json.dumps({"run_id":run_id,"diag":res["diag"],
                    "importance":res["imp"][1].tolist()},allow_nan=False))
            progress=(df.filter(pl.Series(te_mask)).select(["day","clock","rank","ticker","t","sequence"])
                      .with_columns(pl.Series(f"pred_{view}",res["pred_te"])))
            for (progress_day,),frame in progress.partition_by("day",as_dict=True).items():
                frame.write_parquet(progress_dir/f"{progress_day}.{view}.parquet")
            print(f"Completed fold{k} {view}: {int(te_mask.sum())} test events",flush=True)
            pred[view][te_mask] = res["pred_te"]
            fold_of[te_mask] = k
            diags.append(res["diag"])
            f, imp = res["imp"]
            imp_frames.append(pl.DataFrame({"fold": k, "view": view, "feature": f,
                                            "gain_mean_over_iterations": imp}))
        del wfit

    test_mask = fold_of >= 0
    scored = df.select(["day", "clock", "rank", "ticker", "t", "sequence"]) \
        .with_columns([
            pl.Series("fold", fold_of),
            pl.Series("label_status", [STATUS_NAMES[int(s)] for s in status]),
        ] + [pl.Series(f"pred_{v}", pred[v]) for v in views]) \
        .filter(pl.Series(test_mask))
    scored = scored.sort(["day", "clock", "sequence"])

    out.mkdir(parents=True, exist_ok=True)
    scores_dir = out / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for (day,), part in scored.partition_by("day", as_dict=True).items():
        part.write_parquet(scores_dir / f"{day}.parquet")
        written.append({"day": day, "rows": part.height})
    for fr in imp_frames:
        for (k, v), part in fr.partition_by(["fold", "view"], as_dict=True).items():
            part.drop(["fold", "view"]).sort("gain_mean_over_iterations", descending=True) \
                .write_parquet(out / f"importance_{v}_f{k}.parquet")

    # per-clock census (counts only; not metrics)
    clock_census = {}
    for c in CLOCKS:
        m = df["clock"].to_numpy() == c
        clock_census[str(c)] = {
            "scored_rows": int((test_mask & m).sum()),
            "status_counts": {STATUS_NAMES[s]: int((m & (status == s)).sum())
                              for s in STATUS_NAMES},
        }
    # view contrast on common known rows (all tape features observed) — no selection
    common = test_mask.copy()
    tape_feats = feats_by_view["tape"]
    if tape_feats:
        fin = df.select([pl.col(c).cast(pl.Float64, strict=False).is_finite().alias(c)
                         for c in tape_feats]).to_numpy().all(axis=1)
        common &= fin
    contrast = {"common_known_rows": int(common.sum()), "scored_rows": int(test_mask.sum()),
                "note": "views are predeclared and cumulative; no view/feature selection",
                "pairs": {}}
    if common.sum() >= 20:
        for a in range(len(views)):
            for b in range(a + 1, len(views)):
                pa = np.nan_to_num(pred[views[a]][common])
                pb = np.nan_to_num(pred[views[b]][common])
                contrast["pairs"][f"{views[a]}~{views[b]}"] = {
                    "sign_agreement": float(np.mean(np.sign(pa) == np.sign(pb))),
                    "pearson": float(np.corrcoef(pa, pb)[0, 1]),
                    "mean_a": float(pa.mean()), "mean_b": float(pb.mean()),
                }

    meta = {
        "status": "complete",
        "run_id": run_id,
        "producer": "factory/scripts/owned_claim_value.py",
        "script_sha256": sha256_file(SCRIPT),
        "contract": "factory/artifacts/owned_claim_contract.json",
        "contract_sha256": sha256_file(SCRIPT.parents[1]/"artifacts/owned_claim_contract.json"),
        "evidence": evidence,
        "non_evidence_note": smoke_note,
        "predictions_are": "incremental inherited-share dollars per original gross entry notional; NOT P&L",
        "clocks_pooled": list(CLOCKS),
        "views_definition": {v: list(VIEW_RECIPE[v]) for v in VIEWS},
        "views_run": list(views),
        "iterations": ITERATIONS,
        "rounds_per_iteration": 80,
        "params": PARAMS,
        "seed": PARAMS["seed"],
        "objective": "(next_sale-current_sale)/original_fill + max(0,next_owned_value); entry sunk, exit fee common; no clipping",
        "folds": [{"fold": w["fold"], "train_index": w["train_idx"], "test_index": w["test_idx"],
                   "train_days": w["train_days"], "test_days": w["test_days"]} for w in windows],
        "discovery_days_total": len(disc_all),
        "days_used": usable,
        "events_root": str(events_root),
        "events_glob": "events/<day>.parquet",
        "data_root": str(data_root),
        "split_sha256": sha256_file(ls.v2_dir(data_root) / "split.json"),
        "event_manifest": manifest_pin,
        "event_day_sha256": digests,
        "day_markers": markers,
        "feature_allowlist_rule": ("columns starting with 'feature_', numeric/bool, minus "
                                   "metadata/execution/label columns; state excludes "
                                   "'"+HIST_PREFIX+"'/'"+TAPE_PREFIX+"'; history adds "
                                   "'"+HIST_PREFIX+"'; tape adds '"+TAPE_PREFIX+"'"),
        "non_predictors": list(NON_PREDICTORS),
        "forbidden_substrings": list(FORBIDDEN_SUBSTRINGS),
        "feature_views": feats_by_view,
        "row_census": {"events": int(n), "days": n_days, "claims":
                       int(df.select(list(CLAIM_KEY)).unique().height),
                       "scored_test_rows": int(test_mask.sum()),
                       "status": reasons, "per_clock": clock_census},
        "coverage": coverage,
        "outputs": {"scores_dir": str(scores_dir), "diagnostics": str(out / "value_diagnostics.json"),
                    "importance_dir": str(out)},
        "test_days_written": written,
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))

    diag_out = {
        "search": {"views": list(views), "iterations": ITERATIONS, "tuning": "none",
                   "feature_or_view_selection": "none", "folds_run": fold_ids},
        "status_counts": reasons,
        "weight_audit": weights_audit,
        "view_contrast": contrast,
        "per_clock_census": clock_census,
        "models": diags,
        "test_prediction_tail_by_view": {
            v: tail_exposure(pred[v][test_mask]) for v in views},
    }
    (out / "value_diagnostics.json").write_text(json.dumps(diag_out, indent=2, default=str))
    print(f"[owned_claim_value] evidence={evidence} days={n_days} events={n} "
          f"valid={reasons['valid_rows']} test_rows={int(test_mask.sum())} views={list(views)} "
          f"folds={fold_ids} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
