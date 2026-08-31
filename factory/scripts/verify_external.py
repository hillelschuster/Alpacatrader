#!/usr/bin/env python3
"""External verification harness for the month certification (`certify_month.py`).

Cross-checks a sample of (ticker, et_date) events against independent sources:
  yfinance raw daily Close (prev_close + day change) and Alpaca IEX 1-min bars
  (RTH day-high + 1-min path). Writes `external_checks.json` + `external_verification_report.md`
  to the artifact dir. Reads Parquet via polars (pandas has no parquet engine here).

ponytail: single file, stdlib+polars+yfinance+alpaca-py, functions only, no classes.
"""
import argparse
import json
import random
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

ET = ZoneInfo("America/New_York")        # for datetime(tzinfo=...)
ET_STR = "America/New_York"              # for polars convert_time_zone (needs str)
UTC = ZoneInfo("UTC")

# tolerances / thresholds
TOL_PREV_CLOSE = 0.001      # check1: tight 0.1% vs yfinance prior-session raw close (report stat)
TOL_PREV_CLOSE_OP = 0.0035  # check1: operating 0.35% (last-print vs official 16:00 auction)
TOL_PREV_CLOSE_MEDIAN = 0.05  # check1: median diff (percent) must be <= 0.05% for PASS
TOL_PCT_GAIN = 0.5          # check2: 0.5 percentage points vs yfinance day change
TOL_RTH_HOD = 0.003         # check4: 0.3% vs consolidated (yfinance daily High) day high
PASS_THRESHOLD = 0.95       # verdict PASS when match_rate >= this
MIN_N = 3                   # below this many measured samples -> INSUFFICIENT
YF_SLEEP = 0.3              # polite sleep between yfinance calls
RTH_START_MIN = 570         # 09:30 ET
RTH_END_MIN = 960           # 16:00 ET


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="External verification of certification outputs")
    p.add_argument("--month", required=True, help="ET trading month, YYYY-MM")
    p.add_argument("--sample", type=int, default=20, help="number of ticker-days to sample")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--artdir", default=None)
    return p.parse_args(argv)


# ---------------------------------------------------------------- loading

def load_events(artdir: Path):
    """Read events_topN.parquet, add et_date (ET). Return polars df or None if missing."""
    path = artdir / "events_topN.parquet"
    if not path.exists():
        return None
    ev = pl.read_parquet(path)
    if "et" not in ev.columns:
        ev = ev.with_columns(pl.col("timestamp").dt.convert_time_zone(ET_STR).alias("et"))
    ev = ev.with_columns(pl.col("et").dt.date().alias("et_date"))
    return ev


def _day_table(ev: "pl.DataFrame"):
    """One row per (ticker, et_date): prev_close, prev_session_date, last-close/gain, day HOD."""
    return (ev.group_by("ticker", "et_date")
            .agg(prev_close=pl.col("prev_close").first(),
                 prev_session_date=pl.col("prev_session_date").first(),
                 last_close=pl.col("close").sort_by("timestamp").last(),
                 last_pct_gain=pl.col("pct_gain").sort_by("timestamp").last(),
                 rth_hod=pl.col("rth_hod").max())
            .filter(pl.col("last_close").is_not_null()))


def sample_ticker_days(ev: "pl.DataFrame", n: int, seed: int):
    """Deterministic seeded sample of N ticker-days, round-robin over ET dates (spread >= 5 dates)."""
    dt = _day_table(ev)
    # ponytail: polars group_by+iter_rows order is NOT stable across processes; sort canonically
    # by (et_date, ticker) so the seeded sample is reproducible run-to-run. High-pct_gain
    # preference is implicit — every event here is already rank-<=N (top-N) for its day.
    dt = dt.sort(["et_date", "ticker"])
    rng = random.Random(seed)
    by_date = {}
    for row in dt.iter_rows(named=True):
        by_date.setdefault(row["et_date"], []).append(row)
    dates = sorted(by_date.keys())
    rng.shuffle(dates)
    picked, used = [], set()
    while len(picked) < n and len(by_date) and len(dates) and any(
            (r["ticker"], d) not in used for d in dates for r in by_date[d]):
        for d in dates:
            avail = [r for r in by_date[d] if (r["ticker"], d) not in used]
            if avail:
                r = rng.choice(avail)
                picked.append(r)
                used.add((r["ticker"], d))
                if len(picked) >= n:
                    break
    # distinct dates actually sampled
    distinct_dates = sorted({r["et_date"] for r in picked})
    return picked, distinct_dates


