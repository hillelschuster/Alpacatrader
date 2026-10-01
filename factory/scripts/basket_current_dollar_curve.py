#!/usr/bin/env python
"""Experiment A + B: current-dollar continuation / giveback-reentry curves (v0).

Owner: ContinuationAnatomy.  Consumes the shared base written by
``basket_current_dollar_base.py`` (states/executions/members) and the frozen contract
``factory/artifacts/basket/phase2/ATLAS/CV01/contract.json``.  Reads no panel and no
reserved/sealed day: every priced quantity comes from the base files.

A (continuation anatomy).  For every eligible completed decision state the immediate
liquidation is ``W_exit = P_exit*(1-s)`` at the open of the next observed print
(``next_et``); for every later *observed* bar open ``P_j`` up to forced flat the
continuation is ``CV_j = P_j/P_exit - 1`` (the common sell fee cancels).  The immediate
endpoint is CV==0.  Horizon is reported both as elapsed ET minutes from ``decision_et``
and from the immediate exit ``et``; gaps stay missing on the lattice (never imputed).

B (paired ownership).  Per member the anchor is the first causal giveback-10 exit (scan
from bar_index 1, forced-flat precedence at et >= session_end-1, trigger
``close <= running_high*0.9 + 1e-9*running_high``).  Retain keeps q shares; sell-reenter
liquidates at that exit and buys back at the next observed open strictly after the first
completed bar (label >= actual exit et) whose close >= the GROSS exit price.  One sale,
at most one re-entry.  Both branches are published per horizon; no-event members are
counted in the whole census and have identical branches (difference 0).

Pins (declared before any CV number was inspected; contract permits rough descriptive bins):
  * clock baseline bucket  = floor((decision_et - 570) / 30)      (30 ET-minute blocks)
  * tenure baseline bucket = floor(bar_index_at_decision / 15)    (15-minute blocks)
  * one state coordinate   = dist_from_running_high with edges
                             [-inf,-0.20,-0.10,-0.05,-0.02,0.02,+inf]  (6 coarse bins)
  * quantile subsample     = every 16th eligible decision state within a member
                             (deterministic; rate published with every estimate)
No ML, no policy/stop/size optimization, no EV claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

S = 0.005
GIVEBACK = 0.10
GIVEBACK_TOL_REL = 1e-9
HMAX = 1000
QLEVELS = [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
QCOLS = ["p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99"]

CLOCK_BUCKET = 30
CLOCK_ANCHOR = 570
CLOCK_NBINS = 24
TENURE_BUCKET = 15
TENURE_NBINS = 32
DISTRH_EDGES = np.array([-np.inf, -0.20, -0.10, -0.05, -0.02, 0.02, np.inf])
DISTRH_LABELS = ["<=-20%", "-20..-10%", "-10..-5%", "-5..-2%", "-2..2%", ">=+2%"]
# additional single-coordinate diagnostic rulers (bounds declared BEFORE any CV was computed)
RETF_EDGES = np.array([-np.inf, 0.0, 0.10, 0.30, 1.0, np.inf])
RETF_LABELS = ["<=0", "0..10%", "10..30%", "30..100%", ">=100%"]
VOL_EDGES = np.array([-np.inf, -0.5, 0.0, 1.0, np.inf])
VOL_LABELS = ["<=-0.5", "-0.5..0", "0..1", ">=1"]
PCT_EDGES = np.array([0.0, 0.25, 0.50, 0.75, 1.0])          # native 0..1 (verified in base)
PCT_LABELS = ["q1_0..25", "q2_25..50", "q3_50..75", "q4_75..100"]
UNKNOWN_LABEL = "UNKNOWN(missing)"


def bin_unknown(vals, edges, nbins):
    """Discretise a causal coordinate; non-finite values go to an explicit UNKNOWN bucket."""
    b = np.digitize(vals, edges[1:-1])
    return np.where(np.isfinite(vals), b, nbins)


SUBSAMPLE_RATE = 16



def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def js(x):
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def log(msg):
    print(msg, flush=True)


# ----------------------------------------------------------------- pure per-member math (A)
def a_pairs(E, P, Xpos, Xet, dec_et):
    """(h_from_liquidation, h_from_decision, CV) for one member's eligible decisions."""
    if Xpos.size == 0 or E.size == 0:
        z = np.empty(0, np.int64)
        return z, z, np.empty(0, np.float64)
    E = np.asarray(E, dtype=np.int64)
    Xet = np.asarray(Xet, dtype=np.int64)
    dec_et = np.asarray(dec_et, dtype=np.int64)
    P_exit = P[Xpos]
    R = P[None, :] / P_exit[:, None] - 1.0          # (D, n)  ; fee cancels
    hl = E[None, :] - Xet[:, None]
    hd = E[None, :] - dec_et[:, None]
    m = hl >= 0
    return hl[m], hd[m], R[m]


def b_branches(E, P, exit_pos, P_exit, reentry_pos, P_reentry):
    """(h_from_liquidation, retain_ratio, sell_ratio) for endpoints >= the sale bar."""
    j = np.arange(exit_pos, E.size)
    h = E[j] - E[exit_pos]
    retain = P[j] / P_exit
    if reentry_pos is None:
        sell = np.ones_like(retain)
    else:
        sell = np.where(E[j] < E[reentry_pos], 1.0,
                        ((1.0 - S) / (1.0 + S)) * (P[j] / P_reentry))
    return h, retain, sell


def find_giveback(bar_index, et, close, rh, session_end):
    ok = ((bar_index >= 1) & np.isfinite(close) & np.isfinite(rh)
          & (close <= rh * (1.0 - GIVEBACK) + GIVEBACK_TOL_REL * rh))
    hits = np.flatnonzero(ok)
    if hits.size == 0:
        return None
    t = int(hits[0])
    if int(et[t]) >= int(session_end) - 1:
        return None
    return t


def find_reentry(sE, sClose, session_end, exit_et, P_exit):
    """(buy_et, reclaim_et) of the re-entry buy bar, or None."""
    cand = (sE >= exit_et) & np.isfinite(sClose) & (sClose >= P_exit)
    hits = np.flatnonzero(cand)
    if hits.size == 0:
        return None
    r = int(hits[0])
    if int(sE[r]) >= int(session_end) - 1:
        return None
    if r + 1 >= sE.size:
        return None
    buy_et = int(sE[r + 1])
    if buy_et >= int(session_end):
        return None
    return buy_et, int(sE[r])


