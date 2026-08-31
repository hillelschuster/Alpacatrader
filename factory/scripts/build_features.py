"""Build decision-time-safe ML feature table for certified top-gainer events.

Per month M (clean files M-2, M-1, M loaded where available):
- baselines (H008 RVOL): per-ticker trailing-20-session hourly bucket-end cum$vol curve, interpolated;
  rvol set null at 09:30 (expdv=0 -> inf trap, reviewer #6a)
- minute grid per (ticker, et_date) restricted to event tickers: close/high ffill, low/volume NULL
  on missing bars (real bars only for dips and vol stats — reviewer #3/#5)
- features (all <= t only):
    state: pct_gain_grid, gain_vel_5m/15m (time-based grid shifts), n_hod_breaks, tod_min, dow,
           log_close, dist_open, open_gap
    path: ret_1/3/5/10/15/30, realized_vol_15m/efficiency_30m/n_up_bars_15 (real bars only),
          range_pos (running cum), dist_hod
    structure: dip_5m, trap_reclaim (H009-faithful: real-bar dip below max(hod_before*0.997, vwap)
               within last 5 grid-min & close > hod_before; hod_before = running max of REAL highs < t),
               dip_depth_5m, vwap_dist (null before first volume), above_vwap
    volume: log_dollar_volume(cum), dv_5m_rate, dv_accel, rvol, excess_gain (idio vs market median)
    context: market_ret_5m (cross-sectional median 5m return per minute, within-session shift)
- labels: carried from events (fwd_ret_1..60, mfe_60m, mae_60m) + grid t+1m-entry variants
  (fwd60/30/15_t1entry = c(t+1+h)/c(t+1)-1, valid only when both future bars traded). NEVER features.
Output: data/ml_features/features_YYYY-MM.parquet
"""
import argparse, json
from pathlib import Path
import polars as pl

BUCKET_END_MIN = [600, 660, 720, 780, 840, 900, 960]
MOD0 = 570
ET = "America/New_York"
G = ["ticker", "et_date"]


def load_clean(f: Path) -> pl.DataFrame:
    df = pl.read_parquet(f)
    df = df.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    df = df.with_columns(
        pl.col("timestamp").dt.convert_time_zone(ET).alias("et"),
        pl.col("timestamp").dt.convert_time_zone(ET).dt.date().alias("et_date"),
    )
    df = df.with_columns(
        (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32)).alias("tod_min"))
    df = df.filter((pl.col("tod_min") >= 570) & (pl.col("tod_min") < 960)
                   & (pl.col("close") >= 2.0) & (pl.col("volume") >= 100))
    df = df.unique(subset=["timestamp", "ticker"], keep="first")
    return df


def session_prev_close(clean: pl.DataFrame) -> pl.DataFrame:
    sess = (clean.sort("timestamp").group_by("ticker", "et_date")
            .agg(pl.col("close").last().alias("session_close")))
    sess = sess.sort("ticker", "et_date").with_columns(
        pl.col("session_close").shift(1).over("ticker").alias("prev_close"))
    return sess.select(["ticker", "et_date", "prev_close"])


def build_baselines(clean: pl.DataFrame) -> pl.DataFrame:
    c = clean.sort(["ticker", "et"]).with_columns(
        (pl.col("close") * pl.col("volume")).cum_sum().over(G).alias("cumdv"))
    out = []
    for i, bem in enumerate(BUCKET_END_MIN):
        seg = (c.filter(pl.col("tod_min") <= bem)
               .group_by(G).agg(pl.col("cumdv").last().alias(f"b{i}")))
        out.append(seg)
    base = out[0]
    for seg in out[1:]:
        base = base.join(seg, on=G, how="full", coalesce=True)
    base = base.sort(G)
    for i in range(len(BUCKET_END_MIN)):
        base = base.with_columns(
            pl.col(f"b{i}").rolling_mean(window_size=20, min_samples=20).shift(1).over("ticker").alias(f"e{i}"))
    return base.select(["ticker", "et_date"] + [f"e{i}" for i in range(len(BUCKET_END_MIN))])