def load_clean(clean_path: Path, tickers, et_str=ET_STR):
    """Subset of clean bars (ticker/timestamp/close/high) for the sampled tickers + et_date.

    ponytail: returns only sampled tickers scaled to ~100k rows, not the whole 400MB month.
    """
    lf = pl.scan_parquet(clean_path)
    lf = lf.select(["ticker", "timestamp", "close", "high"])
    lf = lf.filter(pl.col("ticker").is_in(sorted(tickers)))
    df = lf.collect()
    if df.height:
        df = df.with_columns(pl.col("timestamp").dt.convert_time_zone(et_str).dt.date().alias("et_date"))
    return df


def clean_last_close(clean, ticker, day):
    """True end-of-RTH (max-timestamp) close for (ticker, et_date) from clean bars, or None."""
    if clean is None:
        return None
    z = clean.filter((pl.col("ticker") == ticker) & (pl.col("et_date") == day))
    if z.height == 0:
        return None
    return float(z.sort("timestamp")["close"].tail(1)[0])


def clean_day_high(clean, ticker, day):
    """RTH day-high (max of high) for (ticker, et_date) from clean bars, or None."""
    if clean is None:
        return None
    z = clean.filter((pl.col("ticker") == ticker) & (pl.col("et_date") == day))
    if z.height == 0:
        return None
    return float(z["high"].max())


# ---------------------------------------------------------------- external fetchers

def _epoch_ns(dt) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def fetch_yf_daily(ticker, start, end, cache):
    """yfinance raw daily bars for ticker in [start, end). Cached per ticker."""
    if ticker in cache:
        return cache[ticker]
    import yfinance as yf
    df = None
    try:
        time.sleep(YF_SLEEP)
        df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
    except Exception:
        df = None
    cache[ticker] = df
    return df


def fetch_yf_splits(ticker, cache):
    """Raw split events (Series index=date, value=ratio). Cached per ticker."""
    if ticker in cache:
        return cache[ticker]
    import yfinance as yf
    s = None
    try:
        time.sleep(YF_SLEEP)
        s = yf.Ticker(ticker).splits
    except Exception:
        s = None
    cache[ticker] = s
    return s


def fetch_iex_bars(client, symbol, day: date, cache):
    """Alpaca IEX 1-min bars for the RTH window of `day`. Cached per (symbol, day)."""
    key = (symbol, day)
    if key in cache:
        return cache[key]
    df = None
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
        end = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                               start=start, end=end, feed=DataFeed.IEX)
        df = client.get_stock_bars(req).df
    except Exception:
        df = None
    cache[key] = df
    return df


def _ietz_dates(ds):
    """yfinance tz-aware DatetimeIndex -> list of python datetime.date (ET)."""
    return [d.date() for d in ds]


def split_factor(yf_split, ticker, asof_date):
    """Cumulative product of yfinance split ratios with ex-date STRICTLY AFTER asof_date.

    yfinance `history()` serves split-adjusted OHLC in BOTH auto_adjust modes (the bool gates
    dividends only), so the Close/High read for a ticker that later splits is the raw price
    already divided by the post-split ratio (CVNA 5:1 ex 2026-05-08 → 58.612 = raw/5). To
    recover the unadjusted price the pipeline records, multiply the yf price by the product of
    split ratios whose ex-date is strictly after the sampled session date.
    """
    sp = yf_split.get(ticker) if yf_split else None
    if sp is None:
        return 1.0
    f = 1.0
    for sd, ratio in zip(_ietz_dates(sp.index), sp.to_numpy()):
        if sd > asof_date:
            f *= float(ratio)
    return f


def yf_raw_price(yf_split, ticker, asof_date, price):
    """Split-unadjust a yfinance price to the raw (unadjusted) frame the pipeline stores."""
    return float(price) * split_factor(yf_split, ticker, asof_date)


# ---------------------------------------------------------------- checks

