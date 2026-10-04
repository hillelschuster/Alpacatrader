#!/usr/bin/env python3
"""OWNED-CLAIM VALUE — predeclared train-only fitted stopping-value iteration.

Independent producer for the owned-claim decision: reads parent-owned event rows
(past-only `feature_*` states, execution columns, next-event label pointers), fits, per
fixed chronological fold and per declared feature view, a fixed number of fitted
value-iteration rounds, and writes TEST predictions only.

Economics kept exactly as contracted:
  * selling now is the reference (incremental inherited dollars 0);
  * value-iteration control (declared limited benchmark): (next_sale-current_sale)/fill
    + max(0,q(next)) over the fixed strict-next support;
  * policy_return: emitted-event chronological RETAIN/WAIT target =
    (chosen-policy exit - current sale)/fill, following the previous policy's actual
    stops one causal decision event at a time; the all-zero first round is a
    full-session hold initialization ruler only and never the resulting policy; the
    strict-next control classification and its status==0 support are NEVER built on this
    path (scoped to value_iteration), so valid wait-liquidations that reprint the same
    delayed opening (zero incremental dollars, not missing data) are still fitted;
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
                         (test days only; label_status is the strict-control status for
                         value_iteration and the policy_return availability category
                         otherwise — see metadata label_method.outcome_status)
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
for _v in (
    "POLARS_MAX_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "2")

import numpy as np  # noqa: E402  (native thread limits must precede numerical imports)
import polars as pl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_study as ls  # noqa: E402  (canonical split loader / coverage rules)
import lightgbm as lgb  # noqa: E402
import owned_claim_policy_targets as opt  # noqa: E402  (chronological policy targets)

SCRIPT = Path(__file__).resolve()
POLICY_TARGETS = SCRIPT.parent / "owned_claim_policy_targets.py"

# ---------------------------------------------------------------- predeclared config
CLOCKS = (540, 560, 569, 571)  # pooled; per-clock census only
FOLDS = ((0, 150, 150, 300), (0, 300, 300, 450), (0, 450, 450, 533))
N_DISCOVERY = 533
ITERATIONS = 8  # fixed; not a CLI knob
VIEWS = ("state", "history", "tape")
CLAIM_KEY = ("day", "clock", "rank", "ticker")  # original causal claim identity
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
    "day",
    "clock",
    "rank",
    "ticker",
    "t",
    "sequence",
    "next_sequence",
    "session_end",
    "event_kind",
    "fill_et",
    "fill_px",
    "status",
    "sell_px",
    "sell_et",
    "sell_volume",
    "next_sell_px",
    "next_sell_et",
    "V15",
    "V30",
    "V60",
    "V120",
)
# substrings that must never appear in an accepted predictor name (leakage tripwire)
FORBIDDEN_SUBSTRINGS = ("next_sell", "sell_px", "sell_et", "sell_volume", "_target", "outcome")
PARAMS = {
    "objective": "regression",  # mean dollar-return regression
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
# policy_return per-row diagnostic categories (see policy_label_status); deliberately
# distinct from STATUS_NAMES so a policy run is never read as the strict control.
POLICY_STATUS_NAMES = ("wait", "terminal", "unknown", "last_event")


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
        return dict.fromkeys(feats)
    row = sub.select(
        [
            pl.col(c).cast(pl.Float64, strict=False).is_finite().fill_null(False).mean().alias(c)
            for c in feats
        ]
    ).row(0)
    return {c: (None if v is None else round(float(v), 6)) for c, v in zip(feats, row, strict=True)}


# ---------------------------------------------------------------- event input
def feature_lists(df: pl.DataFrame) -> dict[str, list[str]]:
    """Explicit predictor allowlist: `feature_`-prefixed numeric columns only.

    Tripwires: an exact non-predictor name, or a forbidden substring, hard-fails rather
    than silently leaking execution/label/target information into a matrix.
    """
    cands = [
        c
        for c in df.columns
        if c.startswith(FEATURE_PREFIX)
        and (df.schema[c].is_numeric() or df.schema[c] == pl.Boolean)
    ]
    for c in cands:
        low = c.lower()
        if c in NON_PREDICTORS or any(s in low for s in FORBIDDEN_SUBSTRINGS):
            raise SystemExit(
                f"predictor allowlist violation: '{c}' looks like metadata/"
                f"execution/label; refusing to train on it"
            )
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
            raise SystemExit(
                f"owned-claim day marker missing for {day} (looked in "
                f"{[str(d) for d in done_dirs]}); refusing to silently drop days"
            )
        meta = json.loads(mpath.read_text())
        status = str(meta.get("status", "")).lower()
        if status not in ("ok", "empty"):
            raise SystemExit(f"owned-claim day {day} marker status={status!r}; refusing")
        markers.append(
            {
                "day": day,
                "status": status,
                "marker": str(mpath),
                "source_sha256": meta.get("source_sha256"),
                "rows": meta.get("rows", meta.get("events")),
            }
        )
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
    for col in (
        "day",
        "clock",
        "rank",
        "ticker",
        "t",
        "sequence",
        "next_sequence",
        "sell_px",
        "sell_et",
        "fill_px",
        "session_end",
        "event_kind",
    ):
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
    chk = df.group_by(key).agg(
        pl.col("sequence").min().alias("mn"),
        pl.col("sequence").max().alias("mx"),
        pl.len().alias("n"),
        pl.col("sequence").n_unique().alias("u"),
    )
    bad = chk.filter(
        (pl.col("mn") != 0) | (pl.col("u") != pl.col("n")) | (pl.col("mx") != pl.col("n") - 1)
    )
    if bad.height:
        raise SystemExit(f"claim sequence not 0-based monotone on {bad.height} claims")
    df = df.with_columns(pl.col("next_sequence").cast(pl.Int64, strict=False).alias("_nseq"))
    probe = (
        df.select([*key, pl.col("sequence").alias("_nseq"), pl.col("_ri").alias("_nx")])
        .drop_nulls("_nseq")
        .unique(subset=[*key, "_nseq"])
    )
    j = df.join(probe, on=[*key, "_nseq"], how="left").sort("_ri")
    nx = j["_nx"].fill_null(-1).to_numpy().astype(np.int64)
    ri = df["_ri"].to_numpy().astype(np.int64)
    if not np.array_equal(ri, np.arange(df.height, dtype=np.int64)):
        raise SystemExit("internal ordering error: _ri is not positional")
    want = df["next_sequence"].cast(pl.Int64, strict=False).fill_null(-1).to_numpy() >= 0
    unresolved = want & (nx < 0)
    if unresolved.any():
        bad_rows = df.filter(pl.Series(unresolved))["sequence"].head(3).to_list()
        raise SystemExit(
            f"{int(unresolved.sum())} rows point at a nonexistent next event "
            f"(example sequences {bad_rows})"
        )
    if (nx[want] <= ri[want]).any():
        raise SystemExit("next_sequence does not point strictly forward within the claim")
    return nx


def classify(df: pl.DataFrame, nx: np.ndarray) -> tuple[np.ndarray, dict]:
    """Per-row outcome status. valid=fit; terminal/beyond pinned to 0; unknown=UNKNOWN."""
    n = df.height
    t = df["t"].to_numpy().astype(np.float64)
    se = (
        df["session_end"].to_numpy().astype(np.float64)
        if "session_end" in df.columns
        else np.full(n, np.inf)
    )
    kind = (
        df["event_kind"].cast(pl.Utf8).to_numpy()
        if "event_kind" in df.columns
        else np.array([""] * n, dtype=object)
    )
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
    mismatch = (
        has
        & np.isfinite(npx)
        & np.isfinite(derived_px)
        & (np.abs(npx - derived_px) > 1e-6 * np.maximum(1.0, np.abs(derived_px)))
    )

    se_nx = np.where(has, se[np.clip(nx, 0, n - 1)], np.nan)
    t_nx = t[np.clip(nx, 0, n - 1)]
    exec_ok = (
        has
        & np.isfinite(npx)
        & np.isfinite(net)
        & np.isfinite(spx)
        & np.isfinite(set_)
        & np.isfinite(se)
        & np.isfinite(se_nx)
        & (spx > 0)
        & (npx > 0)
        & (net > set_)
        & (set_ <= se)
        & (net <= se_nx)
        & (t_nx >= set_)
        & np.isfinite(entry)
        & (entry > 0)
    )
    exec_beyond = has & (np.isfinite(net) & np.isfinite(se_nx) & (net > se_nx))
    status = np.full(n, 2, dtype=np.int8)  # unknown
    status[terminal] = 1
    status[beyond] = 3
    status[exec_ok & ~terminal & ~beyond] = 0  # valid
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
        raise SystemExit(
            "no valid (strictly later executable) retention rows at all; "
            "refusing to fit a value function with no target"
        )
    return status, reasons


def pd_num(df: pl.DataFrame, col: str) -> np.ndarray:
    if col not in df.columns:
        return np.full(df.height, np.nan)
    return df[col].cast(pl.Float64, strict=False).to_numpy().astype(np.float64)


def chronological_metadata(
    df: pl.DataFrame, px: np.ndarray, se: np.ndarray, sell_et: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Emitted-event successor, executable-sale flag and calendar-terminal mask.

    Claim-contiguous, ``sequence``-ordered rows (producer-guaranteed) give each row's
    next emitted event -- the only forward-looking structure the policy_return walk
    needs. No strict ``next_sequence`` liquidation pointer and no outcome chain are
    consulted. ``executable`` is whether this event's own sale is finite, positive and
    in session; ``calendar_terminal`` is the session boundary ``t >= session_end``.
    """
    n = df.height
    seq_all = df["sequence"].to_numpy().astype(np.int64)
    same = np.ones(n, dtype=bool)
    for c in CLAIM_KEY:
        v = df[c].to_numpy()
        same[1:] &= v[1:] == v[:-1]
    starts = ~same
    starts[0] = True
    grp = (np.cumsum(starts) - 1).astype(np.int64)
    successor = opt.chronological_successor(seq_all, grp)
    executable = (
        np.isfinite(px) & (px > 0) & np.isfinite(sell_et) & np.isfinite(se) & (sell_et <= se)
    )
    calendar_terminal = np.isfinite(se) & (pd_num(df, "t") >= se)
    return successor, executable, calendar_terminal