# ----------------------------------------------------------------------------- aggregation
class Cell:
    """Occupancy + equal-member statistics for a (bin, horizon) table."""

    def __init__(self, nbins, hmax=HMAX):
        self.nbins, self.H = nbins, hmax
        z = lambda *s: np.zeros(s)
        self.occ_sum = z(nbins, hmax)
        self.occ_cnt = np.zeros((nbins, hmax), np.int64)
        self.eq_sum = z(nbins, hmax)
        self.eq_cnt = np.zeros((nbins, hmax), np.int64)
        self.pos_sum = z(nbins, hmax)
        self.neg_sum = z(nbins, hmax)
        self.n_pos = np.zeros((nbins, hmax), np.int64)
        self.n_neg = np.zeros((nbins, hmax), np.int64)
        self.mn = np.full((nbins, hmax), np.inf)
        self.mx = np.full((nbins, hmax), -np.inf)
        self.flags = None  # optional extra bool accumulator

    def add_member(self, b_idx, hl, cv, extremes=True):
        """Occupancy-weighted accumulation only. Equal-member is added per (day,family) unit."""
        if hl.size == 0:
            return
        ns = self.nbins * self.H
        flat = b_idx * self.H + hl
        np.clip(flat, 0, ns - 1, out=flat)
        c = np.bincount(flat, minlength=ns).reshape(self.nbins, self.H)
        s = np.bincount(flat, weights=cv, minlength=ns).reshape(self.nbins, self.H)
        self.occ_cnt += c
        self.occ_sum += s
        self.pos_sum += np.bincount(flat, weights=np.where(cv > 0, cv, 0.0),
                                    minlength=ns).reshape(self.nbins, self.H)
        self.neg_sum += np.bincount(flat, weights=np.where(cv < 0, cv, 0.0),
                                    minlength=ns).reshape(self.nbins, self.H)
        self.n_pos += np.bincount(flat[cv > 0], minlength=ns).reshape(self.nbins, self.H)
        self.n_neg += np.bincount(flat[cv < 0], minlength=ns).reshape(self.nbins, self.H)
        if extremes:
            order = np.argsort(flat, kind="stable")
            fs, vs = flat[order], cv[order]
            starts = np.flatnonzero(np.r_[True, fs[1:] != fs[:-1]])
            r0, r1 = fs[starts] // self.H, fs[starts] % self.H
            mns = np.minimum.reduceat(vs, starts)
            mxs = np.maximum.reduceat(vs, starts)
            np.minimum.at(self.mn, (r0, r1), mns)
            np.maximum.at(self.mx, (r0, r1), mxs)

    def rows(self, row_keys, h_shift=0, want_minmax=True):
        out = []
        for i, key in enumerate(row_keys):
            for h in range(self.H):
                n = int(self.occ_cnt[i, h])
                if n == 0:
                    continue
                r = {**key, "h": h + h_shift, "n_pairs": n,
                     "n_member_states": int(self.eq_cnt[i, h]),
                     "mean_occ": self.occ_sum[i, h] / n,
                     "mean_member": (self.eq_sum[i, h] / self.eq_cnt[i, h])
                     if self.eq_cnt[i, h] else None,
                     "pos_sum": self.pos_sum[i, h], "neg_sum": self.neg_sum[i, h],
                     "n_pos": int(self.n_pos[i, h]), "n_neg": int(self.n_neg[i, h])}
                if want_minmax and np.isfinite(self.mn[i, h]):
                    r["cv_min"] = self.mn[i, h]
                    r["cv_max"] = self.mx[i, h]
                out.append(r)
        return out


class EqTmp:
    """Accumulates one (day,family) unit's member means, then folds the unit mean into a Cell.

    Equal-member-day/family path: mean the supported outcomes within a member, average members
    within the unit, then average units. Days with no supporting member are left UNKNOWN (never 0).
    """

    def __init__(self, nbins):
        self.nbins = nbins
        self.s = np.zeros((nbins, HMAX))
        self.c = np.zeros((nbins, HMAX), np.int64)

    def add_member(self, b_idx, hl, cv):
        if hl.size == 0:
            return
        ns = self.nbins * HMAX
        flat = np.asarray(b_idx, dtype=np.int64) * HMAX + hl
        c = np.bincount(flat, minlength=ns).reshape(self.nbins, HMAX)
        s = np.bincount(flat, weights=cv, minlength=ns).reshape(self.nbins, HMAX)
        m = c > 0
        if m.any():
            self.s[m] += s[m] / c[m]
            self.c[m] += 1

    def close_unit(self):
        """Return (unit_mean, supported_mask) then reset."""
        m = self.c > 0
        mean = np.zeros_like(self.s)
        np.divide(self.s, self.c, out=mean, where=m)
        self.s[:] = 0.0
        self.c[:] = 0
        return mean, m

    def flush(self, cell):
        mean, m = self.close_unit()
        if m.any():
            cell.eq_sum[m] += mean[m]
            cell.eq_cnt[m] += 1
        return mean, m


def balanced_mean(unit_mean, unit_mask):
    """Mean over units with support per horizon (days/units with no support stay unknown)."""
    H = unit_mean.shape[1]
    out = np.full(H, np.nan)
    cnt = unit_mask.sum(axis=0)
    tot = np.where(unit_mask, unit_mean, 0.0).sum(axis=0)
    ok = cnt > 0
    out[ok] = tot[ok] / cnt[ok]
    return out, cnt


def balanced_se(unit_mean, unit_mask):
    """Unit-clustered SE of the balanced mean (unweighted over supporting units)."""
    H = unit_mean.shape[1]
    se = np.full(H, np.nan)
    for h in range(H):
        v = unit_mean[unit_mask[:, h], h]
        if v.size > 1:
            se[h] = v.std(ddof=1) / math.sqrt(v.size)
    return se


class QStore:
    """Quantile value store: batched by key, coalesced into few large chunks (bounded overhead)."""
    def __init__(self, batch=8_000_000):
        self.batch = batch
        self._k, self._v, self._n = [], [], 0
        self.store = {}

    def add(self, keys, vals):
        if keys.size == 0:
            return
        self._k.append(np.asarray(keys, dtype=np.int64))
        self._v.append(np.asarray(vals, dtype=np.float32))
        self._n += keys.size
        if self._n >= self.batch:
            self.flush()

    def flush(self):
        if not self._k:
            return
        k = np.concatenate(self._k)
        v = np.concatenate(self._v)
        self._k, self._v, self._n = [], [], 0
        o = np.argsort(k, kind="stable")
        k, v = k[o], v[o]
        b = np.flatnonzero(np.diff(k)) + 1
        for sk, sv in zip(np.split(k, b), np.split(v, b)):
            self.store.setdefault(int(sk[0]), []).append(sv)

    def qcols(self, key, prefix, levels=QLEVELS, cols=QCOLS):
        self.flush()
        return qcols_from(self.store.get(key), prefix, levels, cols)


def qcols_from(parts, prefix, levels=QLEVELS, cols=QCOLS):
    if not parts:
        return {}
    v = np.concatenate(parts).astype(np.float64)
    qv = np.quantile(v, levels)
    out = {f"{prefix}_{c}": float(x) for c, x in zip(cols, qv)}
    out[f"{prefix}_n"] = int(v.size)
    return out


def write_csv(path, rows, cols):
    if not rows:
        Path(path).write_text(",".join(cols) + "\n")
        return
    df = pl.DataFrame([{c: r.get(c) for c in cols} for r in rows])
    df.write_csv(path)


def clustered_se(sumarr, cntarr):
    """Per-h mean of cluster means and its cluster SE (unweighted over clusters)."""
    m = np.full(HMAX, np.nan)
    se = np.full(HMAX, np.nan)
    for h in range(HMAX):
        c = cntarr[:, h]
        if c.sum() == 0:
            continue
        means = sumarr[c > 0, h] / c[c > 0]
        m[h] = means.mean()
        se[h] = means.std(ddof=1) / math.sqrt(means.size) if means.size > 1 else np.nan
    return m, se


