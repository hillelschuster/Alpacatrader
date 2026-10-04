#!/usr/bin/env python3
"""OWNED-CLAIM RETRIEVAL — predeclared past-trajectory nearest-neighbour instrument.

Question
--------
At the first visit of each owned-claim damage/flush/repair/failed_repair "personality",
does looking up similar PAST prefixes and reading their realized signed owned-share
INCREMENT DOLLARS predict the current claim's own increment at 15/30/60/120 minutes
better than a naive scalar mean?  If matching prefix personality earns nothing, this
stops here.  The EXPECTED-dollar estimate is the neighbour MEAN of the retrieved TRAIN
labels; the neighbour MEDIAN is reported only as a separate quantile diagnostic (a
median can be negative while the mean is positive), and the declared benchmark is the
naive TRAIN scalar-mean prediction carried to the identical known TEST rows.

What is retrieved
-----------------
* The source dataset is the parent-owned event table (``owned_claim/events/<day>.parquet``)
  and NOTHING else.  Only ``lifecycle_study.discovery_days`` (533 days through
  2023-03-14) are ever opened; validation days and FREEZE markers are never read.
* ANCHOR = the first emitted event of a claim ``(day, clock, rank, ticker)`` whose
  ``event_kind`` token set contains the focus kind.  One anchor per (claim, kind);
  the canonical claim identity is exactly ``owned_claim_value.CLAIM_KEY``.
* NEIGHBOURS = the K=50 nearest TRAIN anchors of the same fold window and the SAME
  focus kind (mode ``kind``), plus a contrast mode ``pooled`` that draws neighbours
  from any focus kind.  Compare the two to test whether matching the prefix
  personality adds anything.
* Neighbours are de-duplicated by underlying ``(day, ticker)`` across all four
  pooled clocks, so one future path cannot be counted four times at the four clocks.
* NO future / current-test-day neighbour: every fold uses a strictly earlier TRAIN
  day window, so a query at day d can only see anchors with day < d.
* BASELINE = the declared naive TRAIN scalar mean of the horizon label, computed per
  fold / rank-view / declared kind support from finite TRAIN labels only and carried
  as a constant to the same TEST rows.  It is never fit on TEST and the N3/N5 views
  are never pooled.

Representation (exact, compact — deliberately not all 156 raw windows)
---------------------------------------------------------------------
Six declared causal prefix groups (``feature_``-prefixed, past-only by construction;
no execution column, no outcome, no label, no next_* pointer enters the matrix):

  price_geometry (8)   ret_fill, peak_gain, dd_from_high, mae_sofar,
                       recovery_from_low, peak_retention, dd_velocity5, acceleration5
  history_5_15_30 (36) feature_hist_<var>_delta{5,15,30} for
                       {ret_fill, dd_from_high, recovery_from_low, race_rank, v5, dvol5},
                       feature_hist_known{5,15,30}, feature_hist_above_fill{5,15,30},
                       feature_hist_ret1_std{5,15,30}
  rank_peers (19)      race_rank, race_rank_fresh, race_gain, race_n_known, race_fresh2,
                       drank5, drank15, n_peers, sib_ret_mean, sib_above_fill, peer_ret_p50,
                       basket_ret, rel_dvol5, scan3_{mean_ret,positive,known},
                       scan5_{mean_ret,positive,known}
  volume (12)          dvol5, dvol15, vol_med_ratio, tape tr_dvol_5m/15m,
                       tr_intensity_5m/15m, tr_avg_sz, q_spread_bps_med, q_imb_med,
                       q_spread_bps_mean_minmed_5m, q_imb_mean_minmed_5m
  event_memory (5)     feature_hist_count_{damage,flush,repair,failed_repair,profit5}
  structural (3)       remaining_session, minutes_since_high, time_below_fill

Feature handling is TRAIN-only: a feature is kept for a (fold, kind, rank-view) fit
only when its TRAIN-pool finite coverage is >= COV_MIN; the kept features are
standardised by the TRAIN median and a robust scale (IQR, else 1.4826*MAD, else 1);
missing values are imputed at the TRAIN median (=0 after centring) and explicit
missing-indicator dimensions are appended for every kept feature whose TRAIN missing
rate lies in (MISS_LO, MISS_HI).  TEST never contributes to any median, scale,
coverage, indicator choice or neighbour pool.

Target and units
----------------
Target = the anchor's own ``increment_<h>_original_dollar`` label for h in
{15,30,60,120}: signed owned-share increment dollars per ORIGINAL CLAIM CASH budget
(entry sunk, common exit fee; the label already carries the modelled 50bps/side
new-capital factor 0.995/1.005).  Reported below both as dollars per $100 original claim
cash (x100, the primary calibration) and, for the fresh-cost contrast, as a
per-CAUSAL-MARK-dollar return.  The normaliser is carried per query as the anchor's own
RECORDED CAUSAL MARK ``feature_px`` (the completed-bar mark, known at the decision time)
and ORIGINAL FILL ``fill_px``: current causal-mark wealth per original claim cash
= ``feature_px/fill_px * 0.995/1.005`` (``W_mark``).  ``increment_<h>_original_dollar /
W_mark`` is the predicted/realised return on the decision-time mark dollar.  The
``sell_px`` execution reference (first future open) is a LABEL/outcome column and is never
read here as a selector, normaliser or size input.  A query whose causal mark or fill is
unknown or nonpositive is UNKNOWN for the return contrast and is counted
(``n_causal_mark_unknown``); no zero is imputed and no cash is invented.  Unknown labels are
excluded from every statistic and their counts reported.  The EXPECTED-dollar estimate is
the neighbour MEAN of the retrieved TRAIN labels; the neighbour MEDIAN is reported only as
a separate quantile diagnostic and is never interpreted as a mean miscalibration (30
neighbours at -0.01 and 20 at +0.10 original-cash dollars have median -0.01 but mean
+0.034, and the expected action uses the mean).  This is a regression/dollar instrument,
never a classification.

Output
------
A single deterministic JSON.  It reports held-out dollar calibration, positive vs
negative expected action value, representative past matches and support by time/rank.
The expected-dollar calibration, expected-action grouping and fresh-hurdle selection all
use the neighbour MEAN; the neighbour median is reported alongside as a separate quantile
diagnostic.  Every metric block also carries the declared naive TRAIN scalar-mean
comparator: a constant finite TRAIN-pool horizon mean (per fold/rank-view/declared kind
support, canonical original-cash dollars) carried to TEST and scored on the identical
known TEST rows as the retrieval prediction (MAE/RMSE/bias plus skill), so the JSON can
answer whether retrieval beats the naive scalar mean.
The PAPER fresh-cost contrast converts each predicted/actual dollar increment to a
per-causal-mark-dollar return (divide by the query's own
``feature_px/fill_px*0.995/1.005`` ``W_mark``) and compares it with the declared
2*side/(1-side) round-trip hurdle, reporting the fresh round-trip proxy
``(1+return)*(1-side)/(1+side)-1``.  Its intended fresh size is the explicit causal fixed
quantity ``0.9*budget/(feature_px*(1+side))``: it is a function of the decision-time mark
only, so two queries with the same ``feature_px``/``fill_px`` and the same prediction get
the same above-hurdle selection and the same planned quantity regardless of any future
opening; whether that order actually fills and whether the reserved cash suffices is
UNKNOWN (an executor question, never resized here).  Scalar horizon means are explicitly
NOT portfolio P&L and NOT a fill claim.

Runtime: numpy + polars only (brute-force euclidean NN, no external NN/sequence
framework, no data fetch).  The producer stages first-visit anchors per day under
``<events-root>/retrieval_stage`` with a per-day source pin and resumes from a matching
cache, so memory stays bounded to one day's events at a time and an interrupted run
restarts cheaply.  ``--no-stage`` recomputes each day without writing the cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import warnings
from pathlib import Path

# bound native threads before numpy/polars import (serial parent compute)
for _v in (
    "POLARS_MAX_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_v, "2")

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lifecycle_study as ls  # noqa: E402  (canonical split / coverage rules)

SCRIPT = Path(__file__).resolve()
DEFAULT_OUT = SCRIPT.parents[1] / "artifacts" / "owned_claim_retrieval.json"

# ---------------------------------------------------------------- predeclared config
CLOCKS = (540, 560, 569, 571)
FOLDS = ((0, 150, 150, 300), (0, 300, 300, 450), (0, 450, 450, 533))
N_DISCOVERY = 533
CLAIM_KEY = ("day", "clock", "rank", "ticker")  # original causal claim identity
FOCUS_KINDS = ("damage", "flush", "repair", "failed_repair")
HORIZONS = (15, 30, 60, 120)
K = 50  # fixed unique-underlying neighbour count; no hyperparameter grid
K_RAW = 800  # raw candidate cap before underlying de-duplication
RANK_VIEWS = {"N3": 3, "N5": 5}
SIDE = 0.005  # modelled 50bps per side
FRESH_HURDLE = 2.0 * SIDE / (1.0 - SIDE)  # fresh round-trip break-even per causal-mark dollar
OWNED_FEE = 0.995 / 1.005  # modelled new-capital buy/exit factor inside the target label
# per-query CAUSAL normaliser for the per-causal-mark-dollar contrast.  The ONLY known current
# mark is ``feature_px`` (the completed-bar mark at the anchor, past-only and known at the
# decision time); ``fill_px`` is the ORIGINAL fill, already known once the claim filled.
# ``sell_px`` is the *_execution_reference: the first future open / outcome.  It is a LABEL
# column only and is NEVER read here as a selector, a normaliser or a sizing input.
CAUSAL_MARK_COL = "feature_px"  # *_causal_mark: known at the decision time
ORIGINAL_FILL_COL = "fill_px"  # known after the original fill
EXECUTION_REFERENCE_COL = "sell_px"  # *_execution_reference: label/outcome only
NORMALIZER_COLS = (CAUSAL_MARK_COL, ORIGINAL_FILL_COL)
FRESH_BUDGET_FRAC = 0.9  # fresh re-entry/cash rotation budget fraction
COV_MIN = 0.30  # keep a feature only with >=30% TRAIN coverage
MISS_LO, MISS_HI = 0.02, 0.98  # explicit missing-indicator window
MIN_FEATURES = 5
MIN_STAT = 20
N_CAL_BINS = 5
DOLLAR = 100.0  # report dollars per $100 ORIGINAL CLAIM CASH budget
PLANNED_BUDGET = DOLLAR  # reserved settled budget per query for the causal fresh size
QUANTILES = (0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)
KEYS = ("day", "clock", "rank", "ticker", "t", "sequence")
STAGE_VERSION = "1"
T_BUCKETS = ((0, 545), (546, 600), (601, 660), (661, 10**9))
FORBIDDEN_SUBSTRINGS = (
    "next_sell",
    "sell_px",
    "sell_et",
    "sell_volume",
    "_target",
    "outcome",
    "increment_",
    "label_",
    "next_",
)

# exact compact representation (grouped; intersected with the on-disk schema)
_REP_PRICE = (
    "feature_ret_fill",
    "feature_peak_gain",
    "feature_dd_from_high",
    "feature_mae_sofar",
    "feature_recovery_from_low",
    "feature_peak_retention",
    "feature_dd_velocity5",
    "feature_acceleration5",
)


def _history_features() -> tuple[str, ...]:
    out = []
    for h in (5, 15, 30):
        for c in ("ret_fill", "dd_from_high", "recovery_from_low", "race_rank", "v5", "dvol5"):
            out.append(f"feature_hist_{c}_delta{h}")
        out += [
            f"feature_hist_known{h}",
            f"feature_hist_above_fill{h}",
            f"feature_hist_ret1_std{h}",
        ]
    return tuple(out)


_REP_HISTORY = _history_features()
_REP_RANK_PEERS = (
    "feature_race_rank",
    "feature_race_rank_fresh",
    "feature_race_gain",
    "feature_race_n_known",
    "feature_race_fresh2",
    "feature_drank5",
    "feature_drank15",
    "feature_n_peers",
    "feature_sib_ret_mean",
    "feature_sib_above_fill",
    "feature_peer_ret_p50",
    "feature_basket_ret",
    "feature_rel_dvol5",
    "feature_scan3_mean_ret",
    "feature_scan3_positive",
    "feature_scan3_known",
    "feature_scan5_mean_ret",
    "feature_scan5_positive",
    "feature_scan5_known",
)
_REP_VOLUME = (
    "feature_dvol5",
    "feature_dvol15",
    "feature_vol_med_ratio",
    "feature_tape_tr_dvol_5m",
    "feature_tape_tr_dvol_15m",
    "feature_tape_tr_intensity_5m",
    "feature_tape_tr_intensity_15m",
    "feature_tape_tr_avg_sz",
    "feature_tape_q_spread_bps_med",
    "feature_tape_q_imb_med",
    "feature_tape_q_spread_bps_mean_minmed_5m",
    "feature_tape_q_imb_mean_minmed_5m",
)
_REP_EVENT_MEMORY = (
    "feature_hist_count_damage",
    "feature_hist_count_flush",
    "feature_hist_count_repair",
    "feature_hist_count_failed_repair",
    "feature_hist_count_profit5",
)
_REP_STRUCTURAL = (
    "feature_remaining_session",
    "feature_minutes_since_high",
    "feature_time_below_fill",
)
REP_GROUPS = {
    "price_geometry": _REP_PRICE,
    "history_5_15_30": _REP_HISTORY,
    "rank_peers": _REP_RANK_PEERS,
    "volume": _REP_VOLUME,
    "event_memory": _REP_EVENT_MEMORY,
    "structural": _REP_STRUCTURAL,
}
REP_FLAT = tuple(dict.fromkeys(c for g in REP_GROUPS.values() for c in g))

TARGET_LABEL = "increment_{h}_original_dollar"  # canonical only; no legacy alias/unit fallback
REQUIRED_KEYS = ("day", "clock", "rank", "ticker", "t", "sequence", "event_kind")


# ---------------------------------------------------------------- small helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def qstats(a: np.ndarray) -> dict | None:
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    out = {f"p{int(q * 100):02d}": float(np.quantile(a, q)) for q in QUANTILES}
    out.update({"n": int(a.size), "mean": float(a.mean()), "std": float(a.std())})
    return out


def finite_mean(a: np.ndarray) -> float:
    """Mean of the finite entries only (canonical original-cash dollars); NaN if none.

    Used for the declared naive TRAIN scalar-mean comparator: UNKNOWN labels are excluded,
    never imputed with zero, and a slice with no finite label is UNKNOWN (NaN), not a zero.
    """
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else float("nan")


def _average_ranks(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n, dtype=np.float64)
    sx = x[order]
    i = 0
    while i < n:
        j = i + 1
        while j < n and sx[j] == sx[i]:
            j += 1
        ranks[order[i:j]] = (i + j - 1) / 2.0 + 1.0
        i = j
    return ranks


def pearson(x, y) -> float | None:
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size < 3 or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def spearman(x, y) -> float | None:
    if np.asarray(x).size < 3:
        return None
    return pearson(_average_ranks(x), _average_ranks(y))


def calibration_bins(pk: np.ndarray, yk: np.ndarray, nbins: int = N_CAL_BINS) -> list[dict]:
    edges = np.unique(np.quantile(pk, np.linspace(0.0, 1.0, nbins + 1)))
    if edges.size < 2:
        return [
            {
                "bin": 0,
                "lo": float(edges[0]),
                "hi": float(edges[-1]),
                "n": int(pk.size),
                "mean_pred_dollars_per_100_original_cash": float(pk.mean() * DOLLAR),
                "mean_actual_dollars_per_100_original_cash": float(yk.mean() * DOLLAR),
            }
        ]
    b = np.digitize(pk, edges[1:-1])
    rows = []
    for i in range(edges.size - 1):
        m = b == i
        if not m.any():
            continue
        rows.append(
            {
                "bin": i,
                "lo": float(edges[i]),
                "hi": float(edges[i + 1]),
                "n": int(m.sum()),
                "mean_pred_dollars_per_100_original_cash": float(pk[m].mean() * DOLLAR),
                "mean_actual_dollars_per_100_original_cash": float(yk[m].mean() * DOLLAR),
            }
        )
    return rows


# ---------------------------------------------------------------- input
def resolve_event_days(days: list[str], events_root: Path) -> tuple[list[str], list[dict]]:
    """Strict per-day coverage: a missing marker is a hard error, never a silent drop."""
    ev_dir = events_root / "events"
    done_dirs = [events_root / "_done", ev_dir / "_done"]
    usable, markers = [], []
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
                "input_hash": meta.get("input_hash"),
                "rows": meta.get("events", meta.get("rows")),
            }
        )
        if status == "ok":
            usable.append(day)
    return usable, markers


def resolve_labels(schema_keys, horizons=HORIZONS) -> dict[int, str]:
    """Canonical target only: ``increment_<h>_original_dollar`` (per original claim cash,
    modelled 50bps/side).  Never fall back to a legacy alias or a differently-scaled target:
    a wrong target schema is a hard error so units cannot silently drift."""
    labels = {}
    for h in horizons:
        cand = TARGET_LABEL.format(h=h)
        if cand not in schema_keys:
            raise SystemExit(
                f"wrong target schema: canonical label {cand!r} absent from "
                f"{sorted(schema_keys)}; refusing a legacy/different-unit target"
            )
        labels[h] = cand
    return labels


def read_day(path: Path, want: list[str]) -> pl.DataFrame:
    try:
        return pl.read_parquet(path, columns=want)
    except Exception:  # schema drift fallback: read all, select the intersection
        df = pl.read_parquet(path)
        return df.select([c for c in want if c in df.columns])


def build_day_anchors(day_df: pl.DataFrame, feat_cols: list[str], label_cols: dict[int, str]):
    """First emitted event per claim for each focus kind, for a single day."""
    day_df = day_df.sort([*CLAIM_KEY, "sequence"])
    cols = list(
        dict.fromkeys([*KEYS, "anchor_kind", *NORMALIZER_COLS, *label_cols.values(), *feat_cols])
    )
    parts = []
    for kind in FOCUS_KINDS:
        padding = pl.concat_str([pl.lit("|"), pl.col("event_kind").cast(pl.Utf8), pl.lit("|")])
        hit = day_df.filter(padding.str.contains(f"|{kind}|", literal=True))
        if hit.height == 0:
            continue
        first = hit.sort([*CLAIM_KEY, "sequence"]).unique(subset=list(CLAIM_KEY), keep="first")
        first = first.sort(list(CLAIM_KEY)).with_columns(pl.lit(kind).alias("anchor_kind"))
        parts.append(first.select(cols))
    if not parts:
        return None
    return pl.concat(parts, how="diagonal_relaxed")


def anchor_schema_key(feat_cols: list[str], label_cols: dict[int, str]) -> str:
    return sha256_text(
        json.dumps(
            {
                "stage_version": STAGE_VERSION,
                "keys": list(KEYS),
                "focus": list(FOCUS_KINDS),
                "causal_mark_col": CAUSAL_MARK_COL,
                "original_fill_col": ORIGINAL_FILL_COL,
                "normalizer_cols": list(NORMALIZER_COLS),
                "features": feat_cols,
                "labels": {str(k): v for k, v in label_cols.items()},
            },
            sort_keys=True,
        )
    )


def load_or_build_anchors(
    events_root: Path,
    usable: list[str],
    pin_by_day: dict,
    want: list[str],
    feat_cols: list[str],
    label_cols: dict[int, str],
    stage_root: Path,
    no_stage: bool,
):
    """Per-day incremental staging with resume; bounded to one day's events at a time."""
    frames, audit = [], {}
    anch_dir = stage_root / "anchors"
    done_dir = stage_root / "_done"
    if not no_stage:
        anch_dir.mkdir(parents=True, exist_ok=True)
        done_dir.mkdir(parents=True, exist_ok=True)
    for day in usable:
        pin = pin_by_day[day]
        ap = anch_dir / f"{day}.parquet"
        mp = done_dir / f"{day}.json"
        if not no_stage and ap.exists() and mp.exists():
            info = json.loads(mp.read_text())
            if info.get("pin") == pin:
                frames.append(pl.read_parquet(ap))
                audit[day] = {"staged": True, "rows": info.get("rows")}
                continue
        if not no_stage and mp.exists():
            info = json.loads(mp.read_text())
            if info.get("pin") == pin and info.get("rows") == 0:
                audit[day] = {"staged": True, "rows": 0}
                continue
        day_df = read_day(events_root / "events" / f"{day}.parquet", want)
        missing = [c for c in want if c not in day_df.columns]
        if missing:
            raise SystemExit(f"day {day} events lack required columns {missing}")
        a = build_day_anchors(day_df, feat_cols, label_cols)
        if a is None or a.height == 0:
            if not no_stage:
                mp.write_text(json.dumps({"pin": pin, "rows": 0}))
            audit[day] = {"staged": False, "rows": 0}
            continue
        if not no_stage:
            tmp = ap.with_suffix(".tmp.parquet")
            a.write_parquet(tmp)
            tmp.replace(ap)
            mp.write_text(json.dumps({"pin": pin, "rows": a.height}))
        frames.append(a)
        audit[day] = {"staged": False, "rows": a.height}
    if not frames:
        raise SystemExit(f"no anchors built from {events_root / 'events'}")
    return pl.concat(frames, how="diagonal_relaxed"), audit