def policy_label_status(
    executable: np.ndarray, calendar_terminal: np.ndarray, successor: np.ndarray
) -> np.ndarray:
    """Per-row label availability for ``policy_return`` (diagnostic only).

    This is NOT ``classify``: there is no strict next-liquidation chain and no
    strictly-later-print requirement. ``wait`` means this event's own sale is usable and
    a later emitted event exists, so the chronological walk can carry the previous
    policy's chosen stop forward; a reprint of the same opening is still a valid wait
    (fit additionally requires a positive original fill and a known chosen exit).
    ``unknown`` is a current print that cannot be sold, ``terminal`` is the calendar
    session boundary and ``last_event`` is an executable final event with no later
    observation. UNKNOWN is reported as such and never turned into a label.
    """
    executable = np.asarray(executable, dtype=bool)
    calendar_terminal = np.asarray(calendar_terminal, dtype=bool)
    successor = np.asarray(successor, dtype=np.int64)
    status = np.full(successor.shape, "last_event", dtype=object)
    status[executable & (successor >= 0)] = "wait"
    status[calendar_terminal] = "terminal"
    status[~executable] = "unknown"
    return status.astype(str)


def method_outcome_prep(
    df: pl.DataFrame,
    successor: np.ndarray,
    executable: np.ndarray,
    calendar_terminal: np.ndarray,
    algorithm: str,
) -> dict:
    """Shared, method-scoped outcome preparation for ``main`` and the causality tests.

    ``value_iteration`` builds the strict-next control chain (``resolve_next``) and its
    fixed ``status==0`` support through ``classify``; that full-chain classification and
    its no-valid-rows gate stay explicitly scoped to this method.

    ``policy_return`` NEVER runs the strict chain: ``nx``/``status``/``next_sell_px`` are
    None and the fit gate is the dynamic per-iteration chosen-exit support inside
    ``run_view``. Its only per-row metadata are the chronological successor, the
    executable-sale flag and the calendar-terminal mask, so a corpus whose valid
    wait-liquidations all reprint the same delayed opening (zero incremental price, not
    missing data) remains trainable even though the control reports no strict labels.
    """
    if algorithm == "value_iteration":
        nx = resolve_next(df)
        status, reasons = classify(df, nx)
        return {
            "algorithm": algorithm,
            "nx": nx,
            "status": status,
            "next_sell_px": pd_num(df, "next_sell_px"),
            "reasons": reasons,
            "label_status": np.asarray([STATUS_NAMES[int(s)] for s in status]),
        }
    if algorithm == "policy_return":
        executable = np.asarray(executable, dtype=bool)
        calendar_terminal = np.asarray(calendar_terminal, dtype=bool)
        successor = np.asarray(successor, dtype=np.int64)
        label_status = policy_label_status(executable, calendar_terminal, successor)
        reasons = {
            "method": "policy_return",
            "rows": int(df.height),
            "executable_rows": int(executable.sum()),
            "calendar_terminal_rows": int(calendar_terminal.sum()),
            "rows_with_later_event": int((successor >= 0).sum()),
            "wait_rows": int((label_status == "wait").sum()),
            "terminal_rows": int((label_status == "terminal").sum()),
            "unknown_rows": int((label_status == "unknown").sum()),
            "last_event_rows": int((label_status == "last_event").sum()),
            "strict_control": (
                "not computed: policy_return availability is the per-iteration "
                "chosen-exit support (models[].iterations[].support)"
            ),
        }
        return {
            "algorithm": algorithm,
            "nx": None,
            "status": None,
            "next_sell_px": None,
            "reasons": reasons,
            "label_status": label_status,
        }
    raise SystemExit(f"unknown algorithm {algorithm!r}")