def svg_curve(path, series, title, w=780, h=380, pad=52):
    pts_all = [(x, y) for _, s in series for x, y in s if y is not None]
    if not pts_all:
        Path(path).write_text(f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}'/>")
        return
    xs = [p[0] for p in pts_all]
    ys = [p[1] for p in pts_all]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(min(ys), 0.0), max(max(ys), 0.0)
    if y1 - y0 < 1e-12:
        y1 = y0 + 1e-3

    def sx(x):
        return pad + (x - x0) / max(1, x1 - x0) * (w - 2 * pad)

    def sy(y):
        return h - pad - (y - y0) / (y1 - y0) * (h - 2 * pad)

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}'>",
             f"<rect width='{w}' height='{h}' fill='white'/>",
             f"<text x='{pad}' y='22' font-size='14' font-family='monospace'>{title}</text>",
             f"<line x1='{pad}' y1='{sy(0):.1f}' x2='{w-pad}' y2='{sy(0):.1f}' stroke='#999'/>",
             f"<text x='{pad-30}' y='{sy(0):.1f}' font-size='10'>0</text>"]
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]
    for i, (name, s) in enumerate(series):
        d = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in s if y is not None)
        parts.append(f"<polyline fill='none' stroke='{colors[i % len(colors)]}' stroke-width='2' points='{d}'/>")
        parts.append(f"<text x='{w-230}' y='{34+i*16}' font-size='12' fill='{colors[i % len(colors)]}'>{name}</text>")
    parts.append("</svg>")
    Path(path).write_text("\n".join(parts))


# ----------------------------------------------------------------------------- smoke tests
def smoke() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        ok = ok and bool(cond)

    E = np.array([571, 572, 573]); P = np.array([10.0, 10.0, 12.0])
    hl, hd, cv = a_pairs(E, P, np.array([1]), np.array([572]), np.array([572]))
    check("A flat endpoint CV==0", cv[0] == 0.0, f"cv={cv[0]}")
    check("A h-from-liq lattice", list(hl) == [0, 1], f"hl={list(hl)}")
    check("A h-from-decision lattice", list(hd) == [0, 1], f"hd={list(hd)}")

    E = np.array([572, 573])
    hl, hd, cv = a_pairs(E, np.array([10.0, 10.1]), np.array([0]), np.array([572]), np.array([572]))
    check("A fee cancels (+1%)", np.isclose(cv[1], 0.01, atol=1e-15), f"cv={cv[1]}")
    hl, hd, cv = a_pairs(E, np.array([10.0, 9.5]), np.array([0]), np.array([572]), np.array([572]))
    check("A fee cancels (-5%)", np.isclose(cv[1], -0.05, atol=1e-15), f"cv={cv[1]}")
    # and it is NOT the double-fee value
    check("A no doubled friction", not np.isclose(cv[1], -0.05 * (1 - 2 * S) / (1 - 2 * S + 2 * S), atol=1e-6)
          and not np.isclose(cv[1], -0.06, atol=1e-6))

    E = np.array([572, 575])
    hl, hd, cv = a_pairs(E, np.array([10.0, 11.0]), np.array([0]), np.array([572]), np.array([571]))
    check("A gap real elapsed h(liq)=3", int(hl[1]) == 3, f"hl={list(hl)}")
    check("A gap real elapsed h(dec)=4", int(hd[1]) == 4, f"hd={list(hd)}")

    hl, hd, cv = a_pairs(np.array([572]), np.array([10.0]), np.empty(0, int), np.empty(0, int), np.empty(0, int))
    check("A missing immediate -> no pairs", hl.size == 0)

    E0 = np.array([572, 573, 574]); P0 = np.array([10.0, 10.5, 11.0])
    h0, _, c0 = a_pairs(E0, P0, np.array([0]), np.array([572]), np.array([572]))
    h1, _, c1 = a_pairs(np.append(E0, 575), np.append(P0, 9.0), np.array([0]), np.array([572]), np.array([572]))
    check("A past-state invariant under future append",
          np.array_equal(c0, c1[:c0.size]) and np.array_equal(h0, h1[:h0.size]))

    E = np.array([572, 573, 574, 575]); P = np.array([10.0, 9.0, 10.0, 11.0])
    h, ret, sell = b_branches(E, P, 0, 10.0, 2, 10.0)
    check("B pre-reentry cash ratio 1", sell[0] == 1.0 and sell[1] == 1.0)
    check("B buy-bar friction (1-s)/(1+s)", np.isclose(sell[2], (1 - S) / (1 + S), atol=1e-15),
          f"sell[2]={sell[2]:.8f}")
    check("B retain ratio = price ratio", np.isclose(ret[3], 1.1))
    check("B post-buy scales with price", np.isclose(sell[3], (1 - S) / (1 + S) * 1.1))
    h, ret, sell = b_branches(E, P, 0, 10.0, None, None)
    check("B never-reentered -> flat cash", np.allclose(sell, 1.0))

    bi = np.array([0, 1, 2]); close = np.array([10.0, 8.0, 8.0]); rh = np.array([10.0, 10.0, 10.0])
    check("B trigger at et>=session_end-1 cannot fire",
          find_giveback(bi, np.array([958, 959, 960]), close, rh, 960) is None)
    check("B trigger before flat fires",
          find_giveback(bi, np.array([955, 956, 957]), close, rh, 960) == 1)

    sE = np.array([572, 573, 574, 575])
    check("B reclaim 574 -> buy 575",
          find_reentry(sE, np.array([9.9, 9.0, 10.2, 10.3]), 959, 572, 10.0) == (575, 574))
    check("B no buy at/after session_end",
          find_reentry(sE, np.array([9.9, 9.0, 10.2, 10.3]), 575, 572, 10.0) is None)
    # GROSS comparison at the sale bar: close==P_exit reclaims there, buy the next open
    check("B sale-bar close==exit reclaims -> buy next open",
          find_reentry(sE, np.array([10.0, 9.0, 9.0, 9.0]), 959, 572, 10.0) == (573, 572))
    check("B close just below GROSS does not reclaim",
          find_reentry(sE, np.array([9.999, 9.0, 10.0, 9.0]), 959, 572, 10.0) == (575, 574))

    # EQ: day/family unit balancing (Main's toy: day1 one member=2, day2 three members=0)
    t = EqTmp(1)
    t.add_member(np.array([0]), np.array([0]), np.array([2.0]))
    um1, m1 = t.close_unit()
    for _ in range(3):
        t.add_member(np.array([0]), np.array([0]), np.array([0.0]))
    um2, m2 = t.close_unit()
    bm, _ = balanced_mean(np.vstack([um1, um2]), np.vstack([m1, m2]))
    check("EQ day/family-balanced=1 (not global member=0.5)",
          np.isclose(bm[0], 1.0) and np.isclose((2 + 0 + 0 + 0) / 4, 0.5), f"balanced={bm[0]}")
    t = EqTmp(1)
    t.add_member(np.array([0, 0]), np.array([0, 1]), np.array([2.0, 0.0]))
    u, _ = t.close_unit()
    check("EQ within-member mean over supported h", np.isclose(u[0, 0], 2.0) and np.isclose(u[0, 1], 0.0))
    bm2, _ = balanced_mean(np.array([[np.nan, 0.0]]), np.array([[False, True]]))
    check("EQ empty-support stays unknown (not 0)", np.isnan(bm2[0]) and np.isclose(bm2[1], 0.0))

    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ----------------------------------------------------------------------------- main
