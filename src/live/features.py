"""Live feature engine — exact port of factory/scripts/build_features.py grid semantics.

Parity contract (verified by tests/test_feature_parity.py against research parquet):
- grid rows per (ticker, session) from first qualifying bar; fill rows: close/high
  forward-filled, low=None, volume/dv=0, r1=None (real-bar-only stats).
- clean-bar filter: close >= 2.0 AND volume >= 100 (non-qualifying bars treated as
  nonexistent, exactly like load_clean in research).
- rolling stats: min_samples counts non-null values (verified polars semantics).
- rvol: cum_dv / interpolated 20-session bucket-end baseline; null at 09:30.
- market_ret_5m: median of 5-REAL-bar returns across symbols with a real bar (market_frame).
"""
from __future__ import annotations

import math
from collections import deque

MOD0 = 570          # 09:30 bar
MOD_END = 960       # 16:00 (bars 570..959)
BUCKET_RELS = (30, 90, 150, 210, 270, 330, 390)   # 10:00..16:00 relative to 09:30

# Exact frozen feature order (train_ml.py:FEATURES — identical in every scorer).
FEATS = ["pct_gain_grid", "rank", "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m",
         "vwap_dist", "above_vwap", "dist_open", "open_gap", "dist_hod", "range_pos",
         "log_close", "log_dollar_volume", "dv_5m_rate", "dv_accel", "rvol", "excess_gain",
         "market_ret_5m", "tod_min", "dow",
         "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
         "realized_vol_15m", "efficiency_30m", "n_up_bars_15"]

PRICE_FLOOR = 2.0
VOL_FLOOR = 100


def qualifies(close: float, volume: int | float) -> bool:
    return close is not None and close >= PRICE_FLOOR and volume >= VOL_FLOOR


