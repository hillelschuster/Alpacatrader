"""H008 — RVOL (attention surprise) vs 20-day time-of-day baseline on certified top-gainer events.

Hypothesis (ledger): top gainers whose cumulative dollar volume exceeds the time-of-day expected
volume (RVOL vs trailing 20-session baseline > 1.5) outperform same-absolute-dollar-volume / low-RVOL
peers over next 15-60m. Fixes H003's flaw (per-day dollar tercile without TOD normalization).

Method (minimal, sibling-convention):
- clean bars RTH only; per ticker-session cum_dollar_vol = cumsum(close*volume).
- baseline curve per ticker: E[cum$vol at end of each hourly bucket] = mean over the ticker's own
  trailing 20 sessions (strictly prior). Event expected cum$vol = linear interpolation of the
  baseline curve within its bucket. RVOL = cum$vol(t) / E[cum$vol(t)].
- segments: RVOL>1.5 (surprise) vs 1.0-1.5 vs <1.0; plus absolute-cum$vol tercile cross-check
  ("same absolute dollar volume, different RVOL").
- forward returns: exact (ticker, timestamp+h) join, RTH only. Long side (continuation).
- cost: roundtrip = cost_bps x 2 / 10000 (primary --cost-bps 10 = 20bps RT; 20 = 40bps RT).
- events: certified events_topN.parquet (2025-06 + 2025-07). May events excluded: no April data
  locally -> their trailing 20-session baseline would be truncated/absent.

Outputs: results parquet + h008_summary.json (segment metrics per month x horizon x cost).
"""
import argparse
import json
from pathlib import Path
import polars as pl

BUCKET_END_MIN = [600, 660, 720, 780, 840, 900, 960]  # ET minute-of-day: 10:00..16:00
MOD0 = 570  # 09:30


def load_clean(clean_dir: Path) -> pl.DataFrame:
    files = sorted(clean_dir.glob("clean_ohlcv_2025-*.parquet"))
    print(f"Loading {len(files)} clean files ...")
    df = pl.concat([pl.read_parquet(f) for f in files], how="vertical")
    df = df.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
    df = df.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    )
    df = df.filter(
        (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32) >= 570)
        & (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32) < 960))
    print(f" rows {df.height} (RTH only)")
    return df["ticker", "timestamp", "close", "volume", "et", "et_date"]


def build_baselines(clean: pl.DataFrame) -> pl.DataFrame:
    """Per (ticker, session) cum$vol at each hourly bucket end -> trailing-20 mean, shifted 1."""
    c = clean.sort(["ticker", "et"]).with_columns(
        (pl.col("close") * pl.col("volume")).cum_sum().over(["ticker", "et_date"]).alias("cumdv"),
        (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32)).alias("mod"),
    )
    out = []
    for i, bem in enumerate(BUCKET_END_MIN):
        seg = (c.filter(pl.col("mod") <= bem)
                .group_by(["ticker", "et_date"]).agg(pl.col("cumdv").last().alias(f"b{i}")))
        out.append(seg)
    base = out[0]
    for seg in out[1:]:
        base = base.join(seg, on=["ticker", "et_date"], how="full", coalesce=True)
    base = base.sort(["ticker", "et_date"])
    for i in range(len(BUCKET_END_MIN)):
        base = base.with_columns(
            pl.col(f"b{i}").rolling_mean(window_size=20, min_samples=20).shift(1).over("ticker").alias(f"e{i}"))
    return base.select(["ticker", "et_date"] + [f"e{i}" for i in range(len(BUCKET_END_MIN))])


def attach_rvol(events: pl.DataFrame, base: pl.DataFrame, lookup: pl.DataFrame) -> pl.DataFrame:
    ev = events.with_columns(
        (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32)).alias("mod"))
    ev = ev.join(lookup.select(["ticker", "timestamp", "cumdv"]), on=["ticker", "timestamp"], how="left")
    ev = ev.join(base, on=["ticker", "et_date"], how="left")
    # expected cum$vol: linear interpolation of the baseline curve (e0..e6) within the event's bucket
    cond = []
    prev = MOD0
    for i, bem in enumerate(BUCKET_END_MIN):
        frac = (pl.col("mod") - prev) / (bem - prev)
        e = pl.col("e0") * frac if i == 0 else pl.col(f"e{i-1}") + (pl.col(f"e{i}") - pl.col(f"e{i-1}")) * frac
        cond.append(pl.when((pl.col("mod") >= prev) & (pl.col("mod") <= bem)).then(e))
        prev = bem
    ev = ev.with_columns(pl.coalesce(cond).alias("expdv"))
    ev = ev.with_columns((pl.col("cumdv") / pl.col("expdv")).alias("rvol"))
    return ev


