"""Residual gate bounds for the canonical strategy (external review follow-up).

Answers 4 questions with data, no strategy changes:
  Q1  n_bars>=30 (research full-day screen): how many E6 entries had <30 real bars
      AT their entry minute, and what was their PnL? (decides whether the causalized
      "30 bars so far" rule costs anything or should be dropped)
  Q2  rank population: research top-20 rank is computed among gap==1 & eligible tickers
      from the cumulative universe-tag cache. Live superset adds tickers never cached.
      Any ticker with pct_gain >= 8% at minute t is its own day's candidate => cached
      => present in BOTH populations. So entries with gain >= 8% must have IDENTICAL
      rank under the full live population. Verify empirically: recompute dense rank of
      every E6 entry at its entry minute over the FULL clean cross-section (gap==1,
      no cache, no universe filter = maximal live superset) and compare with research rank.
  Q3  split_suspect (full-day close ratio outside [0.5,2], knowable only EOD): count
      excluded ticker-days that were also 8%+ gainers (potential lost E6 candidates).
  Q4  market_ret_5m live proxy: distribution of the research statistic + score
      sensitivity of E6 entries to a constant m5 shift (live watchlist-median bias is
      ~constant within a minute => shifts all candidates' scores together; vis_rank
      invariant; only theta margin moves).
"""
import os, pickle
import numpy as np
import polars as pl
from exposure_design import load_stream, FEATS, THETA, ENTRY_CAP

if os.path.basename(os.getcwd()) == "scripts":
    os.chdir("../..")
model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
MONTHS = {"2025": ["2025-08", "2025-09", "2025-10", "2025-11", "2025-12"],
          "2026": ["2026-01", "2026-02", "2026-03"]}


def e6_entries(year):
    pool, m3, comp = load_stream(year, model)
    comp = comp.filter(pl.col("tod_min") <= ENTRY_CAP)
    eps = {}
    for r in comp.to_dicts():
        eps.setdefault((r["ticker"], str(r["et_date"])[:10]), []).append(r)
    out = []
    for key, evs in eps.items():
        evs.sort(key=lambda r: r["tod_min"])
        pool_evs = [e for e in evs if e["rvol"] is not None and e["rvol"] > 8]
        if not pool_evs:
            continue
        picks, t = [pool_evs[0]], pool_evs[0]["tod_min"]
        while True:
            nxt = next((x for x in pool_evs if x["tod_min"] >= t + 61), None)
            if not nxt:
                break
            picks.append(nxt); t = nxt["tod_min"]
        out.extend(picks)
    return pl.DataFrame(out)


entries = pl.concat([e6_entries("2025"), e6_entries("2026")], how="vertical")
entries = entries.with_columns(pl.col("et_date").dt.to_string().str.slice(0, 7).alias("month"),
                               ((pl.col("fwd60_t1entry") - 0.002) * 1e4).alias("net_bps"))
print(f"=== E6 entries: n={entries.height} (2025+2026) ===")

# ---------------- Q4: market_ret_5m scale + score sensitivity ----------------
m5 = entries["market_ret_5m"].to_numpy()
print("\n[Q4] research market_ret_5m distribution (decimal):")
for q in (5, 25, 50, 75, 95):
    print(f"  p{q}: {np.percentile(m5, q):+.4f}")
print(f"  share |m5| > 0.5%: {(np.abs(m5) > 0.005).mean():.3f}")
ex = entries["excess_gain"].to_numpy()
print("  excess_gain (pct pts) p5/p50/p95: "
      f"{np.percentile(ex,5):+.2f} / {np.percentile(ex,50):+.2f} / {np.percentile(ex,95):+.2f}")
base_score = entries["score"].to_numpy()
for dm in (0.005, 0.01, 0.015):
    X = (entries.select(FEATS)
         .with_columns(pl.col("market_ret_5m") + dm, pl.col("excess_gain") - 100 * dm)
         .to_numpy().astype(np.float32))
    s2 = model.predict(X, num_iteration=model.best_iteration)
    fails = (s2 < THETA) & (base_score >= THETA)
    print(f"  m5 shift {dm*100:+.1f}%: entries falling below theta: {fails.mean():.3f}; "
          f"mean |dscore|/score: {np.mean(np.abs(s2 - base_score) / np.abs(base_score)):.3f}")

