"""Independent feature audit (trust gate for the corrected pipeline).

Hand-recomputes a representative set of features/labels from RAW chronological bars
(pure python, no research helpers) for sampled ticker-days, and compares against BOTH
the research parquet and the live engine (TickerSession). Covers every op class that
could be corrupted by unsorted row order: cum_sum, forward_fill, cum_max/shift, rolling
stats, baseline interpolation (rvol), and self-join labels.

rank / market_ret_5m are order-independent by construction (rank & median are
permutation-invariant; certify_month sorts before both anyway) and are fed from the
parquet as inputs, exactly like tests/test_feature_parity.py.

Run: uv run --no-project --with polars --with numpy --with tzdata python tests/audit_features_independent.py
"""
from __future__ import annotations
import math
import sys
from datetime import date

import polars as pl

sys.path.insert(0, ".")
from src.live.features import TickerSession, qualifies, MOD0  # noqa: E402

RTOL, ATOL = 1e-9, 1e-12
REL = (30, 90, 150, 210, 270, 330, 390)  # bucket ends, relative to 09:30

SAMPLES = [
    ("2025-06", date(2025, 6, 5), 3),
    ("2025-10", date(2025, 10, 15), 3),
    ("2026-02", date(2026, 2, 20), 3),
]
CHECK_COLS = ["cum_dv", "vwap_dist", "above_vwap", "ret_1m", "ret_5m", "ret_30m",
              "n_hod_breaks", "realized_vol_15m", "efficiency_30m", "n_up_bars_15",
              "fwd60_t1entry", "rvol"]


def ok(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= max(ATOL, RTOL * max(abs(a), abs(b)))


def sample_std(xs):
    n = len(xs)
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def load_month(m):
    df = pl.read_parquet(f"data/clean_ohlcv_{m}.parquet")
    df = df.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    df = df.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
    df = df.with_columns(
        (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32)).alias("tod"))
    return df


def audit_ticker_day(span_day, span_hist, ticker, day, ev_rows, pq):
    """span_day: bars for this ticker on `day` (sorted). span_hist: bars for this ticker
    on all prior days (sorted). ev_rows: events (rel, rank, m5) for this ticker-day.
    pq: dict rel -> parquet feature row. Returns mismatch report."""
    bars = {int(r["tod"]) - MOD0: (r["open"], r["high"], r["low"], r["close"], r["volume"])
            for r in span_day.iter_rows(named=True) if qualifies(r["close"], r["volume"])}
    if not bars:
        return ["no bars"]
    first_rel = min(bars)

    # prev session close (independent: last qualifying bar of the previous session)
    sess = sorted({r["et_date"] for r in span_hist.iter_rows(named=True)})
    prev_rows = span_hist.filter(pl.col("et_date") == sess[-1]) if sess else None
    prev_close = None
    if prev_rows is not None and prev_rows.height:
        prev_close = prev_rows.sort("tod")["close"][-1]

    # hand baselines: prior 20 sessions, cumdv at each bucket end (clock <= MOD0+REL[i])
    hist_by_day = {}
    for r in span_hist.iter_rows(named=True):
        if qualifies(r["close"], r["volume"]):
            hist_by_day.setdefault(r["et_date"], []).append(r)
    sess_list = sorted(hist_by_day)
    last20 = sess_list[-20:]
    evec = None
    if len(last20) == 20 and sess_list and sess_list[-1] < day:
        evec = []
        for brel in REL:
            tot = 0.0
            for d in last20:
                cd = 0.0
                for r in hist_by_day[d]:
                    if (r["tod"] - MOD0) <= brel:
                        cd += r["close"] * r["volume"]
                tot += cd
            evec.append(tot / 20)

    def expdv(t_rel):
        if t_rel <= 0 or evec is None:
            return None
        prev = 0
        for i, b in enumerate(REL):
            if t_rel <= b:
                frac = (t_rel - prev) / (b - prev)
                return evec[0] * frac if i == 0 else evec[i - 1] + (evec[i] - evec[i - 1]) * frac
            prev = b
        return evec[6]

    # ---- hand state loop over the full grid ----
    gclose: list = []   # ffilled closes per grid row (None before first bar)
    glow: list = []     # real lows (None on fills)
    gvol: list = []     # real volume (0 on fills)
    ghd: list = []      # ffilled highs
    gcum: list = []     # cum dv
    r1_hist: list = []
    last_c = last_h = None
    cum_dv = vwap_num = vwap_den = 0.0
    day_max = 0.0
    day_min = None
    n_hod = 0
    hand = {}
    for rel in range(first_rel, 390):
        bar = bars.get(rel)
        if bar is None:
            c, h, l, v, has = last_c, last_h, None, 0.0, False
        else:
            _, h, l, c, v = bar
            has = True
        hod_before = day_max
        if has:
            r1 = (c / last_c - 1) if last_c is not None else None
            if h > hod_before:
                n_hod += 1
        else:
            r1 = None
        if h is not None and h > day_max:
            day_max = h
        if l is not None and (day_min is None or l < day_min):
            day_min = l
        dv = c * v
        cum_dv += dv
        vwap_num += dv
        vwap_den += (v if has else 0.0)
        gclose.append(c)
        ghd.append(h)
        glow.append(l)
        gvol.append(v)
        gcum.append(cum_dv)
        r1_hist.append(r1)
        last_c, last_h = c, h
        if rel not in ev_rows:
            continue
        vwap = (vwap_num / vwap_den) if vwap_den > 0 else None
        win15 = [x for x in r1_hist[-15:] if x is not None]
        win30 = [x for x in r1_hist[-30:] if x is not None]
        row = {"cum_dv": cum_dv}
        row["vwap_dist"] = (c / vwap - 1) if (vwap is not None and vwap > 0) else None
        row["above_vwap"] = (1.0 if c > vwap else 0.0) if vwap is not None else None
        for k in (1, 5, 30):
            ck = gclose[-1 - k] if len(gclose) > k else None
            row[f"ret_{k}m"] = (c / ck - 1) if (c is not None and ck is not None) else None
        row["n_hod_breaks"] = float(n_hod)
        row["realized_vol_15m"] = sample_std(win15) if len(win15) >= 10 else None
        row["efficiency_30m"] = (abs(sum(win30)) / max(sum(abs(x) for x in win30), 1e-12)
                                 if len(win30) >= 15 else None)
        row["n_up_bars_15"] = float(sum(1 for x in win15 if x > 0)) if len(win15) >= 10 else None
        if (rel + 61 in bars) and (rel + 1 in bars):
            row["fwd60_t1entry"] = bars[rel + 61][3] / bars[rel + 1][3] - 1
        else:
            row["fwd60_t1entry"] = None
        e = expdv(rel)
        row["rvol"] = (cum_dv / e) if (e is not None and e > 0) else None
        hand[rel] = row

    # ---- compare hand vs parquet ----
    bad = []
    for rel, row in hand.items():
        prow = pq[rel]
        for col in CHECK_COLS:
            if not ok(row[col], prow[col]):
                bad.append(f"{ticker} {day} rel={rel} {col}: hand={row[col]} parquet={prow[col]}")

    # ---- engine replay vs hand & parquet (rank/m5 fed from parquet, as parity) ----
    sess = None
    for rel in range(first_rel, 390):
        bar = bars.get(rel)
        use = bar if (bar is not None and qualifies(bar[3], bar[4])) else None
        if use is None and sess is None:
            continue
        if sess is None:
            sess = TickerSession(rel, use, prev_close, True)
        else:
            sess.on_minute(rel, use)
        if rel in ev_rows:
            rank, m5, dow = ev_rows[rel]
            f = sess.features(rel, rank, m5, ([evec] * 20 if evec else None), dow)
            f["cum_dv"] = sess.cum_dv
            for col in [c for c in CHECK_COLS if not c.startswith("fwd")]:
                if not ok(f[col], hand[rel][col]):
                    bad.append(f"{ticker} {day} rel={rel} {col}: engine={f[col]} hand={hand[rel][col]}")
    return bad