def market_frame(month_clean: pl.DataFrame) -> pl.DataFrame:
    """Cross-sectional median 5m return per (et_date, tod_min) over ALL tickers, within-session shift."""
    mc = month_clean.sort(["ticker", "timestamp"]).with_columns(
        (pl.col("close") / pl.col("close").shift(5).over(G) - 1).alias("_r5"))
    return mc.group_by(["et_date", "tod_min"]).agg(pl.col("_r5").median().alias("market_ret_5m"))


def grid_features(month_clean: pl.DataFrame, tickers: set, market: pl.DataFrame) -> pl.DataFrame:
    sub = month_clean.filter(pl.col("ticker").is_in(list(tickers)))
    anchors = (sub.sort("timestamp").group_by(G)
               .agg(pl.col("open").first().alias("session_open"),
                    pl.col("prev_close").first().alias("pc")))
    grid = (sub.select(["ticker", "et_date"]).unique()
            .join(pl.DataFrame({"tod_min": list(range(MOD0, 960))}), how="cross"))
    bars = sub.select(["ticker", "et_date", "tod_min", "close", "high", "low", "volume",
                       (pl.col("close") * pl.col("volume")).alias("dv_bar")])
    grid = grid.join(bars, on=G + ["tod_min"], how="left").sort(G + ["tod_min"])
    grid = grid.with_columns(pl.col("close").is_not_null().alias("has_bar"))
    grid = grid.with_columns([
        pl.col("close").forward_fill().over(G).alias("c"),
        pl.col("high").forward_fill().over(G).alias("h"),
        pl.col("volume").fill_null(0).alias("v"),
        pl.col("dv_bar").fill_null(0).alias("dv"),
    ])
    # real bars only: low and r1 are null on fill rows so dips/vol stats never see stale values
    grid = grid.with_columns([
        pl.when(pl.col("has_bar")).then(pl.col("low")).alias("l_real"),
        pl.when(pl.col("has_bar")).then(pl.col("c").pct_change().over(G)).alias("r1"),
    ])
    grid = grid.join(anchors, on=G, how="left")
    grid = grid.with_columns(pl.col("h").cum_max().over(G).alias("hod_now"))
    grid = grid.with_columns(pl.col("hod_now").shift(1).over(G).fill_null(0.0).alias("hod_before"))
    grid = grid.with_columns(((pl.col("h") > pl.col("hod_before")) & (pl.col("has_bar")))
                             .cast(pl.Int32).cum_sum().over(G).alias("n_hod_breaks"))
    cumv = pl.col("v").cum_sum().over(G)
    grid = grid.with_columns(pl.when(cumv > 0)
                             .then(pl.col("dv").cum_sum().over(G) / cumv).otherwise(None).alias("vwap"))
    for k in (1, 3, 5, 10, 15, 30):
        grid = grid.with_columns((pl.col("c") / pl.col("c").shift(k).over(G) - 1).alias(f"ret_{k}m"))
    grid = grid.with_columns([
        pl.col("r1").rolling_std(window_size=15, min_samples=10).over(G).alias("realized_vol_15m"),
        (pl.col("r1").rolling_sum(window_size=30, min_samples=15).abs()
         / pl.col("r1").abs().rolling_sum(window_size=30, min_samples=15).clip(1e-12)).over(G).alias("efficiency_30m"),
        (pl.col("r1") > 0).cast(pl.Int32).rolling_sum(window_size=15, min_samples=10).over(G).alias("n_up_bars_15"),
    ])
    lmin5 = pl.col("l_real").rolling_min(window_size=5, min_samples=1).over(G)
    dip_level = pl.max_horizontal(pl.col("hod_before") * 0.997, pl.col("vwap"))
    grid = grid.with_columns([
        (lmin5 < dip_level).alias("dip_5m"),
        (lmin5 / pl.col("hod_before") - 1).clip(-1, 0).alias("dip_depth_5m"),
    ])
    grid = grid.with_columns((pl.col("dip_5m") & (pl.col("c") > pl.col("hod_before"))).cast(pl.Int8).alias("trap_reclaim"))
    grid = grid.with_columns([
        ((pl.col("c") / pl.col("pc") - 1) * 100).alias("pct_gain_grid"),
        (pl.col("c") / pl.col("session_open") - 1).alias("dist_open"),
        (pl.col("session_open") / pl.col("pc") - 1).alias("open_gap"),
        (pl.col("c") / pl.col("hod_now") - 1).alias("dist_hod"),
        ((pl.col("c") - pl.col("l_real").cum_min().over(G))
         / (pl.col("h").cum_max().over(G) - pl.col("l_real").cum_min().over(G)).clip(1e-12)).alias("range_pos"),
        (pl.col("c") / pl.col("vwap") - 1).alias("vwap_dist"),
        (pl.col("c") > pl.col("vwap")).cast(pl.Int8).alias("above_vwap"),
        pl.col("c").log1p().alias("log_close"),
        pl.col("dv").cum_sum().over(G).alias("cum_dv"),
    ])
    grid = grid.with_columns([
        (pl.col("pct_gain_grid") - pl.col("pct_gain_grid").shift(5).over(G)).alias("gain_vel_5m"),
        (pl.col("pct_gain_grid") - pl.col("pct_gain_grid").shift(15).over(G)).alias("gain_vel_15m"),
    ])
    dv5 = pl.col("cum_dv") - pl.col("cum_dv").shift(5).over(G).fill_null(0)
    dv30 = (pl.col("cum_dv").shift(5).over(G).fill_null(0)
            - pl.col("cum_dv").shift(35).over(G).fill_null(0))
    grid = grid.with_columns([
        (dv5 / 5).alias("dv_5m_rate"),
        pl.when(dv30 > 0).then((dv5 / 5) / (dv30 / 30)).otherwise(None).alias("dv_accel"),
    ])
    # t+1m-entry labels (executable entry proxy): valid only if both future bars traded
    grid = grid.with_columns([
        pl.when((pl.col("v").shift(-1).over(G) > 0) & (pl.col("v").shift(-61).over(G) > 0))
        .then(pl.col("c").shift(-61).over(G) / pl.col("c").shift(-1).over(G) - 1).otherwise(None).alias("fwd60_t1entry"),
        pl.when((pl.col("v").shift(-1).over(G) > 0) & (pl.col("v").shift(-31).over(G) > 0))
        .then(pl.col("c").shift(-31).over(G) / pl.col("c").shift(-1).over(G) - 1).otherwise(None).alias("fwd30_t1entry"),
        pl.when((pl.col("v").shift(-1).over(G) > 0) & (pl.col("v").shift(-16).over(G) > 0))
        .then(pl.col("c").shift(-16).over(G) / pl.col("c").shift(-1).over(G) - 1).otherwise(None).alias("fwd15_t1entry"),
    ])
    grid = grid.join(market, on=["et_date", "tod_min"], how="left")
    grid = grid.with_columns((pl.col("pct_gain_grid") - pl.col("market_ret_5m") * 100).alias("excess_gain"))
    # convert to relative minutes (0..389) to match events' tod_min
    grid = grid.with_columns((pl.col("tod_min") - MOD0).alias("tod_min"))
    keep = ["ticker", "et_date", "tod_min", "pct_gain_grid", "gain_vel_5m", "gain_vel_15m",
            "n_hod_breaks", "dip_5m", "trap_reclaim", "dip_depth_5m", "vwap_dist", "above_vwap",
            "dist_open", "open_gap", "dist_hod", "range_pos", "log_close", "cum_dv", "dv_5m_rate",
            "dv_accel", "market_ret_5m", "excess_gain",
            "ret_1m", "ret_3m", "ret_5m", "ret_10m", "ret_15m", "ret_30m",
            "realized_vol_15m", "efficiency_30m", "n_up_bars_15",
            "fwd60_t1entry", "fwd30_t1entry", "fwd15_t1entry"]
    return grid.select(keep)