# ---------------- per-month data work for Q1/Q2/Q3 ----------------
q1_rows, q2_rows, q3_rows = [], [], []
for m in sorted(sum(MONTHS.values(), [])):
    em = entries.filter(pl.col("month") == m)
    clean = pl.scan_parquet(f"data/clean_ohlcv_{m}.parquet")
    clean = clean.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC").alias("ts_utc"))
    cal = (clean.select(pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
           .unique().sort("et_date").with_row_index("session_pos").collect())

    # session table (same construction as certify_month._session_meta, over clean input)
    sess_close = (clean.select("ticker", "ts_utc", "close")
                  .with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
                  .select("ticker", "ts_utc", "close", "et_date")
                  .group_by("ticker", "et_date")
                  .agg(pl.col("close").sort_by("ts_utc").last().alias("session_close"))
                  .collect())
    sess = (sess_close.sort("ticker", "et_date")
            .join(cal, on="et_date", how="left")
            .with_columns([
                pl.col("session_close").shift(1).over("ticker").alias("prev_close"),
                pl.col("session_pos").shift(1).over("ticker").alias("prev_pos")])
            .with_columns([
                (pl.col("session_pos") - pl.col("prev_pos")).alias("gap_sessions"),
                ((pl.col("session_close") / pl.col("prev_close") < 0.5)
                 | (pl.col("session_close") / pl.col("prev_close") > 2.0)).fill_null(False).alias("split_suspect")]))
    # note: first session of the month has within-month prev null -> gap unknown -> excluded
    # from n8 (slight undercount, conservative for us). certify had prior-month span.

    # Q3: split-suspect ticker-days that were also 8%+ gainers
    day_hi = (clean.group_by(pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"), "ticker")
              .agg(pl.col("high").max().alias("day_max_high")).collect()
              .join(sess.select("ticker", "et_date", "prev_close", "split_suspect"),
                    on=["ticker", "et_date"], how="inner"))
    day_hi = day_hi.with_columns(((pl.col("day_max_high") / pl.col("prev_close") - 1) * 100).alias("day_max_gain"))
    sp = day_hi.filter(pl.col("split_suspect") & (pl.col("day_max_gain") >= 8.0))
    q3_rows.append({"month": m, "n_split_total": int(day_hi["split_suspect"].sum()),
                    "n_split_gain8": sp.height})
    ex_ratio = sp.join(sess.select(["ticker", "et_date", "session_close"]),
                       on=["ticker", "et_date"], how="left").with_columns(
        (pl.col("session_close") / pl.col("prev_close")).alias("close_ratio"))
    print(f"[{m}] split_suspect days: {int(day_hi['split_suspect'].sum())}, "
          f"of which 8%+ gainers: {sp.height}"
          + (f", close_ratio p50={np.percentile(ex_ratio['close_ratio'],50):.2f} "
             f"p90={np.percentile(ex_ratio['close_ratio'],90):.2f}" if sp.height else ""))

    if em.height == 0:
        continue

    # Q1: real bars available at entry minute (clean rows = real bars, vol>=100 filtered)
    tick = set(em["ticker"].to_list())
    dates = set(em["et_date"].dt.to_date().to_list()) if em["et_date"].dtype != pl.Date else set(em["et_date"].to_list())
    cf = (clean.filter(pl.col("ticker").is_in(list(tick)))
          .select("ticker", "ts_utc",
                  (pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.hour().cast(pl.Int32) * 60
                   + pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.minute().cast(pl.Int32) - 570).alias("etod_rel"))
          .with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
          .collect())
    cf = cf.filter(pl.col("et_date").is_in(list(dates)))
    tod_lists = cf.group_by("ticker", "et_date").agg(pl.col("etod_rel").sort().alias("tods")).to_dicts()
    tmap = {(r["ticker"], r["et_date"]): r["tods"] for r in tod_lists}
    ej = em.with_columns(pl.col("et_date").dt.to_date().alias("et_date")) \
        if em["et_date"].dtype != pl.Date else em
    bars = [int(np.searchsorted(tmap.get((r["ticker"], r["et_date"]), []), r["tod_min"], side="right"))
            for r in ej.select("ticker", "et_date", "tod_min").to_dicts()]
    ej = ej.with_columns(pl.Series("bars_so_far", bars))
    q1_rows.append(ej.select("ticker", "et_date", "tod_min", "bars_so_far", "net_bps",
                             "pct_gain_grid", "fwd60_t1entry"))

    # Q2: full live-population dense rank at entry minutes (gap==1, price/vol filters only;
    # no universe-cache filter = maximal superset)
    ts = em.select(pl.col("timestamp").dt.cast_time_unit("ns").dt.replace_time_zone("UTC").alias("timestamp")).unique()
    xs = (clean.join(ts.lazy(), on="timestamp", how="semi")
          .select("timestamp", "ticker", "close", "ts_utc")
          .with_columns(pl.col("ts_utc").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
          .collect())
    tags = pl.read_parquet("data/universe_tags.parquet").filter(
        pl.col("exchange").is_in(["NYQ", "NMS", "ASE"]) & (pl.col("quote_type") == "EQUITY"))
    listed = set(tags["ticker"].to_list())
    xs = xs.with_columns(pl.col("ticker").is_in(listed).alias("listed"))
    xs = (xs.join(sess.filter(pl.col("gap_sessions") == 1).select("ticker", "et_date", "prev_close"),
                  on=["ticker", "et_date"], how="inner")
          .with_columns(((pl.col("close") / pl.col("prev_close") - 1) * 100).alias("pg"))
          .filter(pl.col("pg").is_not_null()))
    xs = xs.with_columns(pl.col("pg").rank("dense", descending=True).over("timestamp").alias("full_rank"))
    xsl = xs.filter(pl.col("listed")).with_columns(
        pl.col("pg").rank("dense", descending=True).over("timestamp").alias("listed_rank"))
    emj = em.with_columns(pl.col("timestamp").dt.cast_time_unit("ns").dt.replace_time_zone("UTC").alias("timestamp"))
    ej2 = emj.join(xsl.select("timestamp", "ticker", "full_rank", "listed_rank"), on=["timestamp", "ticker"], how="left")
    q2_rows.append(ej2.select("ticker", "et_date", "tod_min", "rank", "full_rank", "listed_rank", "pct_gain_grid"))

q1 = pl.concat(q1_rows) if q1_rows else None
q2 = pl.concat(q2_rows) if q2_rows else None

print("\n[Q1] real bars available at E6 entry minute (research screen: full-day n_bars>=30):")
if q1 is not None:
    lo = q1.filter(pl.col("bars_so_far") < 30)
    hi = q1.filter(pl.col("bars_so_far") >= 30)
    nn = lambda d: d.filter(pl.col("fwd60_t1entry").is_not_null())
    print(f"  entries total {q1.height}; <30 bars: {lo.height} ({lo.height/q1.height:.1%}); "
          f"labels: lo mean net {nn(lo)['net_bps'].mean() if lo.height else float('nan'):+.1f} bps "
          f"(n={nn(lo).height}), hi mean net {nn(hi)['net_bps'].mean():+.1f} bps (n={nn(hi).height})")
    if lo.height:
        print(f"  <30-bars entries tod_min: min={lo['tod_min'].min()} p50={int(np.percentile(lo['tod_min'],50))} max={lo['tod_min'].max()}")
        print(f"  <30-bars pct_gain>=8% share: {(lo['pct_gain_grid']>=8).mean():.3f}")

print("\n[Q2] research rank vs FULL live-population rank at entry minute:")
if q2 is not None:
    v = q2.filter(pl.col("full_rank").is_not_null())
    same = (v["rank"] == v["full_rank"])
    print(f"  matched entries: {v.height}/{q2.height} (unmatched = ticker absent from cross-section, i.e. no bar at t)")
    print(f"  identical rank: {same.mean():.4f}")
    d = v.filter(~same)
    if d.height:
        print(f"  differing: {d.height} -> pct_gain range {d['pct_gain_grid'].min():.1f}..{d['pct_gain_grid'].max():.1f}")
    print(f"  share entries with pct_gain>=8%: {(v['pct_gain_grid']>=8).mean():.3f}")
    top20_full = (v["full_rank"] <= 20)
    print(f"  still top-20 under FULL population: {top20_full.mean():.4f}")
    vl = q2.filter(pl.col("listed_rank").is_not_null())
    sameL = (vl["rank"] == vl["listed_rank"])
    print(f"\n[Q2b] research rank vs LISTED-EQUITIES-ONLY population (exchange/quote_type known):")
    print(f"  matched: {vl.height}/{q2.height}; identical rank: {sameL.mean():.4f}")
    top20L = (vl["listed_rank"] <= 20)
    print(f"  still top-20 under LISTED population: {top20L.mean():.4f}")
    dl = vl.filter(~sameL)
    if dl.height:
        print(f"  differing: {dl.height} -> pct_gain range {dl['pct_gain_grid'].min():.1f}..{dl['pct_gain_grid'].max():.1f}")
        both20 = dl.filter((dl["rank"] <= 20) & (dl["listed_rank"] <= 20)).height
        rr20 = dl.filter((dl["rank"] <= 20) & (dl["listed_rank"] > 20)).height
        print(f"  of differing: both top-20 {both20}, research-only top-20 {rr20}")

print("\n[Q3] split_suspect excluded ticker-days that were 8%+ gainers (potential lost candidates):")
tot_gain8 = sum(r["n_split_gain8"] for r in q3_rows)
print(f"  total across 8 target months: {tot_gain8} (~{tot_gain8/8:.1f}/month) — cf. ~1071 E6 entries/8mo")