# ---------------------------------------------------------------- scaler / distance
def fit_transform(p_raw: np.ndarray, q_raw: np.ndarray):
    """TRAIN-only robust standardisation + explicit missing indicators.

    Returns (p_scaled, q_scaled, info) or None if too few features survive the coverage filter.
    """
    finite = np.isfinite(p_raw)
    coverage = finite.mean(axis=0) if p_raw.size else np.zeros(p_raw.shape[1])
    keep = coverage >= COV_MIN
    if int(keep.sum()) < MIN_FEATURES:
        return None
    p_kept = p_raw[:, keep]
    med = np.nanmedian(p_kept, axis=0)
    q75 = np.nanpercentile(p_kept, 75, axis=0)
    q25 = np.nanpercentile(p_kept, 25, axis=0)
    iqr = q75 - q25
    mad = 1.4826 * np.nanmedian(np.abs(p_kept - med), axis=0)
    scale = np.where(iqr > 1e-12, iqr, np.where(mad > 1e-12, mad, 1.0))
    miss = (~np.isfinite(p_kept)).mean(axis=0)
    ind = (miss > MISS_LO) & (miss < MISS_HI)

    def transform(a_raw: np.ndarray) -> np.ndarray:
        a_kept = a_raw[:, keep]
        z = (a_kept - med) / scale
        z[~np.isfinite(z)] = 0.0
        if ind.any():
            z = np.concatenate([z, (~np.isfinite(a_kept[:, ind])).astype(np.float64)], axis=1)
        return z.astype(np.float32)

    info = {
        "n_candidate": int(p_raw.shape[1]),
        "n_kept": int(keep.sum()),
        "n_dropped_low_coverage": int((~keep).sum()),
        "n_missing_indicators": int(ind.sum()),
    }
    return transform(p_raw), transform(q_raw), info