def forward_returns(ev: pl.DataFrame, clean: pl.DataFrame) -> pl.DataFrame:
    """No-op: certified events_topN already carries fwd_ret_15m/30m/60m computed on the clean frame.
    Kept as an assertion-style check that the columns exist."""
    missing = [c for c in ("fwd_ret_15m", "fwd_ret_30m", "fwd_ret_60m") if c not in ev.columns]
    if missing:
        raise SystemExit(f"events missing forward-return columns: {missing}")
    return ev


def metrics(df: pl.DataFrame, cost: float) -> dict:
    out = {}
    for h in (15, 30, 60):
        s = df.select(pl.col(f"fwd_ret_{h}m").drop_nulls()).to_series()
        if s.len() == 0:
            continue
        out[f"h{h}"] = {"n": s.len(), "wr": round((s > 0).sum() / s.len(), 3),
                        "avg": float(s.mean()), "exp_net": float(s.mean() - cost)}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clean-dir", type=Path, required=True)
    p.add_argument("--events", type=Path, nargs="+", required=True, help="certified events_topN.parquet paths")
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h008_results.parquet"))
    a = p.parse_args()
    cost = a.cost_bps * 2 / 10000

    clean = load_clean(a.clean_dir)
    base = build_baselines(clean)
    lookup = clean.with_columns((pl.col("close") * pl.col("volume")).cum_sum().over(["ticker", "et_date"]).alias("cumdv"))

    evs = []
    for ep in a.events:
        e = pl.read_parquet(ep)
        month = ep.parent.name.split("_")[-1]
        e = e.with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")).alias("timestamp"))
        evs.append(e.with_columns(pl.lit(month).alias("month")))
        print(f"events {ep}: {e.height} ({month})")
    ev = pl.concat(evs, how="vertical")

    ev = attach_rvol(ev, base, lookup)
    ev = forward_returns(ev, clean)

    n_no_baseline = ev.filter(pl.col("rvol").is_null()).height
    print(f"events without 20-session baseline (dropped): {n_no_baseline}")
    keep_fwd = pl.col("fwd_ret_15m").is_not_null() | pl.col("fwd_ret_30m").is_not_null() | pl.col("fwd_ret_60m").is_not_null()
    ev = ev.filter(pl.col("rvol").is_not_null() & keep_fwd)

    # absolute-cum$vol tercile within month (the "same absolute dollar volume" control)
    ev = ev.with_columns((pl.col("cumdv").rank("ordinal").over("month") / pl.len().over("month")).alias("_q"))
    ev = ev.with_columns(pl.col("_q").cut([1 / 3, 2 / 3], labels=["dv_low", "dv_mid", "dv_high"]).alias("dv_tercile"))

    segs = {
        "RVOL>1.5": pl.col("rvol") > 1.5,
        "RVOL1.0-1.5": (pl.col("rvol") > 1.0) & (pl.col("rvol") <= 1.5),
        "RVOL<1.0": pl.col("rvol") <= 1.0,
    }
    summary = {"cost_bps_per_side": a.cost_bps, "roundtrip_cost": cost,
               "n_events_total": ev.height, "n_dropped_no_baseline": n_no_baseline, "segments": {}}
    for month in sorted(ev["month"].unique().to_list()):
        m = ev.filter(pl.col("month") == month)
        for name, flt in segs.items():
            summary["segments"][f"{month}|{name}"] = metrics(m.filter(flt), cost)
        for t in ("dv_low", "dv_mid", "dv_high"):
            summary["segments"][f"{month}|{t}|RVOL>1.5"] = metrics(m.filter((pl.col("dv_tercile") == t) & (pl.col("rvol") > 1.5)), cost)
            summary["segments"][f"{month}|{t}|RVOL<1.0"] = metrics(m.filter((pl.col("dv_tercile") == t) & (pl.col("rvol") <= 1.0)), cost)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    ev.write_parquet(a.out)
    print(f"\nWrote {a.out} ({ev.height})")
    for k in summary["segments"]:
        if k.split("|")[-1].startswith("RVOL") and len(k.split("|")) == 2:
            v = summary["segments"][k]
            h15, h60 = v.get("h15", {}), v.get("h60", {})
            print(f"{k:26s} h15 n={h15.get('n', 0):6} exp_net={round(h15.get('exp_net', 0) * 100, 3)}% | h60 n={h60.get('n', 0):6} exp_net={round(h60.get('exp_net', 0) * 100, 3)}%")
    jpath = a.out.parent / "h008_summary.json"
    jpath.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {jpath}")


if __name__ == "__main__":
    main()
