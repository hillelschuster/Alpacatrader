#!/usr/bin/env python3
"""H025 exit response curves: full lifecycle replay, development only.

Run with the research environment Python and explicit --data-root. Day shards
are atomically checkpointed; rerunning resumes only matching engine/policy inputs.
No broker connection, gates, tests, live orders, or frozen-policy modifications.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from h025_research_core import Policy, load_day, replay_day

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "factory" / "artifacts" / "h025_research" / "exit"
COSTS = (100, 150, 200)
NOTIONALS = (250, 500, 1000)
RECOVERIES = (.25, .5, .75, 1., 1.25, 1.5, 2.)
STOPS = (.025, .05, .075, .10, .125, .15)
HOLDS = (5, 10, 15, 20, 30, 45, 60, 90, 120)


def policies():
    """Full main effects plus mechanistically joint target/stop/holding families."""
    specs = {}
    def add(fraction=1., stop=.1, hold=30, clock=False, family="baseline"):
        mode = "clock" if clock else "bars"
        name = f"f{fraction:g}_s{stop:g}_{mode}{hold}"
        if name in specs:
            specs[name]["families"].append(family)
            return
        base = Policy(friction_bps=0)
        policy = replace(base, target=1-base.discount+base.discount*fraction,
                         stop=stop, hold_bars=hold,
                         hold_minutes=hold if clock else None)
        specs[name] = {"policy": policy, "fraction": fraction, "stop": stop,
                       "hold": hold, "clock": clock, "families": [family]}
    add()
    for fraction in RECOVERIES:
        add(fraction=fraction, family="natural_recovery")
    for stop in STOPS:
        add(stop=stop, family="stop_depth")
    for clock in (False, True):
        for hold in HOLDS:
            add(hold=hold, clock=clock, family="holding")
    for fraction in (.5, .75, 1., 1.25, 1.5):
        for stop in (.05, .075, .1, .125):
            for hold in (15, 30, 60):
                for clock in (False, True):
                    add(fraction, stop, hold, clock, "joint")
    return specs


def block(date):
    if "2021-02-01" <= date <= "2023-03-14":
        return "original533"
    if ("2023-03-15" <= date <= "2023-12-29" or
        "2025-03-01" <= date <= "2026-05-29"):
        return "temporal_replication"
    return None


def calendar(data_root):
    return sorted(p.stem[5:] for p in (data_root / "leaderboard").glob("path_*.parquet")
                  if block(p.stem[5:]) is not None and
                  p.with_name(f"lb_{p.stem[5:]}.parquet").exists())


def atomic_parquet(frame, path):
    temp = path.with_suffix(".tmp.parquet")
    frame.to_parquet(temp, index=False)
    temp.replace(path)


def day_summary(fills, orders, date, name, admission, ordering, spec):
    gap_loss = np.where(fills.exit_reason == "stop",
        np.maximum(0., -fills.ret_gross.to_numpy(float) - spec["stop"]), 0.) if len(fills) else np.array([])
    row = {"date": date, "block": block(date), "policy": name,
           "admission": admission, "ordering": ordering,
           "fraction": spec["fraction"], "stop": spec["stop"],
           "hold": spec["hold"], "clock": spec["clock"],
           "fills": len(fills), "orders": len(orders),
           "gross_sum": float(fills.ret_gross.sum()) if len(fills) else 0.,
           "occupancy_minutes": float((fills.exit_t - fills.tf + 1).sum()) if len(fills) else 0.,
           "hold_new_bars": int(fills.hold_new_bars.sum()) if len(fills) else 0,
           "pending_minutes": float((orders.end_t-orders.t0).clip(lower=0).sum()) if admission != "pf2POST" and len(orders) else 0.,
           "target": int((fills.exit_reason == "target").sum()) if len(fills) else 0,
           "stop_count": int((fills.exit_reason == "stop").sum()) if len(fills) else 0,
           "time": int(fills.exit_reason.isin(("time", "eod")).sum()) if len(fills) else 0,
           "gap_losses": int((fills.stop_gap > 0).sum()) if len(fills) else 0,
           "gap_loss_return_sum": float(gap_loss.sum()) if len(fills) else 0.,
           "fillbar_ambiguous": int(fills.ambiguous_fill_high.sum()) if len(fills) else 0}
    for cost in COSTS:
        row[f"positive_fills_{cost}"] = int((fills.ret_gross > cost/10000).sum()) if len(fills) else 0
    for notional in NOTIONALS:
        entry = fills.B.to_numpy(float) if len(fills) else np.array([])
        qty = np.floor(notional / entry)
        capital = qty * entry
        row[f"executed_{notional}"] = int((qty >= 1).sum())
        row[f"capital_{notional}"] = float(capital.sum())
        row[f"gross_dollars_{notional}"] = float(np.dot(capital, fills.ret_gross)) if len(fills) else 0.
        row[f"gap_dollars_{notional}"] = float(np.dot(capital, gap_loss)) if len(fills) else 0.
        row[f"capital_minutes_{notional}"] = float(np.dot(capital, fills.exit_t-fills.tf+1)) if len(fills) else 0.
        events = {}
        for start, end, amount in zip(fills.tf if len(fills) else [], fills.exit_t if len(fills) else [], capital):
            events[int(start)] = events.get(int(start), 0.) + amount
            events[int(end)+1] = events.get(int(end)+1, 0.) - amount
        running = peak = 0.
        for minute in sorted(events):
            running += events[minute]
            peak = max(peak, running)
        row[f"peak_capital_{notional}"] = peak
        reserve_minutes = 0.
        if admission != "pf2POST":
            for order in orders.itertuples():
                reserve = np.floor(notional / order.B) * order.B
                reserve_minutes += max(0, order.end_t-order.t0) * reserve
                if order.end_t > order.t0:
                    events[int(order.t0)] = events.get(int(order.t0), 0.) + reserve
                    events[int(order.end_t)] = events.get(int(order.end_t), 0.) - reserve
        running = combined_peak = 0.
        for minute in sorted(events):
            running += events[minute]
            combined_peak = max(combined_peak, running)
        row[f"pending_capital_minutes_{notional}"] = reserve_minutes
        row[f"peak_pending_plus_position_{notional}"] = combined_peak
    return row


def select_specs(families):
    out = policies()
    if families:
        out = {n: s for n, s in out.items() if set(s["families"]) & set(families)}
    if not out:
        raise ValueError("family filter selected no specifications")
    return out


def replay_job(job):
    date, data_root, bulk, fingerprint, orderings, families = job
    bulk = Path(bulk)
    marker = bulk / f"{date}.complete.json"
    if marker.exists() and json.loads(marker.read_text()).get("fingerprint") == fingerprint:
        return date, "resumed"
    day = load_day(Path(data_root), date)
    rows, frames = [], []
    for name, spec in select_specs(families).items():
        for admission, pf in (("ALL", 0), ("pf2PRE", 2)):
            policy = replace(spec["policy"], prior_flush_min=pf)
            for ordering in orderings:
                # Only zero-share rejection changes ideal lifecycle; otherwise reuse
                # the same replay for all integer-quantity economic marks.
                risky = [n for n in NOTIONALS if len(day.paths) and day.paths.c.max() * (1-policy.discount) > n]
                for lifecycle_notional in [0] + risky:
                    variant = replace(policy, order_notional=lifecycle_notional or None)
                    result = replay_day(day, variant, ordering=ordering)
                    fills = result.fills.copy()
                    if len(fills):
                        fills["policy"] = name
                        fills["admission"] = admission
                        fills["ordering"] = ordering
                        fills["lifecycle_notional"] = lifecycle_notional
                        frames.append(fills)
                    row = day_summary(fills, result.orders, date, name, admission, ordering, spec)
                    row["lifecycle_notional"] = lifecycle_notional
                    rows.append(row)
                    if admission == "ALL":
                        post = fills[fills.prior_flush >= 2] if len(fills) else fills
                        row = day_summary(post, result.orders, date, name, "pf2POST", ordering, spec)
                        row["lifecycle_notional"] = lifecycle_notional
                        rows.append(row)
    atomic_parquet(pd.DataFrame(rows), bulk / f"{date}.daily.parquet")
    atomic_parquet(pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
                   bulk / f"{date}.fills.parquet")
    temp = marker.with_suffix(".tmp.json")
    temp.write_text(json.dumps({"fingerprint": fingerprint, "date": date,
                               "source": day.source}, default=str))
    temp.replace(marker)
    return date, "computed"


KEYS = ["policy", "admission", "ordering", "cost_bps", "order_notional"]


def aggregate(frame, keys):
    out = frame.groupby(keys, observed=True).agg(
        days=("date", "size"), fills=("fills", "sum"), orders=("orders", "sum"),
        executed_fills=("executed_fills", "sum"), total_net_dollars=("net_dollars", "sum"),
        net_dollars_per_day=("net_dollars", "mean"),
        worst_day_dollars=("net_dollars", "min"),
        daily_p05_dollars=("net_dollars", lambda x: x.quantile(.05)),
        positive_days=("net_dollars", lambda x: int((x > 0).sum())),
        positive_fills=("positive_fills", "sum"),
        net_return_sum=("net_return_sum", "sum"),
        occupancy_minutes=("occupancy_minutes", "sum"),
        capital_minutes=("capital_minutes", "sum"),
        max_position_capital=("peak_capital", "max"),
        pending_minutes=("pending_minutes", "sum"),
        hold_new_bars=("hold_new_bars", "sum"),
        pending_capital_minutes=("pending_capital_minutes", "sum"),
        max_pending_plus_position_principal=("peak_pending_plus_position", "max"),
        target_count=("target", "sum"), stop_count=("stop_count", "sum"),
        time_count=("time", "sum"), gap_losses=("gap_losses", "sum"),
        gap_loss_dollars=("gap_loss_dollars", "sum"),
        fillbar_ambiguous=("fillbar_ambiguous", "sum")).reset_index()
    denominator = out.fills.replace(0, np.nan)
    out["fills_per_day"] = out.fills / out.days
    out["mean_net_return"] = out.net_return_sum / denominator
    out["positive_fill_share"] = out.positive_fills / denominator
    out["mean_net_dollars_per_executed_fill"] = out.total_net_dollars / out.executed_fills.replace(0, np.nan)
    out["mean_hold_minutes"] = out.occupancy_minutes / denominator
    out["mean_hold_new_bars"] = out.hold_new_bars / denominator
    for kind in ("target", "stop", "time"):
        out[f"{kind}_proportion"] = out[f"{kind}_count"] / denominator
    return out


def paired_effects(bulk, dates, candidate_names):
    """Match exact entry identity; separate direct effect and cohort feedback."""
    rows = []
    baseline = "f1_s0.1_bars30"
    for date in dates:
        fills = pd.read_parquet(bulk / f"{date}.fills.parquet")
        if fills.empty:
            continue
        actual_500 = 500 in pd.read_parquet(bulk / f"{date}.daily.parquet", columns=["lifecycle_notional"]).lifecycle_notional.unique()
        for admission in ("ALL", "pf2PRE"):
            selected = fills[(fills.admission == admission) & (fills.ordering == "pessimistic") &
                             fills.lifecycle_notional.isin((0, 500))]
            selected = selected[selected.lifecycle_notional == (500 if actual_500 else 0)]
            base = selected[selected.policy == baseline]
            identity = ["date", "ticker", "t0", "tf", "B", "c0"]
            for name in candidate_names:
                other = selected[selected.policy == name]
                merged = base.merge(other, on=identity, suffixes=("_base", "_other"))
                qty = np.floor(500 / merged.B)
                direct = float((qty * merged.B * (merged.ret_gross_other - merged.ret_gross_base)).sum())
                base_total = float((np.floor(500/base.B)*base.B*(base.ret_gross-.015)).sum())
                other_total = float((np.floor(500/other.B)*other.B*(other.ret_gross-.015)).sum())
                rows.append({"date": date, "block": block(date), "admission": admission,
                             "policy": name, "matched_fills": len(merged), "base_fills": len(base),
                             "candidate_fills": len(other), "direct_matched_dollars": direct,
                             "cohort_feedback_dollars": other_total-base_total-direct,
                             "total_delta_dollars": other_total-base_total})
    return pd.DataFrame(rows)


def summarize(bulk, dates, fingerprint, families=None):
    daily = pd.concat([pd.read_parquet(bulk / f"{d}.daily.parquet") for d in dates], ignore_index=True)
    for column in ("policy", "admission", "ordering", "block"):
        daily[column] = daily[column].astype("category")
    results = {"response_curves": [], "blocks": [], "months": [], "years": []}
    for cost in COSTS:
        for notional in NOTIONALS:
            economics = daily[daily.lifecycle_notional.isin((0, notional))].drop_duplicates(
                ["date", "policy", "admission", "ordering"], keep="last").copy()
            economics["cost_bps"] = cost
            economics["order_notional"] = notional
            economics["executed_fills"] = economics[f"executed_{notional}"]
            economics["net_dollars"] = economics[f"gross_dollars_{notional}"] - economics[f"capital_{notional}"] * cost / 10000
            economics["net_return_sum"] = economics.gross_sum - economics.fills * cost / 10000
            economics["positive_fills"] = economics[f"positive_fills_{cost}"]
            economics["gap_loss_dollars"] = economics[f"gap_dollars_{notional}"]
            economics["capital_minutes"] = economics[f"capital_minutes_{notional}"]
            economics["peak_capital"] = economics[f"peak_capital_{notional}"]
            economics["pending_capital_minutes"] = economics[f"pending_capital_minutes_{notional}"]
            economics["peak_pending_plus_position"] = economics[f"peak_pending_plus_position_{notional}"]
            economics["month"] = economics.date.str[:7].astype("category")
            economics["year"] = economics.date.str[:4].astype("category")
            for label, extra in (("response_curves", []), ("blocks", ["block"]),
                                 ("months", ["month"]), ("years", ["year"])):
                results[label].append(aggregate(economics, KEYS + extra))
    overall, blocks, months, years = (pd.concat(results[label], ignore_index=True)
        for label in ("response_curves", "blocks", "months", "years"))
    specs = select_specs(families)
    metadata = pd.DataFrame([{"policy": name, "fraction": spec["fraction"], "stop": spec["stop"],
                              "hold": spec["hold"], "clock": spec["clock"],
                              "families": ",".join(spec["families"])} for name, spec in specs.items()])
    overall = overall.merge(metadata, on="policy")
    for name, frame in (("response_curves", overall), ("blocks", blocks), ("months", months), ("years", years)):
        frame.to_csv(ART / f"{name}.csv", index=False)
    atomic_parquet(daily, bulk / "daily.parquet")
    view = overall[(overall.admission == "pf2PRE") & (overall.ordering == "pessimistic") &
                   (overall.cost_bps == 150) & (overall.order_notional == 500)]
    base = view[view.policy == "f1_s0.1_bars30"].iloc[0]
    neighborhoods = []
    for row in view.itertuples():
        same = view[view.clock == row.clock]
        neighbors = same[
            ((same.stop == row.stop) & (same.hold == row.hold) & (abs(same.fraction-row.fraction) <= .250001)) |
            ((same.fraction == row.fraction) & (same.hold == row.hold) & (abs(same.stop-row.stop) <= .025001)) |
            ((same.fraction == row.fraction) & (same.stop == row.stop) & (abs(same.hold-row.hold) <= 15))]
        names = neighbors.policy.tolist()
        b = blocks[(blocks.policy.isin(names)) & (blocks.admission == "pf2PRE") &
                   (blocks.ordering == "pessimistic") & (blocks.cost_bps == 150) & (blocks.order_notional == 500)]
        neighborhoods.append({"policy": row.policy, "neighbors": ",".join(names), "n_neighbors": len(names),
                              "mean_dollars_day": row.net_dollars_per_day,
                              "neighborhood_min_dollars_day": float(neighbors.net_dollars_per_day.min()),
                              "worst_block_neighbor_dollars_day": float(b.net_dollars_per_day.min()),
                              "baseline_dollars_day": float(base.net_dollars_per_day)})
    n = pd.DataFrame(neighborhoods)
    n.to_csv(ART / "neighborhoods.csv", index=False)
    # Require support across both used development blocks and nearby policies;
    # no declaration of an unseen-OOS winner or freeze.
    supported = n[(n.n_neighbors >= 3) &
                  (n.neighborhood_min_dollars_day > base.net_dollars_per_day) &
                  (n.worst_block_neighbor_dollars_day > 0)]
    shortlist = supported.sort_values("worst_block_neighbor_dollars_day", ascending=False)
    candidate = str(shortlist.iloc[0].policy) if len(shortlist) else None
    selected = [candidate] if candidate else [str(view.sort_values("net_dollars_per_day", ascending=False).iloc[0].policy)]
    paired = paired_effects(bulk, dates, selected)
    paired.to_csv(ART / "paired_effect_and_rearm.csv", index=False)
    tail_values = {(name, admission, ordering): []
        for name in ["f1_s0.1_bars30"] + selected
        for admission in ("ALL", "pf2PRE")
        for ordering in sorted(daily.ordering.unique())}
    for date in dates:
        f = pd.read_parquet(bulk / f"{date}.fills.parquet")
        if f.empty:
            continue
        chosen = f[f.policy.isin(["f1_s0.1_bars30"] + selected) & f.lifecycle_notional.isin((0,500))]
        actual_500 = 500 in pd.read_parquet(bulk / f"{date}.daily.parquet", columns=["lifecycle_notional"]).lifecycle_notional.unique()
        chosen = chosen[chosen.lifecycle_notional == (500 if actual_500 else 0)]
        for key, z in chosen.groupby(["policy", "admission", "ordering"], observed=True):
            tail_values[key].append(z.ret_gross.to_numpy() - .015)
    diagnostics = []
    for (name, admission, ordering), values in tail_values.items():
        v = np.concatenate(values) if values else np.array([])
        diagnostics.append({"policy": name, "admission": admission, "ordering": ordering,
                            "n": len(v), "median_net_return": float(np.median(v)) if len(v) else None,
                            "p05_net_return": float(np.quantile(v,.05)) if len(v) else None,
                            "worst_net_return": float(v.min()) if len(v) else None})
    payload = {"fingerprint": fingerprint, "status": "development_research_not_frozen",
               "calendar_days": len(dates), "original_days": sum(block(d)=="original533" for d in dates),
               "replication_days": sum(block(d)=="temporal_replication" for d in dates),
               "excluded": ["2024", "2025-01", "2025-02", "2026-06..2026-08"],
               "policy_count": len(specs), "candidate_exit": candidate,
               "candidate_selection": "pf2PRE pessimistic $500 150bps; positive every neighborhood/block, better neighborhood than baseline; not isolated best cell",
               "runner_component": None,
               "runner_reason": "Historical scaleout studies condition on postfill future-close/path cohorts and fixed fills; insufficient canonical lifecycle evidence for an earned runner.",
               "tails_150bps": diagnostics,
               "baseline_150bps_500": overall[(overall.policy == "f1_s0.1_bars30") &
                   (overall.cost_bps == 150) & (overall.order_notional == 500)].to_dict("records"),
               "candidate_rows": overall[overall.policy == candidate].to_dict("records") if candidate else [],
               "limitations": ["Raw cached OHLC close/reference/splits are unchanged; no new rank-quality claim.",
                   "Integer quantities floor(ORDER_notional/B); zero-share placements rejected before order creation and lifecycle replayed where they can differ. No shared account-capital cap.",
                   "Minute OHLC scenarios do not estimate queue fill probabilities or actual intrabar ordering.",
                   "Pessimistic/optimistic full lifecycle are scenarios, not mathematical bounds because rearm changes cohorts.",
                   "ALL pf2POST is posthoc anatomy, not a pre-order strategy.",
                   "All allowed dates were already H025-seen; temporal replication is not new OOS.",
                   "Occupancy is inclusive minute-bar proxy (exit_t-tf+1), not timestamp exposure; samebar trades consume one bar. Position principal and pending+held reservation are separate uncapped proxies, not account equity/return. Pending interval [t0,end_t), held inclusive labels [tf,exit_t+1). pf2POST has no definable pre-order reserve."]}
    (ART / "summary.json").write_text(json.dumps(payload, indent=2, default=str, allow_nan=True))
    print(json.dumps({"days": len(dates), "policies": len(specs), "candidate": candidate,
                      "baseline_pess_pf2PRE_dollars_day": float(base.net_dollars_per_day)}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--bulk-root", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit-days", type=int)
    parser.add_argument("--families", nargs="*", default=None)
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument("--orderings", nargs="+", choices=("legacy", "pessimistic", "optimistic"),
                        default=["legacy", "pessimistic", "optimistic"])
    args = parser.parse_args()
    ART.mkdir(parents=True, exist_ok=True)
    bulk = args.bulk_root or args.data_root / "h025_research" / "exit"
    bulk.mkdir(parents=True, exist_ok=True)
    dates = calendar(args.data_root)
    if args.limit_days:
        dates = dates[:args.limit_days]
    families = tuple(args.families) if args.families else None
    manifest = {"families": sorted(families) if families else "all",
                "policies": {name: {**spec, "policy": asdict(spec["policy"])} for name,spec in select_specs(families).items()},
                "orderings": args.orderings, "data_root": str(args.data_root.resolve()),
                "engine_sha256": hashlib.sha256(Path(__file__).with_name("h025_research_core.py").read_bytes()).hexdigest(),
                "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (ART / "manifest.json").write_text(json.dumps({**manifest, "fingerprint": fingerprint, "dates": dates}, indent=2))
    if not args.summarize_only:
        jobs = [(date,str(args.data_root),str(bulk),fingerprint,args.orderings,families) for date in dates]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for index, (date, status) in enumerate(pool.map(replay_job, jobs), 1):
                if index % 25 == 0 or index == len(dates):
                    print(f"{index}/{len(dates)} {date} {status}", flush=True)
    for date in dates:
        marker = bulk / f"{date}.complete.json"
        if not marker.exists() or json.loads(marker.read_text()).get("fingerprint") != fingerprint:
            raise RuntimeError(f"Missing matching completed day: {date}")
    summarize(bulk, dates, fingerprint, families)


if __name__ == "__main__":
    main()