def knn_topk(q_mat: np.ndarray, p_mat: np.ndarray, k: int, chunk: int = 512):
    """Exact euclidean top-k (distance-ordered, deterministic tie-break by index)."""
    nq, npool = q_mat.shape[0], p_mat.shape[0]
    k = int(min(k, npool))
    p_sq = (p_mat * p_mat).sum(axis=1)
    idx = np.full((nq, k), -1, dtype=np.int64)
    dist = np.full((nq, k), np.nan, dtype=np.float64)
    for s in range(0, nq, chunk):
        q_chunk = q_mat[s : s + chunk]
        q_sq = (q_chunk * q_chunk).sum(axis=1)
        d2 = q_sq[:, None] + p_sq[None, :] - 2.0 * (q_chunk @ p_mat.T)
        np.maximum(d2, 0.0, out=d2)
        if k < npool:
            part = np.argpartition(d2, k - 1, axis=1)[:, :k]
        else:
            part = np.tile(np.arange(npool, dtype=np.int64), (q_chunk.shape[0], 1))
        d = np.take_along_axis(d2, part, axis=1)
        order = np.lexsort((part, d), axis=1)
        part = np.take_along_axis(part, order, axis=1)
        d = np.take_along_axis(d, order, axis=1)
        idx[s : s + q_chunk.shape[0]] = part
        dist[s : s + q_chunk.shape[0]] = np.sqrt(d)
    return idx, dist


def dedup_underlying(idx: np.ndarray, dist: np.ndarray, pool_uk: np.ndarray, n_uk: int, k: int):
    """Keep the nearest neighbour per underlying (day,ticker); pad with -1/NaN."""
    nq, kr = idx.shape
    if kr == 0:
        return np.full((nq, k), -1, np.int64), np.full((nq, k), np.nan, np.float64)
    flat_row = np.repeat(np.arange(nq, dtype=np.int64), kr)
    flat_uk = pool_uk[idx.ravel()]
    key = flat_row * np.int64(n_uk) + flat_uk
    _, first = np.unique(key, return_index=True)
    sel = np.sort(first)
    sel_row = flat_row[sel]
    counts = np.bincount(sel_row, minlength=nq)
    starts = np.repeat(np.cumsum(counts) - counts, counts)
    order_in = np.arange(sel.size) - starts
    m = order_in < k
    sel_k, rows, pos = sel[m], sel_row[m], order_in[m]
    nbr_idx = np.full((nq, k), -1, dtype=np.int64)
    nbr_dist = np.full((nq, k), np.nan, dtype=np.float64)
    nbr_idx[rows, pos] = idx.ravel()[sel_k]
    nbr_dist[rows, pos] = dist.ravel()[sel_k]
    return nbr_idx, nbr_dist


