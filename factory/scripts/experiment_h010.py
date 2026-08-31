"""H010 overnight gap / opening-range hold vs fail — ponytail minimal."""
import argparse, json
from pathlib import Path
import polars as pl

HORIZONS = [15, 30, 60]
GAP_MIN = 0.05          # |gap| > 5%
OPEN_MINUTE = 570       # 09:30 ET, bar stamped at bar START
OR_START, OR_END = 570, 574   # 09:30..09:34 inclusive (first 5 min), bar-start stamps
HOLD_START, HOLD_END = 575, 584  # 09:35..09:44 (10 bars), bar-start stamps
ENTRY_MINUTE = 585      # 09:45 entry, use bar close

def metrics(df, cost):
    if df.height == 0:
        return {}
    out = {}
    for h in HORIZONS:
        c = f"fwd_ret_{h}m"
        if c not in df.columns:
            continue
        s = df[c].drop_nulls()
        if s.len() == 0:
            continue
        wr = (s > 0).sum() / s.len()
        wr_net = (s > cost).sum() / s.len()
        avg = s.mean()
        med = s.median()
        wins = s.filter(s > 0)
        losses = s.filter(s <= 0)
        aw = wins.mean() if wins.len() > 0 else 0
        al = losses.mean() if losses.len() > 0 else 0
        exp = wr * aw + (1 - wr) * al
        exp_net = exp - cost
        print(f"  h{h}m n={s.len()} wr={wr:.3f} wr_net={wr_net:.3f} avg={avg:.4%} exp_net={exp_net:.4%}")
        out[f"h{h}"] = {"n": int(s.len()), "wr": float(wr), "wr_net": float(wr_net),
                        "avg": float(avg), "median": float(med) if med is not None else None,
                        "exp": float(exp), "exp_net": float(exp_net),
                        "avg_win": float(aw), "avg_loss": float(al)}
    return out

