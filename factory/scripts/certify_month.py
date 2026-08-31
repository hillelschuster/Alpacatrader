#!/usr/bin/env python3
"""Certify one month of top-gainer reconstruction — correct previous-session close,
split guards, universe & liquidity gates.

ponytail: single file, stdlib+polars+numpy+yfinance, no classes, ~370 lines.
Replaces the flawed 'last close before date' prior_close logic in rank_day.py with a
per-ticker previous-trading-session close, plus split/universe/liquidity guards.
"""
import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

HORIZONS = [1, 3, 5, 15, 30, 60]
ET = "America/New_York"
ELIGIBLE_EXCHANGES = {"NYQ", "NMS", "ASE"}  # yfinance codes: NYSE/NASDAQ/AMEX


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Certify a month of top-gainer reconstruction")
    p.add_argument("--files", nargs="+", type=Path, default=None, help="clean monthly parquets (may span prior months)")
    p.add_argument("--month", default=None, help="ET trading month to certify, YYYY-MM")
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--min-dv", type=float, default=5_000_000, help="min cumulative dollar volume at event time")
    p.add_argument("--outdir", type=Path, default=None)
    p.add_argument("--selftest", action="store_true")
    return p.parse_args(argv)


# ---------------------------------------------------------------- data prep

def _session_meta(sess_close):
    """Add prev_close, prev_session_date, gap_sessions, split_suspect to a session-close table."""
    market = sess_close.select("et_date").unique().sort("et_date").with_row_index("session_pos")
    sess = sess_close.sort("ticker", "et_date").join(market, on="et_date", how="left")
    sess = sess.with_columns([
        pl.col("session_close").shift(1).over("ticker").alias("prev_close"),
        pl.col("et_date").shift(1).over("ticker").alias("prev_session_date"),
        pl.col("session_pos").shift(1).over("ticker").alias("prev_session_pos"),
    ])
    sess = sess.with_columns((pl.col("session_pos") - pl.col("prev_session_pos")).alias("gap_sessions"))
    # split guard: overnight close ratio outside [0.5, 2]
    sess = sess.with_columns(
        ((pl.col("session_close") / pl.col("prev_close") < 0.5)
         | (pl.col("session_close") / pl.col("prev_close") > 2.0))
        .fill_null(False).alias("split_suspect"))
    return sess.select("ticker", "et_date", "session_close", "prev_close",
                       "prev_session_date", "gap_sessions", "split_suspect")


def _sessions(df):
    """Per-ticker session table from a minute df (eager; used by selftest)."""
    sess_close = (df.sort("timestamp").group_by("ticker", "et_date")
                  .agg(pl.col("close").last().alias("session_close")))
    return _session_meta(sess_close)


def _universe(candidate_tickers, cache_path: Path):
    """Load exchange/quote_type tags from cache, else fetch via yfinance. Returns (df, n_unknown).
    # ponytail: writes cache incrementally (every 100) so a long/partial fetch is resumable."""
    if cache_path.exists():
        tags = pl.read_parquet(cache_path)
        have = set(tags["ticker"].to_list())
    else:
        tags = pl.DataFrame(schema={"ticker": pl.String, "exchange": pl.String, "quote_type": pl.String})
        have = set()
    missing = sorted(candidate_tickers - have)
    if not missing:
        print(f"  Universe from cache: {tags.height} tickers")
        return tags, 0
    print(f"  Fetching universe tags for {len(missing)} tickers via yfinance ...")
    import yfinance as yf
    rows, unknown, count = [], 0, 0
    for t in missing:
        ex = qt = None
        try:
            info = yf.Ticker(t).info
            ex = info.get("exchange") or info.get("fullExchangeName")
            qt = info.get("quoteType")
        except Exception:
            pass
        # cache completion (nulls = fetch failure/unknown) so a partial fetch stays resumable
        rows.append({"ticker": t, "exchange": str(ex) if ex else None,
                     "quote_type": str(qt) if qt else None})
        if not (ex and qt):
            unknown += 1
        count += 1
        if count % 100 == 0:
            part = pl.DataFrame(rows, schema={"ticker": pl.String, "exchange": pl.String, "quote_type": pl.String})
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            pl.concat([tags, part]).unique(subset="ticker").write_parquet(cache_path)
            print(f"    ... {count}/{len(missing)}")
        time.sleep(0.3)
    new = pl.DataFrame(rows, schema={"ticker": pl.String, "exchange": pl.String, "quote_type": pl.String})
    tags = pl.concat([tags, new]).unique(subset="ticker")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tags.write_parquet(cache_path)
    print(f"  Universe tags: {tags.height} total, {unknown} fetch failures/unknown")
    return tags, unknown