# ---------------------------------------------------------------- metric blocks
def _baseline_block(yk, pk, bk, label: str) -> dict:
    """Declared naive TRAIN scalar-mean comparator, on the identical known TEST rows.

    ``bk`` is the constant finite TRAIN-pool horizon mean in canonical original-cash dollars
    (per fold/view/declared kind support), carried to every TEST row; ``pk`` is the NN
    expected-dollar prediction.  The comparison uses only rows where the actual, the NN
    prediction and the baseline are all finite, i.e. exactly the NN error support.  The
    baseline is fit on TRAIN only (never TEST) and is not pooled across the N3/N5 views.
    """
    common = np.isfinite(yk) & np.isfinite(pk) & np.isfinite(bk)
    out = {
        "label": label,
        "declared": (
            "naive TRAIN scalar mean per fold/view/declared kind support and horizon; "
            "finite TRAIN labels only, carried as a constant to TEST"
        ),
        "n_common_test_rows": int(common.sum()),
        "fit_on_train_only": True,
        "pooled_across_views": False,
    }
    if int(common.sum()) < MIN_STAT:
        out["insufficient"] = True
        return out
    yc, pc, bc = yk[common], pk[common], bk[common]
    err_nn = pc - yc
    err_b = bc - yc
    mae_nn = float(np.abs(err_nn).mean() * DOLLAR)
    mae_b = float(np.abs(err_b).mean() * DOLLAR)
    rmse_nn = float(np.sqrt((err_nn**2).mean()) * DOLLAR)
    rmse_b = float(np.sqrt((err_b**2).mean()) * DOLLAR)
    out.update(
        {
            "n_usable": int(common.sum()),
            "baseline_dollars_per_100_original_cash": float(bc.mean() * DOLLAR),
            "nn_pred_mean_dollars_per_100_original_cash": float(pc.mean() * DOLLAR),
            "actual_mean_dollars_per_100_original_cash": float(yc.mean() * DOLLAR),
            "nn_mae_dollars_per_100_original_cash": mae_nn,
            "baseline_mae_dollars_per_100_original_cash": mae_b,
            "nn_rmse_dollars_per_100_original_cash": rmse_nn,
            "baseline_rmse_dollars_per_100_original_cash": rmse_b,
            "nn_bias_dollars_per_100_original_cash": float(err_nn.mean() * DOLLAR),
            "baseline_bias_dollars_per_100_original_cash": float(err_b.mean() * DOLLAR),
            "mae_skill_vs_scalar_mean": (1.0 - mae_nn / mae_b) if mae_b > 0 else None,
            "rmse_skill_vs_scalar_mean": (1.0 - rmse_nn / rmse_b) if rmse_b > 0 else None,
            "calibration": [
                {
                    "bin": 0,
                    "lo": float(bc.min()),
                    "hi": float(bc.max()),
                    "n": int(bc.size),
                    "mean_pred_dollars_per_100_original_cash": float(bc.mean() * DOLLAR),
                    "mean_actual_dollars_per_100_original_cash": float(yc.mean() * DOLLAR),
                }
            ],
            "note": (
                "the baseline is constant per fold/view/kind/horizon, so its calibration has "
                "one bin; errors are compared against the NN expected-dollar prediction on "
                "the identical known TEST rows.  Positive skill => retrieval beats the naive "
                "scalar mean"
            ),
        }
    )
    return out


def metric_block(y, pmed, pmean, base, causal_mark, original_fill, label: str) -> dict:
    """Held-out regression/dollar block.

    ``y``/``pmed``/``pmean`` are signed increment dollars per ORIGINAL CLAIM CASH budget
    (the primary calibration).  The EXPECTED-dollar estimate is the neighbour MEAN
    ``pmean``; ``pmed`` is retained only as a separate median/quantile diagnostic (a median
    can be negative while the mean is positive, e.g. 30 targets at -0.01 and 20 at +0.10
    original-cash dollars give median -0.01 but mean +0.034, so the median is never treated
    as a mean miscalibration).  ``base`` is the declared naive TRAIN scalar-mean comparator
    carried to the same TEST rows.  ``causal_mark`` is the anchor's recorded causal current
    mark ``feature_px`` (known at the decision time) and ``original_fill`` is the original
    fill ``fill_px`` (known once the claim filled).  They define the causal-mark wealth per
    original claim cash ``W_mark = causal_mark/original_fill*0.995/1.005``.  Dividing a
    dollar increment by ``W_mark`` gives the predicted/realised return on the decision-time
    mark dollar, the only comparable quantity for the fresh 2*side/(1-side) hurdle.  The
    future execution reference ``sell_px`` never enters this function: it must not move the
    above-hurdle selection or the planned size.  A query with unknown/nonpositive causal
    mark or fill is UNKNOWN for the return contrast and counted; it is never imputed with
    zero or fitted cash.
    """
    y = np.asarray(y, dtype=np.float64)
    pmed = np.asarray(pmed, dtype=np.float64)
    pmean = np.asarray(pmean, dtype=np.float64)
    base = np.asarray(base, dtype=np.float64)
    causal_mark = np.asarray(causal_mark, dtype=np.float64)
    original_fill = np.asarray(original_fill, dtype=np.float64)
    norm_ok = (
        np.isfinite(causal_mark)
        & (causal_mark > 0.0)
        & np.isfinite(original_fill)
        & (original_fill > 0.0)
    )
    w_mark = np.full(y.shape, np.nan, dtype=np.float64)
    w_mark[norm_ok] = causal_mark[norm_ok] / original_fill[norm_ok] * OWNED_FEE
    known = np.isfinite(y) & np.isfinite(pmean) & np.isfinite(pmed) & norm_ok
    out = {
        "label": label,
        "n_queries": int(y.size),
        "n_label_known": int(np.isfinite(y).sum()),
        "n_label_unknown": int((~np.isfinite(y)).sum()),
        "n_causal_mark_unknown": int((~norm_ok).sum()),
        "n_no_neighbour_support": int((~np.isfinite(pmean)).sum()),
        "n_usable": int(known.sum()),
        "estimator": "neighbour MEAN of retrieved TRAIN labels (expected original-cash dollars)",
    }
    if int(known.sum()) < MIN_STAT:
        out["insufficient"] = True
        return out
    yk = y[known]
    pk = pmean[known]  # EXPECTED-dollar estimate: neighbour MEAN
    pmedk = pmed[known]  # separate median/quantile diagnostic only
    bk = base[known]
    wk = w_mark[known]
    mk = causal_mark[known]
    pk_ret = pk / wk  # predicted return on the causal-mark dollar
    yk_ret = yk / wk  # realised return on the causal-mark dollar
    pos = pk > 0
    neg = ~pos
    above = pk_ret > FRESH_HURDLE
    # explicit causal fixed fresh size: (0.9*reserved_budget)/(feature_px*(1+side)) per query.
    # It depends on the decision-time mark only, so it is identical for queries sharing
    # feature_px/fill and prediction regardless of any future opening.
    q_plan = FRESH_BUDGET_FRAC * PLANNED_BUDGET / (mk * (1.0 + SIDE))
    fresh_pred = (1.0 + pk_ret) * (1.0 - SIDE) / (1.0 + SIDE) - 1.0
    fresh_actual = (1.0 + yk_ret) * (1.0 - SIDE) / (1.0 + SIDE) - 1.0
    out.update(
        {
            "pred_dollars_per_100_original_cash": qstats(pk * DOLLAR),
            "actual_dollars_per_100_original_cash": qstats(yk * DOLLAR),
            "pred_mean_estimator_bias_dollars_per_100_original_cash": float(
                (pk - yk).mean() * DOLLAR
            ),
            "causal_mark_wealth_per_original_cash": qstats(wk),
            "causal_mark_px": qstats(mk),
            "pred_return_per_causal_mark_dollar": qstats(pk_ret),
            "actual_return_per_causal_mark_dollar": qstats(yk_ret),
            "pearson": pearson(pk, yk),
            "spearman": spearman(pk, yk),
            "calibration": calibration_bins(pk, yk),
            "neighbour_median_quantile_diagnostic": {
                "role": "median/quantile diagnostic only; NOT the expected-dollar estimate",
                "pred_dollars_per_100_original_cash": qstats(pmedk * DOLLAR),
                "pearson": pearson(pmedk, yk),
                "spearman": spearman(pmedk, yk),
                "return_per_causal_mark_dollar": qstats(pmedk / wk),
                "calibration": calibration_bins(pmedk, yk),
                "note": (
                    "the neighbour MEDIAN is a separate quantile diagnostic, not a mean "
                    "miscalibration: 30 neighbours at -0.01 and 20 at +0.10 original-cash "
                    "dollars have median -0.01 but mean +0.034, so expected-dollar "
                    "calibration, action selection and the fresh hurdle all use the MEAN"
                ),
            },
            "expected_action_value": {
                "n_pred_positive": int(pos.sum()),
                "n_pred_nonpositive": int(neg.sum()),
                "mean_actual_dollars_per_100_original_cash_when_pred_positive": float(
                    yk[pos].mean() * DOLLAR
                )
                if pos.any()
                else None,
                "mean_actual_dollars_per_100_original_cash_when_pred_nonpositive": float(
                    yk[neg].mean() * DOLLAR
                )
                if neg.any()
                else None,
                "mean_pred_dollars_per_100_original_cash_when_pred_positive": float(
                    pk[pos].mean() * DOLLAR
                )
                if pos.any()
                else None,
                "selection_note": (
                    "the expected-action group is chosen on the predicted MEAN "
                    "original-cash dollars (pk>0), the expected-dollar estimator; "
                    "the median is reported separately as a quantile diagnostic.  "
                    "It never reads the query's future opening"
                ),
            },
            "paper_fresh_cost": {
                "hurdle_return_per_causal_mark_dollar": float(FRESH_HURDLE),
                "n_pred_above_hurdle": int(above.sum()),
                "frac_pred_above_hurdle": float(above.mean()),
                "planned_fresh_quantity_per_100_original_cash": qstats(q_plan),
                "planned_fresh_quantity_rule": (
                    "0.9*reserved_budget/(feature_px*(1+side)); reserved_budget="
                    f"{PLANNED_BUDGET:g} per query.  Explicit causal fixed size from the "
                    "decision-time mark only, so it is identical for queries sharing feature_px "
                    "regardless of any future opening.  Actual fill and cash affordability are "
                    "UNKNOWN (an executor question, never resized here)"
                ),
                "mean_pred_return_per_causal_mark_dollar_above": float(pk_ret[above].mean())
                if above.any()
                else None,
                "mean_actual_return_per_causal_mark_dollar_above": float(yk_ret[above].mean())
                if above.any()
                else None,
                "mean_pred_fresh_round_trip_return_above": float(fresh_pred[above].mean())
                if above.any()
                else None,
                "mean_actual_fresh_round_trip_return_above": float(fresh_actual[above].mean())
                if above.any()
                else None,
                "proxy": "(1+return_per_causal_mark_dollar)*(1-side)/(1+side)-1",
                "note": (
                    "per-causal-mark-dollar returns are dollar increments divided by the "
                    "query's own causal-mark wealth per original claim cash "
                    "(feature_px/fill_px*0.995/1.005); the 2*side/(1-side) fresh round-trip "
                    "hurdle is zero in the proxy.  Above-hurdle selection and the planned "
                    "fresh size use the MEAN expected-dollar estimate and feature_px only, "
                    "never the future sell_px.  Queries with unknown/nonpositive causal mark "
                    "or fill are UNKNOWN, never imputed.  NOT portfolio P&L, NOT a fill claim"
                ),
                "limitations": (
                    "assumes simultaneous entry/exit at the decision mark and the "
                    "later event mark with 50bps/side; it ignores price gaps, queue "
                    "and partial fills, and any cash-reservation shortfall, so it is "
                    "a comparison ruler rather than an executable payoff"
                ),
            },
        }
    )
    out["scalar_mean_baseline"] = _baseline_block(yk, pk, bk, f"{label}|scalar_mean")
    return out