def build_index(meta):
    """Map each member to a compact integer id; returns numpy views of meta fields."""
    cols = list(meta.columns)
    out = {}
    for c in ("midx", "family", "block", "month", "sleeve_day", "entry_et", "session_end",
              "terminal_censored"):
        out[c] = meta[c].to_numpy() if c in cols else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data/atlas/continuation/cv01/base")
    ap.add_argument("--contract", default="factory/artifacts/basket/phase2/ATLAS/CV01/contract.json")
    ap.add_argument("--out-root", default="data/atlas/continuation/cv01")
    ap.add_argument("--evidence", default="factory/artifacts/basket/phase2/ATLAS/CV01")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)
    if args.smoke:
        return smoke()

    base = Path(args.base)
    for fn in ("states.parquet", "executions.parquet", "members.parquet"):
        if not (base / fn).exists():
            log(f"BLOCKED: base not ready: {base / fn}")
            return 2
    contract = json.loads(Path(args.contract).read_text())
    outA = Path(args.out_root) / "A"
    evA = Path(args.evidence) / "A"
    for d in (outA, evA):
        d.mkdir(parents=True, exist_ok=True)

    log("loading base ...")

    def load(p, wanted):
        lf = pl.scan_parquet(p)
        names = lf.collect_schema().names()
        cols = [c for c in wanted if c in names]
        return lf.select(cols).collect(), set(names)

    states, s_names = load(base / "states.parquet",
                           ["member_id", "et", "bar_index", "decision_et", "clock_eligible",
                            "decision_eligible", "bar_close", "running_high",
                            "dist_from_running_high", "ret_from_fill", "volume_accel",
                            "ret_percentile_candidates"])
    ex, x_names = load(base / "executions.parquet",
                       ["member_id", "et", "bar_index", "bar_open", "next_open", "next_et",
                        "has_next_open", "next_et_ge_session_end"])
    mb, _ = load(base / "members.parquet",
                 ["member_id", "sleeve_day", "family", "block", "month", "entry_et",
                  "session_end", "terminal_censored", "session_peak_ret_from_entry"])
    mb = mb.with_row_index("midx")

    meta = mb
    M = build_index(meta)
    nmem = meta.height
    fam = M["family"] if M["family"] is not None else np.array(["?"] * nmem)
    blk = M["block"] if M["block"] is not None else np.array(["?"] * nmem)
    mon = M["month"] if M["month"] is not None else np.array(["?"] * nmem)
    day = M["sleeve_day"] if M["sleeve_day"] is not None else np.array(["?"] * nmem)
    session_end = M["session_end"] if M["session_end"] is not None else np.full(nmem, 959)
    censored = (M["terminal_censored"] if M["terminal_censored"] is not None
                else np.zeros(nmem, bool)).astype(bool)

    # AUDIT-ONLY future labels (never used as a state, filter or selection rule)
    PEAK_LABELS = ["future_peak<0", "future_peak_0..30", "future_peak_30..100",
                   "future_peak>=100", "unlabelled"]
    peak_ret = (meta["session_peak_ret_from_entry"].fill_null(float("nan")).to_numpy()
                if "session_peak_ret_from_entry" in meta.columns else np.full(nmem, np.nan))

    def peak_class(v):
        if not np.isfinite(v):
            return 4
        return 0 if v < 0 else (1 if v < 0.30 else (2 if v < 1.0 else 3))

    peak_idx = np.array([peak_class(v) for v in peak_ret], np.int64)

    ex = ex.join(meta.select("member_id", "midx"), on="member_id", how="left")

    # ---- path arrays (observed prints only)
    exp = ex.sort(["midx", "et"])
    E_all = exp["et"].to_numpy()
    P_all = exp["bar_open"].to_numpy()
    exmidx = exp["midx"].to_numpy()
    pcount = np.bincount(exmidx, minlength=nmem)
    poff = np.zeros(nmem + 1, np.int64); poff[1:] = np.cumsum(pcount)

    # ---- decisions: eligible states joined to their immediate exit
    nf_col = "next_et_ge_session_end"
    if nf_col not in ex.columns:
        raise SystemExit("BLOCKED: executions.parquet lacks 'next_et_ge_session_end' "
                         "(required by the frozen base; refusing to silently default)")
    join_cols = ["member_id", "midx", "et", "next_open", "next_et"] + (
        ["has_next_open"] if "has_next_open" in ex.columns else []) + [nf_col]
    dec = states.filter(pl.col("decision_eligible")).select(
        [c for c in ("member_id", "et", "bar_index", "decision_et", "dist_from_running_high",
                     "ret_from_fill", "volume_accel", "ret_percentile_candidates")
         if c in states.columns]).join(
        ex.select(join_cols), on=["member_id", "et"], how="left")
    if "has_next_open" not in dec.columns:
        dec = dec.with_columns(pl.col("next_open").is_not_null().alias("has_next_open"))
    dec = dec.with_columns(pl.col(nf_col).fill_null(False).alias("_weld"))
    dec = dec.sort(["midx", "et"])
    Dmx = dec["midx"].to_numpy()
    dbi = dec["bar_index"].to_numpy()
    ddec = dec["decision_et"].to_numpy()
    dnext_et = dec["next_et"].to_numpy()
    dnext_open = dec["next_open"].to_numpy()
    dhas = dec["has_next_open"].to_numpy().astype(bool)
    dweld = dec["_weld"].to_numpy().astype(bool)
    ddist = (dec["dist_from_running_high"].to_numpy()
             if "dist_from_running_high" in dec.columns else np.full(dec.height, np.nan))
    dretf = (dec["ret_from_fill"].to_numpy()
             if "ret_from_fill" in dec.columns else np.full(dec.height, np.nan))
    dvol = (dec["volume_accel"].to_numpy()
            if "volume_accel" in dec.columns else np.full(dec.height, np.nan))
    dpct = (dec["ret_percentile_candidates"].to_numpy()
            if "ret_percentile_candidates" in dec.columns else np.full(dec.height, np.nan))
    dcount = np.bincount(Dmx, minlength=nmem)
    doff = np.zeros(nmem + 1, np.int64); doff[1:] = np.cumsum(dcount)

    n_states_rows = int(states.height)
    n_dec_rows = int(dec.height)
    n_clock_elig = int(states["clock_eligible"].sum()) if "clock_eligible" in states.columns else n_dec_rows
    del states, ex, exp, dec                   # free parquet string columns before the pass

    counts = {
        "members": int(nmem), "members_censored": int(censored.sum()),
        "states_rows": n_states_rows,
        "states_clock_eligible": n_clock_elig,
        "states_decision_eligible": n_dec_rows,
        "decisions_next_et_ge_session_end": int(dweld.sum()),
        "decisions_strict_voluntary": int((~dweld).sum()),
        "weld_note": ("pre-flat decisions whose next print is the session_end bar are valid sales at "
                      "the session_end open; they are counted, not excluded or zeroed; only a re-buy "
                      "at/after session_end is forbidden (0 clock-eligible rows there)"),
        "decisions_missing_immediate": int((~dhas).sum()),
        "decisions_with_immediate": int(dhas.sum()),
        "months": sorted(set(mon.tolist())), "families": sorted(set(fam.tolist())),
        "pins": {"clock_bucket_minutes": CLOCK_BUCKET, "clock_anchor_et": CLOCK_ANCHOR,
                 "tenure_bucket_bars": TENURE_BUCKET,
                 "state_coordinate": "dist_from_running_high",
                 "state_edges": [js(x) for x in DISTRH_EDGES], "state_labels": DISTRH_LABELS,
                 "quantile_subsample_rate": SUBSAMPLE_RATE, "quantile_levels": QLEVELS,
                 "ret_from_fill_edges": [js(x) for x in RETF_EDGES],
                 "ret_from_fill_labels": RETF_LABELS + [UNKNOWN_LABEL],
                 "volume_accel_edges": [js(x) for x in VOL_EDGES],
                 "volume_accel_labels": VOL_LABELS + [UNKNOWN_LABEL],
                 "ret_percentile_candidates_native_range": [0.0, 1.0],
                 "ret_percentile_candidates_edges": [js(x) for x in PCT_EDGES],
                 "ret_percentile_candidates_labels": PCT_LABELS + [UNKNOWN_LABEL],
                 "ruler_note": "single-coordinate descriptive rulers declared before any CV was "
                               "computed; diagnostic bins, not a threshold policy; missing values are "
                               "an explicit UNKNOWN bucket, never 0"},
        "scaling": "q=1 by linear scaling; all CV dimensionless (fee cancels)"}
    log(json.dumps({k: v for k, v in counts.items() if k != "months"}))

    # ---- family/block key index (specific combos; family-overall and overall built separately)
    fam_list = sorted(set(fam.tolist()))
    blk_list = sorted(set(blk.tolist()))
    fb_keys = []
    for f in fam_list:
        for b in blk_list:
            if np.any((fam == f) & (blk == b)):
                fb_keys.append({"family": f, "block": b})
    fb_index = np.full(nmem, 0, np.int64)
    for i in range(nmem):
        for k, row in enumerate(fb_keys):
            if row["family"] == fam[i] and row["block"] == blk[i]:
                fb_index[i] = k
                break
    fam_index = np.array([fam_list.index(f) for f in fam])
    blk_index = np.array([blk_list.index(b) for b in blk])
    mon_list = sorted(set(mon.tolist()))

    nbin_dist = len(DISTRH_LABELS)
    A_fb = Cell(len(fb_keys))
    A_fam = Cell(len(fam_list))
    A_all = Cell(1)
    A_clock = Cell(CLOCK_NBINS)
    A_tenure = Cell(TENURE_NBINS)
    A_dist = Cell(nbin_dist)
    A_retf = Cell(len(RETF_LABELS) + 1)
    A_vol = Cell(len(VOL_LABELS) + 1)
    A_pct = Cell(len(PCT_LABELS) + 1)
    A_block = Cell(len(blk_list))
    A_month = Cell(len(mon_list))
    A_peak = Cell(len(PEAK_LABELS))
    A_hdec = Cell(1)
    qA_fb, qA_dist = QStore(), QStore()

    day_ids = {d: i for i, d in enumerate(sorted(set(day.tolist())))}
    mon_ids = {m: i for i, m in enumerate(sorted(set(mon.tolist())))}
    nday, nmon = len(day_ids), len(mon_ids)
    # (day, family) units: equal-member is averaged within member, then within unit, then over units
    unit_key = list(zip(day.tolist(), fam.tolist()))
    unit_ids = {k: i for i, k in enumerate(sorted(set(unit_key)))}
    nunits = len(unit_ids)
    u_of = np.array([unit_ids[k] for k in unit_key], np.int64)
    u_members = [[] for _ in range(nunits)]
    for m in range(nmem):
        u_members[u_of[m]].append(m)
    u_day = np.zeros(nunits, np.int64)
    u_fam = np.array(["?"] * nunits, dtype=object)
    for k, i in unit_ids.items():
        u_day[i] = day_ids[k[0]]
        u_fam[i] = k[1]
    U_mean = np.zeros((nunits, HMAX)); U_mask = np.zeros((nunits, HMAX), bool)
    mem_contrib = np.zeros(nmem)
    mem_pairs = np.zeros(nmem, np.int64)
    # horizon-specific support companions (conserve per horizon; no zero-imputation)
    sup = {k: np.zeros(HMAX, np.int64) for k in
           ("atrisk", "priced", "missing", "unknown", "invalid",
            "atrisk_d", "priced_d", "missing_d", "unknown_d", "invalid_d", "prewait")}
    eqT = {name: EqTmp(c.nbins) for name, c in
           (("fb", A_fb), ("fam", A_fam), ("all", A_all), ("clock", A_clock),
            ("tenure", A_tenure), ("dist", A_dist), ("hdec", A_hdec), ("block", A_block),
            ("month", A_month), ("peak", A_peak), ("retf", A_retf), ("vol", A_vol),
            ("pct", A_pct))}

    log("A pass ...")
    for u in range(nunits):
        for m in u_members[u]:
            if pcount[m] == 0 or dcount[m] == 0:
                continue
            E = E_all[poff[m]:poff[m + 1]]
            P = P_all[poff[m]:poff[m + 1]]
            sl = slice(doff[m], doff[m + 1])
            good = dhas[sl] & np.isfinite(dnext_open[sl]) & np.isfinite(dnext_et[sl])
            if not good.any():
                continue
            Xet = dnext_et[sl][good].astype(np.int64)
            Xop = dnext_open[sl][good]
            dec_et = ddec[sl][good]
            dbi_m = dbi[sl][good]
            ddist_m = ddist[sl][good]
            dretf_m = dretf[sl][good]
            dvol_m = dvol[sl][good]
            dpct_m = dpct[sl][good]
            Xpos = np.searchsorted(E, Xet, side="left")
            ok_pos = Xpos < E.size
            if not ok_pos.all():
                Xet, Xop, dec_et, dbi_m, ddist_m, dretf_m, dvol_m, dpct_m, Xpos = (
                    a[ok_pos] for a in (Xet, Xop, dec_et, dbi_m, ddist_m, dretf_m, dvol_m,
                                        dpct_m, Xpos))
            if Xpos.size == 0:
                continue
            hl, hd, cv = a_pairs(E, P, Xpos, Xet, dec_et)
            if hl.size == 0:
                continue
            mem_contrib[m] = cv.sum()
            mem_pairs[m] = hl.size

            cnt_per_dec = E.size - Xpos
            rep = np.repeat(np.arange(Xet.size), cnt_per_dec)
            fbix = np.full(hl.size, fb_index[m], np.int64)
            famix = np.full(hl.size, fam_index[m], np.int64)
            allix = np.zeros(hl.size, np.int64)
            cb = np.clip((dec_et - CLOCK_ANCHOR) // CLOCK_BUCKET, 0, CLOCK_NBINS - 1)
            tb = np.clip(dbi_m // TENURE_BUCKET, 0, TENURE_NBINS - 1)
            cbix, tbix = cb[rep], tb[rep]
            blkix = np.full(hl.size, blk_index[m], np.int64)
            monix = np.full(hl.size, mon_ids[mon[m]], np.int64)
            peakix = np.full(hl.size, peak_idx[m], np.int64)
            db = np.digitize(ddist_m, DISTRH_EDGES[1:-1])
            db = np.where(np.isfinite(ddist_m), db, -1)[rep]
            okb = db >= 0

            A_fb.add_member(fbix, hl, cv)
            A_fam.add_member(famix, hl, cv)
            A_all.add_member(allix, hl, cv)
            A_hdec.add_member(allix, hd, cv)
            A_clock.add_member(cbix, hl, cv, extremes=False)
            A_tenure.add_member(tbix, hl, cv, extremes=False)
            A_block.add_member(blkix, hl, cv, extremes=False)
            A_month.add_member(monix, hl, cv, extremes=False)
            A_peak.add_member(peakix, hl, cv, extremes=False)
            if okb.any():
                A_dist.add_member(db[okb], hl[okb], cv[okb])
            b_retf = bin_unknown(dretf_m, RETF_EDGES, len(RETF_LABELS))[rep]
            b_vol = bin_unknown(dvol_m, VOL_EDGES, len(VOL_LABELS))[rep]
            b_pct = bin_unknown(dpct_m, PCT_EDGES, len(PCT_LABELS))[rep]
            A_retf.add_member(b_retf, hl, cv, extremes=False)
            A_vol.add_member(b_vol, hl, cv, extremes=False)
            A_pct.add_member(b_pct, hl, cv, extremes=False)

            eqT["fb"].add_member(fbix, hl, cv)
            eqT["fam"].add_member(famix, hl, cv)
            eqT["all"].add_member(allix, hl, cv)
            eqT["hdec"].add_member(np.zeros(hl.size, np.int64), hd, cv)
            eqT["clock"].add_member(cbix, hl, cv)
            eqT["tenure"].add_member(tbix, hl, cv)
            eqT["block"].add_member(blkix, hl, cv)
            eqT["month"].add_member(monix, hl, cv)
            eqT["peak"].add_member(peakix, hl, cv)
            if okb.any():
                eqT["dist"].add_member(db[okb], hl[okb], cv[okb])
            eqT["retf"].add_member(b_retf, hl, cv)
            eqT["vol"].add_member(b_vol, hl, cv)
            eqT["pct"].add_member(b_pct, hl, cv)

            # ---- horizon-specific support (conserve: atrisk = priced + missing + unknown + invalid)
            L = int(E[-1])
            present = np.zeros(960, bool)
            present[np.clip(E, 0, 959)] = True
            hmax = session_end[m] - Xet                 # within-session target horizon
            g = L - Xet                                 # observed-tape tail length
            wait = Xet - dec_et                         # liquidation wait from decision_et
            D = Xet.size
            Hm = int(max(1, (session_end[m] - dec_et).max() + 1))
            Hm = min(Hm, HMAX)
            hidx = np.arange(Hm)
            t_liq = Xet[:, None] + hidx[None, :]
            pres_l = present[np.clip(t_liq, 0, 959)]
            in_l = hidx[None, :] <= g[:, None]
            sup["missing"][:Hm] += ((~pres_l) & in_l).sum(axis=0)
            sup["atrisk"][:Hm] += (hidx[None, :] <= hmax[:, None]).sum(axis=0)
            sup["unknown"][:Hm] += ((hidx[None, :] > g[:, None])
                                    & (hidx[None, :] <= hmax[:, None])).sum(axis=0)
            t_dec = dec_et[:, None] + hidx[None, :]
            pres_d = present[np.clip(t_dec, 0, 959)]
            in_d = (hidx[None, :] >= wait[:, None]) & (hidx[None, :] <= (wait + g)[:, None])
            sup["missing_d"][:Hm] += ((~pres_d) & in_d).sum(axis=0)
            sup["atrisk_d"][:Hm] += ((hidx[None, :] >= wait[:, None])
                                     & (hidx[None, :] <= (wait + hmax)[:, None])).sum(axis=0)
            sup["unknown_d"][:Hm] += ((hidx[None, :] > (wait + g)[:, None])
                                      & (hidx[None, :] <= (wait + hmax)[:, None])).sum(axis=0)
            sup["prewait"][:Hm] += (hidx[None, :] < wait[:, None]).sum(axis=0)
            sup["priced"] += np.bincount(hl, minlength=HMAX)
            sup["priced_d"] += np.bincount(hd, minlength=HMAX)
            bad = ~np.isfinite(cv)
            if bad.any():
                sup["invalid"] += np.bincount(hl[bad], minlength=HMAX)
                sup["invalid_d"] += np.bincount(hd[bad], minlength=HMAX)

            keep = (rep % SUBSAMPLE_RATE == 0)
            if keep.any():
                qA_fb.add(fbix[keep] * HMAX + hl[keep], cv[keep])
                if okb.any():
                    kk = keep & okb
                    qA_dist.add(db[kk] * HMAX + hl[kk], cv[kk])

        # ---- close the (day,family) unit: average its members, then fold the unit mean in
        for name, cell in (("fb", A_fb), ("fam", A_fam), ("all", A_all), ("clock", A_clock),
                           ("tenure", A_tenure), ("dist", A_dist), ("hdec", A_hdec),
                           ("block", A_block), ("month", A_month), ("peak", A_peak),
                           ("retf", A_retf), ("vol", A_vol), ("pct", A_pct)):
            mean, m = eqT[name].flush(cell)
            if name == "all":
                U_mean[u] = mean[0]
                U_mask[u] = m[0]


    # ------------------------------------------------------------------ A tables
    qA_fb.flush()
    fb_rows = A_fb.rows(fb_keys)
    for r in fb_rows:
        k = next(i for i, kk in enumerate(fb_keys)
                 if kk["family"] == r["family"] and kk["block"] == r["block"])
        r.update(qA_fb.qcols(k * HMAX + r["h"], "cv"))
    fam_rows = A_fam.rows([{"family": f, "block": "ALL"} for f in fam_list])
    for r in fam_rows:
        parts = []
        for k, kk in enumerate(fb_keys):
            if kk["family"] == r["family"]:
                parts += qA_fb.store.get(k * HMAX + r["h"], [])
        r.update(qcols_from(parts, "cv"))
    all_rows = A_all.rows([{"family": "ALL", "block": "ALL"}])
    for r in all_rows:
        parts = []
        for k in range(len(fb_keys)):
            parts += qA_fb.store.get(k * HMAX + r["h"], [])
        r.update(qcols_from(parts, "cv"))
    fb_out = fb_rows + fam_rows + all_rows
    overall = all_rows
    clock_rows = A_clock.rows([{"clock_bucket": i} for i in range(CLOCK_NBINS)], want_minmax=False)
    tenure_rows = A_tenure.rows([{"tenure_bucket": i} for i in range(TENURE_NBINS)], want_minmax=False)
    dist_rows = A_dist.rows([{"dist_bin": i, "label": DISTRH_LABELS[i]} for i in range(nbin_dist)])
    for r in dist_rows:
        r.update(qA_dist.qcols(r["dist_bin"] * HMAX + r["h"], "cv"))
    hdec_rows = A_hdec.rows([{}])
    block_rows = A_block.rows([{"block": b} for b in blk_list], want_minmax=False)
    month_rows = A_month.rows([{"month": m} for m in mon_list], want_minmax=False)
    peak_rows = A_peak.rows([{"future_label": l} for l in PEAK_LABELS], want_minmax=False)
    retf_rows = A_retf.rows([{"retf_bin": i, "label": (RETF_LABELS + [UNKNOWN_LABEL])[i]}
                             for i in range(len(RETF_LABELS) + 1)], want_minmax=False)
    vol_rows = A_vol.rows([{"vol_bin": i, "label": (VOL_LABELS + [UNKNOWN_LABEL])[i]}
                           for i in range(len(VOL_LABELS) + 1)], want_minmax=False)
    pct_rows = A_pct.rows([{"pct_bin": i, "label": (PCT_LABELS + [UNKNOWN_LABEL])[i]}
                           for i in range(len(PCT_LABELS) + 1)], want_minmax=False)

    qcols = [f"cv_{c}" for c in QCOLS] + ["cv_n"]
    base_cols = ["family", "block", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
                 "pos_sum", "neg_sum", "n_pos", "n_neg", "cv_min", "cv_max"] + qcols
    write_csv(outA / "curve_family_block_h.csv", fb_out, base_cols)
    write_csv(outA / "curve_clock_h.csv", clock_rows,
              ["clock_bucket", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
               "pos_sum", "neg_sum", "n_pos", "n_neg"])
    write_csv(outA / "curve_tenure_h.csv", tenure_rows,
              ["tenure_bucket", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
               "pos_sum", "neg_sum", "n_pos", "n_neg"])
    write_csv(outA / "curve_state_distrh_h.csv", dist_rows,
              ["dist_bin", "label", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
               "pos_sum", "neg_sum", "n_pos", "n_neg", "cv_min", "cv_max"] + qcols)
    write_csv(outA / "curve_overall_hdecision.csv", hdec_rows,
              ["h", "n_pairs", "n_member_states", "mean_occ", "mean_member", "pos_sum", "neg_sum",
               "n_pos", "n_neg", "cv_min", "cv_max"] + qcols)
    write_csv(outA / "curve_block_h.csv", block_rows,
              ["block", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
               "pos_sum", "neg_sum", "n_pos", "n_neg"])
    write_csv(outA / "curve_month_h.csv", month_rows,
              ["month", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
               "pos_sum", "neg_sum", "n_pos", "n_neg"])
    write_csv(outA / "curve_attrib_futurepeak_h.csv", peak_rows,
              ["future_label", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
               "pos_sum", "neg_sum", "n_pos", "n_neg"])
    coord_cols = ["label", "h", "n_pairs", "n_member_states", "mean_occ", "mean_member",
                  "pos_sum", "neg_sum", "n_pos", "n_neg"]
    write_csv(outA / "curve_state_retfill_h.csv", retf_rows, ["retf_bin"] + coord_cols)
    write_csv(outA / "curve_state_volaccel_h.csv", vol_rows, ["vol_bin"] + coord_cols)
    write_csv(outA / "curve_state_retpct_h.csv", pct_rows, ["pct_bin"] + coord_cols)

    # ---- horizon-specific support companion (conserves; no zero-imputation)
    sup_rows = []
    for h in range(HMAX):
        a = int(sup["atrisk"][h])
        if a:
            sup_rows.append({"horizon": "h_from_liquidation", "h": h, "n_at_risk": a,
                             "n_priced": int(sup["priced"][h]),
                             "n_missing_internal_print": int(sup["missing"][h]),
                             "n_unknown_after_observed_tail": int(sup["unknown"][h]),
                             "n_invalid_price": int(sup["invalid"][h]),
                             "n_pre_baseline_wait": 0,
                             "conserved": bool(a == int(sup["priced"][h]) + int(sup["missing"][h])
                                               + int(sup["unknown"][h]) + int(sup["invalid"][h]))})
    for h in range(HMAX):
        a = int(sup["atrisk_d"][h])
        if a or sup["prewait"][h]:
            sup_rows.append({"horizon": "h_from_decision", "h": h, "n_at_risk": a,
                             "n_priced": int(sup["priced_d"][h]),
                             "n_missing_internal_print": int(sup["missing_d"][h]),
                             "n_unknown_after_observed_tail": int(sup["unknown_d"][h]),
                             "n_invalid_price": int(sup["invalid_d"][h]),
                             "n_pre_baseline_wait": int(sup["prewait"][h]),
                             "conserved": bool(a == int(sup["priced_d"][h]) + int(sup["missing_d"][h])
                                               + int(sup["unknown_d"][h]) + int(sup["invalid_d"][h]))})
    write_csv(outA / "support_by_horizon.csv", sup_rows,
              ["horizon", "h", "n_at_risk", "n_priced", "n_missing_internal_print",
               "n_unknown_after_observed_tail", "n_invalid_price", "n_pre_baseline_wait", "conserved"])
    sup_ok = all(r["conserved"] for r in sup_rows)
    counts["support_conserved_all_horizons"] = bool(sup_ok)
    counts["support_note"] = ("h_from_liquidation: at_risk = priced + missing_internal_print + "
                              "unknown_after_observed_tail + invalid_price; targets beyond session_end "
                              "are outside the forced-flat horizon (not unknown). h_from_decision adds "
                              "pre_baseline_wait for h before the liquidation executes.")

    # day- and month-clustered uncertainty over (day,family) units (unit -> cluster)
    u_mon = np.array([mon_ids[k[0][:7]] for k in unit_ids], np.int64)

    def cluster_curves(cl_of_unit, ncl):
        csum = np.zeros((ncl, HMAX)); ccnt = np.zeros((ncl, HMAX), np.int64)
        for u in range(nunits):
            m = U_mask[u]
            if m.any():
                csum[cl_of_unit[u]][m] += U_mean[u][m]
                ccnt[cl_of_unit[u]][m] += 1
        return clustered_se(csum, ccnt), ccnt

    (d_m, d_se), dcnt = cluster_curves(u_day, nday)
    (m_m, m_se), mcnt = cluster_curves(u_mon, nmon)
    bm, bcnt = balanced_mean(U_mean, U_mask)          # overall equal-member mean over units
    cluster = {str(h): {"balanced_mean": js(bm[h]), "day_mean": js(d_m[h]), "day_se": js(d_se[h]),
                        "day_clusters": int((dcnt[:, h] > 0).sum()),
                        "month_mean": js(m_m[h]), "month_se": js(m_se[h]),
                        "month_clusters": int((mcnt[:, h] > 0).sum())}
               for h in range(HMAX) if np.isfinite(d_m[h])}

    tot = np.abs(mem_contrib).sum()
    order = np.argsort(-np.abs(mem_contrib))
    conc = {
        "members_with_pairs": int((mem_pairs > 0).sum()),
        "sum_cv_all": js(mem_contrib.sum()),
        "sum_abs_cv": js(tot),
        "top1_share": js(np.abs(mem_contrib).max() / tot if tot else 0.0),
        "top5_share": js(np.sort(np.abs(mem_contrib))[::-1][:5].sum() / tot if tot else 0.0),
        "top20_share": js(np.sort(np.abs(mem_contrib))[::-1][:20].sum() / tot if tot else 0.0),
        "hhi": js(float(((mem_contrib / tot) ** 2).sum()) if tot else 0.0),
        "top_contributors": [{"member_id": meta["member_id"][int(i)], "family": fam[i],
                              "block": blk[i], "sleeve_day": day[i], "n_pairs": int(mem_pairs[i]),
                              "sum_cv": js(mem_contrib[i])} for i in order[:20]],
    }
    (outA / "concentration.json").write_text(json.dumps(conc, indent=1))

    svg_curve(outA / "curve_overall.svg",
              [("mean_member", [(r["h"], r["mean_member"]) for r in overall]),
               ("mean_occ", [(r["h"], r["mean_occ"]) for r in overall]),
               ("p50 (subsample)", [(r["h"], r.get("cv_p50")) for r in overall])],
              "A CV vs h_from_liquidation (all members)")
    svg_curve(evA / "curve_family.svg",
              [(f + " mean_member",
                [(r["h"], r["mean_member"]) for r in fam_rows if r["family"] == f])
               for f in fam_list],
              "A mean_member CV by family")
    svg_curve(evA / "curve_state.svg",
              [(lbl, [(r["h"], r["mean_occ"]) for r in dist_rows if r["dist_bin"] == i])
               for i, lbl in enumerate(DISTRH_LABELS)],
              "A mean_occ CV by dist_from_running_high bin")

    # ------------------------------------------------------------------ manifests + evidence
    def manifest(exp, extra):
        return {"experiment": exp, "code_sha256": sha256_file(Path(__file__)),
                "code_path": str(Path(__file__)),
                "contract_sha256": sha256_file(Path(args.contract)),
                "base": {fn: sha256_file(base / fn) for fn in
                         ("states.parquet", "executions.parquet", "members.parquet")},
                "panel_sha256_expected": contract.get("panel_sha256"),
                "side_friction": S,
                "fees": "one-side 50bp cancels between hold and immediate liquidation",
                "quantiles": f"a uniform 1/{SUBSAMPLE_RATE} decision-state subsample (deterministic), "
                             "labelled per cell by *_n; means/contributions/min/max are exact",
                "scale": "q=1 linear scaling; no EV/alpha/policy claim", **extra}

    (outA / "manifest.json").write_text(json.dumps(manifest("A", {
        "counts": counts, "concentration": {k: v for k, v in conc.items() if k != "top_contributors"},
        "day_month_clusters": cluster}), indent=1))
    (evA / "counts.json").write_text(json.dumps(counts, indent=1))
    for src, dst in ((outA / "curve_family_block_h.csv", evA / "curve_family_block_h.csv"),
                     (outA / "curve_state_distrh_h.csv", evA / "curve_state_distrh_h.csv"),
                     (outA / "curve_state_retfill_h.csv", evA / "curve_state_retfill_h.csv"),
                     (outA / "curve_state_volaccel_h.csv", evA / "curve_state_volaccel_h.csv"),
                     (outA / "curve_state_retpct_h.csv", evA / "curve_state_retpct_h.csv"),
                     (outA / "curve_block_h.csv", evA / "curve_block_h.csv"),
                     (outA / "curve_month_h.csv", evA / "curve_month_h.csv"),
                     (outA / "curve_clock_h.csv", evA / "curve_clock_h.csv"),
                     (outA / "curve_tenure_h.csv", evA / "curve_tenure_h.csv"),
                     (outA / "curve_attrib_futurepeak_h.csv", evA / "curve_attrib_futurepeak_h.csv"),
                     (outA / "curve_overall_hdecision.csv", evA / "curve_overall_hdecision.csv"),
                     (outA / "support_by_horizon.csv", evA / "support_by_horizon.csv"),
                     (outA / "manifest.json", evA / "manifest.json"),
                     (outA / "curve_overall.svg", evA / "curve_overall.svg")):
        Path(dst).write_bytes(Path(src).read_bytes())
    (evA / "headline.json").write_text(json.dumps({
        "A_mean_member_by_h": {str(r["h"]): js(r["mean_member"]) for r in overall},
        "A_mean_occ_by_h": {str(r["h"]): js(r["mean_occ"]) for r in overall},
        "counts_A": counts}, indent=1))
    (Path(args.evidence) / "README_A_curves.md").write_text(
        "# CV01 experiment A — current-dollar continuation curve (ContinuationAnatomy)\n\n"
        f"code={sha256_file(Path(__file__))[:12]} contract={sha256_file(Path(args.contract))[:12]}\n\n"
        "Experiment A only. Experiment B (giveback-10 retain vs sell-close-reclaim-reenter) is owned by "
        "OwnershipContinuity; this producer neither computes nor publishes B.\n\n"
        "Pins declared before any CV number was inspected: clock bucket=30 ET-min (anchor ET570), "
        "tenure bucket=15 bars, state coordinate=dist_from_running_high with edges "
        "[-inf,-0.20,-0.10,-0.05,-0.02,0.02,+inf], quantile subsample=every 16th eligible decision state.\n\n"
        "A: CV=P_future/P_exit-1 (common 50bp fee cancels), immediate endpoint CV=0, horizon from both "
        "decision_et and immediate exit et; gaps missing (real ET elapsed, never imputed); censored/partial "
        "paths kept and counted, never zeroed. All 1,888,885 clock-eligible opportunities are retained in "
        "the risk set; the 185 with no next print stay UNRESOLVED (no CV, counted, never dropped/zeroed). "
        "Means/contributions/min/max exact; quantiles subsampled+labelled per cell by *_n.\n\n"
        "Views: mean_occ = occupancy-weighted (each occupied member-minute pair equally). mean_member = "
        "equal-member day/family path: mean the supported outcomes WITHIN a member, average members "
        "WITHIN the (day,family) unit, then average units; units with no support stay unknown (never 0). "
        "Uncertainty is day- and month-clustered over (day,family) units on the equal-member mean only; "
        "minute snapshots are NOT independent units.\n\n"
        "Time/tenure confound (explicit): the clock and tenure baselines are mechanically confounded (a "
        "later decision clock implies a longer tenure for the same session), so both are descriptive "
        "decompositions of the same curve, not independent causal effects. Family/block/month splits are "
        "descriptive strata, not causal controls.\n\n"
        "Anatomy only: no optimal value, no alpha/EV claim, no policy, no sizing, no sub-minute data.\n\n"
        "Marginal single-coordinate rulers (bounds declared before any CV was computed; diagnostic "
        "bins, not a threshold policy; missing = explicit UNKNOWN bucket, never 0): ret_from_fill "
        "edges [-inf,0,0.10,0.30,1,+inf]; volume_accel edges [-inf,-0.5,0,1,+inf]; "
        "ret_percentile_candidates native 0..1 (verified) quartile bands [0,.25,.5,.75,1]. "
        "No joint grid, no model, no Atlas/NN features.\n")
    log("done A. rows=%d members=%d eligible=%d unresolved=%d" %
        (len(fb_out), nmem, counts["states_decision_eligible"], counts["decisions_missing_immediate"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