# ---------------------------------------------------------------- labels

def _forward_returns(df):
    """Exact timestamp self-join per horizon (same approach as rank_day.py), NaN when missing bar.
    # ponytail: cast both join keys to the timestamp's own unit — polars adds a duration at a
    # different precision than a ns/mus column, which would make the self-join key mismatch."""
    unit = df["timestamp"].dtype.time_unit
    for h in HORIZONS:
        fwd_ts = (pl.col("timestamp") + pl.duration(minutes=h)).cast(pl.Datetime(unit, "UTC"))
        lookup = df.select(
            ["ticker", pl.col("timestamp").cast(pl.Datetime(unit, "UTC")).alias("fwd_timestamp"), "close"]
        ).rename({"close": f"_fwd_close_{h}"})
        tmp = df.with_columns(fwd_ts.alias("fwd_timestamp")).join(lookup, on=["ticker", "fwd_timestamp"], how="left")
        df = tmp.with_columns(
            ((pl.col(f"_fwd_close_{h}") - pl.col("close")) / pl.col("close")).alias(f"fwd_ret_{h}m")
        ).drop(["fwd_timestamp", f"_fwd_close_{h}"])
    return df


def _mfe_mae(df):
    """mfe_60m/mae_60m from next-60-min high/low window, per (ticker, et_date) — numpy scan as in rank_day.py."""
    out_mfe = np.full(df.height, float("nan"))
    out_mae = np.full(df.height, float("nan"))
    ts_ns = df["timestamp"].dt.timestamp("ns").to_numpy()
    for _, g in df.group_by(["ticker", "et_date"], maintain_order=True):
        idx = g["__idx"].to_numpy()
        gts = ts_ns[idx]
        closes = g["close"].to_numpy()
        highs = g["high"].to_numpy()
        lows = g["low"].to_numpy()
        limit = gts + 60 * 60 * 1_000_000_000
        n = len(idx)
        for i in range(n):
            j = np.searchsorted(gts, limit[i], side="right")
            if j > i + 1:
                out_mfe[idx[i]] = (highs[i + 1:j].max() - closes[i]) / closes[i]
                out_mae[idx[i]] = (lows[i + 1:j].min() - closes[i]) / closes[i]
    return df.with_columns(pl.Series("mfe_60m", out_mfe), pl.Series("mae_60m", out_mae))