# ---------------------------------------------------------------- representative rows
def representative_entry(fold, kind, qmeta, nbr_idx_row, nbr_dist_row, pool_meta, pool_y):
    nbrs = []
    for j in range(nbr_idx_row.size):
        gi = int(nbr_idx_row[j])
        if gi < 0:
            break
        yrow = {}
        for hi, h in enumerate(HORIZONS):
            v = float(pool_y[gi, hi])
            yrow[f"h{h}"] = None if not np.isfinite(v) else round(v * DOLLAR, 4)
        nbrs.append(
            {
                "day": pool_meta["day"][gi],
                "ticker": pool_meta["ticker"][gi],
                "clock": int(pool_meta["clock"][gi]),
                "rank": int(pool_meta["rank"][gi]),
                "t": int(pool_meta["t"][gi]),
                "kind": pool_meta["kind"][gi],
                "distance": float(nbr_dist_row[j]),
                "y_dollars_per_100_original_cash": yrow,
            }
        )
        if len(nbrs) >= 8:
            break
    return {
        "fold": int(fold),
        "query_kind": kind,
        "query": {
            "day": qmeta["day"],
            "clock": int(qmeta["clock"]),
            "rank": int(qmeta["rank"]),
            "ticker": qmeta["ticker"],
            "t": int(qmeta["t"]),
        },
        "neighbours": nbrs,
    }


# ---------------------------------------------------------------- main
def folds_for(args, disc_len: int):
    if args.smoke_days:
        n = int(args.smoke_days)
        if n < 12:
            raise SystemExit("--smoke-days must be >= 12")
        b1 = max(2, int(round(150 / N_DISCOVERY * n)))
        b2 = max(b1 + 1, int(round(300 / N_DISCOVERY * n)))
        b3 = max(b2 + 1, int(round(450 / N_DISCOVERY * n)))
        b3 = min(b3, n - 1)
        return ((0, b1, b1, b2), (0, b2, b2, b3), (0, b3, b3, n)), n
    return FOLDS, disc_len


