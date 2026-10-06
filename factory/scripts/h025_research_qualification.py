#!/usr/bin/env python3
"""H025 causal qualification research on historical, uncertified top-three inputs.

Native fixed-fill anatomy is distinct from causal whole-policy replay. No protected
outcomes are read, no policy is frozen, and rank/source certification is not claimed.
Use the main research Python runtime with pandas/numpy/pyarrow installed.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import inspect

import numpy as np
import pandas as pd
from lb18_episodes import build
from lb18_oos import run_engine

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_DATA = Path("/home/hillel/projects/Alpacatrader/data")
DEFAULT_OUT = ROOT / "factory/artifacts/h025_research/qualification"
VERSION = "qualification-v1"
FEATURES = ["range5", "range5_hl", "dollar5", "volx", "nprint20", "fresh_age",
            "pf_age", "top3_age", "pullback", "r15", "n_bars", "reference_positive", "pf_carry", "is_new"]


def allowed(date):
    return ("2021-02-01" <= date <= "2023-12-29" or
            "2025-03-01" <= date <= "2026-05-29")


def calendar(data):
    return sorted(p.name[5:15] for p in (data / "leaderboard").glob("path_*.parquet")
                  if allowed(p.name[5:15]) and
                  (data / "leaderboard" / f"lb_{p.name[5:15]}.parquet").exists())


def source_flags():
    path = ROOT / "factory/artifacts/audit_splits.parquet"
    if not path.exists():
        raise FileNotFoundError("Historical split audit required: " + str(path))
    flags = pd.read_parquet(path)
    return set(map(tuple, flags.loc[flags.date.map(allowed), ["date", "ticker"]].values))


def features(paths, lb):
    if paths.empty:
        cols = list(dict.fromkeys(["date", "ticker", "t", "o", "h", "l", "c", "v", "gain_c", "n_bars", "prior_flush"] + FEATURES))
        return pd.DataFrame(columns=cols)
    p = paths.sort_values(["date", "ticker", "t"]).reset_index(drop=True).copy()
    arrays = build(p)
    for name in FEATURES:
        if name not in p:
            p[name] = False if name in ("reference_positive", "is_new") else np.nan
    for key, g in p.groupby(["date", "ticker"], sort=False):
        ix = g.index
        c = g.c
        new = arrays[key]["new"]
        t = g.t.to_numpy()
        p.loc[ix, "pullback"] = (c / c.cummax() - 1).values
        p.loc[ix, "r15"] = (c / c.shift(15) - 1).values
        p.loc[ix, "range5"] = (c.rolling(5, min_periods=5).max() /
                                      c.rolling(5, min_periods=5).min() - 1).values
        p.loc[ix, "range5_hl"] = (g.h.rolling(5, min_periods=5).max() /
                                         g.l.rolling(5, min_periods=5).min() - 1).values
        dv = pd.Series(np.where(new, g.v * g.c, 0.0))
        p.loc[ix, "dollar5"] = dv.rolling(5, min_periods=1).sum().values
        lastnew = pd.Series(np.where(new, t, np.nan)).ffill().to_numpy()
        p.loc[ix, "fresh_age"] = t - lastnew
        cn = c.to_numpy()[new]
        ln = g.l.to_numpy()[new]
        under = ln <= np.maximum.accumulate(cn) * .9
        starts = under & ~np.r_[False, under[:-1]]
        carried = np.full(len(g), np.nan)
        carried[new] = np.cumsum(starts) - starts
        p.loc[ix, "pf_carry"] = pd.Series(carried).ffill().fillna(0).values
        p.loc[ix, "is_new"] = new
        st = np.full(len(g), np.nan)
        st[np.flatnonzero(new)[starts]] = t[new][starts]
        # Count excludes current start, therefore history age excludes it too.
        previous = pd.Series(st).ffill().shift(1).to_numpy()
        p.loc[ix, "pf_age"] = t - previous
        ref = c / (1 + g.gain_c)
        p.loc[ix, "reference_positive"] = (np.isfinite(ref) & (ref > 0)).values
    first = lb.sort_values("t").drop_duplicates(["date", "ticker"])
    first = first.set_index(["date", "ticker"]).t
    keys = pd.MultiIndex.from_frame(p[["date", "ticker"]])
    p["top3_age"] = p.t.to_numpy() - keys.map(first).to_numpy()
    # Negative pre-promotion ages are never used as predictors.
    p.loc[p.top3_age < 0, "top3_age"] = np.nan
    return p


def attach(fills, feat, flags):
    if fills.empty:
        return fills
    tab = feat[["date", "ticker", "t", "c"] + [x for x in FEATURES if x not in fills.columns]].rename(columns={"t": "t0", "c": "anchor_close"})
    f = fills.merge(tab, on=["date", "ticker", "t0"], how="left", validate="many_to_one")
    f["bid"] = f["B"] if "B" in f else f.anchor_close * .9
    f["split_flag"] = [tuple(x) in flags for x in f[["date", "ticker"]].values]
    f["gross"] = f.ret + .01
    f["occupied_minutes"] = f.exit_t - f.tf + 1
    return f


def atomic_parquet(frame, path):
    tmp = path.with_suffix(".tmp.parquet")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def native_identity():
    text = "".join(inspect.getsource(f) for f in (features, attach, run_engine, build))
    text += Path(inspect.getmodule(run_engine).__file__).read_bytes().hex()
    text += (ROOT / "factory/artifacts/audit_splits.parquet").read_bytes().hex()
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def input_identity(data, date):
    out = {}
    for kind in ("path", "lb"):
        digest = hashlib.sha256()
        with open(data / "leaderboard" / f"{kind}_{date}.parquet", "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        out[kind] = digest.hexdigest()
    return out


def native(data, bulk, dates, flags):
    dest = bulk / ("native_" + native_identity())
    dest.mkdir(parents=True, exist_ok=True)
    for n, date in enumerate(dates):
        out = dest / f"{date}.parquet"
        meta = out.with_suffix(".json")
        identity = input_identity(data, date)
        if out.exists() and meta.exists() and json.loads(meta.read_text()) == identity:
            continue
        paths = pd.read_parquet(data / "leaderboard" / f"path_{date}.parquet")
        lb = pd.read_parquet(data / "leaderboard" / f"lb_{date}.parquet")
        feat = features(paths, lb)
        raw = run_engine(paths.copy(), lb.copy()) if len(paths) and len(lb) else pd.DataFrame()
        f = attach(raw, feat, flags)
        if f.empty:
            f = pd.DataFrame(columns=["date", "ticker", "t0", "tf", "ret", "prior_flush", "rank", "gain"])
        atomic_parquet(f, out)
        meta.write_text(json.dumps(identity))
        if n % 100 == 0:
            print(json.dumps({"mode": "native", "day": date, "completed": n + 1, "fills": len(f)}), flush=True)


def metrics(f, dates):
    days = len(dates)
    if f.empty:
        return {"fills": 0, "trading_days": days, "fills_per_day": 0., "sum_net_per_day": 0., "mean_net": None}
    ret = f.ret.astype(float)
    daily = ret.groupby(f.date).sum().reindex(dates, fill_value=0.)
    months = daily.groupby(daily.index.str[:7]).agg(["sum", "mean", "count"])
    result = {"fills": len(f), "trading_days": days, "active_days": int(f.date.nunique()),
              "fills_per_day": len(f) / days, "mean_net": float(ret.mean()),
              "sum_net": float(ret.sum()), "sum_net_per_day": float(daily.mean()),
              "win_fraction": float((ret > 0).mean()), "median_net": float(ret.median()),
              "p05_net": float(ret.quantile(.05)), "worst_fill": float(ret.min()),
              "worst_day": float(daily.min()), "months_positive": int((months["sum"] > 0).sum()),
              "months": len(months), "monthly": {k: {"sum_net": float(v["sum"]), "sum_net_per_day": float(v["mean"]), "days": int(v["count"])} for k, v in months.iterrows()}}
    if "occupied_minutes" in f:
        result["name_minutes_per_day"] = float(f.occupied_minutes.sum() / days)
    if "bid" in f:
        gross = f.gross.astype(float)
        result["integer_order_dollars"] = {}
        for budget in (250, 500, 1000):
            qty = np.floor(budget / f.bid).clip(lower=0)
            deployed = qty * f.bid
            net = deployed * (gross - .01)
            result["integer_order_dollars"][str(budget)] = {"net_dollars_per_day": float(net.sum() / days), "total_net_dollars": float(net.sum()), "zero_quantity_fills": int((qty == 0).sum()), "mean_deployed_dollars": float(deployed.mean()), "cost_convention": "100bps of actually allocated entry notional, matches legacy additive return"}
        result["cost_sensitivity"] = {str(bps): {"mean_net": float((gross - bps / 10000).mean()), "sum_net_per_day": float((gross - bps / 10000).sum() / days)} for bps in (100, 150, 200)}
    return result


def native_report(bulk, dates, out):
    tables = [pd.read_parquet(bulk / ("native_" + native_identity()) / f"{d}.parquet") for d in dates]
    f = pd.concat(tables, ignore_index=True)
    report = {"scope": "Fixed legacy fills; anatomy only, not causal policy results. Original rank inputs uncertified; known split flag removal does not repair or recertify ranks.", "days": len(dates), "by_year": {}, "feature_anatomy": {}}
    for year in sorted({d[:4] for d in dates}):
        ds = [d for d in dates if d.startswith(year)]
        fy = f[f.date.str.startswith(year)]
        report["by_year"][year] = {"legacy_all": metrics(fy, ds), "posthoc_pf2": metrics(fy[fy.prior_flush >= 2], ds), "split_flagged_fills": int(fy.split_flag.sum()) if len(fy) else 0}
    cuts = {"range5": [0, .01, .02, .03, .05, .1, np.inf], "gain": [1, 1.5, 2, 3, 5, np.inf], "r15": [.03, .05, .1, .2, np.inf], "fresh_age": [0, 1, 3, 5, 20, np.inf], "top3_age": [0, 15, 30, 60, 120, np.inf], "volx": [0, .5, 1, 2, 4, np.inf], "dollar5": [0, 10000, 100000, 1000000, 10000000, np.inf], "prior_flush": [0, 1, 2, 3, 5, np.inf], "t0": [570, 630, 690, 750, 810, 960]}
    for name, edges in cuts.items():
        rows = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            sub = f[(f[name] >= lo) & (f[name] < hi)]
            rows.append({"lo": lo, "hi": None if np.isinf(hi) else hi, "pooled": metrics(sub, dates), "by_year": {y: metrics(sub[sub.date.str.startswith(y)], [d for d in dates if d.startswith(y)]) for y in report["by_year"]}})
        report["feature_anatomy"][name] = rows
    (out / "native_anatomy.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"mode": "native-report", "days": len(dates), "fills": len(f), "by_year": {y: {k: v.get("fills") if isinstance(v, dict) else v for k, v in r.items()} for y, r in report["by_year"].items()}}), flush=True)



def specifications():
    """Mechanistic sweeps, not a Cartesian best-cell search."""
    specs = [{"name": f"pf_min_{v}", "family": "prior_flush", "parameters": {"prior_flush_min": v}, "conditions": []} for v in (0, 1, 2, 3)]
    sweeps = {
        "gain": ("gain_min", [1., 1.25, 1.5, 2., 3.]),
        "thrust": ("r15_min", [.0, .03, .05, .1, .2]),
        "freshness": ("pullback_min", [-.03, -.02, -.01, -.005, 0.]),
        "rank": ("rank_max", [1, 2, 3]),
    }
    for family, (key, values) in sweeps.items():
        for v in values:
            specs.append({"name": f"{family}_{v}", "family": family,
                          "parameters": {"prior_flush_min": 2, key: v}, "conditions": []})
    for field, direction, values in [
        ("t", "le", [630, 690, 750, 810, 959]),
        ("range5", "ge", [.01, .02, .03, .05, .1]),
        ("range5", "le", [.01, .02, .03, .05, .1]),
        ("dollar5", "ge", [10000., 100000., 1000000., 5000000.]),
        ("volx", "ge", [.5, 1., 2., 4.]),
        ("nprint20", "ge", [.25, .5, .75, 1.]),
        ("fresh_age", "le", [0, 1, 3, 5]),
        ("top3_age", "le", [15, 30, 60, 120]),
        ("pf_age", "le", [5, 15, 30, 60]),
    ]:
        for v in values:
            specs.append({"name": f"{field}_{direction}_{v}", "family": field,
                          "parameters": {"prior_flush_min": 2}, "conditions": [[field, direction, v]]})
    # Two-dimensional neighborhoods selected by mechanism, not outcome labels.
    for r in (.02, .03, .05):
        for pf in (1, 2, 3):
            specs.append({"name": f"history_range_pf{pf}_r{r}", "family": "history_range",
                          "parameters": {"prior_flush_min": pf}, "conditions": [["range5", "ge", r]]})
    for t in (690, 750, 810):
        for thrust in (.03, .05, .1):
            specs.append({"name": f"clock_thrust_t{t}_v{thrust}", "family": "clock_thrust",
                          "parameters": {"prior_flush_min": 2, "r15_min": thrust}, "conditions": [["t", "le", t]]})
    for activity in (.5, .75, 1.):
        for dollars in (100000., 1000000.):
            specs.append({"name": f"activity_liquidity_n{activity}_d{dollars}", "family": "activity_liquidity",
                          "parameters": {"prior_flush_min": 2}, "conditions": [["nprint20", "ge", activity], ["dollar5", "ge", dollars]]})
    for pf in (0, 1, 2, 3):
        specs.append({"name": f"carry_pf_{pf}", "family": "counter_activity", "parameters": {"prior_flush_min": pf, "flush_counter": "carried"}, "conditions": []})
        specs.append({"name": f"fresh_pf_{pf}", "family": "counter_activity", "parameters": {"prior_flush_min": pf, "fresh_state_only": True}, "conditions": []})
    specs.append({"name": "carry_fresh_pf2", "family": "counter_activity", "parameters": {"prior_flush_min": 2, "flush_counter": "carried", "fresh_state_only": True}, "conditions": []})
    for activity in (.25, .5, .75, 1.):
        specs.append({"name": f"carry_activity_{activity}", "family": "counter_activity", "parameters": {"prior_flush_min": 2, "flush_counter": "carried"}, "conditions": [["nprint20", "ge", activity]]})
    return specs


def condition_filter(conditions):
    def filt(frame):
        keep = pd.Series(True, index=frame.index)
        for field, direction, value in conditions:
            keep &= frame[field].ge(value) if direction == "ge" else frame[field].le(value)
        return keep.fillna(False)
    return filt


def curves(data, bulk, dates, flags):
    from h025_research_core import Policy, load_day, replay_day
    specs = specifications()
    producer = "".join(inspect.getsource(f) for f in (curves, condition_filter, features, attach, build))
    producer += Path(inspect.getmodule(replay_day).__file__).read_bytes().hex()
    identity = hashlib.sha256((json.dumps(specs, sort_keys=True) + producer + native_identity()).encode()).hexdigest()[:12]
    dest = bulk / ("curves_" + identity)
    dest.mkdir(parents=True, exist_ok=True)
    (bulk / "curve_manifest.json").write_text(json.dumps({"version": VERSION, "directory": dest.name, "specifications": specs, "days": dates, "producer_identity": identity}, indent=2))
    chrono_names = {"pf_min_0", "pf_min_1", "pf_min_2", "pf_min_3", "range5_ge_0.02", "range5_ge_0.03", "range5_ge_0.05", "history_range_pf2_r0.03", "clock_thrust_t750_v0.05", "activity_liquidity_n0.75_d100000.0", "carry_pf_2", "fresh_pf_2", "carry_fresh_pf2"}
    for n, date in enumerate(dates):
        out = dest / f"{date}.parquet"
        daily_path = dest / f"{date}.json"
        meta = dest / f"{date}.source.json"
        source_identity = input_identity(data, date)
        if out.exists() and daily_path.exists() and meta.exists() and json.loads(meta.read_text()) == source_identity:
            continue
        day = load_day(data, date)
        feat = features(day.paths.copy(), day.lb.copy())
        added = [x for x in FEATURES if x not in day.states.columns]
        states = day.states.merge(feat[["date", "ticker", "t"] + added], on=["date", "ticker", "t"], how="left", validate="many_to_one")
        day = replace(day, states=states)
        fill_tables, daily = [], []
        for spec in specs:
            policy = replace(Policy(), **spec["parameters"])
            for ordering in (["legacy", "pessimistic", "optimistic"] if spec["name"] in chrono_names else ["legacy"]):
                result = replay_day(day, policy, ordering=ordering, state_filter=condition_filter(spec["conditions"]))
                f = result.fills.copy()
                if len(f):
                    # Recompute qualification annotation at its own causal anchor.
                    f = attach(f, feat, flags)
                    f["gross"] = f.ret + policy.friction_bps / 10000
                    f["policy"] = spec["name"]
                    f["ordering"] = ordering
                    fill_tables.append(f)
                daily.append({"date": date, "policy": spec["name"], "ordering": ordering, "fills": len(f), "orders": len(result.orders), "sum_net": float(f.ret.sum()) if len(f) else 0.})
        frame = pd.concat(fill_tables, ignore_index=True) if fill_tables else pd.DataFrame(columns=["policy", "ordering", "date", "ret", "prior_flush", "ticker", "tf"])
        atomic_parquet(frame, out)
        temp = daily_path.with_suffix(".tmp.json")
        temp.write_text(json.dumps(daily))
        temp.replace(daily_path)
        meta.write_text(json.dumps(source_identity))
        if n % 50 == 0:
            print(json.dumps({"mode": "curves", "day": date, "completed": n + 1, "policies": len(specs), "scenario_fills": len(frame)}), flush=True)


def cohort(f):
    return set(map(tuple, f[["date", "ticker", "tf"]].values)) if len(f) else set()


def curve_report(bulk, dates, out):
    manifest = json.loads((bulk / "curve_manifest.json").read_text())
    dest = bulk / manifest["directory"]
    f = pd.concat([pd.read_parquet(dest / f"{d}.parquet") for d in dates], ignore_index=True)
    daily = pd.DataFrame([row for d in dates for row in json.loads((dest / f"{d}.json").read_text())])
    scopes = {"pooled": dates, "original_dev": [d for d in dates if d <= "2023-03-14"], "later_development": [d for d in dates if d > "2023-03-14"]}
    scopes.update({y: [d for d in dates if d.startswith(y)] for y in sorted({d[:4] for d in dates})})
    report = {"version": VERSION, "days": len(dates), "dates": {"first": dates[0], "last": dates[-1]}, "rank_source": "HISTORICAL_SUBSTRATE_UNCERTIFIED; source correction/full-universe rerank prerequisite before new rank claims. No rank>3 tested.", "selection_status": "DEVELOPMENT ONLY; no new OOS, no freeze", "legacy_posthoc": {}, "policies": [], "scope_days": {k: len(v) for k, v in scopes.items()}}
    base = f[(f.policy == "pf_min_0") & (f.ordering == "legacy")]
    baseline_pf2 = base[base.prior_flush >= 2]
    causalbase = f[(f.policy == "pf_min_2") & (f.ordering == "legacy")]
    report["legacy_posthoc"] = {scope: metrics(baseline_pf2[baseline_pf2.date.isin(ds)], ds) for scope, ds in scopes.items()}
    for spec in manifest["specifications"]:
        available = sorted(daily.loc[daily.policy == spec["name"], "ordering"].unique())
        for ordering in available:
            sub = f[(f.policy == spec["name"]) & (f.ordering == ordering)]
            entry = {**spec, "ordering": ordering, "scopes": {}}
            for scope, ds in scopes.items():
                sf = sub[sub.date.isin(ds)]
                b = causalbase[causalbase.date.isin(ds)]
                orig = baseline_pf2[baseline_pf2.date.isin(ds)]
                keys, bkeys, okeys = cohort(sf), cohort(b), cohort(orig)
                entry["scopes"][scope] = {**metrics(sf, ds), "orders": int(daily.loc[(daily.policy == spec["name"]) & (daily.ordering == ordering) & daily.date.isin(ds), "orders"].sum()), "retained_causal_pf2_fill_fraction": len(keys & bkeys) / len(bkeys) if bkeys else None, "new_vs_causal_pf2_fills": len(keys - bkeys), "retained_legacy_posthoc_pf2_fills": len(keys & okeys), "known_split_flag_fills": int(sf.split_flag.sum()) if len(sf) else 0, "unflagged_fixed_fill_sensitivity": metrics(sf[~sf.split_flag.astype(bool)], ds) if len(sf) else metrics(sf, ds)}
            report["policies"].append(entry)
    (out / "qualification_curves.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    daily.to_csv(out / "policy_daily.csv", index=False)
    flat = []
    for entry in report["policies"]:
        for scope, r in entry["scopes"].items():
            flat.append({"policy": entry["name"], "family": entry["family"], "ordering": entry["ordering"], "scope": scope, **{k: v for k, v in r.items() if not isinstance(v, dict)}})
    pd.DataFrame(flat).to_csv(out / "response_curves.csv", index=False)
    write_readme(report, out)
    print(json.dumps({"mode": "curve-report", "days": len(dates), "policies": len(manifest["specifications"]), "scenario_rows": len(report["policies"]), "legacy_all_fills": len(base), "posthoc_pf2_fills": len(baseline_pf2), "causal_pf2_fills": len(causalbase)}), flush=True)


def write_readme(report, out):
    entries = [e for e in report["policies"] if e["ordering"] == "legacy"]
    byname = {e["name"]: e for e in entries}
    lines = ["# H025 qualification: causal historical research", "", f"Actual legitimate available calendar: **{report['days']} days**, not an assumed 1066 or 1047. {report['scope_days']['original_dev']} original development days; later blocks are already-seen DEVELOPMENT/temporal replication, not new OOS.", "", "## Evidence boundary", "", "Original top-three ranked inputs remain uncertified (raw close/split/reference quality). Historical split flags are sensitivity labels, not a corrected full-universe board. Filtering flagged fills is post-hoc sensitivity only and does not re-rank. Source correction must precede new rank-based economic claims. Frozen published OOS pass remains unchanged; 2024, Jan/Feb2025 and Jun/Aug2026 outcomes are excluded from this study.", "", "All predictors are observable at the anchor: gain, 15-row clock-grid close thrust, cummax-CLOSE pullback, legacy episode count, clock, stored rank1–3, trailing5 clock-grid CLOSE range, trailing5 HL range, new-bar-only trailing5-minute dollar volume, current-completed-bar/20-new-bar volx, 20-clock-minute print density, last-new-bar age, elapsed time since prior episode start, elapsed time since already-observed top3 appearance. No fc or future MFE qualifies orders. Legacy prior_flush is zero on stale rows by original build semantics; this is preserved, not silently repaired.", "", "## Whole-policy prior-flush curves", "", "| minimum prior_flush | fills | mean net100 | total net/day | $500 integer-order net/day | 2022 total net/day |", "|---|---:|---:|---:|---:|---:|"]
    for v in (0, 1, 2, 3):
        e = byname[f"pf_min_{v}"]
        r, y = e["scopes"]["pooled"], e["scopes"]["2022"]
        dollars = r.get("integer_order_dollars", {}).get("500", {}).get("net_dollars_per_day", 0)
        lines.append(f"| {v} | {r['fills']} | {r['mean_net']:.4%} | {r['sum_net_per_day']:.4%} | ${dollars:.3f} | {y['sum_net_per_day']:.4%} |")
    old = report["legacy_posthoc"]["pooled"]
    lines += ["", f"Legacy ALL-order lifecycle followed by postfill pf2 subset: n={old['fills']}, mean={old['mean_net']:.4%}, total/day={old['sum_net_per_day']:.4%}. This is not the pf2 PRE-order policy above.", "", "## Mechanistic neighborhoods (not best-cell selection)", "", "Every sweep and joint neighborhood is in response_curves.csv and qualification_curves.json, including null/negative cells, month/year, frequency, active days, tail, name-minute occupancy, 100/150/200bps, $250/$500/$1000 integer-order dollars and causal-pf2 cohort retention. Net/day includes all calendar days, including zero-fill days. Independent order budgets imply no aggregate capital cap; summed returns are not compounded account returns. Cost is additive on allocated entry notional, matching legacy 100bps convention.", "", "Range5 is a mandatory 2022 inversion check, not a universally robust predictor:", "", "| range5 lower cut | pooled mean | pooled total/day | 2021 total/day | 2022 mean | 2022 total/day | 2023 total/day |", "|---|---:|---:|---:|---:|---:|---:|"]
    for cut in (.01, .02, .03, .05, .1):
        e = byname[f"range5_ge_{cut}"]["scopes"]
        def fmt(scope, key):
            val = e[scope].get(key)
            return "NA" if val is None else f"{val:.4%}"
        lines.append(f"| {cut:.0%} | {fmt('pooled','mean_net')} | {fmt('pooled','sum_net_per_day')} | {fmt('2021','sum_net_per_day')} | {fmt('2022','mean_net')} | {fmt('2022','sum_net_per_day')} | {fmt('2023','sum_net_per_day')} |")
    lines += ["", "## Candidate conditions (research-only, no freeze)", ""]
    candidates = []
    base = byname["pf_min_2"]["scopes"]
    for family in ("history_range", "clock_thrust", "activity_liquidity", "counter_activity"):
        members = [e for e in entries if e["family"] == family]
        nonworse = [e for e in members if all(e["scopes"][y]["sum_net_per_day"] >= base[y]["sum_net_per_day"] for y in ("2021", "2022", "2023")) and e["scopes"]["pooled"]["sum_net_per_day"] > base["pooled"]["sum_net_per_day"]]
        candidates.append({"family": family, "nonworse_original_years": [e["name"] for e in nonworse], "conditions": [{"name": e["name"], "parameters": e["parameters"], "conditions": e["conditions"]} for e in members], "status": "historical hypothesis only; source uncertified; no freeze"})
        lines.append(f"- **{family}**: {len(nonworse)}/{len(members)} cells improve pooled total/day while not reducing 2021/2022/2023 total/day relative to causalpf2. Conditions: " + ", ".join(e["name"] for e in nonworse) + ("." if nonworse else "none; retain negative/inversion evidence rather than choosing highest per-fill EV."))
    lines += ["", "This cross-year comparison is descriptive, not a preregistered adoption gate; no family is certified by passing it. Original development versus later seen-data replication is reported separately. Pessimistic/optimistic intrabar modes are lifecycle chronology SCENARIOS, not guaranteed aggregate P&L bounds because exit timing changes later rearm/fill cohorts. No exchange queue probabilities or broker order calls.", "", "## Reproduce / resume", "", "Run factory/scripts/h025_research_qualification.py with --data-root explicitly bound to the external data root and --mode native or --mode curves, then --mode report. Bulk per-day outputs live under data/h025_research/qualification; each completed day is atomic and resumable with producer/feature/core and source content identities. --limit is for targeted research smoke only. No builds, gates, linters, formatters or tests run."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    (out / "candidate_conditions.json").write_text(json.dumps(candidates, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--bulk", type=Path)
    ap.add_argument("--mode", choices=["native", "curves", "report"], default="native")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    bulk = a.bulk or a.data_root / "h025_research/qualification"
    dates = calendar(a.data_root)
    if a.limit:
        dates = dates[:a.limit]
    a.output.mkdir(parents=True, exist_ok=True)
    bulk.mkdir(parents=True, exist_ok=True)
    flags = source_flags()
    if a.mode == "native":
        native(a.data_root, bulk, dates, flags)
        native_report(bulk, dates, a.output)
    elif a.mode == "curves":
        curves(a.data_root, bulk, dates, flags)
        curve_report(bulk, dates, a.output)
    else:
        native_report(bulk, dates, a.output)
        curve_report(bulk, dates, a.output)


if __name__ == "__main__":
    main()