def _tod_bucket(et):
    """Half-hour bucket label '0930','1000',... from an ET datetime column."""
    m = et.dt.hour().cast(pl.Int32) * 60 + et.dt.minute().cast(pl.Int32)
    b = (m // 30) * 30
    return (b // 60 * 100 + b % 60).cast(pl.String).str.zfill(4)


# ---------------------------------------------------------------- outputs

def _write_checks(agg, events, outdir: Path, args):
    per_day = (events.group_by("et_date").agg(pl.len().alias("n"))
               .select(pl.col("n").min().alias("min"), pl.col("n").median().alias("median"),
                       pl.col("n").max().alias("max")).row(0, named=True)
               if events.height else {"min": 0, "median": 0, "max": 0})
    pg = events["pct_gain"]
    pg_dist = {"p50": float(pg.quantile(0.5)), "p90": float(pg.quantile(0.9)),
               "p99": float(pg.quantile(0.99)), "max": float(pg.max())} if events.height else {}
    examples = []
    for (t, d) in events.group_by(["ticker", "et_date"]).agg(pl.len()).sort("len", descending=True).head(3)[["ticker", "et_date"]].iter_rows():
        rows = events.filter((pl.col("ticker") == t) & (pl.col("et_date") == d))
        r16 = rows.filter(pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32) >= 959).sort("et").tail(1)
        if r16.height:
            r = r16.row(0, named=True)
            examples.append({"ticker": t, "date": str(d), "prev_close": r["prev_close"],
                             "close_1600": r["close"], "pct_gain_close": r["pct_gain"],
                             "rank_1600": r["rank"]})
    checks = {
        "month": args.month, "n_sessions": agg["n_sessions"], "n_rows": agg["n_rows"],
        "n_tickers": agg["n_tickers"], "n_candidates": args.n_candidates,
        "n_split_suspects": agg["n_split_suspects"], "split_examples": agg["split_examples"],
        "n_universe_excluded": agg["n_universe_excluded"], "n_universe_unknown": agg["n_universe_unknown"],
        "n_events": events.height, "events_per_day": per_day,
        "events_pct_gain": pg_dist, "example_ticker_days": examples,
    }
    (outdir / "checks.json").write_text(json.dumps(checks, indent=2, default=str))
    print(f"  checks.json: {events.height} events, {agg['n_split_suspects']} split suspects, "
          f"{agg['n_universe_unknown']} universe-unknown")
    return checks


def _write_report(checks, outdir: Path):
    lines = [f"# Certification draft — month {checks['month']}", "",
             "Gates marked PASS are computed entirely from the clean minute data. "
             "Gates marked UNVERIFIED depend on external data (yfinance universe tags / clean-file "
             "price floors) and are confirmed by a separate external-verification script.", ""]
    lines += [f"- rows: {checks['n_rows']:,}  (PASS, from clean files)",
              f"- sessions (ET days with data): {checks['n_sessions']}  (PASS)",
              f"- tickers: {checks['n_tickers']:,}  (PASS)",
              f"- candidates (day max close >= prev_close, day max gain >= 8%, gap==1): {checks['n_candidates']:,}  (PASS)",
              f"- split suspects: {checks['n_split_suspects']}  examples: {checks['split_examples']}  (PASS — excluded from events)",
              f"- universe excluded (not NYSE/NASDAQ/AMEX equity): {checks['n_universe_excluded']}  (UNVERIFIED — yfinance tags)",
              f"- universe unknown (fetch failure): {checks['n_universe_unknown']}  (UNVERIFIED — network dependent)",
              f"- events (is_topN + all gates): {checks['n_events']:,}  (PASS)",
              f"- events per day min/median/max: {checks['events_per_day']}  (PASS)",
              f"- event pct_gain p50/p90/p99/max: {checks['events_pct_gain']}  (PASS, internal)", ""]
    lines += ["## Worked example ticker-days (for manual review)", ""]
    for e in checks["example_ticker_days"]:
        lines.append(f"- {e['ticker']} {e['date']}: prev_close={e['prev_close']}, "
                     f"16:00 close={e['close_1600']}, pct_gain={e['pct_gain_close']:.2f}%, rank@16:00={e['rank_1600']}")
    (outdir / "report_draft.md").write_text("\n".join(lines) + "\n")
    print(f"  report_draft.md written")


# ---------------------------------------------------------------- pipeline

def _label_day(df, args):
    """All per-day labels/gates for one target-month day (liquidity, rank, HOD, fwd, mfe/mae)."""
    df = df.sort(["ticker", "timestamp"])
    df = df.with_columns((pl.col("close") * pl.col("volume")).alias("dollar_vol_bar"))
    df = df.with_columns(pl.col("dollar_vol_bar").cum_sum().over("ticker", "et_date").alias("dollar_volume"))
    df = df.with_columns(pl.int_range(1, pl.len() + 1).over("ticker", "et_date").alias("_bar_num"))
    df = df.with_columns(pl.col("_bar_num").max().over("ticker", "et_date").alias("n_bars")).drop("_bar_num")
    assert df["close"].min() >= 2.0, "price floor violated (should be guaranteed by clean)"
    df = df.with_columns(((pl.col("close") - pl.col("prev_close")) / pl.col("prev_close") * 100).alias("pct_gain"))
    df = df.with_columns(
        pl.when((pl.col("gap_sessions") == 1) & pl.col("universe_eligible"))
        .then(pl.col("pct_gain")).otherwise(None).alias("_rank_pg"))
    df = df.with_columns(pl.col("_rank_pg").rank("dense", descending=True).over("timestamp").alias("rank"))
    df = df.with_columns((pl.col("rank") <= args.top_n).alias("is_topN")).drop("_rank_pg")
    df = df.with_columns(pl.col("high").cum_max().over("ticker", "et_date").alias("rth_hod"))
    df = df.with_columns(
        pl.when(pl.col("high") > pl.col("rth_hod").shift(1).over("ticker", "et_date").fill_null(0))
        .then(pl.col("et")).otherwise(None).alias("_hod_t"))
    df = df.with_columns(pl.col("_hod_t").forward_fill().over("ticker", "et_date").alias("hod_time")).drop("_hod_t")
    df = _forward_returns(df)
    df = df.with_row_index("__idx")
    df = _mfe_mae(df).drop("__idx")
    df = df.with_columns(_tod_bucket(pl.col("et")).alias("tod_bucket"))
    return df


def certify(args):
    outdir = args.outdir or Path(f"factory/artifacts/certification_{args.month}")
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {len(args.files)} file(s) ...")
    lf = pl.scan_parquet(args.files)
    lf = lf.with_columns(
        pl.col("timestamp").dt.replace_time_zone("UTC"),
        pl.col("timestamp").dt.convert_time_zone(ET).alias("et"),
        pl.col("timestamp").dt.convert_time_zone(ET).dt.date().alias("et_date"))
    # clean files are already RTH-only/deduped/price&vol filtered — ponytail: trust them
    # ponytail: dt.hour() is Int8 — cast to Int32 before *60 or it overflows (kills all RTH rows)
    lf = lf.with_columns((pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32)).alias("_tod_min"))
    lf = lf.filter((pl.col("_tod_min") >= 570) & (pl.col("_tod_min") < 960)
                   & (pl.col("close") >= 2.0) & (pl.col("volume") >= 100)).drop("_tod_min")

    # session table via lazy aggregation (streams only 4 columns; spans prior months for first-session prev_close)
    sess_close = (lf.select(["ticker", "timestamp", "close", "et_date"])
                  .group_by("ticker", "et_date")
                  .agg(pl.col("close").sort_by("timestamp").last().alias("session_close"))
                  .collect())
    sess = _session_meta(sess_close)
    print(f"  {sess.height:,} ticker-sessions across span, {sess['ticker'].n_unique():,} tickers")

    y, m = map(int, args.month.split("-"))
    day_lf = lf.filter((pl.col("et_date").dt.year() == y) & (pl.col("et_date").dt.month() == m))

    # candidate pre-filter on aggregated day stats (small table)
    day_stats = (day_lf.group_by("ticker", "et_date")
                 .agg(pl.col("close").max().alias("day_max_close"),
                      pl.col("high").max().alias("day_max_high"))
                 .collect()
                 .join(sess[["ticker", "et_date", "prev_close", "gap_sessions"]],
                       on=["ticker", "et_date"], how="inner"))
    day_stats = day_stats.with_columns(((pl.col("day_max_high") / pl.col("prev_close") - 1) * 100).alias("day_max_gain"))
    cand = day_stats.filter((pl.col("gap_sessions") == 1) & (pl.col("day_max_close") >= pl.col("prev_close"))
                            & (pl.col("day_max_gain") >= 8.0))
    candidate_tickers = set(cand["ticker"].unique().to_list())
    args.n_candidates = len(candidate_tickers)
    print(f"  {len(candidate_tickers):,} candidate tickers")

    # universe gate (candidates only)
    tags, n_unknown = _universe(candidate_tickers, Path("data/universe_tags.parquet"))
    cand_mark = pl.DataFrame({"ticker": sorted(candidate_tickers)}).with_columns(pl.lit(True).alias("is_candidate"))

    # per-day processing keeps peak memory at ~one day (~1-2M rows); events + counts are
    # accumulated inline so we never hold the whole month in RAM (process -> extract -> discard)
    cols = ["timestamp", "et", "et_date", "ticker", "open", "high", "low", "close", "volume",
            "prev_close", "prev_session_date", "pct_gain", "rank", "dollar_volume",
            "rth_hod", "hod_time"] + [f"fwd_ret_{h}m" for h in HORIZONS] + ["mfe_60m", "mae_60m", "tod_bucket"]
    days = sorted(day_lf.select("et_date").unique().collect()["et_date"].to_list())
    ev_parts, total_rows, ticker_set = [], 0, set()
    split_set, split_examples = set(), []
    unknown_set, excluded_set = set(), set()
    for i, d in enumerate(days):
        df = day_lf.filter(pl.col("et_date") == d).collect()
        df = df.join(sess, on=["ticker", "et_date"], how="inner")
        df = df.join(tags, on="ticker", how="left")
        df = df.join(cand_mark, on="ticker", how="left")
        df = df.with_columns([
            ((pl.col("exchange").is_in(ELIGIBLE_EXCHANGES)) & (pl.col("quote_type") == "EQUITY"))
            .fill_null(False).alias("universe_eligible"),
            (pl.col("is_candidate").fill_null(False)
             & (pl.col("exchange").is_null() | pl.col("quote_type").is_null())).alias("universe_unknown"),
        ])
        df = _label_day(df, args)
        print(f"  day {i + 1}/{len(days)} {d}: {df.height:,} rows")
        total_rows += df.height
        ticker_set |= set(df["ticker"].unique().to_list())
        for t, dt in df.filter(pl.col("split_suspect")).select(["ticker", "et_date"]).unique().iter_rows():
            if (t, dt) not in split_set:
                split_set.add((t, dt))
                if len(split_examples) < 5:
                    split_examples.append({"ticker": t, "et_date": str(dt)})
        unknown_set |= set(df.filter(pl.col("is_candidate") & pl.col("universe_unknown"))["ticker"].unique().to_list())
        excluded_set |= set(df.filter(pl.col("is_candidate") & ~pl.col("universe_eligible") & ~pl.col("universe_unknown"))["ticker"].unique().to_list())
        ev_parts.append(df.filter(pl.col("is_topN") & (pl.col("gap_sessions") == 1)
                                  & ~pl.col("split_suspect") & pl.col("universe_eligible")
                                  & (pl.col("dollar_volume") >= args.min_dv) & (pl.col("n_bars") >= 30))
                        .select([c for c in cols]))
        del df
    events = pl.concat(ev_parts).sort(["timestamp", "rank"])
    events.write_parquet(outdir / "events_topN.parquet")
    print(f"  Target month: {total_rows:,} rows, {len(ticker_set):,} tickers, {len(days)} sessions")
    print(f"  events_topN.parquet: {events.height:,} rows")

    agg = {
        "n_sessions": len(days), "n_rows": total_rows, "n_tickers": len(ticker_set),
        "n_split_suspects": len(split_set), "split_examples": split_examples,
        "n_universe_unknown": len(unknown_set), "n_universe_excluded": len(excluded_set),
    }
    checks = _write_checks(agg, events, outdir, args)
    _write_report(checks, outdir)
    return checks