def main() -> int:
    ap = argparse.ArgumentParser(description="owned-claim predeclared retrieval instrument")
    ap.add_argument("--data-root", default=None)
    ap.add_argument(
        "--events-root", default=None, help="default <data-root>/harvest01/lifecycle/v2/owned_claim"
    )
    ap.add_argument(
        "--out", default=None, help="default factory/artifacts/owned_claim_retrieval.json"
    )
    ap.add_argument(
        "--folds",
        default=",".join(str(i) for i in range(len(FOLDS))),
        help="fold indices to run (subset = non-evidence diagnostic)",
    )
    ap.add_argument(
        "--smoke-days",
        type=int,
        default=0,
        help="tiny non-evidence run over the first N discovery days",
    )
    ap.add_argument(
        "--stage-root",
        default=None,
        help="per-day anchor cache root; default <events-root>/retrieval_stage",
    )
    ap.add_argument(
        "--no-stage",
        action="store_true",
        help="ignore the per-day anchor cache (still bounded per day)",
    )
    args = ap.parse_args()

    data_root = ls.bps.resolve_data_root(args.data_root)
    split = ls.load_split(data_root)
    disc_all = list(split["discovery_days"])
    if len(disc_all) != N_DISCOVERY:
        raise SystemExit(f"discovery day count {len(disc_all)} != {N_DISCOVERY}")
    events_root = (
        Path(args.events_root) if args.events_root else ls.v2_dir(data_root) / "owned_claim"
    )
    out_path = Path(args.out) if args.out else DEFAULT_OUT

    folds_all, disc_len = folds_for(args, len(disc_all))
    disc = disc_all[:disc_len]
    fold_ids = [int(x) for x in args.folds.split(",") if x.strip() != ""]
    if not fold_ids or any(i < 0 or i >= len(folds_all) for i in fold_ids):
        raise SystemExit(f"--folds must index {list(range(len(folds_all)))}")

    evidence = True
    notes = []
    if args.smoke_days:
        evidence = False
        notes.append(f"non-evidence smoke run over first {disc_len} discovery days; folds rescaled")
    if fold_ids != list(range(len(folds_all))):
        evidence = False
        notes.append(f"fold subset {fold_ids} of {list(range(len(folds_all)))} (diagnostic only)")

    windows = []
    for i in fold_ids:
        tr0, tr1, te0, te1 = folds_all[i]
        tr0, tr1 = min(tr0, len(disc)), min(tr1, len(disc))
        te0, te1 = min(te0, len(disc)), min(te1, len(disc))
        if te0 >= te1:
            raise SystemExit(f"fold {i}: empty test window after clamping ({te0},{te1})")
        if tr1 > te0:
            raise SystemExit(f"fold {i}: train overlaps test ({tr0},{tr1}) vs ({te0},{te1})")
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
    if not set(days_needed) <= set(disc_all):
        raise SystemExit("event days escape the discovery split; refusing")
    usable, markers = resolve_event_days(days_needed, events_root)

    first_schema = pl.read_parquet_schema(events_root / "events" / f"{usable[0]}.parquet")
    avail = set(first_schema)
    missing_keys = [k for k in REQUIRED_KEYS if k not in avail]
    if missing_keys:
        raise SystemExit(f"event schema missing required columns {missing_keys}: {sorted(avail)}")
    label_cols = resolve_labels(avail)
    feat_cols = [c for c in REP_FLAT if c in avail]
    missing_feats = [c for c in REP_FLAT if c not in avail]
    if len(feat_cols) < MIN_FEATURES:
        raise SystemExit(f"too few representation features present ({len(feat_cols)}): {feat_cols}")
    for c in feat_cols:
        low = c.lower()
        if not c.startswith("feature_") or any(s in low for s in FORBIDDEN_SUBSTRINGS):
            raise SystemExit(f"representation tripwire: '{c}' is not a clean past-only feature")
    if EXECUTION_REFERENCE_COL in NORMALIZER_COLS:
        raise SystemExit(
            f"causal tripwire: the future execution reference {EXECUTION_REFERENCE_COL!r} may "
            "not be a normaliser; refusing"
        )
    if CAUSAL_MARK_COL in feat_cols or EXECUTION_REFERENCE_COL in feat_cols:
        raise SystemExit(
            f"causal tripwire: normaliser/execution columns ({CAUSAL_MARK_COL!r}, "
            f"{EXECUTION_REFERENCE_COL!r}) may not enter the representation features; refusing"
        )

    want = list(
        dict.fromkeys([*KEYS, "event_kind", *NORMALIZER_COLS, *label_cols.values(), *feat_cols])
    )
    schema_key = anchor_schema_key(feat_cols, label_cols)
    pin_by_day = {
        m["day"]: sha256_text(f"{m['day']}|{m['source_sha256']}|{m['input_hash']}|{schema_key}")
        for m in markers
    }
    stage_root = Path(args.stage_root) if args.stage_root else events_root / "retrieval_stage"
    anc, stage_audit = load_or_build_anchors(
        events_root, usable, pin_by_day, want, feat_cols, label_cols, stage_root, args.no_stage
    )
    n_anc = anc.height
    if n_anc == 0:
        raise SystemExit("zero anchors; refusing")
    missing_norm = [c for c in NORMALIZER_COLS if c not in anc.columns]
    if missing_norm:
        raise SystemExit(f"anchor table lacks causal normaliser columns {missing_norm}; refusing")

    day_index = {d: i for i, d in enumerate(disc)}
    x_raw = anc.select([pl.col(c).cast(pl.Float64, strict=False) for c in feat_cols]).to_numpy()
    x_raw = np.asarray(x_raw, dtype=np.float64)
    y_all = np.column_stack(
        [
            np.asarray(
                anc.select(pl.col(label_cols[h]).cast(pl.Float64, strict=False)).to_numpy(),
                dtype=np.float64,
            )
            for h in HORIZONS
        ]
    )
    fill_arr = np.asarray(
        anc.select(pl.col(ORIGINAL_FILL_COL).cast(pl.Float64, strict=False)).to_numpy(),
        dtype=np.float64,
    ).ravel()
    causal_mark_arr = np.asarray(
        anc.select(pl.col(CAUSAL_MARK_COL).cast(pl.Float64, strict=False)).to_numpy(),
        dtype=np.float64,
    ).ravel()

    day_arr = np.asarray(anc["day"].to_list())
    ticker_arr = np.asarray(anc["ticker"].to_list())
    kind_arr = np.asarray(anc["anchor_kind"].to_list())
    clock_arr = anc["clock"].to_numpy().astype(np.int64)
    rank_arr = anc["rank"].to_numpy().astype(np.int64)
    t_arr = anc["t"].to_numpy().astype(np.int64)
    day_idx_arr = np.array([day_index[d] for d in day_arr.tolist()], dtype=np.int64)

    uk_str = [f"{d}|{tk}" for d, tk in zip(day_arr.tolist(), ticker_arr.tolist(), strict=True)]
    uk_codes = {s: i for i, s in enumerate(sorted(set(uk_str)))}
    uk_all = np.array([uk_codes[s] for s in uk_str], dtype=np.int64)
    n_uk = len(uk_codes)

    acc = {
        "mode": [],
        "fold": [],
        "view": [],
        "kind": [],
        "day": [],
        "clock": [],
        "rank": [],
        "ticker": [],
        "t": [],
        "nneigh": [],
        "dmean": [],
        "fill_px": [],
        "causal_mark_px": [],
    }
    for h in HORIZONS:
        acc[f"y{h}"] = []
        acc[f"pmed{h}"] = []
        acc[f"pmean{h}"] = []
        acc[f"base{h}"] = []
        acc[f"nk{h}"] = []
    reps = []
    scaler_audit = {}
    fold_window = {w["fold"]: w for w in windows}

    for mode in ("kind", "pooled"):
        for view_name, view_rank in RANK_VIEWS.items():
            for fold in fold_ids:
                w = fold_window[fold]
                tr0, tr1 = w["train_idx"]
                te0, te1 = w["test_idx"]
                in_train = (day_idx_arr >= tr0) & (day_idx_arr < tr1)
                in_test = (day_idx_arr >= te0) & (day_idx_arr < te1)
                for kind in FOCUS_KINDS:
                    if mode == "kind":
                        pool = in_train & (kind_arr == kind) & (rank_arr <= view_rank)
                    else:
                        pool = (
                            in_train
                            & np.isin(kind_arr, np.asarray(FOCUS_KINDS))
                            & (rank_arr <= view_rank)
                        )
                    query = in_test & (kind_arr == kind) & (rank_arr <= view_rank)
                    npool, nq = int(pool.sum()), int(query.sum())
                    tag = f"{mode}|{view_name}|{kind}|fold{fold}"
                    if npool == 0 or nq == 0:
                        scaler_audit[tag] = {"n_pool": npool, "n_query": nq, "skipped": True}
                        continue
                    p_raw, q_raw = x_raw[pool], x_raw[query]
                    fitted = fit_transform(p_raw, q_raw)
                    if fitted is None:
                        scaler_audit[tag] = {
                            "n_pool": npool,
                            "n_query": nq,
                            "skipped": True,
                            "reason": "too_few_features",
                        }
                        continue
                    p_mat, q_mat, sinfo = fitted
                    idx, dist = knn_topk(q_mat, p_mat, K_RAW)
                    pool_uk = uk_all[pool]
                    nbr_idx, nbr_dist = dedup_underlying(idx, dist, pool_uk, n_uk, K)
                    valid = nbr_idx >= 0
                    pool_global = np.nonzero(pool)[0]
                    pool_y = y_all[pool_global]
                    # declared naive TRAIN scalar-mean comparator: finite TRAIN-pool horizon
                    # means in canonical original-cash dollars, per fold/view/declared kind
                    # support.  Carried as a constant to the TEST rows; never fit on TEST.
                    base_h = np.array([finite_mean(pool_y[:, hi]) for hi in range(len(HORIZONS))])
                    nneigh = valid.sum(axis=1).astype(np.int64)
                    dmean = np.where(valid, nbr_dist, np.nan)
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        dmean = np.nanmean(dmean, axis=1)
                    scaler_audit[tag] = {"n_pool": npool, "n_query": nq, **sinfo}

                    q_day = day_arr[query]
                    q_ticker = ticker_arr[query]
                    q_clock = clock_arr[query]
                    q_rank = rank_arr[query]
                    q_t = t_arr[query]

                    pmed_all = np.full((nq, len(HORIZONS)), np.nan)
                    pmean_all = np.full((nq, len(HORIZONS)), np.nan)
                    nk_all = np.zeros((nq, len(HORIZONS)), dtype=np.int64)
                    for hi, h in enumerate(HORIZONS):
                        yv = np.full((nq, K), np.nan)
                        if K > 0:
                            yv[valid] = pool_y[nbr_idx[valid], hi]
                        nk_all[:, hi] = (~np.isnan(yv)).sum(axis=1)
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", RuntimeWarning)
                            pmed_all[:, hi] = np.nanmedian(yv, axis=1)
                            pmean_all[:, hi] = np.nanmean(yv, axis=1)
                        acc[f"y{h}"].extend(y_all[query][:, hi].tolist())
                        acc[f"pmed{h}"].extend(pmed_all[:, hi].tolist())
                        acc[f"pmean{h}"].extend(pmean_all[:, hi].tolist())
                        acc[f"base{h}"].extend([float(base_h[hi])] * nq)
                        acc[f"nk{h}"].extend(nk_all[:, hi].tolist())
                    acc["mode"].extend([mode] * nq)
                    acc["fold"].extend([fold] * nq)
                    acc["view"].extend([view_name] * nq)
                    acc["kind"].extend([kind] * nq)
                    acc["day"].extend(q_day.tolist())
                    acc["clock"].extend(q_clock.tolist())
                    acc["rank"].extend(q_rank.tolist())
                    acc["ticker"].extend(q_ticker.tolist())
                    acc["t"].extend(q_t.tolist())
                    acc["nneigh"].extend(nneigh.tolist())
                    acc["dmean"].extend(dmean.tolist())
                    acc["fill_px"].extend(fill_arr[query].tolist())
                    acc["causal_mark_px"].extend(causal_mark_arr[query].tolist())

                    if mode == "kind" and view_name == "N5":
                        # diagnostic example pick only (extreme h60 median magnitude to show
                        # illustrative past matches); this is NOT expected-dollar action
                        # selection, which uses the neighbour MEAN
                        score = np.abs(pmed_all[:, 2])
                        finite = np.isfinite(score)
                        if finite.any():
                            order = np.argsort(-np.where(finite, score, -np.inf), kind="stable")
                            pool_meta = {
                                "day": day_arr[pool_global].tolist(),
                                "ticker": ticker_arr[pool_global].tolist(),
                                "clock": clock_arr[pool_global],
                                "rank": rank_arr[pool_global],
                                "t": t_arr[pool_global],
                                "kind": kind_arr[pool_global].tolist(),
                            }
                            for qi in order[:2]:
                                qmeta = {
                                    "day": q_day[qi],
                                    "clock": q_clock[qi],
                                    "rank": q_rank[qi],
                                    "ticker": q_ticker[qi],
                                    "t": q_t[qi],
                                }
                                reps.append(
                                    representative_entry(
                                        fold,
                                        kind,
                                        qmeta,
                                        nbr_idx[qi],
                                        nbr_dist[qi],
                                        pool_meta,
                                        pool_y,
                                    )
                                )

    str_cols = ("day", "ticker", "kind", "mode", "view")
    tbl = {k: np.asarray(v) for k, v in acc.items() if k not in str_cols}
    tbl["day"] = np.asarray(acc["day"])
    tbl["ticker"] = np.asarray(acc["ticker"])
    tbl["kind"] = np.asarray(acc["kind"])
    tbl["mode"] = np.asarray(acc["mode"])
    tbl["view"] = np.asarray(acc["view"])

    def group_metrics(mode: str, view_name: str) -> dict:
        m = (tbl["mode"] == mode) & (tbl["view"] == view_name)
        out = {}
        for kind in (*FOCUS_KINDS, "__all_focus__"):
            km = m if kind == "__all_focus__" else (m & (tbl["kind"] == kind))
            if not km.any():
                out[kind] = {}
                continue
            blocks = {}
            for h in HORIZONS:
                blocks[f"h{h}"] = metric_block(
                    tbl[f"y{h}"][km],
                    tbl[f"pmed{h}"][km],
                    tbl[f"pmean{h}"][km],
                    tbl[f"base{h}"][km],
                    tbl["causal_mark_px"][km],
                    tbl["fill_px"][km],
                    f"{mode}|{view_name}|{kind}|h{h}",
                )
            out[kind] = blocks
        return out

    metrics = {
        "A_same_kind_neighbours": {v: group_metrics("kind", v) for v in RANK_VIEWS},
        "B_pooled_neighbours_all_kinds": {v: group_metrics("pooled", v) for v in RANK_VIEWS},
    }

    # regression/dollar contrast (same-kind vs pooled); no classification objective
    personality_contrast = {}
    for v in RANK_VIEWS:
        for kind in (*FOCUS_KINDS, "__all_focus__"):
            entry = {}
            for h in HORIZONS:
                a = metrics["A_same_kind_neighbours"][v].get(kind, {}).get(f"h{h}", {})
                b = metrics["B_pooled_neighbours_all_kinds"][v].get(kind, {}).get(f"h{h}", {})
                sa = a.get("scalar_mean_baseline") or {}
                sb = b.get("scalar_mean_baseline") or {}
                entry[f"h{h}"] = {
                    "pearson_same_kind": a.get("pearson"),
                    "pearson_pooled": b.get("pearson"),
                    "spearman_same_kind": a.get("spearman"),
                    "spearman_pooled": b.get("spearman"),
                    "pred_bias_dollars_per_100_original_cash_same_kind": a.get(
                        "pred_mean_estimator_bias_dollars_per_100_original_cash"
                    ),
                    "pred_bias_dollars_per_100_original_cash_pooled": b.get(
                        "pred_mean_estimator_bias_dollars_per_100_original_cash"
                    ),
                    "nn_mae_dollars_per_100_original_cash_same_kind": sa.get(
                        "nn_mae_dollars_per_100_original_cash"
                    ),
                    "baseline_mae_dollars_per_100_original_cash_same_kind": sa.get(
                        "baseline_mae_dollars_per_100_original_cash"
                    ),
                    "mae_skill_vs_scalar_mean_same_kind": sa.get("mae_skill_vs_scalar_mean"),
                    "nn_mae_dollars_per_100_original_cash_pooled": sb.get(
                        "nn_mae_dollars_per_100_original_cash"
                    ),
                    "baseline_mae_dollars_per_100_original_cash_pooled": sb.get(
                        "baseline_mae_dollars_per_100_original_cash"
                    ),
                    "mae_skill_vs_scalar_mean_pooled": sb.get("mae_skill_vs_scalar_mean"),
                }
            personality_contrast.setdefault(v, {})[kind] = entry

    # ---- support / census
    def counts_by(mask, arr):
        vals, counts = np.unique(arr[mask], return_counts=True)
        return {str(v): int(c) for v, c in zip(vals.tolist(), counts.tolist(), strict=True)}

    support = {
        "anchors_total": int(n_anc),
        "anchors_by_kind": counts_by(np.ones(n_anc, bool), kind_arr),
        "anchors_by_kind_clock": {k: counts_by(kind_arr == k, clock_arr) for k in FOCUS_KINDS},
        "anchors_by_kind_rank": {k: counts_by(kind_arr == k, rank_arr) for k in FOCUS_KINDS},
        "families": {
            "distinct_underlying_day_ticker": int(n_uk),
            "anchors_per_family_mean": (float(n_anc) / n_uk) if n_uk else None,
            "note": (
                "anchors are one per (claim,kind) so the N3/N5 rank views stay valid; "
                "underlying (day,ticker) duplication is removed at the neighbour stage"
            ),
        },
        "neighbour_dedup": {
            "K_fixed": K,
            "K_raw_candidates": K_RAW,
            "mean_unique_underlying_neighbours": (
                float(np.nanmean(tbl["nneigh"])) if tbl["nneigh"].size else None
            ),
            "mean_neighbour_distance": (
                float(np.nanmean(tbl["dmean"])) if tbl["dmean"].size else None
            ),
        },
    }
    for w in windows:
        fold = w["fold"]
        tr0, tr1 = w["train_idx"]
        te0, te1 = w["test_idx"]
        tr_mask = (day_idx_arr >= tr0) & (day_idx_arr < tr1)
        te_mask = (day_idx_arr >= te0) & (day_idx_arr < te1)
        per_kind = {}
        for k in FOCUS_KINDS:
            per_kind[k] = {
                "train_anchors": int((tr_mask & (kind_arr == k)).sum()),
                "test_anchors": int((te_mask & (kind_arr == k)).sum()),
                "test_anchors_N3": int((te_mask & (kind_arr == k) & (rank_arr <= 3)).sum()),
                "test_anchors_N5": int((te_mask & (kind_arr == k) & (rank_arr <= 5)).sum()),
            }
        t_bucket = {k: {} for k in FOCUS_KINDS}
        for k in FOCUS_KINDS:
            km = te_mask & (kind_arr == k)
            for lo, hi in T_BUCKETS:
                t_bucket[k][f"{lo}-{hi if hi < 10**9 else 'end'}"] = int(
                    (km & (t_arr >= lo) & (t_arr <= hi)).sum()
                )
        support.setdefault("per_fold", {})[str(fold)] = {
            "train_days": [w["train_days"][0], w["train_days"][-1]] if w["train_days"] else None,
            "test_days": [w["test_days"][0], w["test_days"][-1]] if w["test_days"] else None,
            "per_kind": per_kind,
            "test_anchor_t_buckets": t_bucket,
        }

    # ---- leakage attestation
    overlaps = []
    for w in windows:
        a = set(w["train_days"]) & set(w["test_days"])
        if a:
            overlaps.append(sorted(a))

    def _pool_before_test(w):
        if not w["train_days"] or not w["test_days"]:
            return True
        return w["train_days"][-1] < w["test_days"][0]

    pool_before_test = all(_pool_before_test(w) for w in windows)
    leakage = {
        "train_test_day_overlap": overlaps,
        "no_test_day_in_pool": all(not o for o in overlaps),
        "no_future_cross_day_neighbour": pool_before_test,
        "pool_strictly_before_test": pool_before_test,
        "train_only_statistics": (
            "coverage filter, medians, robust scales, missing "
            "indicators and the naive scalar-mean baseline are fit on "
            "the TRAIN pool only"
        ),
        "estimator": (
            "the expected-dollar estimate is the neighbour MEAN; the neighbour "
            "median is a separate quantile diagnostic, never the expected value"
        ),
        "scalar_mean_baseline": (
            "per fold/rank-view/declared kind support from finite TRAIN "
            "labels only, carried as a constant to TEST and scored on "
            "the identical known TEST rows; never fit on TEST and never "
            "pooled across the N3/N5 views"
        ),
        "neighbour_clock_dedup": (
            "neighbours are de-duplicated by underlying (day,ticker) across the four pooled clocks"
        ),
        "features": (
            "feature_-prefixed past-only; execution/label/next_* columns excluded. "
            "The causal normalisers feature_px (causal mark) and fill_px (original "
            "fill) are carried only for unit conversion, never as predictors; the "
            "future execution reference sell_px is not read at all"
        ),
        "labels": "increment_<h>_original_dollar used as target only; never a feature",
        "unknown_labels": "excluded from statistics and counted (n_label_unknown)",
        "unknown_marks": (
            "queries with unknown/nonpositive causal mark (feature_px) or "
            "original fill (fill_px) are UNKNOWN for the per-causal-mark-dollar "
            "return contrast and the planned size, and are counted "
            "(n_causal_mark_unknown); never imputed with zero or fitted cash"
        ),
        "discovery_only": "lifecycle_study.discovery_days only; no validation/FREEZE access",
    }

    env_pin = {
        "numpy": np.__version__,
        "polars": pl.__version__,
        "nn": "numpy brute-force euclidean on TRAIN-standardised vector + missing indicators",
    }
    manifest_path = events_root / "manifest.json"
    manifest_pin = None
    manifest_source = None
    if manifest_path.exists():
        man = json.loads(manifest_path.read_text())
        manifest_source = man.get("source_sha256")
        manifest_pin = {
            "path": str(manifest_path),
            "sha256": sha256_file(manifest_path),
            "source_sha256": manifest_source,
            "n_feature_columns": len(man.get("feature_columns", []) or []),
            "discovery_days": len(man.get("discovery_days", []) or []),
        }
    marker_pin_rows = [
        {
            "day": m["day"],
            "source_sha256": m["source_sha256"],
            "input_hash": m["input_hash"],
            "rows": m["rows"],
        }
        for m in markers
    ]
    marker_pin = sha256_text(json.dumps(marker_pin_rows, sort_keys=True))
    config_pin = json.dumps(
        {
            "K": K,
            "K_RAW": K_RAW,
            "views": RANK_VIEWS,
            "folds": [list(f) for f in folds_all],
            "focus": list(FOCUS_KINDS),
            "horizons": list(HORIZONS),
            "side": SIDE,
            "feat_cols": feat_cols,
            "label_cols": label_cols,
            "causal_mark_col": CAUSAL_MARK_COL,
            "original_fill_col": ORIGINAL_FILL_COL,
            "execution_reference_col": EXECUTION_REFERENCE_COL,
            "normalizer_cols": list(NORMALIZER_COLS),
        },
        sort_keys=True,
    )
    run_id = sha256_text(
        "|".join(
            [
                sha256_file(SCRIPT),
                sha256_file(ls.v2_dir(data_root) / "split.json"),
                str(manifest_source),
                marker_pin,
                config_pin,
            ]
        )
    )

    result = {
        "status": "complete",
        "producer": "factory/scripts/owned_claim_retrieval.py",
        "run_id": run_id,
        "script_sha256": sha256_file(SCRIPT),
        "evidence": evidence,
        "non_evidence_note": "; ".join(notes) if notes else None,
        "instrument": {
            "type": "predeclared past-trajectory nearest-neighbour retrieval",
            "focus_kinds": list(FOCUS_KINDS),
            "anchor_rule": (
                "first emitted event per claim (day,clock,rank,ticker) whose "
                "event_kind token set contains the focus kind; one anchor per "
                "(claim,kind).  family=(day,ticker) duplication is removed at the "
                "neighbour stage so the four clocks of one underlying are not "
                "counted four times"
            ),
            "neighbour_rule": (
                "K=50 nearest unique-underlying (day,ticker) TRAIN anchors by "
                "euclidean distance on the TRAIN-standardised compact prefix "
                "vector; mode kind = same focus kind, mode pooled = any focus kind"
            ),
            "clock_dedup": (
                "neighbours de-duplicated by underlying (day,ticker) across the four pooled clocks"
            ),
            "K": K,
            "K_raw_candidates": K_RAW,
            "clocks": list(CLOCKS),
            "rank_views": {
                v: {"query_rank_max": r, "source_pool_rank_max": r} for v, r in RANK_VIEWS.items()
            },
            "horizons_minutes": list(HORIZONS),
            "estimator": (
                "neighbour MEAN of the K retrieved TRAIN horizon labels, in "
                "canonical original-cash dollars; the neighbour median is reported "
                "separately as a quantile diagnostic, not as the expected value"
            ),
            "scalar_mean_baseline": (
                "declared naive comparator: finite TRAIN-pool horizon "
                "mean per fold/rank-view/declared kind support, carried "
                "as a constant to the identical known TEST rows; never "
                "fit on TEST, never pooled across N3/N5"
            ),
            "target": (
                "increment_<h>_original_dollar at the anchor: signed owned-share "
                "increment dollars per ORIGINAL CLAIM CASH budget; entry sunk, common "
                "exit fee; reported per $100 original claim cash (primary) and as a "
                "per-causal-mark-dollar return for the fresh-cost contrast"
            ),
            "normaliser": (
                "causal_mark=feature_px (recorded completed-bar mark, known at the decision "
                "time) and original_fill=fill_px; causal-mark wealth per original claim cash "
                "W_mark=feature_px/fill_px*0.995/1.005.  The execution_reference=sell_px "
                "(first future open) is a LABEL column only: never a selector, normaliser or "
                "size input.  Unknown/nonpositive causal mark or fill -> UNKNOWN, counted "
                "(n_causal_mark_unknown), never imputed"
            ),
            "fresh_size": (
                "planned_fresh_quantity=0.9*reserved_budget/(feature_px*(1+side)); "
                "a causal fixed size from the decision-time mark, identical across "
                "future openings.  Actual fill / cash affordability are UNKNOWN"
            ),
            "no_tuning": "one fixed K, no hyperparameter grid, no feature/model selection loop",
        },
        "representation": {
            "groups": {g: list(cols) for g, cols in REP_GROUPS.items()},
            "resolved_features": feat_cols,
            "unavailable_features": missing_feats,
            "coverage_min": COV_MIN,
            "missing_indicator_window": [MISS_LO, MISS_HI],
            "standardisation": "TRAIN median / robust scale (IQR else 1.4826*MAD else 1)",
        },
        "source_pins": {
            "data_root": str(data_root),
            "events_root": str(events_root),
            "stage_root": str(stage_root),
            "split_sha256": sha256_file(ls.v2_dir(data_root) / "split.json"),
            "event_manifest": manifest_pin,
            "event_day_markers": {"n": len(markers), "combined_pin_sha256": marker_pin},
            "env": env_pin,
        },
        "folds": [
            {**w, "train_days": len(w["train_days"]), "test_days": len(w["test_days"])}
            for w in windows
        ],
        "scaler_audit": scaler_audit,
        "staging": {
            "root": str(stage_root),
            "schema_key": schema_key,
            "days": len(stage_audit),
            "staged_days": int(sum(1 for a in stage_audit.values() if a["staged"])),
            "built_days": int(sum(1 for a in stage_audit.values() if not a["staged"])),
            "anchor_rows": int(n_anc),
            "note": "per-day anchor cache with source-day pins; memory bounded to one day",
        },
        "support": support,
        "metrics": metrics,
        "personality_contrast_same_kind_vs_pooled": personality_contrast,
        "representative_matches": reps[:24],
        "leakage_attestation": leakage,
        "claim_boundary": (
            "Held-out scalar horizon means and the paper fresh-cost contrast "
            "are NOT portfolio P&L and NOT a fill model; the "
            "per-causal-mark-dollar fresh proxy ignores price gaps and "
            "queue/cash constraints, so no tradable-profit claim is made from "
            "any scalar horizon mean."
        ),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.smoke_days:
        out_path = out_path.with_name(out_path.stem + ".smoke" + out_path.suffix)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, default=str))
    tmp.replace(out_path)

    n_known15 = int(np.isfinite(tbl["y15"]).sum())
    print(
        f"[owned_claim_retrieval] evidence={evidence} anchors={n_anc} queries={tbl['mode'].size} "
        f"known_h15={n_known15} folds={fold_ids} -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