# ---------------------------------------------------------------- weights
def fold_weights(df: pl.DataFrame, tr_mask: np.ndarray, fit_mask: np.ndarray):
    """Inverse claim-event-count weights, then exactly one unit per day.

    Each claim's events share 1/(claim events in the train window); per-day division makes
    every train day contribute the same total weight regardless of blocked/roster counts.
    ``fit_mask`` is a global boolean selecting this round's fit rows (a subset of
    ``tr_mask``); the control passes ``status == 0`` and policy_return passes its
    per-iteration known-exit support, so the returned weights are always aligned to the
    rows actually labelled in that round.
    """
    key = list(CLAIM_KEY)
    sub = df.filter(tr_mask).with_columns(pl.Series("_valid", fit_mask[tr_mask]))
    claim_n = sub.group_by(key).agg(pl.len().alias("_claim_n"))
    sub = sub.join(claim_n, on=key, how="left")
    sub = sub.with_columns((1.0 / pl.col("_claim_n")).alias("_raw"))
    day_sum = (
        sub.filter(pl.col("_valid")).group_by("day").agg(pl.col("_raw").sum().alias("_day_raw"))
    )
    sub = sub.join(day_sum, on="day", how="left")
    w = (
        sub.filter(pl.col("_valid"))["_raw"].to_numpy()
        / sub.filter(pl.col("_valid"))["_day_raw"].to_numpy()
    )
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