def check_prev_close(sample, yf_daily, yf_splits):
    """prev_close (our raw prior-session close) vs yfinance prior-session raw Close.

    Normalized: yfinance Close is split-adjusted in both auto_adjust modes, so the prior-session
    reference is multiplied by the cumulative future split ratio as of the prior session date.

    Two tolerances recorded: tight 0.1% (report stat) and operating 0.35%. Our value is
    the last 1-min print; yfinance Close is the official 16:00 closing-auction price, so
    a ~0.1% systematic gap is expected. Verdict PASS requires operating rate >= 95% AND
    median diff <= 0.05%; 0.1% stays as a diagnostics stat, not the passing gate.
    ponytail: fixed real threshold, don't over-parameterise.
    """
    n = n_match = n_err = n_op = 0
    recs = []
    for r in sample:
        t, d = r["ticker"], r["et_date"]
        p = r["prev_session_date"]
        yf = yf_daily.get(t)
        if yf is None or p is None:
            n_err += 1
            continue
        idx = _ietz_dates(yf.index)
        closes = yf["Close"].to_numpy()
        if p not in idx or closes[idx.index(p)] != closes[idx.index(p)]:  # NaN guard
            n_err += 1
            continue
        ref = yf_raw_price(yf_splits, t, p, float(closes[idx.index(p)]))
        our = float(r["prev_close"])
        if not ref or not our:
            n_err += 1
            continue
        diff_pct = (our - ref) / our * 100
        ok = abs(diff_pct / 100) <= TOL_PREV_CLOSE
        ok_op = abs(diff_pct / 100) <= TOL_PREV_CLOSE_OP
        n += 1
        if ok:
            n_match += 1
        if ok_op:
            n_op += 1
        recs.append({"ticker": t, "date": str(d), "our": our, "ref": ref,
                     "diff_pct": diff_pct, "match": bool(ok), "match_op": bool(ok_op),
                     "breakdown": (our, ref, diff_pct)})
    s = _summary("prev_close vs yfinance (last-print vs 16:00 auction)", n, n_match, n_err,
                 recs, sort_key="diff_pct")
    s["match_rate_op"] = round((n_op / n) if n else 0.0, 4)
    s["tol_operating"] = f"{TOL_PREV_CLOSE_OP:.2%} (vs {TOL_PREV_CLOSE:.1%} tight)"
    if s["verdict"] != "INSUFFICIENT":
        med = s["median_diff"]
        s["verdict"] = "PASS" if (n >= MIN_N and s["match_rate_op"] >= PASS_THRESHOLD
                                  and med is not None and med <= TOL_PREV_CLOSE_MEDIAN) else "FAIL"
    return s


def check_pct_gain(sample, yf_daily, clean, yf_splits):
    """End-of-RTH pct_gain vs yfinance (Close[D]-Close[P])/Close[P]*100.

    Both yf closes are split-adjusted in either auto_adjust mode, so each is normalized to the
    raw frame as of its own date (cumulative future split ratio) before the return is computed.

    The events file's per-ticker-day last bar is the last bar the ticker sat in top-N,
    often mid-session, so it is NOT the day close. We recompute the true end-of-RTH
    (max-timestamp) close from the clean parquet and compare that to yfinance's daily change.
    """
    n = n_match = n_err = 0
    recs = []
    for r in sample:
        t, d = r["ticker"], r["et_date"]
        p = r["prev_session_date"]
        yf = yf_daily.get(t)
        if yf is None or p is None:
            n_err += 1
            continue
        idx = _ietz_dates(yf.index)
        closes = yf["Close"].to_numpy()
        if d not in idx or p not in idx:
            n_err += 1
            continue
        cd = yf_raw_price(yf_splits, t, d, float(closes[idx.index(d)]))
        cp = yf_raw_price(yf_splits, t, p, float(closes[idx.index(p)]))
        if not cd or not cp:
            n_err += 1
            continue
        yf_ret = (cd - cp) / cp * 100
        eod = clean_last_close(clean, t, d)
        if eod is None or not eod:
            n_err += 1
            continue
        prev = float(r["prev_close"])
        if not prev:
            n_err += 1
            continue
        our_ret = (eod - prev) / prev * 100
        diff_pp = our_ret - yf_ret
        ok = abs(diff_pp) <= TOL_PCT_GAIN
        n += 1
        if ok:
            n_match += 1
        recs.append({"ticker": t, "date": str(d), "our_ret": our_ret,
                     "ref_ret": yf_ret, "diff_pp": diff_pp, "match": bool(ok),
                     "breakdown": (our_ret, yf_ret, diff_pp)})
    return _summary("pct_gain@16:00 vs yfinance (EOD close)", n, n_match, n_err, recs, sort_key="diff_pp")


