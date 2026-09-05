"""Path-feature builder for the top-3 program (frozen v1 definitions).

Every function takes ONLY bars with et <= t-1 (plus premarket + prev close) and
returns plain dicts. Key invariant (tested): appending future bars to the input
frame MUST NOT change any feature value.

Feature vector (28): pm/gap 6, early returns 5, shape/accel 5, pullbacks 4,
rank/volume 5, day-state 3. See build_vector() docstring for exact definitions.
"""
from __future__ import annotations
import math

T_OPEN = 570  # 09:30 ET


def is_common_stock(symbol: str, quote_type: str | None = None) -> bool:
    """Universe definition (NOT a signal filter): common-stock momentum thesis.
    Excludes warrants/units/preferred/odd symbology + known ETFs. quote_type from
    universe_tags.parquet where available ('ETF' cut documented as non-PIT)."""
    s = symbol.upper()
    if any(c in s for c in (".", "/", "+", "-", " ")):
        return False
    if s.endswith(("W", "U", "R")) and len(s) > 4:
        # trailing-W/U/R warrants & units (e.g. ARBEW); R = rights
        return False
    if quote_type == "ETF":
        return False
    return True


def _bars_le(bars, t_et: int, lag: int = 1):
    return bars[bars["et"] <= t_et - lag].sort_values("timestamp")


def zseries_30m(bars, t_et: int = 600, lag: int = 1):
    """Z-scored 1-min close-to-close returns, 09:30->t (30 values at t=600).
    Scale-free shape for DTW arm. Returns list or None if <15 bars."""
    import numpy as np
    b = _bars_le(bars, t_et, lag)
    b = b[(b["et"] >= T_OPEN)]
    if len(b) < 15:
        return None
    c = b["close"].to_numpy(dtype=float)
    r = np.diff(np.log(np.maximum(c, 1e-9)))
    if len(r) < 2 or np.std(r) <= 0:
        return None
    z = (r - r.mean()) / r.std()
    return [round(float(x), 4) for x in z[-30:]]


def dtw(a, b, w: int = 3):
    """Symmetric DTW with Sakoe-Chiba window. Pure python/numpy, small n."""
    import numpy as np
    n, m = len(a), len(b)
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    A = np.asarray(a, dtype=float)
    B = np.asarray(b, dtype=float)
    for i in range(1, n + 1):
        for j in range(max(1, i - w), min(m, i + w) + 1):
            cost = abs(A[i - 1] - B[j - 1])
            D[i, j] = cost + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return float(D[n, m])