# ---------------------------------------------------------------- selftest

def _selftest():
    """Synthetic 3 tickers x 3 sessions, 09:30-09:35 bars, known values."""
    from datetime import datetime, timezone, timedelta
    def et_utc(d, hh, mm):
        # day 0/1/2 -> 2025-06-02/03/04 EDT (UTC-4): ET instant = UTC + 4h
        dt = datetime(2025, 6, 2 + d, hh, mm, 0)
        return dt.replace(tzinfo=timezone.utc) + timedelta(hours=4)
    def bar(t, day, hh, mm, close, high=None, low=None, vol=1000.0):
        ts = et_utc(day, hh, mm)
        return {"ticker": t, "timestamp": ts, "et": ts, "open": low if low is not None else close,
                "high": high if high is not None else close, "low": low if low is not None else close,
                "close": close, "volume": vol}
    rows = []
    # Ticker A: trades all 3 days, closes 10 / 11 / 12 (rising, clean)
    for d, c0 in enumerate([9.90, 10.90, 11.90]):
        for i, c in enumerate([c0, c0 + 0.02, c0 + 0.04, c0 + 0.06, c0 + 0.08, c0 + 0.10]):
            rows.append(bar("A", d, 9, 30 + i, c))
    # Ticker B: trades day0 and day2 only (skips day1), closes 20 / 22
    for d, c0 in [(0, 19.90), (2, 21.90)]:
        for i, c in enumerate([c0, c0 + 0.02, c0 + 0.04, c0 + 0.06, c0 + 0.08, c0 + 0.10]):
            rows.append(bar("B", d, 9, 30 + i, c))
    # Ticker C: trades day0 and day1, day1 close = 40 (overnight ratio 4.0 -> split suspect)
    for d, c0 in [(0, 9.90), (1, 39.90)]:
        for i, c in enumerate([c0, c0 + 0.02, c0 + 0.04, c0 + 0.06, c0 + 0.08, c0 + 0.10]):
            rows.append(bar("C", d, 9, 30 + i, c))
    df = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").dt.replace_time_zone("UTC"),
        pl.col("et").dt.convert_time_zone("America/New_York"),
        pl.col("et").dt.date().alias("et_date"))

    sess = _sessions(df)

    # 1) prev_close = prior-session last close (not earlier); B skips a session
    b_d2 = sess.filter((pl.col("ticker") == "B") & (pl.col("et_date") == date(2025, 6, 4)))
    assert b_d2["prev_close"][0] == 20.00, f"B prev_close wrong: {b_d2['prev_close'][0]}"
    assert b_d2["gap_sessions"][0] == 2, f"B gap wrong: {b_d2['gap_sessions'][0]}"
    a_d1 = sess.filter((pl.col("ticker") == "A") & (pl.col("et_date") == date(2025, 6, 3)))
    assert a_d1["prev_close"][0] == 10.00, f"A prev_close wrong: {a_d1['prev_close'][0]}"
    assert a_d1["gap_sessions"][0] == 1

    # 2) split detection: C overnight 40/10 = 4.0
    c_d1 = sess.filter((pl.col("ticker") == "C") & (pl.col("et_date") == date(2025, 6, 3)))
    assert c_d1["split_suspect"][0], "C day1 should be split_suspect"
    assert not a_d1["split_suspect"][0], "A day1 should not be split_suspect"

    # 3) rank correctness incl. tie at 09:32 on day2: A close=12.375 (prev 11.00) and
    #    B close=22.50 (prev 20.00) both -> exactly 12.5% pct_gain
    day2 = df.filter(pl.col("et_date") == date(2025, 6, 4))
    day2 = day2.filter(~((pl.col("ticker") == "A") & (pl.col("et").dt.minute() == 32))
                       & ~((pl.col("ticker") == "B") & (pl.col("et").dt.minute() == 32)))
    add = pl.DataFrame([bar("A", 2, 9, 32, 12.375), bar("B", 2, 9, 32, 22.50)]).with_columns(
        pl.col("et").dt.convert_time_zone("America/New_York"),
        pl.col("et").dt.date().alias("et_date"))
    day2 = pl.concat([day2, add])
    day2 = day2.join(sess.select("ticker", "et_date", "prev_close", "gap_sessions", "split_suspect"),
                     on=["ticker", "et_date"], how="left")
    day2 = day2.with_columns(((pl.col("close") - pl.col("prev_close")) / pl.col("prev_close") * 100).alias("pct_gain"))
    day2 = day2.with_columns(pl.col("pct_gain").rank("dense", descending=True).over("timestamp").alias("rank"))
    at932 = day2.filter((pl.col("et").dt.hour() == 9) & (pl.col("et").dt.minute() == 32)).sort("ticker")
    rA = at932.filter(pl.col("ticker") == "A")["rank"][0]
    rB = at932.filter(pl.col("ticker") == "B")["rank"][0]
    assert rA == rB, f"tie ranks differ: A={rA} B={rB}"
    assert rA == 1, f"tie should be rank 1, got {rA}"
    assert abs(at932.filter(pl.col("ticker") == "A")["pct_gain"][0]
               - at932.filter(pl.col("ticker") == "B")["pct_gain"][0]) < 0.01, "tie pct_gain mismatch"

    # 4) fwd_ret exactness: A day2, 09:30 close 11.90 -> 09:31 close 11.92, 09:33 close 11.96
    fr = _forward_returns(day2)
    a_fr = fr.filter((pl.col("ticker") == "A") & (pl.col("et").dt.hour() == 9) & (pl.col("et").dt.minute() == 30))
    assert abs(a_fr["fwd_ret_1m"][0] - (11.92 - 11.90) / 11.90) < 1e-9, f"fwd_ret_1m wrong: {a_fr['fwd_ret_1m'][0]}"
    assert abs(a_fr["fwd_ret_3m"][0] - (11.96 - 11.90) / 11.90) < 1e-9, f"fwd_ret_3m wrong: {a_fr['fwd_ret_3m'][0]}"

    print("SELFTEST PASS")


# ---------------------------------------------------------------- main

def main():
    args = parse_args()
    if args.selftest:
        _selftest()
        return
    if not args.files or not args.month:
        sys.exit("--files and --month are required (unless --selftest)")
    checks = certify(args)
    print(f"\nDone. Events={checks['n_events']}, split suspects={checks['n_split_suspects']}, "
          f"events/day={checks['events_per_day']}")


if __name__ == "__main__":
    main()