def run(clean_dir, cost_bps, out, min_dollar_open=0.0):
    cost = cost_bps * 2 / 10000
    clean_dir = Path(clean_dir)
    files = sorted(clean_dir.glob("clean_*.parquet"))
    if not files:
        files = sorted(clean_dir.glob("*.parquet"))
    print(f"Loading {len(files)} clean files ...")
    clean = pl.concat([pl.scan_parquet(str(f)).collect() for f in files])
    print(f" raw rows {clean.height}")
    clean = clean.with_columns([
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    ])
    clean = clean.filter((pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute() >= 570)
                         & (pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute() < 960))
    clean = clean.filter(pl.col("close") >= 2.0)
    clean = clean.sort(["ticker", "timestamp"])
    print(f" RTH filtered {clean.height}")

    # prior_close per (ticker, et_date): previous session's last RTH close
    daily_last = clean.sort("timestamp").group_by(["ticker", "et_date"]).agg(pl.col("close").last().alias("last_close"))
    daily_last = daily_last.sort(["ticker", "et_date"]).with_columns(pl.col("last_close").shift(1).over("ticker").alias("prior_close"))
    first_sess = daily_last.filter(pl.col("prior_close").is_null())
    first_sess_dates = sorted(first_sess["et_date"].unique().to_list())
    print(f" ticker-days with no prior_close (dropped) {first_sess.height} across {len(first_sess_dates)} dates (first session {first_sess_dates})")
    daily_last = daily_last.filter(pl.col("prior_close").is_not_null())
    # count of ticker-days dropped specifically for first session (no prior in June-only)
    first_date = min(clean["et_date"].unique().to_list())
    n_first = clean.filter(pl.col("et_date") == first_date).select(["ticker"]).n_unique()
    print(f"  first session {first_date}: {n_first} tickers dropped (no prior_close in June-only)")
    clean = clean.join(daily_last.select(["ticker", "et_date", "prior_close"]), on=["ticker", "et_date"], how="inner")

    # 09:30 open per ticker-day: open of the bar stamped 09:30 (first RTH bar)
    hm = pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute()
    open_0930 = (clean.with_columns(hm.alias("hm"))
                 .filter(pl.col("hm") == OPEN_MINUTE)
                 .select(["ticker", "et_date", "open", "timestamp"])
                 .rename({"open": "open_0930", "timestamp": "open_ts"}))
    clean = clean.join(open_0930.select(["ticker", "et_date", "open_0930"]), on=["ticker", "et_date"], how="left")
    n_noopen = clean.group_by(["ticker", "et_date"]).agg(pl.col("open_0930").first().alias("_o")).filter(pl.col("_o").is_null()).height
    print(f" ticker-days with NO 09:30 bar (delayed open -> excluded) {n_noopen}")
    clean = clean.filter(pl.col("open_0930").is_not_null())
    clean = clean.with_columns(
        ((pl.col("open_0930") - pl.col("prior_close")) / pl.col("prior_close")).alias("gap")
    )
    clean = clean.sort(["ticker", "et_date", "timestamp"])

    # ---- output-identical candidate pre-filter (only days that can emit a signal) ----
    # A day can emit a signal only if |gap|>5%. Keep ALL bars of candidate days so later
    # or_high, hold/fail scan, entry close and forward lookups are preserved exactly.
    cand_days = clean.group_by(["ticker", "et_date"]).agg(pl.col("gap").first().alias("_g")) \
                     .filter(pl.col("_g").abs() > GAP_MIN).select(["ticker", "et_date"])
    clean = clean.join(cand_days, on=["ticker", "et_date"], how="inner")
    clean = clean.sort(["ticker", "et_date", "timestamp"])
    n_cand = clean.select(["ticker", "et_date"]).unique().height
    print(f" candidate days (|gap|>{GAP_MIN:.0%}) {n_cand} rows {clean.height}")

    # ---- liquidity gate: intraday-open dollar volume 09:30-09:45 (observable at entry) ----
    # The certified June data is dominated by thin/OTC names; without a dollar-volume floor the
    # opening-range window has <5 bars and the hypothesis is untestable. Gate is at/past entry, no
    # forward-return lookahead.
    clean = clean.with_columns((pl.col("close") * pl.col("volume")).alias("_dollar"))
    hmcx = pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute().cast(pl.Int32)
    open_dv = (clean.with_columns(hmcx.alias("_hm"))
               .filter((pl.col("_hm") >= 570) & (pl.col("_hm") <= ENTRY_MINUTE))
               .group_by(["ticker", "et_date"]).agg(pl.col("_dollar").sum().alias("dollar_open")))
    clean = clean.join(open_dv, on=["ticker", "et_date"], how="left")
    if min_dollar_open:
        ndrop = clean.group_by(["ticker", "et_date"]).agg(pl.col("dollar_open").first().alias("_d")).filter(pl.col("_d") < min_dollar_open).height
        print(f" liquidity gate dollar_open>={min_dollar_open:,.0f}: drops {ndrop} of {n_cand} candidate days")
        clean = clean.filter(pl.col("dollar_open") >= min_dollar_open)
    clean = clean.drop(["_dollar"])

    # ---- per candidate ticker-day: or_high, hold/fail scan, entry close ----
    rows = []
    for (ticker, et_date), sub in clean.group_by(["ticker", "et_date"]):
        sub = sub.sort("timestamp")
        hms_list = sub["et"].dt.hour().cast(pl.Int32).to_list()
        hms_list = [h * 60 + m for h, m in zip(hms_list, sub["et"].dt.minute().cast(pl.Int32).to_list())]
        ts = sub["timestamp"].to_list()
        highs = sub["high"].to_list()
        lows = sub["low"].to_list()
        closes = sub["close"].to_list()
        or_high = None
        or_bars = 0
        for i, m in enumerate(hms_list):
            if OR_START <= m <= OR_END:
                or_high = highs[i] if or_high is None else max(or_high, highs[i])
                or_bars += 1
        if or_high is None:
            continue
        # full bar availability in hold window (independent of fail-break)
        hold_avail = sum(1 for m in hms_list if HOLD_START <= m <= HOLD_END)
        holds = True
        fail_time = None
        hold_bars = 0
        for i, m in enumerate(hms_list):
            if HOLD_START <= m <= HOLD_END:
                hold_bars += 1
                if lows[i] <= or_high:
                    holds = False
                    fail_time = ts[i]
                    break
        entry_close = None
        entry_ts = None
        entry_et = None
        for i, m in enumerate(hms_list):
            if m == ENTRY_MINUTE:
                entry_ts = ts[i]
                entry_et = sub["et"][i]
                entry_close = closes[i]
                break
        if entry_close is None:
            continue
        if hold_avail == 0:
            continue  # no bars in hold window -> hold/fail indeterminate
        well = (or_bars == 5) and (hold_avail == 10)
        rows.append({
            "ticker": ticker, "et_date": et_date, "timestamp": entry_ts, "et": entry_et,
            "entry_close": entry_close, "gap": sub["gap"].first(), "open_0930": sub["open_0930"].first(),
            "prior_close": sub["prior_close"].first(), "or_high": or_high,
            "or_bars": or_bars, "hold_bars": hold_bars, "hold_avail": hold_avail,
            "holds_opening_range_flag": holds, "fail_time": fail_time,
            "well_covered": well,
        })
    if not rows:
        print(" no candidate signals")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({}).write_parquet(out)
        (Path(out).parent / f"{Path(out).stem}_summary.json").write_text(json.dumps({"n_signals": 0}))
        return
    sig = pl.DataFrame(rows).sort(["ticker", "et_date", "timestamp"])
    n_well = int(sig.filter(pl.col("well_covered")).height)
    print(f" signals (one per candidate ticker-day) {sig.height}"
          f"  hold {sig.filter(pl.col('holds_opening_range_flag')).height}"
          f"  fail {sig.filter(~pl.col('holds_opening_range_flag')).height}")
    print(f"  well-covered (or=5 bars & hold=10 bars): {n_well} ({n_well/sig.height:.1%})  sparse: {sig.height-n_well}")
    print(f"  fail_time present {sig.filter(pl.col('fail_time').is_not_null()).height}")

    # direction + segment label + gap bin + time_bucket (constant)
    sig = sig.with_columns([
        pl.when(pl.col("gap") > 0).then(pl.lit("up")).otherwise(pl.lit("down")).alias("gap_dir"),
        pl.when(pl.col("gap").abs() < 0.10).then(pl.lit("5-10%"))
          .when(pl.col("gap").abs() < 0.20).then(pl.lit("10-20%"))
          .otherwise(pl.lit("20%+")).alias("gap_bin"),
        pl.lit("09:30-10:00").alias("time_bucket"),
    ])
    sig = sig.with_columns(
        pl.when((pl.col("gap_dir") == "up") & pl.col("holds_opening_range_flag")).then(pl.lit("gap-up-hold"))
          .when((pl.col("gap_dir") == "up") & (~pl.col("holds_opening_range_flag"))).then(pl.lit("gap-up-fail"))
          .when((pl.col("gap_dir") == "down") & pl.col("holds_opening_range_flag")).then(pl.lit("gap-down-hold"))
          .otherwise(pl.lit("gap-down-fail")).alias("segment")
    )

    # forward returns via timestamp join at entry_ts + h
    sig = sig.with_row_index("__idx")
    for h in HORIZONS:
        fwd_map = clean.select(["ticker", "timestamp", "close"]).rename({"timestamp": "fwd_ts", "close": f"_fwd_{h}"})
        sig = sig.with_columns((pl.col("timestamp") + pl.duration(minutes=h)).cast(pl.Datetime("ns", "UTC")).alias("fwd_ts"))
        sig = sig.join(fwd_map, on=["ticker", "fwd_ts"], how="left")
        sig = sig.with_columns(((pl.col(f"_fwd_{h}") - pl.col("entry_close")) / pl.col("entry_close")).alias(f"fwd_ret_{h}m"))
        sig = sig.drop(["fwd_ts", f"_fwd_{h}"])

    # liquidity-driven forward-return nulls
    print(f"  fwd-ret nulls (thin tickers, exact-bar join): "
          f"h15 {sig['fwd_ret_15m'].is_null().sum()} h30 {sig['fwd_ret_30m'].is_null().sum()} h60 {sig['fwd_ret_60m'].is_null().sum()}")

    # ---- reporting ----
    segs = ["gap-up-hold", "gap-up-fail", "gap-down-hold", "gap-down-fail"]
    print("\n=== BY SEGMENT (exp_net = expectancy - roundtrip cost) ===")
    seg_m = {}
    for s in segs:
        sub = sig.filter(pl.col("segment") == s)
        print(f"\n{s} n={sub.height}")
        seg_m[s] = metrics(sub, cost)

    print("\n=== BASELINE / control ===")
    all_m = metrics(sig, cost)
    up_m = metrics(sig.filter(pl.col("gap_dir") == "up"), cost)
    dn_m = metrics(sig.filter(pl.col("gap_dir") == "down"), cost)

    def edge(a, b):
        d = {}
        for h in HORIZONS:
            if f"h{h}" in a and f"h{h}" in b:
                d[f"h{h}"] = {"A": a[f"h{h}"]["exp_net"], "B": b[f"h{h}"]["exp_net"],
                              "A-B": a[f"h{h}"]["exp_net"] - b[f"h{h}"]["exp_net"]}
        return d
    up_dir = edge(seg_m.get("gap-up-hold", {}), seg_m.get("gap-up-fail", {}))
    dn_dir = edge(seg_m.get("gap-down-fail", {}), seg_m.get("gap-down-hold", {}))
    print("\n=== HYPOTHESIS DIRECTION (exp_net diff) ===")
    print(" gap-up continuation: hold - fail")
    for k, v in up_dir.items():
        print(f"  {k}: hold {v['A']:.4%} fail {v['B']:.4%} diff {v['A-B']:.4%}")
    print(" gap-down fade: fail - hold")
    for k, v in dn_dir.items():
        print(f"  {k}: fail {v['A']:.4%} hold {v['B']:.4%} diff {v['A-B']:.4%}")

    # well-covered subset (only ticker-days with full 5-bar OR + 10-bar hold window)
    sig_w = sig.filter(pl.col("well_covered"))
    print(f"\n=== WELL-COVERED SUBSET (or=5 & hold=10 bars) n={sig_w.height} ===")
    wseg_m = {}
    for s in segs:
        sub = sig_w.filter(pl.col("segment") == s)
        print(f"\n well {s} n={sub.height}")
        wseg_m[s] = metrics(sub, cost)
    wup_dir = edge(wseg_m.get("gap-up-hold", {}), wseg_m.get("gap-up-fail", {}))
    wdn_dir = edge(wseg_m.get("gap-down-fail", {}), wseg_m.get("gap-down-hold", {}))
    print("\n=== WELL-COVERED HYPOTHESIS DIRECTION (exp_net diff) ===")
    print(" gap-up continuation: hold - fail")
    for k, v in wup_dir.items():
        print(f"  {k}: hold {v['A']:.4%} fail {v['B']:.4%} diff {v['A-B']:.4%}")
    print(" gap-down fade: fail - hold")
    for k, v in wdn_dir.items():
        print(f"  {k}: fail {v['A']:.4%} hold {v['B']:.4%} diff {v['A-B']:.4%}")

    print("\n=== by gap_bin (all) ===")
    gap_m = {}
    for b in ["5-10%", "10-20%", "20%+"]:
        sub = sig.filter(pl.col("gap_bin") == b)
        if sub.height == 0:
            continue
        print(f"\n {b} n={sub.height}")
        gap_m[b] = metrics(sub, cost)

    # ---- spot-check: one candidate ticker-day, print bars vs flags ----
    prow = sig.head(1)
    t, dd = prow["ticker"][0], prow["et_date"][0]
    sub = clean.filter((pl.col("ticker") == t) & (pl.col("et_date") == dd)).sort("timestamp")
    sub = sub.with_columns((pl.col("et").dt.hour().cast(pl.Int32) * 60 + pl.col("et").dt.minute()).alias("hm"))
    sub = sub.filter((pl.col("hm") >= 570) & (pl.col("hm") <= 590)).select(["et", "hm", "open", "high", "low", "close"])
    print(f"\n=== SPOT-CHECK {t} {dd} or_high={prow['or_high'][0]} holds={prow['holds_opening_range_flag'][0]} entry_close={prow['entry_close'][0]} gap={prow['gap'][0]:.3%} ===")
    print(sub)

    # ---- save ----
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    keep_cols = ["timestamp", "et", "et_date", "ticker", "prior_close", "open_0930", "gap", "or_high",
                 "or_bars", "hold_bars", "hold_avail", "well_covered", "dollar_open",
                 "entry_close", "holds_opening_range_flag", "fail_time", "gap_dir", "segment", "gap_bin",
                 "time_bucket"] + [f"fwd_ret_{h}m" for h in HORIZONS]
    sig.select([c for c in keep_cols if c in sig.columns]).write_parquet(out)
    print(f"\nWrote {out} {sig.height} rows")

    summary = {
        "hypothesis": "H010",
        "data": "data/clean_ohlcv_2025-06.parquet (June only, certified)",
        "dates": [str(d) for d in sorted(sig["et_date"].unique().to_list())],
        "n_sessions_signal": int(sig["et_date"].n_unique()),
        "n_signals": int(sig.height),
        "n_hold": int(sig.filter(pl.col("holds_opening_range_flag")).height),
        "n_fail": int(sig.filter(~pl.col("holds_opening_range_flag")).height),
        "n_well_covered": int(sig_w.height),
        "coverage_fraction": float(sig_w.height / sig.height),
        "cost_bps_per_side": cost_bps,
        "roundtrip_cost": cost,
        "all": all_m, "up_dir": up_m, "down_dir": dn_m,
        "segments": seg_m,
        "hyp_direction": {"gap_up_hold_minus_fail": up_dir, "gap_down_fail_minus_hold": dn_dir},
        "well_covered": {"segments": wseg_m,
                         "hyp_direction": {"gap_up_hold_minus_fail": wup_dir, "gap_down_fail_minus_hold": wdn_dir}},
        "by_gap_bin": gap_m,
        "windows": {"or": "09:30-09:34 (5 bars)", "hold": "09:35-09:44 (10 bars)",
                    "entry": "09:45 close", "bar_stamp": "START"},
        "params": {"gap_min_pct": 5, "entry": "09:45 close", "horizons_min": HORIZONS,
                   "bar_stamp": "START", "universe": "ticker-days with true 09:30 bar",
                   "well_covered_required": "or=5 bars & hold=10 bars",
                   "min_dollar_open": min_dollar_open},
    }
    jpath = out.parent / f"{out.stem}_summary.json"
    with open(jpath, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Wrote {jpath}")
    return summary

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clean-dir", type=Path, required=True)
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--min-dollar-open", type=float, default=0.0)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h010_results.parquet"))
    a = p.parse_args()
    run(a.clean_dir, a.cost_bps, a.out, a.min_dollar_open)

if __name__ == "__main__":
    main()