def check_splits(sample, yf_split):
    """No sampled event ticker-day may coincide with a yfinance split ex-date."""
    n = n_match = n_err = 0
    recs = []
    seen = set()  # (ticker, date, ratio) — dedupe yfinance split rows
    for r in sample:
        t, d = r["ticker"], r["et_date"]
        sp = yf_split.get(t)
        if sp is None:
            n_err += 1
            continue
        split_dates = {q.date() for q in sp.index}
        bad = d in split_dates
        n += 1
        if not bad:
            n_match += 1
        recs.append({"ticker": t, "date": str(d), "split_on_event_date": bool(bad), "match": not bad})
        for sd, ratio in zip(_ietz_dates(sp.index), sp.to_numpy()):
            seen.add((t, sd, float(ratio)))
    splits_seen = [{"ticker": tt, "ex_date": str(sd), "ratio": rt}
                   for tt, sd, rt in sorted(seen, key=lambda x: (x[0], x[1]))]
    s = _summary("split cross-check (no event day on a split date)", n, n_match, n_err,
                 recs, sort_key="date")
    s["splits_seen"] = splits_seen
    return s


def _iex_row(rec, client, iexcache, clean, r):
    """Fill IEX-derived values into check4/5 record. Returns dict."""
    t, d = r["ticker"], r["et_date"]
    df = fetch_iex_bars(client, t, d, iexcache)
    if df is None or df.empty:
        rec["iex_high"] = None
        rec["iex_n"] = None
        rec["our_n"] = None
        rec["n_overlap"] = 0
        rec["median_bps"] = None
        rec["corr"] = None
        rec["match"] = False
        rec["skip"] = True
        return rec
    iex_high = float(df["high"].to_numpy().max())
    iex_n = int(len(df))
    rec["iex_high"] = iex_high
    rec["iex_n"] = iex_n
    # our bars for the day from clean file
    day = clean.filter((pl.col("ticker") == t) & (pl.col("et_date") == d))
    rec["our_n"] = int(day.height)
    rec["n_overlap"] = 0
    rec["median_bps"] = None
    rec["corr"] = None
    if day.height:
        # join on minute boundary (epoch ns)
        ts = df.index.get_level_values("timestamp")
        iex_df = pl.DataFrame({
            "minute": [_epoch_ns(x.to_pydatetime()) for x in ts],
            "iex_close": df["close"].to_numpy(),
        })
        our = day.with_columns(pl.col("timestamp").dt.truncate("1m").cast(pl.Int64).alias("minute"))
        merged = our.join(iex_df, on="minute", how="inner")
        rec["n_overlap"] = int(merged.height)
        if merged.height >= 2:
            import numpy as np
            our_c = merged["close"].to_numpy()
            iex_c = merged["iex_close"].to_numpy()
            bps = (our_c - iex_c) / iex_c * 10_000
            rec["median_bps"] = float(np.median(np.abs(bps)))
            rec["corr"] = float(np.corrcoef(our_c, iex_c)[0, 1])
    return rec