class TickerSession:
    """Per-symbol state for one trading day. Created lazily on first qualifying bar."""

    __slots__ = (
        "bars", "closes", "grid", "r1_hist", "prev_close", "prev_sess_ok", "session_open",
        "session_open_set", "cum_dv", "vwap_num", "vwap_den", "day_max_h", "day_min_l",
        "hod_before", "n_hod_breaks", "bucket_snaps", "n_bars", "last_c", "last_h",
        "first_qual_rel",
    )

    def __init__(self, t_rel: int, bar: tuple, prev_close: float | None, prev_sess_ok: bool):
        # bars: tod_rel -> (o,h,l,c,v) real qualifying bars only
        self.bars: dict[int, tuple] = {}
        self.closes: deque = deque(maxlen=6)          # real closes, for 5-real-bar return
        # grid rows: [t_rel, has_bar, c, h, l, cum_dv]; l None on fill rows
        self.grid: deque = deque(maxlen=40)
        self.r1_hist: deque = deque(maxlen=40)        # r1 or None
        self.prev_close = prev_close
        self.prev_sess_ok = prev_sess_ok
        self.session_open: float | None = None
        self.session_open_set = False
        self.cum_dv = 0.0
        self.vwap_num = 0.0
        self.vwap_den = 0.0
        self.day_max_h = 0.0                          # research: hod_before fill_null(0.0)
        self.day_min_l: float | None = None
        self.hod_before = 0.0
        self.n_hod_breaks = 0
        self.bucket_snaps: list[float | None] = [None] * 7
        self.n_bars = 0
        self.last_c: float | None = None
        self.last_h: float | None = None
        self.first_qual_rel = t_rel
        self.on_minute(t_rel, bar)

    def on_minute(self, t_rel: int, bar: tuple | None) -> None:
        """Advance the grid by one minute. bar = (o,h,l,c,v) qualifying or None (fill row)."""
        self.hod_before = self.day_max_h              # hod_now.shift(1) over grid
        if bar is None:
            c = self.last_c
            h = self.last_h
            l = None
            v = 0.0
            has_bar = False
            dv = 0.0
            r1 = None
        else:
            o, h, l, c, v = bar
            if not self.session_open_set:
                self.session_open = o
                self.session_open_set = True
            has_bar = True
            dv = c * v
            r1 = (c / self.last_c - 1) if self.last_c is not None else None
            self.n_bars += 1
        cum = self.cum_dv + dv
        # hod break: (h > hod_before) & has_bar, on ffilled h
        if has_bar and h is not None and h > self.hod_before:
            self.n_hod_breaks += 1
        if h is not None and h > self.day_max_h:
            self.day_max_h = h
        if l is not None and (self.day_min_l is None or l < self.day_min_l):
            self.day_min_l = l
        self.cum_dv = cum
        self.vwap_num += dv
        self.vwap_den += v if has_bar else 0.0
        self.grid.append([t_rel, has_bar, c, h, l, cum])
        self.r1_hist.append(r1)
        self.last_c = c
        self.last_h = h
        if has_bar:
            self.closes.append(c)
            self.bars[t_rel] = bar
        # bucket-end snapshot: research cumdv at last bar with tod <= bucket end.
        # BUCKET_RELS are in bar-START clock terms; t_rel 389 = 15:59 bar = session last.
        for i, b in enumerate(BUCKET_RELS[:-1]):
            if t_rel == b:
                self.bucket_snaps[i] = cum
        if t_rel >= BUCKET_RELS[-1] - 1:
            self.bucket_snaps[6] = cum

    def ret_5_real(self) -> float | None:
        """5-REAL-bar return (market_frame: close.shift(5).over(ticker) on real bars only)."""
        if len(self.closes) < 6:
            return None
        c0 = self.closes[-1]
        c5 = self.closes[-6]
        return (c0 / c5 - 1) if c5 else None

    def expected_dv(self, baseline_sessions: list | None, t_rel: int) -> float | None:
        """20-session bucket-end baseline, linear interpolation (build_baselines + rvol_attach)."""
        if t_rel <= 0 or not baseline_sessions or len(baseline_sessions) < 20:
            return None
        n = len(baseline_sessions)
        e = [sum(s[i] for s in baseline_sessions) / n for i in range(7)]
        prev_rel = 0
        for i, b in enumerate(BUCKET_RELS):
            if t_rel <= b:
                frac = (t_rel - prev_rel) / (b - prev_rel)
                if i == 0:
                    return e[0] * frac
                return e[i - 1] + (e[i] - e[i - 1]) * frac
            prev_rel = b
        return e[6]

    def rvol(self, baseline_sessions: list | None, t_rel: int) -> float | None:
        exp = self.expected_dv(baseline_sessions, t_rel)
        if exp is None or exp <= 0:
            return None
        return self.cum_dv / exp

    # ---- features (research-exact; see tests/test_feature_parity.py) ----

    def features(self, t_rel: int, rank: int | None, market_ret_5m: float | None,
                 baseline_sessions: list | None, dow: int) -> dict:
        g = self.grid
        c = g[-1][2]
        pc = self.prev_close
        hod_now = self.day_max_h
        hod_before = self.hod_before
        vwap = (self.vwap_num / self.vwap_den) if self.vwap_den > 0 else None

        def grid_close_back(k: int):
            return g[-1 - k][2] if len(g) > k else None

        pct_gain = (c / pc - 1) * 100 if (c is not None and pc) else None
        f = {}
        f["pct_gain_grid"] = pct_gain
        f["rank"] = rank
        f["n_hod_breaks"] = self.n_hod_breaks

        # dips: lmin5 = rolling_min(l_real, 5, min_samples=1) — nulls ignored
        lmin5 = None
        for i in range(min(5, len(g))):
            l = g[-1 - i][4]
            if l is not None and (lmin5 is None or l < lmin5):
                lmin5 = l
        dip_level = hod_before * 0.997
        if vwap is not None and vwap > dip_level:
            dip_level = vwap
        f["dip_5m"] = bool(lmin5 is not None and lmin5 < dip_level)
        if hod_before > 0:
            f["dip_depth_5m"] = min(0.0, max(-1.0, lmin5 / hod_before - 1)) if lmin5 is not None else None
        else:
            # hod_before==0 -> inf -> clip(-1,0) -> 0.0 (polars)
            f["dip_depth_5m"] = 0.0 if lmin5 is not None else None
        f["trap_reclaim"] = 1 if (f["dip_5m"] and c is not None and c > hod_before) else 0

        f["vwap_dist"] = (c / vwap - 1) if (c is not None and vwap) else None
        f["above_vwap"] = (1 if c > vwap else 0) if (c is not None and vwap is not None) else None
        f["dist_open"] = (c / self.session_open - 1) if (c is not None and self.session_open) else None
        f["open_gap"] = (self.session_open / pc - 1) if (self.session_open and pc) else None
        f["dist_hod"] = (c / hod_now - 1) if (c is not None and hod_now) else None
        den = hod_now - self.day_min_l if (self.day_min_l is not None) else None
        f["range_pos"] = ((c - self.day_min_l) / max(den, 1e-12)
                          if (c is not None and den is not None) else None)
        f["log_close"] = math.log1p(c) if c is not None else None
        f["log_dollar_volume"] = math.log1p(self.cum_dv)

        pg_back5 = None
        if len(g) > 5 and pc:
            c5 = g[-6][2]
            pg_back5 = (c5 / pc - 1) * 100 if c5 is not None else None
        f["gain_vel_5m"] = (pct_gain - pg_back5) if (pct_gain is not None and pg_back5 is not None) else None
        # gain_vel_15m is not in FEATS; computed only if needed (skipped)

        cum5 = g[-6][5] if len(g) > 5 else 0.0
        cum35 = g[-36][5] if len(g) > 35 else 0.0
        dv5 = self.cum_dv - cum5
        dv30 = cum5 - cum35
        f["dv_5m_rate"] = dv5 / 5
        f["dv_accel"] = (dv5 / 5) / (dv30 / 30) if dv30 > 0 else None

        f["rvol"] = self.rvol(baseline_sessions, t_rel)
        f["market_ret_5m"] = market_ret_5m
        f["excess_gain"] = (pct_gain - 100 * market_ret_5m) if (pct_gain is not None and market_ret_5m is not None) else None
        f["tod_min"] = t_rel
        f["dow"] = dow

        for k in (1, 3, 5, 10, 15, 30):
            ck = grid_close_back(k)
            f[f"ret_{k}m"] = (c / ck - 1) if (c is not None and ck) else None

        # rolling stats over r1 (None on fill rows; min_samples counts non-null)
        r1 = list(self.r1_hist)
        win15 = r1[-15:]
        valid15 = [x for x in win15 if x is not None]
        f["realized_vol_15m"] = _sample_std(valid15) if len(valid15) >= 10 else None
        win30 = r1[-30:]
        valid30 = [x for x in win30 if x is not None]
        if len(valid30) >= 15:
            s = sum(valid30)
            sa = sum(abs(x) for x in valid30)
            f["efficiency_30m"] = abs(s) / max(sa, 1e-12)
        else:
            f["efficiency_30m"] = None
        f["n_up_bars_15"] = (sum(1 for x in valid15 if x > 0) if len(valid15) >= 10 else None)
        return f


def _sample_std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)