def rvol_attach(events: pl.DataFrame, base: pl.DataFrame, month_clean: pl.DataFrame) -> pl.DataFrame:
    ev = events.with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")).alias("timestamp"),
        (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32)).alias("tod_min"))
    lookup = month_clean.with_columns(
        (pl.col("close") * pl.col("volume")).cum_sum().over(G).alias("cumdv"))
    ev = ev.join(lookup.select(["ticker", "timestamp", "cumdv"]), on=["ticker", "timestamp"], how="left")
    ev = ev.join(base, on=["ticker", "et_date"], how="left")
    cond = []
    prev = MOD0
    for i, bem in enumerate(BUCKET_END_MIN):
        frac = (pl.col("tod_min") - prev) / (bem - prev)
        e = pl.col("e0") * frac if i == 0 else pl.col(f"e{i-1}") + (pl.col(f"e{i}") - pl.col(f"e{i-1}")) * frac
        cond.append(pl.when((pl.col("tod_min") >= prev) & (pl.col("tod_min") <= bem)).then(e))
        prev = bem
    ev = ev.with_columns(pl.coalesce(cond).alias("expdv"))
    # 09:30 events: expdv=0 -> inf; set rvol null there instead
    ev = ev.with_columns(pl.when(pl.col("tod_min") > MOD0)
                         .then(pl.col("cumdv") / pl.col("expdv")).otherwise(None).alias("rvol"))
    return ev


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--month", required=True, help="YYYY-MM to featurize")
    p.add_argument("--clean-dir", type=Path, default=Path("data"))
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    y, m = map(int, a.month.split("-"))
    # span for baselines: M-2, M-1, M (20-session lookback reaches 2 months back early in month)
    months = []
    for mm in (m - 2, m - 1, m):
        yy = y if mm > 0 else y - 1
        mm2 = mm if mm > 0 else mm + 12
        f = a.clean_dir / f"clean_ohlcv_{yy}-{mm2:02d}.parquet"
        if f.exists():
            months.append(f"{yy}-{mm2:02d}")
    print(f"[{a.month}] span files: {months}")
    events = pl.read_parquet(Path(f"factory/artifacts/certification_{a.month}/events_topN.parquet"))
    print(f"[{a.month}] events: {events.height:,}")

    span = pl.concat([load_clean(a.clean_dir / f"clean_ohlcv_{mm}.parquet") for mm in months], how="vertical")
    cur = span.filter(pl.col("et_date").dt.year() == y).filter(pl.col("et_date").dt.month() == m)
    tickers = set(events["ticker"].unique().to_list())
    base = build_baselines(span)
    cur = cur.join(session_prev_close(span), on=G, how="left")
    mkt = market_frame(cur)
    gf = grid_features(cur, tickers, mkt)
    ev = rvol_attach(events, base, cur)
    ev = ev.with_columns(
        (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32) - MOD0).alias("tod_min"))
    ev = ev.join(gf, on=G + ["tod_min"], how="left")
    ev = ev.with_columns([pl.col("et").dt.weekday().alias("dow"), pl.lit(a.month).alias("month")])
    ev = ev.drop([f"e{i}" for i in range(len(BUCKET_END_MIN))] + ["expdv", "cumdv"], strict=False)
    out = a.out or Path(f"data/ml_features/features_{a.month}.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    ev.write_parquet(out)
    cov_cols = ("rvol", "ret_30m", "vwap_dist", "efficiency_30m", "market_ret_5m", "dv_accel",
                "trap_reclaim", "fwd60_t1entry", "fwd_ret_60m", "pct_gain_grid")
    cov = ev.select([pl.col(c).is_not_null().mean().alias(c) for c in cov_cols])
    stats = {k: round(v, 3) for k, v in cov.row(0, named=True).items()}
    print(f"[{a.month}] wrote {out} rows={ev.height:,} coverage={stats}")
    cov_path = Path("factory/artifacts/ml") / f"coverage_{a.month}.json"
    cov_path.parent.mkdir(parents=True, exist_ok=True)
    cov_path.write_text(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
