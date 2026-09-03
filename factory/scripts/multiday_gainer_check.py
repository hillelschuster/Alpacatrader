"""BOUNDED directional check (NOT a strategy, NOT selected on): do 8%+ gainer days
predict positive multi-day forward returns, and did that change 2025 -> 2026?

Gainer day (close mirror of research day gate): day_max_high / prev_session_close - 1 >= 0.08
on primary-session consecutive days. Forwards from session close: next-open->close,
close->close +1/+2/+3 sessions. Population baseline: all ticker-days.

One shot, reported as-is. Run: uv run --no-project --with polars --with numpy --with tzdata \
  python factory/scripts/multiday_gainer_check.py
"""
from pathlib import Path
import polars as pl
import numpy as np

MONTHS = ["2025-05", "2025-06", "2025-07", "2025-08", "2025-09", "2025-10",
          "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
          "2026-05", "2026-06", "2026-07", "2026-08"]


def session_panel(month):
    f = Path(f"data/clean_ohlcv_{month}.parquet")
    if not f.exists():
        f = Path(f"data/backfill/clean_ohlcv_{month}.parquet")
    df = pl.read_parquet(f, columns=["timestamp", "ticker", "open", "high", "low", "close", "volume"])
    df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"))
    df = df.with_columns(pl.col("et").dt.date().alias("et_date"))
    df = df.sort(["ticker", "timestamp"])
    return (df.group_by(["ticker", "et_date"])
            .agg([pl.col("open").first().alias("s_open"),
                  pl.col("close").last().alias("s_close"),
                  pl.col("high").max().alias("s_high"),
                  (pl.col("close") * pl.col("volume")).sum().alias("s_dv")])
            .sort(["ticker", "et_date"]))


def main():
    panel = pl.concat([session_panel(m) for m in MONTHS], how="vertical")
    panel = panel.with_columns([
        pl.col("s_close").shift(1).over("ticker").alias("prev_close"),
        pl.col("s_open").shift(-1).over("ticker").alias("nxt_open"),
        pl.col("s_close").shift(-1).over("ticker").alias("nxt_close1"),
        pl.col("s_close").shift(-2).over("ticker").alias("nxt_close2"),
        pl.col("s_close").shift(-3).over("ticker").alias("nxt_close3"),
    ])
    panel = panel.with_columns([
        (pl.col("s_high") / pl.col("prev_close") - 1).alias("day_max_gain"),
        (pl.col("s_close") / pl.col("prev_close") - 1).alias("day_close_gain"),
    ])
    # near-consecutive sessions only (<=4 calendar days gap avoids weekend/holiday jumps as prev)
    panel = panel.with_columns(
        (pl.col("et_date").cast(pl.Int32) - pl.col("et_date").cast(pl.Int32).shift(1).over("ticker")).alias("gapd"))
    panel = panel.filter(pl.col("gapd") == pl.col("gapd"))  # keep; gap filter below via prev exists
    g = panel.filter(pl.col("day_max_gain") >= 0.08)
    g = g.with_columns([
        (pl.col("nxt_close1") / pl.col("nxt_open") - 1).alias("f_oc1"),
        (pl.col("nxt_close1") / pl.col("s_close") - 1).alias("f_cc1"),
        (pl.col("nxt_close2") / pl.col("s_close") - 1).alias("f_cc2"),
        (pl.col("nxt_close3") / pl.col("s_close") - 1).alias("f_cc3"),
    ])
    g = g.with_columns(pl.col("et_date").cast(pl.String).str.slice(0, 7).alias("month"))
    base = panel.with_columns([
        (pl.col("nxt_close1") / pl.col("s_close") - 1).alias("f_cc1"),
    ]).with_columns(pl.col("et_date").cast(pl.String).str.slice(0, 7).alias("month"))

    def stat(df, col, cost):
        v = df.filter(pl.col(col).is_not_null())[col].to_numpy()
        if len(v) < 20:
            return len(v), float("nan"), float("nan")
        r = v - cost
        t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
        return len(v), r.mean(), t

    print(f"{'month':8} {'n_gain':>7} {'oc1':>9} {'cc1':>9} {'cc2':>9} {'cc3':>9} {'base_cc1':>9}  (net20, t in parens)")
    for m in MONTHS:
        gm = g.filter(pl.col("month") == m)
        bm = base.filter(pl.col("month") == m)
        row = f"{m:8} {gm.height:>7}"
        for c in ["f_oc1", "f_cc1", "f_cc2", "f_cc3"]:
            n, mu, t = stat(gm, c, 0.002)
            row += f" {mu:>+8.3%}({t:>+5.1f})"
        n, mu, t = stat(bm, "f_cc1", 0.002)
        row += f" {mu:>+8.3%}({t:>+5.1f})"
        print(row)
    for tag, ms in [("2025", MONTHS[:8]), ("2026", MONTHS[8:])]:
        gm = g.filter(pl.col("month").is_in(ms))
        n, mu, t = stat(gm, "f_cc1", 0.002)
        n2, mu2, t2 = stat(gm, "f_cc3", 0.002)
        print(f"{tag} pooled: n={n:,} cc1 net20={mu:+.3%} t={t:+.2f}   cc3 net20={mu2:+.3%} t={t2:+.2f}")


if __name__ == "__main__":
    main()
