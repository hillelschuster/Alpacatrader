#!/usr/bin/env python
"""Shared helpers for the F2/F12 analysis passes (surfaces / gates / predictability).

polars + numpy only.  No sklearn / scipy.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F2_golden"
PANEL_PATH = OUT / "state_panel.parquet"

CHECKPOINTS = (580, 585, 590, 600, 615, 630, 645, 660, 690, 720)
# Frozen dual-block split (PRE-REG-BASKET-02 §5).
BLOCK1 = ("2021-02", "2023-12")
BLOCK2 = ("2025-02", "2026-05")
COHORTS = [("ge100", 1.00, np.inf), ("50_100", 0.50, 1.00), ("30_50", 0.30, 0.50),
           ("10_30", 0.10, 0.30), ("lt10", -np.inf, 0.10)]


def load_panel() -> pl.DataFrame:
    if not PANEL_PATH.exists():
        raise SystemExit(f"missing panel {PANEL_PATH}; run basket_f2_panel.py first")
    return pl.read_parquet(PANEL_PATH)


def block_of(date: str) -> int:
    m = date[:7]
    if BLOCK1[0] <= m <= BLOCK1[1]:
        return 1
    if BLOCK2[0] <= m <= BLOCK2[1]:
        return 2
    return 0  # outside dev (should not happen)


def cohort_of(rem_mfe: float) -> str | None:
    if rem_mfe != rem_mfe:
        return None
    for name, lo, hi in COHORTS:
        if lo <= rem_mfe < hi:
            return name
    return None


def auc(y: np.ndarray, score: np.ndarray) -> float:
    """Rank (Mann-Whitney) AUC with average ranks for ties."""
    y = np.asarray(y, dtype=np.float64)
    s = np.asarray(score, dtype=np.float64)
    ok = np.isfinite(s) & np.isfinite(y)
    y, s = y[ok], s[ok]
    npos = float(y.sum())
    nneg = float(len(y) - npos)
    if npos == 0 or nneg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=np.float64)
    ss = s[order]
    i = 0
    r = np.arange(1, len(s) + 1, dtype=np.float64)
    while i < len(s):
        j = i
        while j + 1 < len(s) and ss[j + 1] == ss[i]:
            j += 1
        avg = r[i:j + 1].mean()
        ranks[order[i:j + 1]] = avg
        i = j + 1
    return (ranks[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)


def bucketize(x: np.ndarray, nq: int = 5) -> list[tuple[str, float, float]]:
    """Return (label, lo, hi) edges.  Discrete features (<=8 unique) use values."""
    xx = x[np.isfinite(x)]
    if len(xx) == 0:
        return []
    uniq = np.unique(xx)
    if len(uniq) <= 8:
        edges = list(uniq)
        out = []
        for i, v in enumerate(edges):
            out.append((f"={_fmt(v)}", float(v), float(v) if i == len(edges) - 1 else float(edges[i + 1])))
        return out
    qs = np.quantile(xx, np.linspace(0, 1, nq + 1))
    out = []
    for i in range(nq):
        lo, hi = float(qs[i]), float(qs[i + 1])
        out.append((f"q{i+1}[{_fmt(lo)},{_fmt(hi)}]", lo, hi))
    return out


def _fmt(v: float) -> str:
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


def in_bucket(x: np.ndarray, lo: float, hi: float, last: bool) -> np.ndarray:
    m = (x >= lo)
    m &= (x <= hi) if last else (x < hi)
    return m & np.isfinite(x)


def stats(y_t50: np.ndarray, y_t30: np.ndarray, y_t100: np.ndarray,
          rem_mfe: np.ndarray, rem_mae: np.ndarray, nhba: np.ndarray) -> dict:
    n = len(y_t50)
    if n == 0:
        return {"n": 0}
    return {
        "n": int(n),
        "p_touch30": float(np.nanmean(y_t30)),
        "p_touch50": float(np.nanmean(y_t50)),
        "p_touch100": float(np.nanmean(y_t100)),
        "e_rem_mfe": float(np.nanmean(rem_mfe)),
        "e_rem_mae": float(np.nanmean(rem_mae)),
        "p_nhba": float(np.nanmean(nhba)),
    }


# --------------------------------------------------------------------------- #
# hand-rolled logistic regression (numpy only)
# --------------------------------------------------------------------------- #
def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 lr: float = 0.5, iters: int = 4000, tol: float = 1e-9) -> np.ndarray:
    """Batch gradient descent on mean log-loss + L2 (intercept unpenalised).

    ``X`` must already include a leading 1-column and be standardised.
    """
    n, p = X.shape
    w = np.zeros(p, dtype=np.float64)
    prev = np.inf
    for _ in range(iters):
        z = X @ w
        pr = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = X.T @ (pr - y) / n
        reg = l2 * np.r_[0.0, w[1:]] / n
        w -= lr * (g + reg)
        ll = -np.mean(y * np.log(pr + 1e-12) + (1 - y) * np.log(1 - pr + 1e-12))
        if abs(prev - ll) < tol:
            break
        prev = ll
    return w


def predict_proba(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(X @ w, -30, 30)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def calibration_deciles(y: np.ndarray, p: np.ndarray, nq: int = 10) -> list[dict]:
    order = np.argsort(p)
    y, p = y[order], p[order]
    out = []
    for i in range(nq):
        lo = int(round(i * len(p) / nq))
        hi = int(round((i + 1) * len(p) / nq))
        if hi <= lo:
            continue
        out.append({"bin": i + 1, "n": hi - lo, "p_mean": float(p[lo:hi].mean()),
                    "y_rate": float(y[lo:hi].mean())})
    return out


def standardize(train: np.ndarray, other: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = np.nanmean(train, axis=0)
    sd = np.nanstd(train, axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    return (train - mu) / sd, (other - mu) / sd


def impute(arr: np.ndarray, fill: np.ndarray) -> np.ndarray:
    out = np.array(arr, dtype=np.float64)
    bad = ~np.isfinite(out)
    if bad.any():
        idx = np.where(bad)
        out[idx] = np.take(fill, idx[1])
    return out


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    tmp.replace(path)