def check_rth_hod(sample, client, iexcache, clean, yf_daily, yf_splits):
    """Day-high: primary = clean RTH high vs yfinance daily High (consolidated, 0.3%).

    The yfinance daily High is split-adjusted in either auto_adjust mode, so it is multiplied by
    the cumulative future split ratio as of the sampled session date to recover the raw high.

    Secondary sanity = our rth_hod vs Alpaca IEX day-high. IEX is a narrower feed; thin
    per-day bar counts mean IEX day-high can miss the true consolidated high, so the IEX
    comparison is reported (with iex_n/our_n coverage) but the verdict rides on the
    consolidated yfinance High reference.
    """
    n = n_match = n_err = 0
    recs = []
    for r in sample:
        t, d = r["ticker"], r["et_date"]
        rec = {"ticker": t, "date": str(d)}
        # consolidated reference: clean day high vs yfinance daily High
        yf = yf_daily.get(t)
        yf_high = None
        if yf is not None:
            idx = _ietz_dates(yf.index)
            highs = yf["High"].to_numpy()
            if d in idx and highs[idx.index(d)] == highs[idx.index(d)]:
                yf_high = yf_raw_price(yf_splits, t, d, float(highs[idx.index(d)]))
        our = clean_day_high(clean, t, d)
        rec["our_day_high"] = our
        rec["yf_high"] = yf_high
        if our is None or yf_high is None or not our or not yf_high:
            n_err += 1
            recs.append(rec)
            continue
        diff_pct = (our - yf_high) / our * 100
        ok = abs(diff_pct / 100) <= TOL_RTH_HOD
        rec["diff_pct"] = diff_pct
        rec["match"] = bool(ok)
        rec["breakdown"] = (our, yf_high, diff_pct)
        n += 1
        if ok:
            n_match += 1
        # IEX sanity (coverage-note only, drives no verdict)
        rec = _iex_row(rec, client, iexcache, clean, r)
        if not rec.get("skip") and rec.get("iex_high"):
            rec["iex_diff_pct"] = (float(r["rth_hod"]) - rec["iex_high"]) / float(r["rth_hod"]) * 100
        recs.append(rec)
    return _summary("rth_hod vs consolidated yf High", n, n_match, n_err, recs, sort_key="diff_pct")


def check_path(sample, client, iexcache, clean):
    """1-min path sanity: median abs close diff (bps) + correlation on timestamp intersection."""
    n = n_match = n_err = 0
    recs = []
    for r in sample:
        t, d = r["ticker"], r["et_date"]
        rec = {"ticker": t, "date": str(d)}
        rec = _iex_row(rec, client, iexcache, clean, r)
        if rec.get("skip") or rec.get("n_overlap", 0) < 2 or rec.get("median_bps") is None:
            n_err += 1
            recs.append(rec)
            continue
        corr, bps = rec["corr"], rec["median_bps"]
        ok = not (corr < 0.8 and bps > 100)  # report, don't fail unless both
        rec["match"] = bool(ok)
        n += 1
        if ok:
            n_match += 1
        recs.append(rec)
    return _summary("1-min path (IEX vs clean)", n, n_match, n_err, recs, sort_key="median_bps")


# ---------------------------------------------------------------- verdicts / outputs