def main():
    total_bad = 0
    n_events = 0
    for month, day, k in SAMPLES:
        feats = pl.read_parquet(f"data/ml_features/features_{month}.parquet")
        ev_day = feats.filter(pl.col("et_date") == day)
        tickers = (ev_day.filter(pl.col("tod_min") >= 60)["ticker"].unique().sort()
                   .head(k).to_list())
        y, m = map(int, month.split("-"))
        months = [f"{y + (mm - 1) // 12}-{(mm - 1) % 12 + 1:02d}"
                  for mm in (m, m - 1, m - 2)]
        span = pl.concat([load_month(mm) for mm in months if mm != month], how="vertical")
        cur = load_month(month)
        full = pl.concat([span, cur], how="vertical")
        for t in tickers:
            day_bars = cur.filter((pl.col("ticker") == t) & (pl.col("et_date") == day)).sort("tod")
            hist = full.filter((pl.col("ticker") == t) & (pl.col("et_date") < day)).sort("et_date", "tod")
            ev = ev_day.filter(pl.col("ticker") == t).sort("tod_min")
            ev_rows = {}
            pq = {}
            for r in ev.iter_rows(named=True):
                rel = int(r["tod_min"])
                ev_rows[rel] = (r["rank"], r["market_ret_5m"], r["dow"])
                pq[rel] = r
            bad = audit_ticker_day(day_bars, hist, t, day, ev_rows, pq)
            total_bad += len(bad)
            n_events += len(ev_rows)
            status = "OK" if not bad else f"{len(bad)} MISMATCHES"
            print(f"  {month} {day} {t}: {len(ev_rows)} events -> {status}")
            for b in bad[:6]:
                print("    ", b)
        print(f"[{month}] done")
    print(f"\nTOTAL: {n_events} event rows checked, {total_bad} mismatches")
    print("VERDICT:", "PASS" if total_bad == 0 else "FAIL")


if __name__ == "__main__":
    main()
