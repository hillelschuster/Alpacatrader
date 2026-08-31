"""Extract top-N entry events — ponytail minimal."""
import argparse
from pathlib import Path
import polars as pl

# ponytail: fixed buckets, no config needed
def time_bucket(et) -> str:
    hm = et.hour * 60 + et.minute
    if hm < 600:
        return "09:30-10:00"
    h = et.hour
    return f"{h:02d}:00-{h+1:02d}:00" if h < 15 else "15:00-16:00"

def extract_events(df: pl.DataFrame, top_n: int = 20) -> pl.DataFrame:
    # filter topN
    if "is_topN" in df.columns:
        filt = df.filter(pl.col("is_topN"))
        # if top_n differs from file's top_n (20), re-filter by rank
        if top_n != 20:
            filt = filt.filter(pl.col("rank") <= top_n)
    else:
        filt = df.filter(pl.col("rank") <= top_n)

    if filt.height == 0:
        return filt.clear() if hasattr(filt, "clear") else filt.head(0)

    # ensure et column
    if "et" not in filt.columns:
        filt = filt.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"))
    if "et" not in df.columns:
        df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"))

    # session_high per ticker = cum_max close up to T (from full df sorted)
    # build lookup ticker+timestamp -> session_high
    full = df.select(["ticker", "timestamp", "close"]).sort(["ticker", "timestamp"])
    full = full.with_columns(pl.col("close").cum_max().over("ticker").alias("session_high"))
    # dedup entry detection on filtered
    filt = filt.sort(["ticker", "timestamp"])
    # compute diff per ticker using shift
    filt = filt.with_columns(
        (pl.col("timestamp") - pl.col("timestamp").shift(1).over("ticker")).alias("_gap")
    )
    # gap == 1 minute -> continuation
    one_min = pl.duration(minutes=1)
    filt = filt.with_columns(
        ((pl.col("_gap") != one_min) | pl.col("_gap").is_null()).alias("_is_entry")
    )
    events = filt.filter(pl.col("_is_entry"))
    # entry_type
    events = events.with_columns(pl.col("timestamp").cum_count().over("ticker").alias("_cnt"))
    events = events.with_columns(
        pl.when(pl.col("_cnt") == 1).then(pl.lit("first_entry")).otherwise(pl.lit("reentry")).alias("entry_type"),
        pl.lit(0).alias("bars_since_entry"),
    )
    # join session_high
    events = events.join(full.select(["ticker", "timestamp", "session_high"]), on=["ticker", "timestamp"], how="left")
    # time_bucket
    # use python map for simplicity
    ets = events["et"].to_list()
    buckets = [time_bucket(e) for e in ets]
    events = events.with_columns(pl.Series("time_bucket", buckets))

    keep = ["timestamp", "ticker", "rank", "pct_gain", "close", "dollar_volume", "session_high",
            "entry_type", "bars_since_entry", "time_bucket",
            "fwd_ret_1m", "fwd_ret_3m", "fwd_ret_5m", "fwd_ret_15m", "fwd_ret_30m", "fwd_ret_60m",
            "mfe_60m", "mae_60m", "et"]
    out_cols = [c for c in keep if c in events.columns]
    events = events.select(out_cols).sort("timestamp")
    return events.drop("_gap", "_is_entry", "_cnt") if "_gap" in events.columns else events

def print_stats(events: pl.DataFrame, label: str = ""):
    if events.height == 0:
        print(f"{label} no events")
        return
    print(f"{label} total events: {events.height}")
    print(f"  unique tickers: {events['ticker'].n_unique()}")
    if "time_bucket" in events.columns:
        print("  by time_bucket:")
        for r in events.group_by("time_bucket").len().sort("time_bucket").iter_rows(named=True):
            print(f"    {r['time_bucket']}: {r['len']}")
    if "entry_type" in events.columns:
        for r in events.group_by("entry_type").len().iter_rows(named=True):
            print(f"  {r['entry_type']}: {r['len']}")
    # avg per day
    if "et" in events.columns:
        days = events["et"].dt.date().n_unique()
        if days:
            print(f"  avg events/day: {events.height/days:.1f} over {days} days")

def main():
    p = argparse.ArgumentParser(description="Extract top-N entry events")
    p.add_argument("--ranked-dir", type=Path, default=Path("factory/artifacts"))
    p.add_argument("--file", type=Path, default=None)
    p.add_argument("--date", type=str, default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    if args.file:
        f = args.file
        if not f.exists():
            raise SystemExit(f"not found: {f}")
        df = pl.read_parquet(f)
        events = extract_events(df, args.top_n)
        out = args.out or Path(f"factory/artifacts/events_{args.date or f.stem.replace('ranked_','')}.parquet")
        out.parent.mkdir(parents=True, exist_ok=True)
        events.write_parquet(out)
        print(f"Wrote {out} ({events.height} events)")
        if args.stats:
            print_stats(events)
            print("\nSample 5:")
            print(events.head(5))
        return

    if args.all:
        files = sorted(args.ranked_dir.glob("ranked_*.parquet"))
        if not files:
            raise SystemExit(f"no ranked_*.parquet in {args.ranked_dir}")
        all_events = []
        for f in files:
            df = pl.read_parquet(f)
            ev = extract_events(df, args.top_n)
            all_events.append(ev)
            print(f"  {f.name}: {ev.height} events")
        concat = pl.concat(all_events, how="vertical") if all_events else pl.DataFrame()
        out = args.out or args.ranked_dir / "events_all.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        concat.write_parquet(out)
        print(f"Wrote {out} ({concat.height} events from {len(files)} files)")
        if args.stats:
            print_stats(concat, "ALL")
        return

    # single date mode via ranked-dir
    if not args.date:
        p.error("--date YYYY-MM-DD or --all or --file required")
    f = args.ranked_dir / f"ranked_{args.date}.parquet"
    if not f.exists():
        raise SystemExit(f"not found: {f}")
    df = pl.read_parquet(f)
    events = extract_events(df, args.top_n)
    out = args.out or args.ranked_dir / f"events_{args.date}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    events.write_parquet(out)
    print(f"Wrote {out} ({events.height} events)")
    if args.stats:
        print_stats(events)
        print("\nSample 5:")
        print(events.head(5))

if __name__ == "__main__":
    main()