# policy_return targets live in owned_claim_policy_targets.py: emitted-event
# chronological traversal matching replay, with chosen-exit availability and no
# outcome-status censoring of the fit mask.


# ---------------------------------------------------------------- one view / fold
def run_view(
    df: pl.DataFrame,
    feats: list[str],
    tr_mask: np.ndarray,
    te_mask: np.ndarray,
    status: np.ndarray,
    nx: np.ndarray,
    succ: np.ndarray,
    px: np.ndarray,
    npx: np.ndarray,
    executable: np.ndarray,
    w: np.ndarray,
    view: str,
    fold: int,
    algorithm: str,
    calendar_terminal: np.ndarray,
) -> dict:
    tr_pos = np.flatnonzero(tr_mask)
    te_pos = np.flatnonzero(te_mask)
    loc_arr = np.full(df.height, -1, dtype=np.int64)
    loc_arr[tr_pos] = np.arange(len(tr_pos), dtype=np.int64)

    x_tr = matrix(df, feats, tr_mask)
    x_te = matrix(df, feats, te_mask)
    entry_all = pd_num(df, "fill_px")

    # ---- declared control support (value_iteration): fixed status==0 rows. Never built
    # for policy_return, whose only gate is the per-iteration chosen-exit support below.
    if algorithm == "value_iteration":
        control_fit_glob = np.flatnonzero(tr_mask & (status == 0))
        control_fit_local = loc_arr[control_fit_glob]
        nxt_glob = nx[control_fit_glob]
        nxt_local = loc_arr[nxt_glob]
        if (nxt_local < 0).any():
            raise SystemExit("a valid train row points at a next event outside its train days")
        step_dollars = (npx[control_fit_glob] - px[control_fit_glob]) / entry_all[control_fit_glob]
    else:
        control_fit_glob = control_fit_local = nxt_glob = nxt_local = step_dollars = None

    # ---- chronological emitted-event arrays (policy_return): next emitted event, not
    # the strict next-liquidation pointer, so a release at an event that reprints the
    # same opening is still observed.
    price_train = px[tr_pos]
    entry_train = entry_all[tr_pos]
    exec_train = executable[tr_pos]
    succ_glob = succ[tr_pos]
    has_succ = succ_glob >= 0
    succ_train = np.full(len(tr_pos), -1, dtype=np.int64)
    if has_succ.any():
        mapped = loc_arr[succ_glob[has_succ]]
        if (mapped < 0).any():
            raise SystemExit("policy_return successor outside the train window")
        succ_train[has_succ] = mapped

    records, gains = [], []
    p_prev_tr = np.zeros(len(tr_pos), dtype=np.float64)
    p_prev_te = np.zeros(len(te_pos), dtype=np.float64)
    p_final_te = np.zeros(len(te_pos), dtype=np.float64)
    # Dataset rows are reused only when this round's fit mask is identical; a changing
    # policy_return support rebuilds the binned matrix with the round's own weights.
    ds = None
    prev_fit = None
    for it in range(ITERATIONS):
        support = weights_audit = None
        if algorithm == "policy_return":
            destination, known, _ = opt.policy_wait_targets(
                price_train, entry_train, exec_train, succ_train, p_prev_tr, it
            )
            fit_local = np.flatnonzero(known)
            if fit_local.size == 0:
                raise SystemExit(f"policy_return iteration {it}: no known-exit fit rows")
            y = (price_train[destination[fit_local]] - price_train[fit_local]) / entry_train[
                fit_local
            ]
            fit_mask = np.zeros(df.height, dtype=bool)
            fit_mask[tr_pos[fit_local]] = True
            w_it, weights_audit = fold_weights(df, tr_mask, fit_mask)
            support = opt.target_support(succ_train, exec_train, entry_train, destination, known)
            support.update({"label_method": "policy_return", "iteration": it})
            if prev_fit is None or not np.array_equal(fit_local, prev_fit):
                ds = lgb.Dataset(
                    x_tr[fit_local],
                    label=y,
                    weight=w_it,
                    feature_name=feats,
                    params=PARAMS,
                    free_raw_data=False,
                ).construct()
            else:
                ds.set_label(y)
                ds.set_weight(w_it)
            prev_fit = fit_local
        else:
            fit_local = control_fit_local
            q_next = np.where(status[nxt_glob] == 1, 0.0, np.maximum(0.0, p_prev_tr[nxt_local]))
            y = step_dollars + q_next
            if ds is None:
                ds = lgb.Dataset(
                    x_tr[fit_local],
                    label=y,
                    weight=w,
                    feature_name=feats,
                    params=PARAMS,
                    free_raw_data=False,
                ).construct()
            else:
                ds.set_label(y)
        model = lgb.train(PARAMS, ds, num_boost_round=80)
        p_tr = model.predict(x_tr, num_threads=PARAMS["num_threads"]).astype(np.float64)
        p_te = model.predict(x_te, num_threads=PARAMS["num_threads"]).astype(np.float64)
        d_tr = np.abs(p_tr - p_prev_tr)
        d_te = np.abs(p_te - p_prev_te)
        gains.append(np.asarray(model.feature_importance(importance_type="gain"), dtype=np.float64))
        rec = {
            "fold": fold,
            "view": view,
            "iteration": it,
            "rows_fit": int(fit_local.size),
            "features": len(feats),
            "rounds": 80,
            "target_quantiles": qstats(y),
            "target_tail": tail_exposure(y),
            "train_pred_quantiles": qstats(p_tr),
            "test_pred_quantiles": qstats(p_te),
            "delta_train_mean_abs": float(d_tr.mean()),
            "delta_train_max_abs": float(d_tr.max()),
            "delta_test_mean_abs": float(d_te.mean()),
            "delta_test_max_abs": float(d_te.max()),
        }
        if algorithm == "policy_return":
            rec["support"] = support
            rec["weights_audit"] = weights_audit
        records.append(rec)
        p_prev_tr, p_prev_te = p_tr, p_te
        p_final_te = p_te
        del model
    # Only the calendar-known terminal state has zero continuation. policy_return never
    # pins TEST predictions from an outcome-derived validity flag.
    pin = calendar_terminal[te_pos] if algorithm == "policy_return" else status[te_pos] == 1
    p_final_te = p_final_te.copy()
    p_final_te[pin] = 0.0
    imp = np.mean(np.stack(gains), axis=0)
    top = sorted(zip(feats, imp.tolist(), strict=True), key=lambda kv: kv[1], reverse=True)
    diag = {
        "fold": fold,
        "view": view,
        "algorithm": algorithm,
        "features": feats,
        "importance_mean_gain": dict(top),
        "importance_gain_by_iteration": {
            f"it{it}": {k: float(v) for k, v in zip(feats, g.tolist(), strict=True)}
            for it, g in enumerate(gains)
        },
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
    ap.add_argument(
        "--events-root", default=None, help="default <data-root>/harvest01/lifecycle/v2/owned_claim"
    )
    ap.add_argument("--out", default=None, help="default <events-root>/value")
    ap.add_argument("--views", default=",".join(VIEWS))
    ap.add_argument(
        "--algorithm",
        choices=("value_iteration", "policy_return"),
        default="value_iteration",
        help="value_iteration = declared limited strict-next FVI benchmark; "
        "policy_return = chronological whole-policy RETAIN/WAIT cashflow labels",
    )
    ap.add_argument(
        "--folds",
        default=",".join(str(i) for i in range(len(FOLDS))),
        help="fold indices to run (subset = non-evidence diagnostic run)",
    )
    ap.add_argument(
        "--smoke-days",
        type=int,
        default=0,
        help="tiny non-evidence run over the first N discovery days",
    )
    args = ap.parse_args()

    data_root = ls.bps.resolve_data_root(args.data_root)
    split = ls.load_split(data_root)
    disc_all = list(split["discovery_days"])
    if len(disc_all) != N_DISCOVERY:
        raise SystemExit(f"discovery day count {len(disc_all)} != {N_DISCOVERY}")
    disc = list(disc_all)
    events_root = (
        Path(args.events_root) if args.events_root else ls.v2_dir(data_root) / "owned_claim"
    )
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
        smoke_note = (
            f"non-evidence smoke run over first {n} discovery days; fold windows "
            f"rescaled to the smoke day count; model parameters/iterations unchanged"
        )
    else:
        folds = FOLDS
    if len(fold_ids) != len(folds) or tuple(fold_ids) != tuple(range(len(folds))):
        evidence = False
        smoke_note = (
            smoke_note + "; " if smoke_note else ""
        ) + f"fold subset {fold_ids} of {list(range(len(folds)))} (diagnostic only)"
    if tuple(views) != VIEWS:
        evidence = False
        smoke_note = (smoke_note + "; " if smoke_note else "") + f"view subset {list(views)}"

    # day windows (contract indices into the canonical chronological discovery list)
    windows = []
    for i in fold_ids:
        tr0, tr1, te0, te1 = folds[i]
        tr0, tr1, te0, te1 = (
            min(tr0, len(disc)),
            min(tr1, len(disc)),
            min(te0, len(disc)),
            min(te1, len(disc)),
        )
        if te0 >= te1:
            raise SystemExit(f"fold {i}: empty test window after clamping ({te0},{te1})")
        windows.append(
            {
                "fold": i,
                "train_idx": [tr0, tr1],
                "test_idx": [te0, te1],
                "train_days": disc[tr0:tr1],
                "test_days": disc[te0:te1],
            }
        )
    days_needed = sorted({d for w in windows for d in w["train_days"] + w["test_days"]})
    usable, markers = resolve_event_days(days_needed, events_root)
    df, digests = load_events(usable, events_root)
    df = df.with_columns(pl.col("day").cast(pl.Utf8))
    if not set(df["day"].unique().to_list()) <= set(disc_all):
        raise SystemExit("event rows contain days outside the discovery split; refusing")

    feats_by_view = feature_lists(df)
    px = pd_num(df, "sell_px")
    se = pd_num(df, "session_end")
    set_ = pd_num(df, "sell_et")
    n_days = len(usable)

    manifest_pin = None
    mpath = events_root / "manifest.json"
    if mpath.exists():
        man = json.loads(mpath.read_text())
        manifest_pin = {
            "path": str(mpath),
            "sha256": sha256_file(mpath),
            "source_sha256": man.get("source_sha256"),
            "n_feature_columns": len(man.get("feature_columns", []) or []),
            "discovery_days": len(man.get("discovery_days", []) or []),
        }
        allowed_manifest_days = [disc_all] + ([disc] if args.smoke_days else [])
        if man.get("discovery_days") and list(man["discovery_days"]) not in allowed_manifest_days:
            raise SystemExit("event manifest days disagree with the split or explicit smoke prefix")

    n = df.height
    # Chronological emitted-event order (claim-contiguous, sequence 0-based) plus the
    # executable-sale flag and the calendar-terminal mask for the whole-event walk.
    succ, executable, calendar_terminal = chronological_metadata(df, px, se, set_)

    # Method-scoped outcome prep (see method_outcome_prep). value_iteration builds the
    # strict-next control chain and its no-valid gate; policy_return builds none of that
    # and gates only on the per-round chosen-exit support in run_view. No label is
    # fabricated or bootstrapped when no chosen exit is known.
    algorithm = args.algorithm
    prep = method_outcome_prep(df, succ, executable, calendar_terminal, algorithm)
    nx = prep["nx"]
    status = prep["status"]
    npx = prep["next_sell_px"]
    reasons = prep["reasons"]
    label_status = prep["label_status"]
    pred = {v: np.full(n, np.nan, dtype=np.float32) for v in views}
    fold_of = np.full(n, -1, dtype=np.int32)
    diags, weights_audit, coverage = [], {}, {}
    imp_frames = []
    out.mkdir(parents=True, exist_ok=True)
    cache_dir = out / "cache"
    progress_dir = out / "progress_scores"
    cache_dir.mkdir(exist_ok=True)
    progress_dir.mkdir(exist_ok=True)
    run_id = hashlib.sha256(
        (
            sha256_file(SCRIPT)
            + sha256_file(POLICY_TARGETS)
            + json.dumps(digests, sort_keys=True)
            + json.dumps(windows, sort_keys=True)
            + json.dumps(views)
            + algorithm
        ).encode()
    ).hexdigest()
    (out / "metadata.json").write_text(
        json.dumps({"status": "running", "run_id": run_id, "method": algorithm}) + "\n"
    )

    for w in windows:
        k = w["fold"]
        tr_mask = df["day"].is_in(w["train_days"]).to_numpy()
        te_mask = df["day"].is_in(w["test_days"]).to_numpy()
        if algorithm == "value_iteration":
            wfit, waudit = fold_weights(df, tr_mask, status == 0)
            waudit.update(
                {"fold": k, "train_days": len(w["train_days"]), "test_days": len(w["test_days"])}
            )
            weights_audit[f"fold{k}"] = waudit
        else:
            # policy_return has no control status==0 support to weight; its per-round
            # chosen-exit weights are audited inside run_view.
            wfit = None
            weights_audit[f"fold{k}"] = {
                "method": algorithm,
                "note": "no control status==0 weights are built; per-iteration chosen-exit "
                "weights live in models[].iterations[].weights_audit",
                "train_days": len(w["train_days"]),
                "test_days": len(w["test_days"]),
            }
        for view in views:
            feats = feats_by_view[view]
            coverage[f"fold{k}_{view}"] = {
                "n_features": len(feats),
                "train_observed_fraction": observed_coverage(df, feats, tr_mask),
                "test_observed_fraction": observed_coverage(df, feats, te_mask),
            }
            cache_json = cache_dir / f"{view}_fold{k}.json"
            cache_pred = cache_dir / f"{view}_fold{k}.parquet"
            cached = json.loads(cache_json.read_text()) if cache_json.exists() else {}
            if (
                cached.get("run_id") == run_id
                and cached.get("algorithm") == algorithm
                and cache_pred.exists()
            ):
                pp = pl.read_parquet(cache_pred)["pred"].to_numpy()
                if len(pp) != int(te_mask.sum()):
                    raise SystemExit("cached prediction shape mismatch")
                res = {
                    "pred_te": pp,
                    "diag": cached["diag"],
                    "imp": (feats, np.array(cached["importance"])),
                }
            else:
                res = run_view(
                    df,
                    feats,
                    tr_mask,
                    te_mask,
                    status,
                    nx,
                    succ,
                    px,
                    npx,
                    executable,
                    wfit,
                    view,
                    k,
                    algorithm,
                    calendar_terminal,
                )
                pl.DataFrame({"pred": res["pred_te"]}).write_parquet(cache_pred)
                cache_json.write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "algorithm": algorithm,
                            "method": algorithm,
                            "script_sha256": sha256_file(SCRIPT),
                            "policy_targets_sha256": sha256_file(POLICY_TARGETS),
                            "support": [r.get("support") for r in res["diag"]["iterations"]],
                            "diag": res["diag"],
                            "importance": res["imp"][1].tolist(),
                        },
                        allow_nan=False,
                    )
                )
            progress = (
                df.filter(pl.Series(te_mask))
                .select(["day", "clock", "rank", "ticker", "t", "sequence"])
                .with_columns(pl.Series(f"pred_{view}", res["pred_te"]))
            )
            for (progress_day,), frame in progress.partition_by("day", as_dict=True).items():
                frame.write_parquet(progress_dir / f"{progress_day}.{view}.parquet")
            print(f"Completed fold{k} {view}: {int(te_mask.sum())} test events", flush=True)
            pred[view][te_mask] = res["pred_te"]
            fold_of[te_mask] = k
            diags.append(res["diag"])
            f, imp = res["imp"]
            imp_frames.append(
                pl.DataFrame(
                    {"fold": k, "view": view, "feature": f, "gain_mean_over_iterations": imp}
                )
            )
        del wfit

    test_mask = fold_of >= 0
    scored = (
        df.select(["day", "clock", "rank", "ticker", "t", "sequence"])
        .with_columns(
            [
                pl.Series("fold", fold_of),
                pl.Series("label_status", label_status),
            ]
            + [pl.Series(f"pred_{v}", pred[v]) for v in views]
        )
        .filter(pl.Series(test_mask))
    )
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
            part.drop(["fold", "view"]).sort(
                "gain_mean_over_iterations", descending=True
            ).write_parquet(out / f"importance_{v}_f{k}.parquet")

    # per-clock census (counts only; not metrics)
    clock_census = {}
    for c in CLOCKS:
        m = df["clock"].to_numpy() == c
        if status is not None:
            status_counts = {STATUS_NAMES[s]: int((m & (status == s)).sum()) for s in STATUS_NAMES}
        else:
            status_counts = {
                name: int((m & (label_status == name)).sum()) for name in POLICY_STATUS_NAMES
            }
        clock_census[str(c)] = {
            "scored_rows": int((test_mask & m).sum()),
            "status_counts": status_counts,
        }
    # view contrast on common known rows (all tape features observed) — no selection
    common = test_mask.copy()
    tape_feats = feats_by_view["tape"]
    if tape_feats:
        fin = (
            df.select(
                [pl.col(c).cast(pl.Float64, strict=False).is_finite().alias(c) for c in tape_feats]
            )
            .to_numpy()
            .all(axis=1)
        )
        common &= fin
    contrast = {
        "common_known_rows": int(common.sum()),
        "scored_rows": int(test_mask.sum()),
        "note": "views are predeclared and cumulative; no view/feature selection",
        "pairs": {},
    }
    if common.sum() >= 20:
        for a in range(len(views)):
            for b in range(a + 1, len(views)):
                pa = np.nan_to_num(pred[views[a]][common])
                pb = np.nan_to_num(pred[views[b]][common])
                contrast["pairs"][f"{views[a]}~{views[b]}"] = {
                    "sign_agreement": float(np.mean(np.sign(pa) == np.sign(pb))),
                    "pearson": float(np.corrcoef(pa, pb)[0, 1]),
                    "mean_a": float(pa.mean()),
                    "mean_b": float(pb.mean()),
                }

    meta = {
        "status": "complete",
        "run_id": run_id,
        "producer": "factory/scripts/owned_claim_value.py",
        "script_sha256": sha256_file(SCRIPT),
        "policy_targets_source": "factory/scripts/owned_claim_policy_targets.py",
        "policy_targets_sha256": sha256_file(POLICY_TARGETS),
        "contract": "factory/artifacts/owned_claim_contract.json",
        "contract_sha256": sha256_file(SCRIPT.parents[1] / "artifacts/owned_claim_contract.json"),
        "evidence": evidence,
        "algorithm": algorithm,
        "method": algorithm,
        "non_evidence_note": smoke_note,
        "predictions_are": (
            "incremental inherited-share dollars per original gross entry notional; NOT P&L"
        ),
        "clocks_pooled": list(CLOCKS),
        "views_definition": {v: list(VIEW_RECIPE[v]) for v in VIEWS},
        "views_run": list(views),
        "iterations": ITERATIONS,
        "rounds_per_iteration": 80,
        "params": PARAMS,
        "seed": PARAMS["seed"],
        "objective": (
            "(complete future policy exit-current sale)/original_fill; "
            "full policy cashflows, entry sunk, common exit fee"
            if algorithm == "policy_return"
            else (
                "(next_sale-current_sale)/original_fill + max(0,next_owned_value); "
                "entry sunk, exit fee common; no clipping"
            )
        ),
        "label_method": {
            "value_iteration": (
                "declared control: fixed status==0 support, "
                "y=(next_sale-current_sale)/original_fill + max(0,previous_q_at_next_event)"
            ),
            "policy_return": (
                "chronological emitted-event traversal matching owned_claim_replay: "
                "wait at each event; sell at the first event with previous_q<0 at THAT "
                "event's own sell_et/sell_px; calendar terminal forced; y=(chosen_exit-"
                "current_sale)/original_fill. Iteration0 all-zero q is a full-session "
                "hold initialization ruler only. Fit support is dynamic per iteration "
                "(known chosen exit); UNKNOWN exits are excluded, never zeroed. "
                "Fixed-horizon V15/V30/V60/V120 labels untouched."
            ),
            "test_policy": (
                "no TEST prices/action labels are fit; TRAIN-only. TEST predictions use "
                "features only; the only future-derived pin is the calendar session "
                "terminal (t>=session_end) set to zero for policy_return."
            ),
            "outcome_status": {
                "value_iteration": (
                    "strict-control classify over the next_sequence pointer: per-row "
                    "valid/terminal/unknown/beyond_session; its no-valid-rows gate is scoped "
                    "to this method only"
                ),
                "policy_return": (
                    "no strict-control classification is computed at the entrypoint; the "
                    "per-row label_status column is the policy method's own availability: "
                    + ", ".join(POLICY_STATUS_NAMES)
                    + " (wait = executable print with a later emitted event, terminal = "
                    "calendar session boundary, unknown = non-executable current print, "
                    "last_event = final executable event). No label is fabricated when no "
                    "chosen exit is known."
                ),
            },
            "dataset_reuse": (
                "LightGBM Dataset bins are reused only when an iteration's fit mask is "
                "identical; a changed support rebuilds with that round's weights"
            ),
            "weights": (
                "inverse claim-event-count then one unit per day, recomputed per iteration "
                "for policy_return; no control status==0 weights are built for policy_return"
            ),
        },
        "folds": [
            {
                "fold": w["fold"],
                "train_index": w["train_idx"],
                "test_index": w["test_idx"],
                "train_days": w["train_days"],
                "test_days": w["test_days"],
            }
            for w in windows
        ],
        "discovery_days_total": len(disc_all),
        "days_used": usable,
        "events_root": str(events_root),
        "events_glob": "events/<day>.parquet",
        "data_root": str(data_root),
        "split_sha256": sha256_file(ls.v2_dir(data_root) / "split.json"),
        "event_manifest": manifest_pin,
        "event_day_sha256": digests,
        "day_markers": markers,
        "feature_allowlist_rule": (
            "columns starting with 'feature_', numeric/bool, minus "
            "metadata/execution/label columns; state excludes "
            "'" + HIST_PREFIX + "'/'" + TAPE_PREFIX + "'; history adds "
            "'" + HIST_PREFIX + "'; tape adds '" + TAPE_PREFIX + "'"
        ),
        "non_predictors": list(NON_PREDICTORS),
        "forbidden_substrings": list(FORBIDDEN_SUBSTRINGS),
        "feature_views": feats_by_view,
        "row_census": {
            "events": int(n),
            "days": n_days,
            "claims": int(df.select(list(CLAIM_KEY)).unique().height),
            "scored_test_rows": int(test_mask.sum()),
            "status": reasons,
            "per_clock": clock_census,
        },
        "coverage": coverage,
        "outputs": {
            "scores_dir": str(scores_dir),
            "diagnostics": str(out / "value_diagnostics.json"),
            "importance_dir": str(out),
        },
        "test_days_written": written,
    }
    (out / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))

    diag_out = {
        "search": {
            "method": algorithm,
            "views": list(views),
            "iterations": ITERATIONS,
            "tuning": "none",
            "feature_or_view_selection": "none",
            "folds_run": fold_ids,
        },
        "status_counts": reasons,
        "weight_audit": weights_audit,
        "view_contrast": contrast,
        "per_clock_census": clock_census,
        "models": diags,
        "test_prediction_tail_by_view": {v: tail_exposure(pred[v][test_mask]) for v in views},
    }
    (out / "value_diagnostics.json").write_text(json.dumps(diag_out, indent=2, default=str))
    if status is not None:
        status_note = f"valid={reasons['valid_rows']}"
    else:
        status_note = (
            f"wait={reasons['wait_rows']} unknown={reasons['unknown_rows']} "
            f"terminal={reasons['terminal_rows']}"
        )
    print(
        f"[owned_claim_value] method={algorithm} evidence={evidence} days={n_days} events={n} "
        f"{status_note} test_rows={int(test_mask.sum())} views={list(views)} "
        f"folds={fold_ids} -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
