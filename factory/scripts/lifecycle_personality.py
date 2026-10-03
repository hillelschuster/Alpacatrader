#!/usr/bin/env python3
"""LIFECYCLE-01 Stage A — route types and their time-resolved separability.

Route types are FORWARD-defined rulers (grouping only, never predictors):
  monster, sustained_runner, second_leg_runner, transient_spike, recoverable_flush,
  fake_recovery, exhausted_leader, slow_death, immediate_dud, resurrection, mid.

Separability answers the owner's question: **when does a route become distinguishable
from causal information available at that minute, and through which combination?**

FRAMING (owner caution, binding): route labels are ANATOMY TOOLS, not a target. This module
never ships a classifier and never optimises classification. The curves below are a
descriptive clock: at what tenure does the causal personality vector start carrying
information about the route, and is that information a *combination* (vs any single
threshold). The deployable object is Stage B: causal personality -> marginal EV of each
capital destination (this name / sibling / outsider / cash / re-entry).
At each minute t we train a prefix-only multiclass model (expanding chronology) on the
causal personality vector at t, score the remaining discovery days, and report per-route
one-vs-rest AUC — alongside the best SINGLE feature AUC at the same t, so the gap between
"a combination" and "a threshold" is visible.

Discovery half only. No validation outcomes are read. The second half stays untouched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import polars as pl

import lifecycle_study as ls
import lifecycle_features  # noqa: F401  (route-memory columns are added by load_panel)
import lightgbm as lgb

STATE = ["ret_fill", "peak_gain", "dd_from_high", "mae_sofar", "minutes_since_high",
         "minutes_since_low", "recovery_from_low", "peak_retention", "time_below_fill",
         "ret1", "ret3", "ret5", "ret10", "ret15", "accel5", "nh5", "nh15", "nh30", "streak_up",
         "v5", "v15", "v30", "v60", "dvol5", "dvol15", "vol_med_ratio", "range5", "px_age",
         "race_gain", "race_rank", "race_rank_fresh", "race_n_known", "drank5", "drank15",
         "sib_ret_mean", "sib_above_fill", "basket_ret", "peer_ret_p25", "peer_ret_p50",
         "peer_ret_p75", "rel_dvol5", "own_decision_gain", "tenure", "rank"]
ROUTES = ("monster", "sustained_runner", "second_leg_runner", "transient_spike",
          "recoverable_flush", "fake_recovery", "exhausted_leader", "slow_death",
          "immediate_dud", "resurrection", "mid")


def route_of(path: dict) -> str:
    """Forward path summary -> route type (priority order; rulers, not predictors)."""
    mfe, mae, captured = path["mfe"], path["mae"], path["captured"]
    peak_t, legs, reclaim, resurrection, dd_first = (path["t_peak"], path["legs"],
                                                     path["reclaim"], path["resurrect"],
                                                     path["dd_first"])
    if captured is None:
        return "unknown"
    if resurrection:
        return "resurrection"
    if mfe >= 1.0:
        return "monster"
    if mfe >= 0.30:
        if captured < 0.10:
            return "transient_spike"
        if legs >= 2:
            return "second_leg_runner"
        if peak_t is not None and peak_t >= 120:
            return "sustained_runner"
        return "transient_spike"
    if reclaim and captured >= 0.10:
        return "recoverable_flush"
    if mae <= -0.15 and not reclaim:
        return "fake_recovery"
    if mfe >= 0.20 and (peak_t is None or peak_t <= 60) and captured < 0:
        return "exhausted_leader"
    if mfe < 0.05 and dd_first is not None and dd_first <= 30:
        return "immediate_dud"
    if mfe < 0.10 and captured <= -0.10:
        return "slow_death"
    return "mid"


def path_summary(df: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for key, s in df.group_by(["day", "clock", "rank", "ticker"]):
        s = s.sort("t")
        t = s["t"].to_numpy()
        ret = s["ret_fill"].to_numpy().astype(float)
        peak = s["peak_gain"].to_numpy().astype(float)
        low = s["mae_sofar"].to_numpy().astype(float)
        ok = np.isfinite(ret) & np.isfinite(peak)
        if not ok.any():
            continue
        t, ret, peak, low = t[ok], ret[ok], peak[ok], low[ok]
        i_peak = int(np.nanargmax(peak))
        # distinct legs: >=20% advances separated by >=10% pullbacks
        legs, anchor = 0, 1.0
        run_max = 1.0
        for i in range(len(peak)):
            run_max = max(run_max, 1 + peak[i])
            if 1 + peak[i] >= anchor * 1.20:
                legs += 1
                anchor = 1 + peak[i]
        # reclaim: after a >=15% drawdown from a prior high, a later high exceeds it
        reclaim = False
        for i in range(1, len(peak)):
            if (1 + ret[i]) / (1 + peak[i]) - 1 <= -0.15:
                if np.nanmax(peak[i:]) > peak[i - 1] + 1e-9:
                    reclaim = True
                break
        # resurrection: after >=20% below entry, a >=30% advance from the running low
        resurrection = False
        below = np.where(ret <= -0.20)[0]
        if below.size:
            i0 = int(below[0])
            if np.nanmax(ret[i0:]) - low[i0] >= 0.30 and ret[-1] > -0.20:
                resurrection = True
        dd_first = None
        early = t <= t[0] + 30
        if early.any():
            m = (1 + ret[early]) / (1 + peak[early]) - 1
            dd_first = int(t[early][np.nanargmin(m)] - t[0]) if m.size else None
        rows.append({"day": key[0], "clock": key[1], "rank": key[2], "ticker": key[3],
                     "mfe": float(np.nanmax(peak)), "mae": float(np.nanmin(low)),
                     "captured": float(ret[-1]), "t_peak": int(t[i_peak] - t[0]),
                     "legs": legs, "reclaim": bool(reclaim),
                     "resurrect": bool(resurrection), "dd_first": dd_first,
                     "tenure": int(t[-1] - t[0])})
    out = pl.DataFrame(rows, infer_schema_length=None)
    return out.with_columns(pl.struct(pl.all()).map_elements(
        lambda r: route_of(r), return_dtype=pl.String).alias("route"))


def separability(df: pl.DataFrame, routes: pl.DataFrame, clock: int, dest: Path) -> dict:
    d = df.join(routes.select(["day", "clock", "rank", "ticker", "route"]),
                on=["day", "clock", "rank", "ticker"], how="left")
    d = d.filter(pl.col("filled_asof") & pl.col("route").is_not_null() & (pl.col("rank") <= 5))
    days = sorted(d["day"].unique().to_list())
    cut = days[int(len(days) * 0.6)]
    feats = [c for c in STATE if c in d.columns]
    curve, single = [], []
    grid = list(range(10, 250, 10))
    for h in grid:
        s = d.filter(pl.col("tenure") == h)
        if s.height < 2000:
            continue
        train = s.filter(pl.col("day") < cut)
        test = s.filter(pl.col("day") >= cut)
        if train.height < 500 or test.height < 500:
            continue
        classes = sorted(set(train["route"].to_list()) & set(test["route"].to_list()))
        if len(classes) < 3:
            continue
        x_tr = train.select([pl.col(c).cast(pl.Float64) for c in feats]).to_numpy().astype(np.float32)
        x_te = test.select([pl.col(c).cast(pl.Float64) for c in feats]).to_numpy().astype(np.float32)
        y_tr = np.array([classes.index(v) for v in train["route"].to_list()])
        model = lgb.train({"objective": "multiclass", "num_class": len(classes),
                           "num_leaves": 31, "min_data_in_leaf": 200, "learning_rate": 0.08,
                           "num_threads": 3, "verbosity": -1, "seed": 7, "deterministic": True},
                          lgb.Dataset(x_tr, label=y_tr), num_boost_round=60)
        prob = model.predict(x_te)
        from sklearn.metrics import roc_auc_score
        y_te = np.array([classes.index(v) for v in test["route"].to_list()])
        for ci, cls in enumerate(classes):
            yb = (y_te == ci).astype(int)
            if yb.sum() < 20 or yb.sum() > len(yb) - 20:
                continue
            auc = float(roc_auc_score(yb, prob[:, ci]))
            best_f, best_a = None, 0.0
            for f in feats:
                x = test[f].to_numpy().astype(float)
                m = np.isfinite(x)
                if m.sum() < 200 or len(set(yb[m])) < 2:
                    continue
                a = roc_auc_score(yb[m], x[m])
                a = max(a, 1 - a)
                if a > best_a:
                    best_f, best_a = f, a
            curve.append({"clock": clock, "tenure": h, "route": cls, "n": int(yb.sum()),
                          "days": test["day"].n_unique(), "auc_combo": auc,
                          "best_single": best_f, "auc_single": best_a})
            single.append({"clock": clock, "tenure": h, "route": cls, "feature": best_f,
                           "auc": best_a})
    out = pl.DataFrame(curve, infer_schema_length=None)
    out.write_parquet(dest / f"personality_separability_{clock}.parquet")
    lines = [f"# LIFECYCLE-01 Stage A — when personality carries route information (clock {clock})", "",
             "DESCRIPTIVE ONLY. Route labels are anatomy rulers; no classifier is deployed and",
             "classification is not an objective. The curve answers: at which tenure does the causal",
             "personality vector separate a route from the rest, and does a combination beat the best",
             "single observable? Prefix-trained (first 60% of discovery days), scored on the rest.", "",
             "| tenure | route | n | days | AUC combination | best single | AUC single |",
             "|---|---|---|---|---|---|---|"]
    for r in out.sort(["tenure", "route"]).iter_rows(named=True):
        lines.append(f"| +{r['tenure']}m | {r['route']} | {r['n']} | {r['days']} | "
                     f"{r['auc_combo']:.3f} | {r['best_single']} | {r['auc_single']:.3f} |")
    (dest / f"personality_separability_{clock}.md").write_text("\n".join(lines) + "\n")
    return {"clock": clock, "rows": out.height}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--clock", type=int, default=569)
    ap.add_argument("--stage", default="routes")
    a = ap.parse_args()
    root = a.data_root / "harvest01/lifecycle/v2"
    out = root / "report"
    out.mkdir(parents=True, exist_ok=True)
    days = ls.discovery_days(a.data_root)
    df = ls.load_panel(days, a.data_root, clocks=(a.clock,))
    if a.stage == "routes":
        routes = path_summary(df)
        routes.write_parquet(out / f"personality_routes_{a.clock}.parquet")
        counts = routes.group_by("route").agg(pl.len().alias("n")).sort("n", descending=True)
        lines = [f"# LIFECYCLE-01 Stage A — route census (clock {a.clock}, discovery half)", "",
                 "| route | n | share | median MFE | median captured | median t_peak |",
                 "|---|---|---|---|---|---|"]
        for r in counts.iter_rows(named=True):
            sub = routes.filter(pl.col("route") == r["route"])
            lines.append(f"| {r['route']} | {r['n']} | {r['n']/routes.height:.1%} | "
                         f"{100*sub['mfe'].median():+.1f}% | {100*sub['captured'].median():+.1f}% | "
                         f"{sub['t_peak'].median():.0f}m |")
        (out / f"personality_routes_{a.clock}.md").write_text("\n".join(lines) + "\n")
        print(f"routes clock={a.clock} members={routes.height} types={counts.height}")
    else:
        routes = pl.read_parquet(out / f"personality_routes_{a.clock}.parquet")
        print(json.dumps(separability(df, routes, a.clock, out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
