#!/usr/bin/env python3
"""LIFECYCLE-01 Stage B — the next-dollar map: where should the next dollar go, right now?

Destinations considered per minute: each available roster name (this name, a sibling) and
CASH (value 0). For every (day, clock, minute, ticker) the frame carries the causal
personality state and the EXECUTABLE forward value of deploying a fresh dollar there now:

    F_h = open(first bar >= t+h) / open(first bar >= t) - 1        (gross, no friction)
    downside_h = min low / open(first bar >= t) - 1                (executable excursion)

A chronological prefix model estimates E[F_h | state] per horizon; the evaluation is
CROSS-SECTIONAL: at each minute rank the available names by the prediction and measure what
the top-ranked destination actually paid, versus the equal-weight cross-section and versus
cash — net of measured friction (fresh entry pays a round trip). Tail preservation and the
dud tax are reported alongside, because the objective is EV through the window with the tail
kept and the tax measured, never classification.

Discovery half only; validation is never read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl
import lightgbm as lgb

import lifecycle_study as ls
import lifecycle_features  # noqa: F401

STATE = ["ret_fill", "peak_gain", "dd_from_high", "mae_sofar", "minutes_since_high",
         "minutes_since_low", "recovery_from_low", "peak_retention", "time_below_fill",
         "ret1", "ret3", "ret5", "ret10", "ret15", "accel5", "nh5", "nh15", "nh30", "streak_up",
         "v5", "v15", "v30", "v60", "dvol5", "dvol15", "vol_med_ratio", "range5", "px_age",
         "race_gain", "race_rank", "race_rank_fresh", "race_n_known", "drank5", "drank15",
         "sib_ret_mean", "sib_above_fill", "basket_ret", "peer_ret_p25", "peer_ret_p50",
         "peer_ret_p75", "rel_dvol5", "own_decision_gain", "tenure", "rank", "clock"]
HORIZONS = (15, 30, 60, 120, "F_window")
FRICTION_SIDE = 0.005


def deploy_frame(df: pl.DataFrame, end: int) -> pl.DataFrame:
    """One row per (day, clock, t, ticker) with the executable value of a fresh dollar."""
    d = df.filter(pl.col("filled_asof") & pl.col("sell_px").is_finite()
                  & (pl.col("rank") <= 5) & (pl.col("t") <= end))

    for h in (15, 30, 60, 120):
        # the frame already carries V_h = value of holding h more minutes (both executable opens)
        d = d.with_columns(pl.col(f"V{h}").alias(f"F{h}"))
    # value of a fresh dollar held to the WINDOW end (not the session close)
    d = d.with_columns(pl.when(pl.col("t") <= end)
                       .then(pl.col("sell_px").first().over(["day", "ticker"])
                             .shift(-1)).otherwise(None).alias("_unused"))
    win = (d.filter(pl.col("t") == end).select(["day", "ticker", "sell_px"])
           .rename({"sell_px": "_win_px"}))
    d = d.join(win, on=["day", "ticker"], how="left")
    d = d.with_columns((pl.col("_win_px") / pl.col("sell_px") - 1).alias("F_window")).drop("_win_px")
    return d


def fit_predict(d: pl.DataFrame, days: list[str], horizon: str) -> pl.DataFrame:
    feats = [c for c in STATE if c in d.columns]
    out = []
    # two chronological OOF segments: short prefix -> 150:300, long prefix -> 300:533
    for tr_lo, tr_hi, te_lo, te_hi in ((0, 150, 150, 300), (0, 300, 300, 533)):
        train = d.filter(pl.col("day").is_in(days[tr_lo:tr_hi]) & (pl.col("t") % 3 == 0))
        test = d.filter(pl.col("day").is_in(days[te_lo:te_hi]))
        if train.height < 1000 or test.height < 1000:
            continue
        y = train[horizon].to_numpy().astype(float)
        ok = np.isfinite(y)
        x = train.select([pl.col(c).cast(pl.Float64) for c in feats]).to_numpy().astype(np.float32)
        model = lgb.train({"objective": "regression", "num_leaves": 15, "max_depth": 5,
                           "min_data_in_leaf": 400, "learning_rate": 0.05, "lambda_l2": 20.0,
                           "num_threads": 3, "verbosity": -1, "seed": 11, "deterministic": True},
                          lgb.Dataset(x[ok], label=y[ok]), num_boost_round=80)
        xt = test.select([pl.col(c).cast(pl.Float64) for c in feats]).to_numpy().astype(np.float32)
        out.append(test.with_columns(pl.Series("pred", model.predict(xt))))
    return pl.concat(out, how="diagonal_relaxed") if out else pl.DataFrame()


def evaluate(scored: pl.DataFrame, horizon: str, out: Path, clock: int) -> dict:
    s = scored.filter(pl.col(horizon).is_finite() & pl.col("pred").is_finite())
    rows = []
    for (day, t), g in s.group_by(["day", "t"]):
        g = g.sort("pred", descending=True)
        if g.height == 0:
            continue
        top1 = float(g[horizon][0])
        top2 = float(g[horizon][:2].mean()) if g.height >= 2 else top1
        avg = float(g[horizon].mean())
        cash = 0.0
        rows.append({"day": day, "t": t, "n": g.height, "top1": top1, "top2": top2,
                     "avg": avg, "cash": cash,
                     "top1_ticker": g["ticker"][0], "top1_rank": int(g["rank"][0]),
                     "top1_downside": float(g["fmae30"][0]) if g["fmae30"][0] is not None else None,
                     "top1_dd": float(g["dd_from_high"][0])})
    per = pl.DataFrame(rows, infer_schema_length=None)
    if per.height == 0:
        return {"clock": clock, "horizon": horizon, "days": 0}
    per = per.with_columns([
        (pl.col("top1") - 2*FRICTION_SIDE).alias("top1_net"),
        (pl.col("top2") - 2*FRICTION_SIDE).alias("top2_net"),
    ])
    daily = per.group_by("day").agg([pl.col(c).mean().alias(c) for c in
                                     ("top1", "top2", "avg", "cash", "top1_net", "top2_net")])
    def stat(col):
        v = daily[col].to_numpy().astype(float)
        v = v[np.isfinite(v)]
        return {"mean": float(v.mean()) if v.size else None,
                "se": float(v.std(ddof=1)/np.sqrt(v.size)) if v.size > 1 else None,
                "pos": float((v > 0).mean()) if v.size else None}
    res = {"clock": clock, "horizon": horizon, "days": daily.height,
           "top1_gross": stat("top1"), "top1_net": stat("top1_net"),
           "top2_net": stat("top2_net"), "avg": stat("avg"), "cash": stat("cash"),
           "top1_rank_mix": per.group_by("top1_rank").len().sort("top1_rank").to_dicts(),
           "top1_median_downside30": float(per["top1_downside"].median()) if per["top1_downside"].is_not_null().any() else None}
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--clock", type=int, default=569)
    ap.add_argument("--end", type=int, default=780)
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    out = root / "report"
    days = ls.discovery_days(a.data_root)
    df = ls.load_panel(days, a.data_root, clocks=(a.clock,))
    d = deploy_frame(df, a.end)
    evidence = {"clock": a.clock, "end": a.end, "rows": d.height, "horizons": {}}
    lines = [f"# LIFECYCLE-01 Stage B — next-dollar map (clock {a.clock}, window to {a.end})", "",
             "At each minute the available names are ranked by predicted executable forward value;",
             "the top destination's realized value is compared with the equal-weight cross-section and",
             "cash, gross and net of a measured 100 bps round trip. Fresh deployment, no sunk entry.","",
             "| horizon | days | top1 gross | top1 net | top2 net | equal-weight | cash | top1>0 |",
             "|---|---|---|---|---|---|---|---|"]
    for h in HORIZONS:
        col = f"F{h}" if isinstance(h, int) else h
        if col not in d.columns or d[col].is_finite().sum() < 5000:
            continue
        scored = fit_predict(d, days, col)
        if scored.height == 0:
            continue
        # persist the chronological OOF predictions for the architecture stage (Stage C)
        keep = ["day", "clock", "rank", "ticker", "t", "tenure", "sell_px", "sell_et",
                "session_end", "ret_fill", "peak_gain", "dd_from_high", "pred", col]
        scored.select([c for c in keep if c in scored.columns]).write_parquet(
            root / "report" / f"nextdollar_scores_{a.clock}_{col}.parquet")
        r = evaluate(scored, col, out, a.clock)
        evidence["horizons"][str(h)] = r
        if r.get("days"):
            lines.append(f"| {h if not isinstance(h, int) else f'+{h}m'} | {r['days']} | {100*r['top1_gross']['mean']:+.3f}% "
                         f"(se {100*r['top1_gross']['se']:.3f}) | {100*r['top1_net']['mean']:+.3f}% | "
                         f"{100*r['top2_net']['mean']:+.3f}% | {100*r['avg']['mean']:+.3f}% | "
                         f"0 | {100*r['top1_gross']['pos']:.0f}% |")
    (out / f"nextdollar_{a.clock}.md").write_text("\n".join(lines) + "\n")
    (out / f"nextdollar_{a.clock}.json").write_text(json.dumps(evidence, indent=2, default=float) + "\n")
    print(f"next-dollar map clock={a.clock} rows={d.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