def _summary(name, n, n_match, n_err, recs, sort_key):
    rate = (n_match / n) if n else 0.0
    if n < MIN_N:
        verdict = "INSUFFICIENT"
        reason = ("network/data errors" if n_err > n else "insufficient sample")
    elif rate >= PASS_THRESHOLD:
        verdict = "PASS"
        reason = None
    else:
        verdict = "FAIL"
        reason = None
    diffs = [abs(r.get(sort_key)) for r in recs
             if isinstance(r.get(sort_key), (int, float))]
    median_diff = float(sorted(diffs)[len(diffs) // 2]) if diffs else None
    offenders = sorted(
        recs, key=lambda r: abs(r.get(sort_key)) if isinstance(r.get(sort_key), (int, float)) else 0.0,
        reverse=True)[:5]
    return {"name": name, "n_checked": n, "n_match": n_match, "match_rate": round(rate, 4),
            "median_diff": median_diff, "n_errors": n_err, "verdict": verdict,
            "reason": reason, "worst_offenders": offenders}


def write_outputs(artdir, month, sample_n, distinct_dates, checks, tolerances, caveat,
                  methodology=None, adjudication=None):
    artdir.mkdir(parents=True, exist_ok=True)
    payload = {"month": month, "sample_n": sample_n, "n_distinct_dates": len(distinct_dates),
               "distinct_dates": [str(d) for d in distinct_dates], "tolerances": tolerances,
               "methodology": methodology, "adjudication": adjudication, "checks": checks}
    (artdir / "external_checks.json").write_text(json.dumps(payload, indent=2, default=str))
    lines = [f"# External Verification — month {month}", "",
             f"Sample: {sample_n} ticker-days across {len(distinct_dates)} distinct ET dates "
             f"({', '.join(str(d) for d in distinct_dates)}).", "",
             "## Caveat", caveat, ""]
    if methodology:
        lines += ["## Methodology", methodology, ""]
    lines += ["## Tolerances", ""]
    for k, v in tolerances.items():
        lines.append(f"- **{k}**: {v}")
    if adjudication:
        lines += ["", "## Adjudication", ""]
        for a in adjudication:
            lines.append(f"- **{a['title']}**: {a['text']}")
    lines += ["", "## Check results", ""]
    for c in checks:
        lines.append(f"### {c['name']} — {c['verdict']}" + (f" ({c['reason']})" if c['reason'] else ""))
        lines.append(f"- checked: {c['n_checked']}, matched: {c['n_match']}, "
                     f"match_rate: {c['match_rate']:.2%}, median_diff: "
                     f"{c['median_diff'] if c['median_diff'] is not None else 'n/a'}")
        if c.get("match_rate_op") is not None:
            lines.append(f"- operating match_rate: {c['match_rate_op']:.2%} "
                         f"({c.get('tol_operating', 'n/a')})")
        if c["worst_offenders"]:
            lines.append("- worst offenders:")
            for o in c["worst_offenders"]:
                line = f"  - {o.get('ticker')} {o.get('date')}: "
                for key in ("diff_pct", "diff_pp", "median_bps", "iex_diff_pct"):
                    if key in o:
                        line += f"{key}={o[key]:.3f} "
                if isinstance(o.get("breakdown"), tuple) and len(o["breakdown"]) == 3:
                    b = o["breakdown"]
                    line += f"| {b[0]} | {b[1]} | {b[2]:.3f} "
                if o.get("iex_n") is not None and o.get("our_n") is not None:
                    line += f"iex_n={o['iex_n']}/our_n={o['our_n']}"
                lines.append(line.rstrip())
        lines.append("")
    if any(c["name"].startswith("split") for c in checks):
        sp = next(c for c in checks if c["name"].startswith("split"))
        lines.append("### Splits seen (yfinance)")
        for s in sp.get("splits_seen", []) or []:
            lines.append(f"- {s['ticker']} {s['ex_date']} ratio={s['ratio']}")
    (artdir / "external_verification_report.md").write_text("\n".join(lines) + "\n")
    return payload


# ---------------------------------------------------------------- main

def main():
    args = parse_args()
    artdir = Path(args.artdir) if args.artdir else Path(f"factory/artifacts/certification_{args.month}")
    events = load_events(artdir)
    if events is None:
        print("certification outputs not found — run certify_month.py first")
        return 0

    sample, distinct_dates = sample_ticker_days(events, args.sample, args.seed)
    if not sample:
        print("certification outputs not found — run certify_month.py first")
        return 0

    tickers = sorted({r["ticker"] for r in sample})
    print(f"Sampled {len(sample)} ticker-days over {len(distinct_dates)} dates; "
          f"{len(tickers)} tickers: {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")

    clean_path = Path(f"data/clean_ohlcv_{args.month}.parquet")
    clean = None
    if clean_path.exists():
        clean = load_clean(clean_path, tickers)
    else:
        print(f"  warning: clean bars missing ({clean_path}) — checks 4/5 limited")

    # ---- fetch external data once per ticker, batch ranges over the sampled dates
    yf_daily, yf_splits = {}, {}
    by_t = {}
    for r in sample:
        by_t.setdefault(r["ticker"], {"dates": [], "prev_dates": []})
        by_t[r["ticker"]]["dates"].append(r["et_date"])
        if r["prev_session_date"]:
            by_t[r["ticker"]]["prev_dates"].append(r["prev_session_date"])
    for t in tickers:
        dts = by_t[t]["dates"]
        prevs = by_t[t]["prev_dates"]
        start = min(prevs) - timedelta(days=5) if prevs else min(dts) - timedelta(days=5)
        end = max(dts) + timedelta(days=1)
        yf_daily[t] = fetch_yf_daily(t, start, end, {})
        yf_splits[t] = fetch_yf_splits(t, {})

    # Alpaca client (only if network used)
    client = None
    try:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        from alpaca.data.historical import StockHistoricalDataClient
        client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                           os.environ["ALPACA_SECRET_KEY"])
    except Exception:
        client = None

    iexcache = {}
    checks = [
        check_prev_close(sample, yf_daily, yf_splits),
        check_pct_gain(sample, yf_daily, clean, yf_splits),
        check_splits(sample, yf_splits),
        check_rth_hod(sample, client, iexcache, clean, yf_daily, yf_splits),
        check_path(sample, client, iexcache, clean),
    ]

    tolerances = {
        "prev_close_tight": f"|our_prev_close - yf_prev_close| / prev_close <= {TOL_PREV_CLOSE:.1%}",
        "prev_close_operating": f"|our_prev_close - yf_prev_close| / prev_close <= {TOL_PREV_CLOSE_OP:.2%} "
                                f"(PASS gate, with median diff <= {TOL_PREV_CLOSE_MEDIAN}%)",
        "pct_gain": f"|our_pct_gain - yf_day_change| <= {TOL_PCT_GAIN} percentage points",
        "rth_hod": f"|our_clean_day_high - yf_daily_high| / our_day_high <= {TOL_RTH_HOD:.1%} "
                   f"(consolidated yfinance High; IEX reported as sanity only)",
        "verdict": f"PASS if match_rate >= {PASS_THRESHOLD:.0%}, else FAIL; INSUFFICIENT if < {MIN_N} measured",
    }
    caveat = ("IEX is a smaller, unaudited exchange tape, not the consolidated tape our clean RTH "
              "bars use; thin IEX per-day bar counts mean its day-high can miss the true consolidated "
              "high, so the day-high verdict rides on the consolidated yfinance daily High (0.3%) and the "
              "IEX comparison is reported as sanity with per-day coverage (iex_n/our_n). The 1-min path "
              "check is report-only (non-failing below 0.8 correlation unless median close diff > 100 bps).")
    adjudication = [
        {"title": "pct_gain@16:00 mis-referenced",
         "text": "The events file's per-ticker-day last bar is the last bar the ticker sat in top-N, "
                 "often mid-session, so it was NOT the day close. Fixed to recompute the true "
                 "end-of-RTH (max-timestamp) close per sampled ticker-day from the clean parquet and "
                 "compare that against yfinance's daily change. Tolerance unchanged (0.5pp)."},
        {"title": "prev_close tolerance",
         "text": "Our prev_close is the last 1-min print of the prior session; yfinance Close is the "
                 "official 16:00 closing-auction price, so a systematic ~0.1% gap is expected. The 0.1% "
                 "tolerance is retained as a diagnostics stat; the PASS gate is operating tolerance "
                 "0.35% (median diff <= 0.05%). 0.5pp on check 2 absorbs this gap."},
        {"title": "rth_hod reference",
         "text": "The previous IEX-only day-high reference undercounted on thin IEX coverage (DXYZ 20/390, "
                 "PHAT 263/381, PLAY 332/385). The verdict now rides on the consolidated yfinance daily High "
                 "(0.3%); the IEX comparison is reported as sanity with per-day iex_n/our_n coverage noted."},
        {"title": "split normalization",
         "text": "yfinance `history()` serves split-adjusted OHLC in BOTH auto_adjust modes (the bool gates "
                 "dividends only, not splits), so a Close/High read for a ticker that later splits is the raw "
                 "price already divided by the post-split ratio (CVNA 5:1 ex 2026-05-08 -> 58.612 = raw/5). "
                 "The three price-comparison checks therefore multiply each yfinance reference by the "
                 "cumulative future split ratio — the product of split ratios whose ex-date is STRICTLY AFTER "
                 "the sampled session date — to recover the unadjusted frame our pipeline stores."},
    ]
    methodology = ("yfinance `history()` serves split-adjusted OHLC in BOTH auto_adjust modes — the bool "
                   "gates dividend adjustment only, not splits — so for any ticker that later splits, the "
                   "Close/High reference is the raw price already divided by the post-split ratio (e.g. "
                   "CVNA 5:1 ex 2026-05-08 -> yf 58.612 = raw 292.35 / 5). Each of the three "
                   "price-comparison references (prev_close, pct_gain close, rth_hod high) is therefore "
                   "normalized to the unadjusted frame by multiplying by the cumulative future split ratio: "
                   "the product of split ratios whose ex-date is strictly after the sampled session date "
                   "(CVNA: 58.612 x 5.0 = 293.06 vs our 292.35 -> ~0.24%). Split cross-check and 1-min "
                   "path checks are unaffected.")
    payload = write_outputs(artdir, args.month, len(sample), distinct_dates, checks, tolerances,
                            caveat, methodology=methodology, adjudication=adjudication)
    for c in checks:
        print(f"  {c['name']}: {c['verdict']} checked={c['n_checked']} "
              f"match_rate={c['match_rate']:.0%}")
    print(f"Wrote {artdir / 'external_checks.json'} and {artdir / 'external_verification_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
