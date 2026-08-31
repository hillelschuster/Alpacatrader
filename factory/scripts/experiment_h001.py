"""H001 pullback-reclaim — ponytail minimal. Chronological, no lookahead."""
import argparse, json
from pathlib import Path
from datetime import time
import polars as pl

HORIZONS = [1,3,5,15,30,60]

def time_bucket(et):
    hm = et.hour*60+et.minute
    if hm < 600: return "09:30-10:00"
    h=et.hour
    return f"{h:02d}:00-{h+1:02d}:00" if h<15 else "15:00-16:00"

def run(events_path, clean_path, pullback_pct, reclaim, cost_bps, out_path):
    events = pl.read_parquet(events_path)
    # only first_entry
    if "entry_type" in events.columns:
        events = events.filter(pl.col("entry_type")=="first_entry")
    events = events.sort("timestamp")
    n_events = events.height
    print(f"Events (first_entry): {n_events}")

    # load clean, add ET helpers
    clean = pl.read_parquet(clean_path)
    # ET conversion
    clean = clean.with_columns(
        pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
        pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"),
    )
    # sort for grouping
    clean = clean.sort(["ticker","timestamp"])

    # build dict: (ticker, et_date) -> bars df as python lists for speed
    # group via polars partition would be heavy; use dict of slices
    # collect unique keys present in events to filter clean early
    keys = set(zip(events["ticker"].to_list(), events["et_date"].to_list() if "et_date" in events.columns else
                   [e.date() for e in events["et"].to_list()]))
    # if events lacks et_date compute
    if "et_date" not in events.columns:
        events = events.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
        keys = set(zip(events["ticker"].to_list(), events["et_date"].to_list()))

    # filter clean to only relevant ticker/dates
    # build filter via join trick: create small df of keys and join
    key_df = pl.DataFrame({"ticker":[k[0] for k in keys], "et_date":[k[1] for k in keys]})
    clean = clean.join(key_df, on=["ticker","et_date"], how="inner")

    # group
    groups = {}
    for (ticker, d), sub in clean.group_by(["ticker","et_date"]):
        # keep sorted lists
        sub = sub.sort("timestamp")
        groups[(ticker, d)] = sub

    # ponytail: no config, fixed 15:00 ET cutoff
    cutoff_t = time(15,0)
    pullback_thr = pullback_pct / 100

    rows = []
    for r in events.iter_rows(named=True):
        ticker = r["ticker"]
        et_date = r["et_date"]
        ts = r["timestamp"]
        sess_high = r["session_high"]
        rank = r.get("rank")
        pct_gain = r.get("pct_gain")
        tbucket = r.get("time_bucket")
        # ensure uuid for join later
        bars = groups.get((ticker, et_date))
        if bars is None or bars.height == 0:
            rows.append(dict(timestamp=ts, ticker=ticker, rank=rank, pct_gain=pct_gain, time_bucket=tbucket,
                             session_high=sess_high, pullback_start=None, pullback_low=None,
                             signal_ts=None, signal_price=None, has_signal=False, no_signal_reason="no_bars"))
            continue
        # find idx of entry bar (exact ts match)
        ts_list = bars["timestamp"].to_list()
        try:
            idx0 = ts_list.index(ts)
        except ValueError:
            # find first bar >= ts
            idx0 = -1
            for i, t in enumerate(ts_list):
                if t >= ts:
                    idx0 = i
                    break
            if idx0 == -1:
                rows.append(dict(timestamp=ts, ticker=ticker, rank=rank, pct_gain=pct_gain, time_bucket=tbucket,
                                 session_high=sess_high, pullback_start=None, pullback_low=None,
                                 signal_ts=None, signal_price=None, has_signal=False, no_signal_reason="no_entry_bar"))
                continue

        close_list = bars["close"].to_list()
        high_list = bars["high"].to_list()
        low_list = bars["low"].to_list()
        et_list = bars["et"].to_list()

        high = sess_high
        # include entry bar high
        high = max(high, high_list[idx0])

        pullback_start = None
        pullback_low = None
        signal_idx = None
        pullback_found = False
        no_reason = None

        for i in range(idx0+1, len(ts_list)):
            et = et_list[i]
            # cutoff 15:00 ET
            if et.time() >= cutoff_t:
                break
            cur_close = close_list[i]
            cur_high = high_list[i]
            cur_low = low_list[i]

            if not pullback_found:
                high = max(high, cur_high)  # track high via bar high
                # detect pullback: drop from session high >= thr (use low for intraday wick detection)
                if cur_low <= high * (1 - pullback_thr):
                    pullback_found = True
                    pullback_start = high
                    pullback_low = cur_low
                elif cur_close <= high * (1 - pullback_thr):
                    pullback_found = True
                    pullback_start = high
                    pullback_low = cur_low
            else:
                if cur_low < pullback_low:
                    pullback_low = cur_low
                # reclaim: close above pullback_start (or high above)
                do_reclaim = reclaim  # flag, but spec says reclaim above pullback_start
                # if reclaim True use close > pullback_start else also same; param kept for CLI compat
                if cur_close > pullback_start:
                    signal_idx = i
                    break

        if signal_idx is None:
            reason = "no_reclaim" if pullback_found else "no_pullback"
            # check if cutoff hit
            if pullback_found is False and no_reason is None:
                pass
            rows.append(dict(timestamp=ts, ticker=ticker, rank=rank, pct_gain=pct_gain, time_bucket=tbucket,
                             session_high=sess_high, pullback_start=pullback_start, pullback_low=pullback_low,
                             signal_ts=None, signal_price=None, has_signal=False, no_signal_reason=reason))
            continue

        sig_ts = ts_list[signal_idx]
        sig_price = close_list[signal_idx]
        # forward returns: look ahead N bars (not wall-clock gap corrected — bars are 1-min RTH, dense)
        fwd = {}
        mfe = None; mae = None
        closes_ahead = close_list
        highs_ahead = high_list
        lows_ahead = low_list
        for h in HORIZONS:
            j = signal_idx + h
            if j < len(closes_ahead):
                fwd[h] = (closes_ahead[j] - sig_price) / sig_price
            else:
                fwd[h] = None
        # MFE/MAE over 60m
        end = min(signal_idx+60, len(closes_ahead)-1)
        if end > signal_idx:
            window_highs = highs_ahead[signal_idx+1:end+1]
            window_lows = lows_ahead[signal_idx+1:end+1]
            if window_highs:
                mfe = (max(window_highs) - sig_price)/sig_price
                mae = (min(window_lows) - sig_price)/sig_price

        row = dict(timestamp=ts, ticker=ticker, rank=rank, pct_gain=pct_gain, time_bucket=tbucket,
                   session_high=sess_high, pullback_start=pullback_start, pullback_low=pullback_low,
                   signal_ts=sig_ts, signal_price=sig_price, has_signal=True, no_signal_reason=None,
                   mfe_60m=mfe, mae_60m=mae, et_date=et_date)
        for h in HORIZONS:
            row[f"fwd_ret_{h}m"] = fwd[h]
        rows.append(row)

    df = pl.DataFrame(rows)
    # ensure et_date for split
    if "et_date" not in df.columns and "timestamp" in df.columns:
        df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))

    total = df.height
    sig = df.filter(pl.col("has_signal"))
    n_sig = sig.height
    n_no = total - n_sig
    print(f"Total first_entry: {total}  with_signal: {n_sig} ({n_sig/total*100:.1f}%)  no_signal: {n_no} ({n_no/total*100:.1f}%)")
    if df.filter(pl.col("has_signal")==False).height>0:
        print(" no_signal breakdown:")
        for rr in df.filter(pl.col("has_signal")==False).group_by("no_signal_reason").len().sort("no_signal_reason").iter_rows(named=True):
            print(f"  {rr['no_signal_reason']}: {rr['len']}")

    # metrics helper
    def metrics(sub, label):
        if sub.height==0:
            print(f" {label}: n=0")
            return {}
        out={}
        for h in HORIZONS:
            col=f"fwd_ret_{h}m"
            if col not in sub.columns: continue
            s=sub[col].drop_nulls()
            if s.len()==0: continue
            wr=(s>0).sum()/s.len()
            avg=s.mean(); med=s.median()
            wins=s.filter(s>0); losses=s.filter(s<=0)
            avg_w=wins.mean() if wins.len()>0 else 0
            avg_l=losses.mean() if losses.len()>0 else 0
            # expectancy gross
            exp = wr*avg_w + (1-wr)*avg_l if wins.len()>0 or losses.len()>0 else avg
            # net with cost (roundtrip cost_bps*2? spec says cost-bps per side = 10 -> 20 rt; we treat param as per-side? Implement as roundtrip = cost_bps*2? CLI --cost-bps 10 means 10 per side -> use *2)
            # ponytail: interpret cost_bps as per-side, apply roundtrip
            cost = cost_bps*2/10000
            exp_net = exp - cost
            # win net
            wr_net=(s > cost).sum()/s.len() if s.len()>0 else None
            print(f" {label} h={h}m: n={s.len()} wr={wr:.3f} wr_net={wr_net:.3f} avg={avg:.4f} med={med:.4f} exp={exp:.4f} exp_net={exp_net:.4f} avg_win={avg_w:.4f} avg_loss={avg_l:.4f}")
            out[f"h{h}"]={"n":s.len(),"wr":wr,"wr_net":wr_net,"avg":avg,"median":med,"exp":exp,"exp_net":exp_net,"avg_win":avg_w,"avg_loss":avg_l}
        return out

    print("\n=== ALL signals ===")
    all_m = metrics(sig, "ALL")

    # segmentation
    print("\n=== by time_bucket ===")
    seg_tb={}
    if "time_bucket" in sig.columns:
        for tb in sig["time_bucket"].unique().sort().to_list():
            seg_tb[tb]=metrics(sig.filter(pl.col("time_bucket")==tb), tb)

    print("\n=== by rank bucket ===")
    seg_rank={}
    # rank bins: 1-5, 6-10, 11-20
    def rank_bin(r):
        if r<=5: return "1-5"
        if r<=10: return "6-10"
        return "11-20"
    if "rank" in sig.columns and sig.height>0:
        bins=sig.with_columns(pl.col("rank").map_elements(rank_bin, return_dtype=pl.String).alias("rank_bin"))
        for b in ["1-5","6-10","11-20"]:
            seg_rank[b]=metrics(bins.filter(pl.col("rank_bin")==b), b)

    print("\n=== by pct_gain bucket ===")
    seg_gain={}
    def gain_bin(g):
        if g is None: return "unknown"
        if g<10: return "<10%"
        if g<20: return "10-20%"
        return "20%+"
    if "pct_gain" in sig.columns and sig.height>0:
        gb=sig.with_columns(pl.col("pct_gain").map_elements(gain_bin, return_dtype=pl.String).alias("gain_bin"))
        for b in ["<10%","10-20%","20%+"]:
            seg_gain[b]=metrics(gb.filter(pl.col("gain_bin")==b), b)

    # chronological split: train first 14 days, test last 7
    print("\n=== Chronological split: train first 14 days / test last 7 ===")
    dates_sorted = sorted([d for d in df["et_date"].unique().to_list() if d is not None])
    print(f" dates: {dates_sorted[0]} .. {dates_sorted[-1]} ({len(dates_sorted)} days)")
    split = 14
    train_dates = set(dates_sorted[:split])
    test_dates = set(dates_sorted[split:])
    train_sig = sig.filter(pl.col("et_date").is_in(list(train_dates)))
    test_sig = sig.filter(pl.col("et_date").is_in(list(test_dates)))
    print(f" train: {len(train_dates)} days n_sig={train_sig.height}")
    train_m=metrics(train_sig, "TRAIN")
    print(f" test: {len(test_dates)} days n_sig={test_sig.height}")
    test_m=metrics(test_sig, "TEST")

    # save parquet + json
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # signals parquet: only signal rows with metrics
    sig.write_parquet(out_path)
    print(f"\nWrote {out_path} ({sig.height} signals)")

    summary = {
        "total_first_entry": total,
        "with_signal": n_sig,
        "no_signal": n_no,
        "signal_rate": n_sig/total if total else 0,
        "pullback_pct": pullback_pct,
        "cost_bps_per_side": cost_bps,
        "roundtrip_cost": cost_bps*2/10000,
        "all_metrics": all_m,
        "by_time_bucket": seg_tb,
        "by_rank": seg_rank,
        "by_gain": seg_gain,
        "train_metrics": train_m,
        "test_metrics": test_m,
        "dates": [str(d) for d in dates_sorted],
        "train_dates": [str(d) for d in sorted(train_dates)],
        "test_dates": [str(d) for d in sorted(test_dates)],
    }
    # json-serializable: already floats
    jpath = out_path.parent / "h001_summary.json"
    # also respect arg if out different?
    # spec says factory/artifacts/h001_summary.json
    jpath2 = Path("factory/artifacts/h001_summary.json")
    jpath2.parent.mkdir(parents=True, exist_ok=True)
    # write both
    import json as _json
    with open(jpath, "w") as f: _json.dump(summary, f, indent=2, default=str)
    if jpath != jpath2:
        with open(jpath2, "w") as f: _json.dump(summary, f, indent=2, default=str)
    print(f"Wrote {jpath} and {jpath2}")
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--events", type=Path, default=Path("factory/artifacts/events_all.parquet"))
    p.add_argument("--clean", type=Path, default=Path("data/clean_ohlcv_2025-07.parquet"))
    p.add_argument("--pullback-pct", type=float, default=1.0)
    p.add_argument("--reclaim", action="store_true", default=True)
    p.add_argument("--no-reclaim", dest="reclaim", action="store_false")
    p.add_argument("--cost-bps", type=float, default=10)
    p.add_argument("--out", type=Path, default=Path("factory/artifacts/h001_results.parquet"))
    a=p.parse_args()
    run(a.events, a.clean, a.pullback_pct, a.reclaim, a.cost_bps, a.out)

if __name__=="__main__":
    main()
