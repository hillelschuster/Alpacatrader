"""Chronological per-minute top-gainer ranker — ponytail minimal."""
import argparse
from pathlib import Path
import polars as pl

HORIZONS = [1, 3, 5, 15, 30, 60]

def rank_day(fpath: Path, date_str: str, top_n: int = 20, out: Path | None = None, events_only: bool = False) -> pl.DataFrame:
    target = pl.date(*map(int, date_str.split("-")))  # used for filtering via et_date
    print(f"Loading {fpath.name} ...")
    df = pl.read_parquet(fpath)
    # ensure UTC
    if df["timestamp"].dtype.time_zone is None:
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    # ET column
    df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"))
    df = df.with_columns(pl.col("et").dt.date().alias("et_date"))
    # dedup & price floor if not already cleaned (idempotent)
    df = df.unique(subset=["timestamp", "ticker"], keep="first")
    # RTH filter
    df = df.filter((pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32) >= 570) & (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32) < 960))
    df = df.filter(pl.col("close") >= 2.0)
    df = df.filter(pl.col("volume") >= 1)  # keep any volume; cleaner uses 100 but don't be stricter here

    # split prior vs day
    target_py = __import__("datetime").date(*map(int, date_str.split("-")))
    prior = df.filter(pl.col("et_date") < target_py)
    day = df.filter(pl.col("et_date") == target_py)
    if day.height == 0:
        raise SystemExit(f"No bars for {date_str} (ET) in file")
    print(f"  Prior universe: {prior.height:,} rows, {prior['ticker'].n_unique():,} tickers")
    print(f"  Target day {date_str}: {day.height:,} rows, {day['ticker'].n_unique():,} tickers")

    # prior close = last close before target date per ticker
    if prior.height > 0:
        prior_close = prior.sort("timestamp").group_by("ticker").agg(pl.col("close").last().alias("prior_close"))
    else:
        prior_close = pl.DataFrame({"ticker": [], "prior_close": []}, schema={"ticker": pl.String, "prior_close": pl.Float64})
    # ponytail: drop tickers with no prior close rather than fabricate
    day = day.join(prior_close, on="ticker", how="inner")
    if day.height == 0:
        raise SystemExit("No tickers with prior close — cannot rank")
    print(f"  After prior_close join: {day.height:,} rows, {day['ticker'].n_unique():,} tickers")

    # pct_gain & dollar vol
    day = day.with_columns(
        ((pl.col("close") - pl.col("prior_close")) / pl.col("prior_close") * 100).alias("pct_gain"),
        (pl.col("close") * pl.col("volume")).alias("dollar_vol_bar"),
    )
    day = day.sort(["timestamp", "ticker"])
    # cumulative dollar volume per ticker intraday
    day = day.with_columns(pl.col("dollar_vol_bar").cum_sum().over("ticker").alias("dollar_volume"))
    # rank per minute by pct_gain desc
    day = day.with_columns(pl.col("pct_gain").rank("dense", descending=True).over("timestamp").alias("rank"))
    day = day.with_columns((pl.col("rank") <= top_n).alias("is_topN"))

    # forward labels: join on timestamp+delta per horizon (exact minute, no leakage)
    # Build lookup dict from day itself (ticker, timestamp) -> close/high/low
    for h in HORIZONS:
        fwd_ts = (pl.col("timestamp") + pl.duration(minutes=h)).cast(pl.Datetime("ns", "UTC"))
        lookup = day.select(["ticker", "timestamp", "close", "high", "low"]).rename({"timestamp": "fwd_timestamp", "close": f"_fwd_close_{h}", "high": f"_fwd_high_{h}", "low": f"_fwd_low_{h}"})
        tmp = day.with_columns(fwd_ts.alias("fwd_timestamp"))
        tmp = tmp.join(lookup, on=["ticker", "fwd_timestamp"], how="left")
        day = tmp.with_columns(((pl.col(f"_fwd_close_{h}") - pl.col("close")) / pl.col("close")).alias(f"fwd_ret_{h}m")).drop(["fwd_timestamp", f"_fwd_close_{h}", f"_fwd_high_{h}", f"_fwd_low_{h}"])

    # MFE/MAE within 60m window if cheap: max high / min low of next 60 bars whose timestamp within 60m
    # ponytail: per-ticker python loop over sorted numpy arrays — fast enough for one day (~hundreds k rows)
    import numpy as np
    # compute mfe/mae via grouped apply
    def add_mfe_mae(pdf: pl.DataFrame) -> pl.DataFrame:
        # pdf is already day sorted by timestamp
        out_mfe = np.full(pdf.height, float("nan"))
        out_mae = np.full(pdf.height, float("nan"))
        # group by ticker
        for _, g in pdf.group_by("ticker", maintain_order=True):
            idx = g["__idx"].to_numpy()
            ts = g["timestamp"].to_numpy()  # datetime64 ns
            closes = g["close"].to_numpy()
            highs = g["high"].to_numpy()
            lows = g["low"].to_numpy()
            # convert ts to int64 ns for delta
            ts_ns = g["timestamp"].dt.timestamp("ns").to_numpy() if hasattr(g["timestamp"], "dt") else np.array([t.timestamp()*1e9 for t in ts])
            # brute O(n*window) but window small
            n = len(idx)
            for i in range(n):
                # find j where ts[j] <= ts[i]+60m and j>i
                limit = ts_ns[i] + 60 * 60 * 1_000_000_000
                # slice ahead
                j_end = i + 1
                while j_end < n and ts_ns[j_end] <= limit:
                    j_end += 1
                if j_end > i + 1:
                    window_high = highs[i+1:j_end].max()
                    window_low = lows[i+1:j_end].min()
                    out_mfe[idx[i]] = (window_high - closes[i]) / closes[i] if closes[i] else float("nan")
                    out_mae[idx[i]] = (window_low - closes[i]) / closes[i] if closes[i] else float("nan")
        pdf = pdf.with_columns([pl.Series("mfe_60m", out_mfe), pl.Series("mae_60m", out_mae)])
        return pdf.drop("__idx")

    day = day.with_row_index("__idx")
    day = add_mfe_mae(day)

    # select output columns
    keep = ["timestamp", "et", "ticker", "open", "high", "low", "close", "volume", "prior_close", "pct_gain", "rank", "is_topN", "dollar_volume"] + [f"fwd_ret_{h}m" for h in HORIZONS] + ["mfe_60m", "mae_60m"]
    day = day.select([c for c in keep if c in day.columns]).sort(["timestamp", "rank"])

    if events_only:
        day = day.filter(pl.col("is_topN"))

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        day.write_parquet(out)
        print(f"  Written {out} ({day.height:,} rows, {out.stat().st_size/1e6:.1f} MB)")

    # self-check
    top_at = day.filter(pl.col("is_topN")).sort("timestamp").head(1)
    if top_at.height > 0:
        r = top_at.row(0, named=True)
        print(f"\n  Self-check: {r['ticker']} @ {r['timestamp']} ET={r['et']} rank={r['rank']} pct_gain={r['pct_gain']:.2f}% close={r['close']} fwd_5m={r.get('fwd_ret_5m')} fwd_60m={r.get('fwd_ret_60m')}")
    # also check rank correctness at that minute
    if top_at.height > 0:
        ts0 = top_at["timestamp"][0]
        snap = day.filter(pl.col("timestamp") == ts0).sort("rank").head(5)
        print("  Top 5 at that minute:")
        print(snap.select(["ticker", "pct_gain", "rank", "close", "fwd_ret_5m"]).head(5))

    return day

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Chronological per-minute top-gainer ranker")
    p.add_argument("--file", type=Path, required=True, help="clean parquet (or raw)")
    p.add_argument("--date", required=True, help="YYYY-MM-DD (ET RTH date)")
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--events-only", action="store_true", help="only persist is_topN rows")
    args = p.parse_args()
    if not args.file.exists():
        print(f"File not found: {args.file}")
        raise SystemExit(1)
    out = args.out or Path(f"factory/artifacts/ranked_{args.date}.parquet")
    rank_day(args.file, args.date, args.top_n, out, args.events_only)