def build_vector(sess_ticker_bars, pm_ticker_bars, prev_close, t_et: int = 600,
                 lag: int = 1, rank_ctx: dict | None = None) -> dict | None:
    """28-feature vector. sess_ticker_bars: session frame for ONE ticker (any span —
    only et<=t-lag is read). pm_ticker_bars: premarket frame or None.
    rank_ctx: {rank_now, rank_first, rank_switches, separation, n_big, median_top20}
    computed by caller from snapshot table (needs cross-section)."""
    import numpy as np
    b = _bars_le(sess_ticker_bars, t_et, lag)
    b = b[b["et"] >= T_OPEN]
    if len(b) < 10:
        return None
    o = b["open"].to_numpy(float)
    h = b["high"].to_numpy(float)
    lo = b["low"].to_numpy(float)
    c = b["close"].to_numpy(float)
    v = b["volume"].to_numpy(float)
    last, first_open = c[-1], o[0]
    ret = np.diff(np.log(np.maximum(c, 1e-9)))
    f = {}
    # PM/gap (6)
    if pm_ticker_bars is not None and len(pm_ticker_bars):
        p = pm_ticker_bars.sort_values("timestamp")
        ph, pl = float(p["high"].max()), float(p["low"].min())
        f["pm_range_pct"] = (ph - pl) / first_open
        f["pm_last_to_open"] = first_open / float(p["close"].iloc[-1]) - 1
        f["pm_dollar_vol"] = math.log1p(float((p["close"] * p["volume"]).sum()))
        f["pm_missing"] = 0.0
        f["gap_vs_pmhigh"] = first_open / ph - 1 if ph > 0 else 0.0
    else:
        f["pm_range_pct"] = 0.0
        f["pm_last_to_open"] = 0.0
        f["pm_dollar_vol"] = 0.0
        f["pm_missing"] = 1.0
        f["gap_vs_pmhigh"] = 0.0
    f["gap_pct"] = (first_open / prev_close - 1) if prev_close else 0.0
    # early returns (5)
    f["ret_open_T"] = last / first_open - 1
    i35 = b[b["et"] <= 575]
    f["ret_0935_T"] = last / float(i35["close"].iloc[-1]) - 1 if len(i35) else 0.0
    f["ret_last30"] = last / first_open - 1
    h15 = b[b["et"] > t_et - lag - 15]
    f["ret_last15"] = last / float(h15["close"].iloc[0]) - 1 if len(h15) else 0.0
    f["max_1m_gain"] = float(np.max(ret)) if len(ret) else 0.0
    # shape/accel (5)
    e1 = b[(b["et"] >= 570) & (b["et"] < 585)]
    f["accel"] = f["ret_last15"] - (float(e1["close"].iloc[-1]) / first_open - 1 if len(e1) else 0.0)
    f["n_up_min_share"] = float(np.mean(ret > 0)) if len(ret) else 0.5
    f["path_conc"] = f["max_1m_gain"] / max(abs(f["ret_open_T"]), 1e-6)
    f["time_of_high"] = float(b["et"].iloc[int(np.argmax(h))] - 570)
    f["reversal_from_high"] = float(h.max() / last - 1)
    # pullbacks/structure (4)
    f["n_pullbacks"] = 0
    f["max_pullback_depth"] = 0.0
    peak, trough, in_pb = h[0], h[0], False
    for hh, cc in zip(h, c):
        if hh > peak:
            peak = hh
            if in_pb:
                in_pb = False
        dd = peak / min(cc, peak) - 1 if min(cc, peak) > 0 else 0.0
        if dd >= 0.005 and not in_pb:
            in_pb = True
            trough = cc
        if in_pb:
            trough = min(trough, cc)
            if cc / trough - 1 >= 0.005:
                f["n_pullbacks"] += 1
                in_pb = False
                peak = cc
        f["max_pullback_depth"] = max(f["max_pullback_depth"], dd if in_pb else 0.0)
    f["dist_HOD"] = last / h.max() - 1
    dv = float((c * v).sum())
    vv = float(v.sum())
    f["dist_VWAP"] = last / (dv / vv) - 1 if vv > 0 else 0.0
    # rank/volume (5)
    r = rank_ctx or {}
    f["rank_now"] = float(r.get("rank_now", 0))
    f["rank_first"] = float(r.get("rank_first", 0))
    f["rank_switches"] = float(r.get("rank_switches", 0))
    f["dollar_vol_to_T"] = math.log1p(dv)
    f["dvol_rank_pct"] = float(r.get("dvol_rank_pct", 0.5))
    # day-state (3)
    f["separation"] = float(r.get("separation", 0))
    f["n_big"] = float(r.get("n_big", 0))
    f["median_top20_gain"] = float(r.get("median_top20_gain", 0))
    return {k: (round(float(x), 6) if x == x else 0.0) for k, x in f.items()}


FEATURES = ["pm_range_pct", "pm_last_to_open", "pm_dollar_vol", "pm_missing",
            "gap_vs_pmhigh", "gap_pct", "ret_open_T", "ret_0935_T", "ret_last30",
            "ret_last15", "max_1m_gain", "accel", "n_up_min_share", "path_conc",
            "time_of_high", "reversal_from_high", "n_pullbacks", "max_pullback_depth",
            "dist_HOD", "dist_VWAP", "rank_now", "rank_first", "rank_switches",
            "dollar_vol_to_T", "dvol_rank_pct", "separation", "n_big", "median_top20_gain"]
