"""Feature-parity test: live engine (src/live/features.py) vs research parquet.

Replays 2025-08-01 through the live TickerSession engine minute-by-minute and compares
the 30 FEATS against the research features grid (features_2025-08.parquet). Baselines,
prev_close and market_ret_5m come from research helpers (build_features.py) so the test
isolates per-ticker feature math. Then replays M3+composite+E6 decisions on the day and
compares entry sets against the same rules applied to research rows.

Run:
  uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
    python tests/test_feature_parity.py
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, ".")
sys.path.insert(0, "factory/scripts")
from src.live.features import TickerSession, FEATS, MOD0, qualifies  # noqa: E402
import build_features as bf  # research helpers (functions only; main guarded)  # noqa: E402

MONTH = "2025-08"
DAY = np.datetime64("2025-08-01")
THETA = 0.00115
ENTRY_CAP_TOD = 328
HOLD = 60
CYCLES = 3
RTOL = 1e-9
ATOL = 1e-12


def _close(a, b):
    if a is None or b is None:
        return (a is None) == (b is None)
    a, b = float(a), float(b)
    if np.isnan(a) and np.isnan(b):
        return True
    return abs(a - b) <= max(ATOL, RTOL * max(abs(a), abs(b)))


def main():
    feats = pl.read_parquet(f"data/ml_features/features_{MONTH}.parquet")
    ev = feats.filter(pl.col("et_date") == DAY)
    tickers = sorted(ev["ticker"].unique().to_list())
    print(f"day {DAY} events={ev.height} tickers={len(tickers)}")

    # research context: span = prior month + month (event tickers only, pushdown),
    # run through the SAME filters as build_features.load_clean, then research helpers
    spans = []
    for m in ("2025-07", MONTH):
        lf = pl.scan_parquet(f"data/clean_ohlcv_{m}.parquet")
        lf = lf.with_columns(
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
            pl.col("timestamp").dt.convert_time_zone("America/New_York").alias("et"),
            pl.col("timestamp").dt.convert_time_zone("America/New_York").dt.date().alias("et_date"))
        lf = lf.with_columns(
            (pl.col("et").dt.hour().cast(pl.Int32) * 60
             + pl.col("et").dt.minute().cast(pl.Int32)).alias("tod_min"))
        lf = lf.filter((pl.col("tod_min") >= 570) & (pl.col("tod_min") < 960)
                       & (pl.col("close") >= 2.0) & (pl.col("volume") >= 100))
        if m == "2025-07":
            lf = lf.filter(pl.col("ticker").is_in(tickers))
        spans.append(lf)
    span_jul = spans[0].collect()
    span_aug_all = spans[1]  # keep lazy for the full-market m5

    cur = span_aug_all.filter(pl.col("et_date") == DAY)
    cur_ev = cur.filter(pl.col("ticker").is_in(tickers)).collect()
    span_ev = pl.concat([span_jul, cur_ev], how="vertical")

    base = bf.build_baselines(span_ev)
    base_day = base.filter(pl.col("et_date") == DAY)
    evecs = {r["ticker"]: [r[f"e{i}"] for i in range(7)] for r in base_day.to_dicts()}

    prev = bf.session_prev_close(span_ev).filter(pl.col("et_date") == DAY)
    prevs = {r["ticker"]: r["prev_close"] for r in prev.to_dicts()}

    mkt = bf.market_frame(cur.collect())
    mkt = {r["tod_min"]: r["market_ret_5m"] for r in mkt.filter(pl.col("et_date") == DAY).to_dicts()}

    # bars per event ticker for the day
    bars = {}
    for r in cur_ev.sort("timestamp").to_dicts():
        bars.setdefault(r["ticker"], {})[r["tod_min"]] = (
            r["open"], r["high"], r["low"], r["close"], r["volume"])
    ev_by_ticker = {}
    for r in ev.to_dicts():
        ev_by_ticker.setdefault(r["ticker"], []).append(r)

    # ---- replay and compare features ----
    compare_cols = [c for c in FEATS if c not in ("rank", "market_ret_5m", "tod_min", "dow")] \
        + ["cum_dv", "market_ret_5m", "tod_min", "dow"]
    fails = {}
    n_rows = 0
    for tk in tickers:
        tday = bars.get(tk, {})
        if not tday:
            continue
        pcv = prevs.get(tk)
        evec = evecs.get(tk)
        baseline_sessions = [evec] * 20 if (evec is not None and all(x is not None for x in evec)) else None
        sess = None
        ev_rows = {r["tod_min"]: r for r in ev_by_ticker[tk]}
        dow = ev_by_ticker[tk][0]["dow"]
        for tod in range(570, 960):
            b = tday.get(tod)
            rel = tod - MOD0
            if sess is None:
                if b is None or not qualifies(b[3], b[4]):
                    continue
                sess = TickerSession(rel, b, pcv, True)
            else:
                sess.on_minute(rel, b if (b is not None and qualifies(b[3], b[4])) else None)
            if rel in ev_rows:
                row = ev_rows[rel]
                f = sess.features(rel, row["rank"], mkt.get(tod), baseline_sessions, dow)
                f["cum_dv"] = sess.cum_dv
                n_rows += 1
                for col in compare_cols:
                    mine, ref = f.get(col), row.get(col)
                    if isinstance(ref, (bool, np.bool_)) or isinstance(mine, bool):
                        ok = bool(mine) == bool(ref)
                    elif col in ("n_hod_breaks", "trap_reclaim", "above_vwap", "n_up_bars_15", "tod_min", "dow"):
                        ok = (mine is None and ref is None) or (mine is not None and ref is not None and int(mine) == int(ref))
                    else:
                        ok = _close(mine, ref)
                    if not ok:
                        fails.setdefault(col, []).append((tk, rel, mine, ref))
    print(f"\ncompared {n_rows} event rows x {len(compare_cols)} cols")
    total_bad = sum(len(v) for v in fails.values())
    if total_bad:
        for col, v in fails.items():
            print(f"MISMATCH {col}: {len(v)}  e.g. {v[:3]}")
    else:
        print("ALL FEATURE COLUMNS MATCH")

    # ---- decision replay: M3 + composite + E6 on engine features vs research rows ----
    model = pickle.load(open("factory/artifacts/ml/model_v1.pkl", "rb"))
    rows = []
    # rebuild engine states once more, capturing features at every event minute
    engine_rows = []
    for tk in tickers:
        tday = bars.get(tk, {})
        if not tday:
            continue
        pcv = prevs.get(tk)
        evec = evecs.get(tk)
        baseline_sessions = [evec] * 20 if (evec is not None and all(x is not None for x in evec)) else None
        sess = None
        ev_rows = {r["tod_min"]: r for r in ev_by_ticker[tk]}
        dow = ev_by_ticker[tk][0]["dow"]
        for tod in range(570, 960):
            b = tday.get(tod)
            rel = tod - MOD0
            if sess is None:
                if b is None or not qualifies(b[3], b[4]):
                    continue
                sess = TickerSession(rel, b, pcv, True)
            else:
                sess.on_minute(rel, b if (b is not None and qualifies(b[3], b[4])) else None)
            if rel in ev_rows:
                row = ev_rows[rel]
                f = sess.features(rel, row["rank"], mkt.get(tod), baseline_sessions, dow)
                f["ticker"] = tk
                f["tod_min"] = rel
                engine_rows.append(f)
    eng = pl.DataFrame([{k: (None if v is None else v) for k, v in r.items()} for r in engine_rows])
    for c in FEATS:
        eng = eng.with_columns(pl.col(c).cast(pl.Float64, strict=False))
    eng = eng.with_columns(pl.Series("score", model.predict(
        eng.select(FEATS).to_numpy().astype(np.float32), num_iteration=model.best_iteration)))
    eng = eng.sort(["tod_min", "ticker"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["tod_min"]).alias("vis_rank"))
    eng_pool = eng.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= THETA)
                          & (pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03)
                          & (pl.col("tod_min") < 270) & (pl.col("tod_min") <= ENTRY_CAP_TOD))

    # research side: same pipeline on parquet rows
    ref = feats.filter(pl.col("et_date") == DAY)
    ref = ref.with_columns(pl.col("cum_dv").log1p().alias("log_dollar_volume"))
    ref = ref.with_columns(pl.Series("score", model.predict(
        ref.select(FEATS).to_numpy().astype(np.float32), num_iteration=model.best_iteration)))
    ref = ref.sort(["et_date", "tod_min", "ticker"]).with_columns(
        pl.col("score").rank("ordinal", descending=True).over(["et_date", "tod_min"]).alias("vis_rank"))
    ref_pool = ref.filter((pl.col("vis_rank") <= 2) & (pl.col("score") >= THETA)
                          & (pl.col("rvol") > 4) & (pl.col("vwap_dist") > 0.03)
                          & (pl.col("tod_min") < 270) & (pl.col("tod_min") <= ENTRY_CAP_TOD))

    def e6_sim(df):
        eps = {}
        for r in df.to_dicts():
            eps.setdefault(r["ticker"], []).append(r)
        out = []
        for tk, evs in eps.items():
            pool = [e for e in evs if e["rvol"] is not None and e["rvol"] > 8]
            if not pool:
                continue
            picks, t = [pool[0]], pool[0]["tod_min"]
            while len(picks) < CYCLES:
                nxt = next((x for x in pool if x["tod_min"] >= t + HOLD + 1), None)
                if not nxt:
                    break
                picks.append(nxt)
                t = nxt["tod_min"]
            for k, e in enumerate(picks):
                out.append((tk, e["tod_min"], k + 1, round(e["score"], 9)))
        return sorted(out)

    e_eng, e_ref = e6_sim(eng_pool), e6_sim(ref_pool)
    n_diff = 0
    for a, b in zip(e_eng, e_ref):
        if a[:3] != b[:3] or abs(a[3] - b[3]) > 1e-6:
            n_diff += 1
            if n_diff <= 5:
                print("ENTRY DIFF engine/ref:", a, b)
    print(f"\nE6 entries: engine={len(e_eng)} research={len(e_ref)} diffs={n_diff}")
    if total_bad == 0 and n_diff == 0 and len(e_eng) == len(e_ref):
        print("PARITY OK — features and E6 decisions match research")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
