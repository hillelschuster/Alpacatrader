#!/usr/bin/env python3
"""Read-only H025 execution anatomy; no network clients or order APIs.

Native bars end at t and cover [t-1,t). Orders based on state t0 are already
available at t0: the first eligible bar ends at t0+1, with no extra minute.
Minute chronology is a sensitivity, not empirical queue/fill probability.
Run with explicit --data-root and --sip-root. Daily artifacts are resumable.
"""
import argparse
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from h025_research_core import Policy, allowed_dates, load_day, replay_day
from sip_bars import combine

ROOT = Path(__file__).resolve().parents[2]
FIRST_END = "2023-03-14"
NOTIONALS = (250, 500, 1000)
COSTS = (100, 150, 200)


def clean_json(obj):
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def write_json(path, obj):
    path.write_text(json.dumps(clean_json(obj), indent=2, allow_nan=False))


def minute_ts(day, minute):
    return (pd.Timestamp(day, tz="America/New_York") +
            pd.Timedelta(minutes=int(minute))).tz_convert("UTC")


def paired_exit(d, fill, ordering):
    """Hold original fill fixed, vary ONLY target/stop chronology.

    This is not a full-lifecycle policy: altered exits can change rearm/refresh.
    Pessimistic fillbar high may precede buy; close>=target proves a subsequent
    target crossing (unless stop first). Later both-touch bars use stop first.
    """
    B, target = float(fill.B), float(fill.c0)
    stop = .9 * B
    pos = d["newpos"]
    start = int(np.searchsorted(d["t"][pos], int(fill.tf)))
    for count, j in enumerate(pos[start:], 1):
        opening, high, low, close = (float(d[k][j]) for k in ("o", "h", "l", "c"))
        target_ok = high >= target
        if count == 1 and ordering == "pessimistic":
            target_ok = close >= target
        if ordering == "optimistic" and target_ok and not (count > 1 and opening <= stop):
            return target / B - 1, int(d["t"][j]), "target", 0.0
        if low <= stop:
            px = min(opening, stop)
            return px / B - 1, int(d["t"][j]), "stop", max(0., (stop-px)/B)
        if target_ok:
            return target / B - 1, int(d["t"][j]), "target", 0.0
        if count >= 30:
            return close / B - 1, int(d["t"][j]), "time", 0.0
    j = int(pos[-1])
    return float(d["c"][j])/B-1, int(d["t"][j]), "session", 0.0


def annotate(day, fills):
    if fills.empty:
        return fills.copy()
    out = fills.copy()
    rows = []
    for fill in fills.itertuples(index=False):
        d = day.arrays[(str(fill.date), str(fill.ticker))]
        j = int(np.searchsorted(d["t"], int(fill.tf)))
        ex = int(np.searchsorted(d["t"], int(fill.exit_t)))
        p = int(np.searchsorted(d["t"][d["newpos"]], int(fill.tf)))
        previous_t = int(d["t"][d["newpos"][p-1]]) if p else None
        row = {
            "fill_o": float(d["o"][j]), "fill_h": float(d["h"][j]),
            "fill_l": float(d["l"][j]), "fill_c": float(d["c"][j]),
            "fill_volume": float(d["v"][j]), "exit_volume": float(d["v"][ex]),
            "entry_open_below_B": bool(d["o"][j] < fill.B),
            "missing_clock_minutes_before_fill": max(0, int(fill.tf)-previous_t-1) if previous_t else None,
            "fillbar_high_ge_target": bool(d["h"][j] >= fill.c0),
            "fillbar_close_ge_target": bool(d["c"][j] >= fill.c0),
            "fillbar_stop_touch": bool(d["l"][j] <= .9*fill.B),
            "legacy_stop_gap_loss": max(0., (.9*fill.B-d["o"][ex])/fill.B) if str(fill.exit_reason) == "stop" else 0.,
            "occupancy_clock_minutes": int(fill.exit_t)-int(fill.tf)+1,
        }
        for ordering in ("pessimistic", "optimistic"):
            gross, t, reason, gap = paired_exit(d, fill, ordering)
            row.update({f"paired_{ordering}_gross": gross, f"paired_{ordering}_exit_t": t,
                        f"paired_{ordering}_reason": reason, f"paired_{ordering}_gap_loss": gap})
        rows.append(row)
    return pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def filtered_tape(path, symbols):
    if not path.exists():
        return pd.DataFrame()
    table = pq.read_table(path, filters=[("symbol", "in", sorted(symbols))])
    frame = table.to_pandas()
    if len(frame):
        frame["ts_utc"] = pd.to_datetime(frame["ts_utc"], utc=True)
        frame = frame.sort_values("ts_utc", kind="stable")
    return frame


def micro_day(day, fills, sip_root):
    """Existing local NET only; original533 only. ALL-print support, not eligibility.

    Queue/service scenarios use buy prints <=B and target-sell prints >=c0.
    For market exits, total subsequent print volume is merely a capacity ruler;
    it is NOT executable bid depth or evidence of a passive queue fill.
    """
    if fills.empty or str(fills.iloc[0]["date"]) > FIRST_END:
        return pd.DataFrame(), pd.DataFrame()
    date = str(fills.iloc[0]["date"])
    symbols = set(fills["ticker"].astype(str))
    trades = filtered_tape(sip_root / "trades" / f"{date}.parquet", symbols)
    quotes = filtered_tape(sip_root / "quotes" / f"{date}.parquet", symbols)
    ts_groups = {s: g for s, g in trades.groupby("symbol")} if len(trades) else {}
    q_groups = {s: g for s, g in quotes.groupby("symbol")} if len(quotes) else {}
    rows, frontier = [], []
    for f in fills.itertuples(index=False):
        tr = ts_groups.get(f.ticker, pd.DataFrame())
        qt = q_groups.get(f.ticker, pd.DataFrame())
        start, end = minute_ts(date, f.tf-1), minute_ts(date, f.tf)
        order_start = minute_ts(date, f.t0)
        ex_start, ex_end = minute_ts(date, f.exit_t-1), minute_ts(date, f.exit_t)
        row = {"date": date, "ticker": f.ticker, "t0": int(f.t0), "tf": int(f.tf),
               "B": float(f.B), "c0": float(f.c0), "prior_flush": int(f.prior_flush),
               "ret": float(f.ret), "trade_cache_present": bool(len(tr)),
               "quote_cache_present": bool(len(qt)), "eligibility": "unclassified_ALL_prints"}
        if tr.empty:
            rows.append(row)
            continue
        win = tr[(tr.ts_utc >= start) & (tr.ts_utc < end)]
        window = tr[(tr.ts_utc >= order_start) & (tr.ts_utc < ex_end)]
        touches = win[win.price <= f.B]
        earlier_touch = window[window.price <= f.B]
        row["raw_touch_before_credited_minute"] = bool(len(earlier_touch) and earlier_touch.ts_utc.iloc[0] < start)
        if len(window):
            timestamps = window.ts_utc.dt.tz_convert("America/New_York")
            ends = timestamps.dt.hour*60+timestamps.dt.minute+1
            d = day.arrays[(str(f.date), str(f.ticker))]
            path_new_t = set(map(int, d["t"][d["newpos"]]))
            row["raw_print_minutes_absent_from_path_NEW"] = len(set(map(int, ends))-path_new_t)
            row["raw_whole_window_min_over_B"] = float(window.price.min()/f.B-1)
        if len(win) and {"conditions", "tape", "exchange"}.issubset(win.columns):
            axes = [combine(list(c) if c is not None else [], str(tp)) for c,tp in zip(win.conditions, win.tape)]
            eligible = win[np.array([a[1] == 2 for a in axes])]
            row["price_updating_fill_prints"] = len(eligible)
            row["price_updating_touch_support"] = bool((eligible.price <= f.B).any())
            row["price_updating_touch_volume"] = float(eligible.loc[eligible.price <= f.B, "size"].sum())
            row["unknown_condition_prints"] = sum(bool(a[3]) for a in axes)
            row["fullfield_condition_policy"] = "existing sip_bars.combine high_low=2; NOT venue/order eligibility"
            if len(eligible):
                row["price_updating_fill_low"] = float(eligible.price.min())
                row["price_updating_fill_high"] = float(eligible.price.max())
                row["price_updating_low_minus_path_bps"] = 1e4*(eligible.price.min()/f.fill_l-1)
                row["price_updating_high_minus_path_bps"] = 1e4*(eligible.price.max()/f.fill_h-1)
                etouch = eligible[eligible.price <= f.B]
                if len(etouch):
                    eanchor = etouch.ts_utc.iloc[0]
                    row["price_updating_target_before_touch"] = bool((eligible.loc[eligible.ts_utc < eanchor, "price"] >= f.c0).any())
                    row["price_updating_target_strictly_after_touch"] = bool((eligible.loc[eligible.ts_utc > eanchor, "price"] >= f.c0).any())
        row.update({"fill_minute_prints": int(len(win)), "whole_window_prints": int(len(window)),
                    "raw_touch_support": bool(len(touches)),
                    "condition_field_present": "conditions" in tr,
                    "exchange_field_present": "exchange" in tr,
                    "tape_field_present": "tape" in tr})
        if len(win):
            raw = {"o": float(win.price.iloc[0]), "h": float(win.price.max()),
                   "l": float(win.price.min()), "c": float(win.price.iloc[-1])}
            for k, v in raw.items():
                row[f"raw_fill_{k}"] = v
                row[f"raw_minus_path_{k}_bps"] = 1e4*(v/getattr(f, f"fill_{k}")-1)
            row["raw_fill_volume"] = float(win["size"].sum())
            row["raw_volume_over_path"] = row["raw_fill_volume"]/float(f.fill_volume) if f.fill_volume > 0 else None
        if len(touches):
            anchor = touches.ts_utc.iloc[0]
            after = win[win.ts_utc > anchor]
            same = win[win.ts_utc == anchor]
            row.update({"first_touch_utc": anchor.isoformat(),
                        "raw_target_before_touch": bool((win.loc[win.ts_utc < anchor, "price"] >= f.c0).any()),
                        "raw_target_strictly_after_touch": bool((after.price >= f.c0).any()),
                        "raw_target_same_timestamp": bool((same.price >= f.c0).any()),
                        "same_timestamp_print_count": int(len(same)),
                        "touch_volume_at_or_below_B": float(touches["size"].sum())})
            if len(qt):
                qprev = qt[qt.ts_utc <= anchor]
                if len(qprev):
                    q = qprev.iloc[-1]
                    row.update({"quote_age_s": float((anchor-q.ts_utc).total_seconds()),
                                "bid_px": float(q.bid_price), "ask_px": float(q.ask_price),
                                "bid_size_raw": float(q.bid_size), "ask_size_raw": float(q.ask_size),
                                "quote_unit_multiplier": 100,
                                "bid_size_shares": 100*float(q.bid_size),
                                "ask_size_shares": 100*float(q.ask_size),
                                "quote_size_basis": "repo_historical_cutoff_pre_2025-11-03_round_lots"})
        # Fixed-price, fixed-touch capacity frontiers. Unknown Qbuy/Qsell,
        # service fractions and latency are ASSUMED, not fitted probabilities.
        for latency in (0, 1, 5, 15, 60):
            activation = order_start + pd.Timedelta(seconds=latency)
            buy = win[(win.ts_utc >= activation) & (win.price <= f.B)]
            buy_anchor = buy.ts_utc.iloc[0] if len(buy) else None
            reason = str(f.exit_reason)
            sell = tr.iloc[:0]
            if buy_anchor is not None:
                if reason in ("target", "stop"):
                    exits = tr[(tr.ts_utc >= ex_start) & (tr.ts_utc < ex_end) & (tr.ts_utc > buy_anchor)]
                    exits = exits[exits.price >= f.c0] if reason == "target" else exits[exits.price <= .9*f.B]
                    if len(exits):
                        sell_activation = exits.ts_utc.iloc[0]+pd.Timedelta(seconds=latency)
                        sell = tr[(tr.ts_utc >= sell_activation) & (tr.ts_utc < ex_end)]
                        if reason == "target":
                            sell = sell[sell.price >= f.c0]
                else:
                    sell_activation = ex_end+pd.Timedelta(seconds=latency)
                    sell = tr[(tr.ts_utc >= sell_activation) & (tr.ts_utc < ex_end+pd.Timedelta(minutes=1))]
            vb, vs = float(buy["size"].sum()), float(sell["size"].sum())
            for budget in NOTIONALS:
                qty = math.floor(budget/f.B)
                for service in (.01, .1, .5, 1.):
                    frontier.append({"date": date, "ticker": f.ticker, "tf": int(f.tf),
                                     "order_notional": budget, "integer_qty": qty,
                                     "deployed_dollars": qty*f.B, "latency_seconds_assumed": latency,
                                     "service_fraction_assumed": service,
                                     "buy_all_print_volume": vb, "sell_all_print_volume": vs,
                                     "max_Qbuy_shares_assuming_full_fill": service*vb-qty,
                                     "max_Qsell_shares_assuming_full_fill": service*vs-qty,
                                     "buy_supported_if_Q0": qty > 0 and service*vb >= qty,
                                     "sell_supported_if_Q0": qty > 0 and service*vs >= qty,
                                     "sell_mode": "target_prints_ge_target" if str(f.exit_reason) == "target" else "ALL_prints_market_exit_proxy",
                                     "net_dollars_100bps_legacy_assuming_full_fill": qty*f.B*f.ret})
        rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame(frontier)


def summarize(fills, dates, capacity_fills=None):
    if fills.empty:
        return {"fills": 0, "calendar_days": len(dates), "net_return_units_per_day": 0.}
    r = fills["ret"]
    d = {"fills": len(fills), "calendar_days": len(dates), "fill_days": fills.date.nunique(),
         "fills_per_day": len(fills)/len(dates), "mean_net_per_fill": r.mean(),
         "net_return_units_per_day": r.sum()/len(dates), "net_return_units_sum": r.sum(),
         "net_win_fraction": (r > 0).mean(), "worst_net_fill": r.min(),
         "q01_net_fill": r.quantile(.01), "median_net_fill": r.median(),
         "entry_gap_count": int(fills.entry_open_below_B.sum()),
         "fillbar_target_count": int(fills.fillbar_high_ge_target.sum()),
         "fillbar_target_without_close_proof": int((fills.fillbar_high_ge_target & ~fills.fillbar_close_ge_target).sum()),
         "fillbar_stop_and_target_count": int((fills.fillbar_high_ge_target & fills.fillbar_stop_touch).sum()),
         "stop_gap_count": int((fills.legacy_stop_gap_loss > 0).sum()),
         "stop_gap_return_units_loss": fills.legacy_stop_gap_loss.sum(),
         "stop_gap_loss_per_fill": fills.legacy_stop_gap_loss.mean(),
         "stop_gap_loss_per_day": fills.legacy_stop_gap_loss.sum()/len(dates),
         "occupancy_clock_minutes_sum": fills.occupancy_clock_minutes.sum(),
         "occupancy_clock_minutes_per_day": fills.occupancy_clock_minutes.sum()/len(dates),
         "paired_pessimistic_mean_net": (fills.paired_pessimistic_gross-.01).mean(),
         "paired_optimistic_mean_net": (fills.paired_optimistic_gross-.01).mean(),
         "paired_pessimistic_return_units_per_day": (fills.paired_pessimistic_gross-.01).sum()/len(dates),
         "paired_optimistic_return_units_per_day": (fills.paired_optimistic_gross-.01).sum()/len(dates)}
    capacity = []
    for budget in NOTIONALS:
        selected = fills if capacity_fills is None else capacity_fills[capacity_fills.order_notional==budget]
        qty = np.floor(budget/selected.B)
        deployed = qty*selected.B
        for cost in COSTS:
            pnl = deployed*(selected.ret_gross-cost/1e4)
            loss_gap = deployed*selected.legacy_stop_gap_loss
            daily = pnl.groupby(selected.date).sum().reindex(dates, fill_value=0.)
            capacity.append({"order_notional": budget, "friction_bps": cost, "actual_policy_fills": len(selected),
                             "zero_quantity_fills": int((qty==0).sum()),
                             "total_net_dollars": pnl.sum(), "net_dollars_per_calendar_day": daily.mean(),
                             "worst_day_dollars": daily.min(), "q01_day_dollars": daily.quantile(.01),
                             "worst_fill_dollars": pnl.min(),
                             "stop_gap_dollars_loss": loss_gap.sum(),
                             "occupancy_clock_minutes_per_day": selected.occupancy_clock_minutes.sum()/len(dates),
                             "months_dollars": [{"month":m, "days":int(sum(x.startswith(m) for x in dates)), "net_dollars_per_day":float(daily[[x.startswith(m) for x in dates]].mean())} for m in sorted({x[:7] for x in dates})],
                             "years_dollars": [{"year":y, "days":int(sum(x.startswith(y) for x in dates)), "net_dollars_per_day":float(daily[[x.startswith(y) for x in dates]].mean())} for y in sorted({x[:4] for x in dates})],
                             "fill_minute_participation_median": (qty/selected.fill_volume.replace(0,np.nan)).median(),
                             "exit_minute_participation_median": (qty/selected.exit_volume.replace(0,np.nan)).median(),
                             "paired_pessimistic_net_dollars_per_day": (deployed*(selected.paired_pessimistic_gross-cost/1e4)).sum()/len(dates),
                             "paired_optimistic_net_dollars_per_day": (deployed*(selected.paired_optimistic_gross-cost/1e4)).sum()/len(dates)})
    d["integer_order_capacity_no_queue_assumed"] = capacity
    d["monthly"] = []
    for month in sorted({x[:7] for x in dates}):
        s = fills[fills.date.str.startswith(month)]
        nd = sum(x.startswith(month) for x in dates)
        d["monthly"].append({"month": month, "days": nd, "fills": len(s),
                              "net_return_units_per_day": s.ret.sum()/nd,
                              "mean_net_per_fill": s.ret.mean()})
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", required=True, type=Path)
    ap.add_argument("--sip-root", required=True, type=Path)
    ap.add_argument("--stage", choices=("original533", "replication", "all"), default="original533")
    ap.add_argument("--output-root", type=Path, default=ROOT/"factory/artifacts/h025_research/execution")
    ap.add_argument("--bulk-root", required=True, type=Path)
    ap.add_argument("--limit-days", type=int, default=0)
    a = ap.parse_args()
    dates = list(allowed_dates(a.data_root, stage=a.stage))
    if a.limit_days:
        dates = dates[:a.limit_days]
    a.bulk_root.mkdir(parents=True, exist_ok=True)
    a.output_root.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name("h025_research_core.py").read_bytes()+Path(__file__).with_name("sip_bars.py").read_bytes()+json.dumps({"stage":a.stage,"policy":asdict(Policy()),"data_root":str(a.data_root),"sip_root":str(a.sip_root)}).encode()).hexdigest()
    daily = a.bulk_root/signature[:16]
    daily.mkdir(exist_ok=True)
    for n, date in enumerate(dates, 1):
        dest = daily/date
        if (dest/"complete.json").exists():
            continue
        day = load_day(a.data_root, date)
        dest.mkdir(exist_ok=True)
        capacity_frames = []
        for policy_name, policy in (("all_orders", Policy()), ("pf2_preorder", Policy(prior_flush_min=2))):
            for ordering in ("legacy", "pessimistic", "optimistic"):
                replay = replay_day(day, policy, ordering=ordering)
                fills = annotate(day, replay.fills)
                fills.to_parquet(dest/f"{policy_name}_{ordering}.parquet", index=False)
                for budget in NOTIONALS:
                    if len(day.states) and (day.states.c*.9 > budget).any():
                        budget_policy = Policy(prior_flush_min=policy.prior_flush_min, order_notional=budget)
                        actual = annotate(day, replay_day(day, budget_policy, ordering=ordering).fills)
                    else:
                        actual = fills.copy()
                    actual["order_notional"] = budget
                    actual["cell"] = f"{policy_name}_{ordering}"
                    capacity_frames.append(actual)
                write_json(dest/f"{policy_name}_{ordering}_orders.json", {"count": len(replay.orders)})
                if policy_name == "all_orders" and ordering == "legacy":
                    micro, frontier = micro_day(day, fills, a.sip_root)
                    micro.to_parquet(dest/"micro.parquet", index=False)
                    frontier.to_parquet(dest/"frontier.parquet", index=False)
        pd.concat(capacity_frames, ignore_index=True).to_parquet(dest/"integer_capacity_fills.parquet", index=False)
        manifests = {}
        if date <= FIRST_END:
            for lane in ("trades", "quotes"):
                mp = a.sip_root/lane/f"{date}.manifest.json"
                if mp.exists():
                    metadata = json.loads(mp.read_text())
                    manifests[lane] = {k:metadata.get(k) for k in ("status", "errors", "window_utc", "symbols_requested", "symbols_with_data", "rows", "sha256", "min_ts", "max_ts")}
        write_json(dest/"NET_metadata.json", manifests)
        write_json(dest/"complete.json", {"date": date, "signature": signature, "source": day.source})
        if n % 25 == 0 or n == len(dates):
            print(f"execution persisted {n}/{len(dates)} {date}", flush=True)
    out = {"stage": a.stage, "calendar_days": len(dates), "first_day": min(dates), "last_day": max(dates),
           "signature": signature, "daily_artifacts": str(daily), "excluded_spent_feb2025_days": 19,
           "cohorts": {},
           "scope": "DEVELOPMENT / temporal replication; no new OOS", "cells": {},
           "clock": "native completed bar t=[t-1,t); order available at t0; no extra minute shift",
           "assumptions": ["Original H025 name qualification held fixed; raw/reference source not certified.",
                           "Legacy unchanged. Pessimistic/optimistic are OHLC chronology scenarios, not exact broker fill bounds.",
                           "Paired fill-fixed sensitivities distinct from full-lifecycle counterfactual rearm/refresh.",
                           "Minute volume / local ALL-print volume are support proxies, never empirical queue probabilities.",
                           "Qbuy/Qsell, service fraction and latency unknown. Frontier values are assumed scenarios.",
                           "Historical quotes normalize round lots x100; an as-of quote is not full depth or queue ahead.",
                           "No protected OOS prices read; NET micro only original533. Old stripped caches cannot establish condition/exchange eligibility.",
                           "Integer qty=floor(ORDER notional/B), costs charged on deployed notional; idle order cash earns zero. Actual budget policy rejects qty0 BEFORE placement; replays when high-price states can change pending refresh.",
                           "Sum-return units/day is fixed order-notional turnover PnL, NOT account return or compounded return."]}
    capacity_all = pd.concat([pd.read_parquet(daily/date/"integer_capacity_fills.parquet") for date in dates], ignore_index=True)
    capacity_all.to_parquet(a.bulk_root/f"{a.stage}_integer_capacity_fills.parquet", index=False)
    collected = {}
    for policy in ("all_orders", "pf2_preorder"):
        for ordering in ("legacy", "pessimistic", "optimistic"):
            key = f"{policy}_{ordering}"
            frames = [pd.read_parquet(daily/date/f"{key}.parquet") for date in dates]
            fills = pd.concat(frames, ignore_index=True)
            collected[key] = fills
            out["cells"][key] = summarize(fills, dates, capacity_all[capacity_all.cell==key])
            out["cells"][key]["orders_count"] = sum(json.loads((daily/date/f"{key}_orders.json").read_text())["count"] for date in dates)
            if len(fills):
                fills.to_parquet(a.bulk_root/f"{a.stage}_{key}_fills.parquet", index=False)
                if policy == "all_orders":
                    out["cells"][f"pf2_POSTHOC_{ordering}"] = summarize(fills[fills.prior_flush>=2], dates, capacity_all[(capacity_all.cell==key)&(capacity_all.prior_flush>=2)])
    for cohort in ("original533", "replication"):
        cohort_dates = sorted(set(allowed_dates(a.data_root, cohort)) & set(dates))
        if not cohort_dates:
            continue
        out["cohorts"][cohort] = {}
        for key, fills in collected.items():
            cf = fills[fills.date.isin(cohort_dates)]
            cc = capacity_all[(capacity_all.cell==key)&capacity_all.date.isin(cohort_dates)]
            out["cohorts"][cohort][key] = summarize(cf, cohort_dates, cc)
            if key.startswith("all_orders"):
                out["cohorts"][cohort]["pf2_POSTHOC_"+key.removeprefix("all_orders_")] = summarize(cf[cf.prior_flush>=2], cohort_dates, cc[cc.prior_flush>=2])
    out["local_NET_request_metadata"] = [{"date":date, **json.loads((daily/date/"NET_metadata.json").read_text())} for date in dates if date <= FIRST_END]
    micro = pd.concat([pd.read_parquet(daily/date/"micro.parquet") for date in dates], ignore_index=True)
    frontier = pd.concat([pd.read_parquet(daily/date/"frontier.parquet") for date in dates], ignore_index=True)
    micro.to_parquet(a.bulk_root/f"{a.stage}_micro.parquet", index=False)
    frontier.to_parquet(a.bulk_root/f"{a.stage}_capacity_frontier.parquet", index=False)
    if len(micro):
        coverage = {"baseline_fills_in_original533": len(micro), "micro_trade_cache_fills": int(micro.trade_cache_present.sum()),
                    "micro_quote_cache_fills": int(micro.quote_cache_present.sum())}
        for col in ("raw_touch_support", "raw_target_before_touch", "raw_target_strictly_after_touch", "raw_target_same_timestamp", "raw_touch_before_credited_minute", "price_updating_touch_support", "price_updating_target_before_touch", "price_updating_target_strictly_after_touch"):
            coverage[col+"_count"] = int(micro[col].fillna(False).sum()) if col in micro else 0
        for col in ("raw_minus_path_o_bps", "raw_minus_path_h_bps", "raw_minus_path_l_bps", "raw_minus_path_c_bps", "raw_volume_over_path", "quote_age_s", "price_updating_low_minus_path_bps", "price_updating_high_minus_path_bps", "raw_print_minutes_absent_from_path_NEW"):
            if col in micro:
                s = micro[col].dropna()
                coverage[col] = {"n": len(s), "median": s.median(), "q01": s.quantile(.01), "q99": s.quantile(.99)}
        ambiguous = micro.merge(collected["all_orders_legacy"][["date","ticker","tf","fillbar_high_ge_target","fillbar_close_ge_target","paired_pessimistic_gross","paired_optimistic_gross"]], on=["date","ticker","tf"], how="left")
        ambiguity = ambiguous.fillbar_high_ge_target & ~ambiguous.fillbar_close_ge_target
        coverage["ambiguous_high_fills"] = int(ambiguity.sum())
        coverage["ambiguous_high_without_raw_fill_minute"] = int((ambiguity & (ambiguous.fill_minute_prints.fillna(0)==0)).sum()) if "fill_minute_prints" in ambiguous else int(ambiguity.sum())
        coverage["uncovered_ambiguity_gross_return_width"] = float((ambiguous.loc[ambiguity & ~ambiguous.trade_cache_present,"paired_optimistic_gross"]-ambiguous.loc[ambiguity & ~ambiguous.trade_cache_present,"paired_pessimistic_gross"]).sum())
        out["local_NET_micro"] = coverage
    if len(frontier):
        out["micro_assumed_capacity_scenarios"] = frontier.groupby(["order_notional","latency_seconds_assumed","service_fraction_assumed"]).agg(n=("integer_qty","size"),buy_Q0_support_rate=("buy_supported_if_Q0","mean"),sell_Q0_proxy_support_rate=("sell_supported_if_Q0","mean"),median_max_Qbuy=("max_Qbuy_shares_assuming_full_fill","median"),median_max_Qsell=("max_Qsell_shares_assuming_full_fill","median")).reset_index().to_dict("records")
    published = ROOT/"factory/artifacts/lb18_subminute.json"
    if published.exists():
        old = json.loads(published.read_text())
        out["protected_published_readout_only"] = {"path": str(published), "study": old.get("study"), "coverage": old.get("coverage"), "boundary": "Own OOS cache starts at tf, AFTER credited [tf-1,tf). Published metadata only; no cache price rows opened."}
    write_json(a.output_root/f"{a.stage}_summary.json", out)
    lines = ["# H025 execution decomposition", "", f"Computed {len(dates)} legitimate {a.stage} days ({min(dates)} .. {max(dates)}).", "", "Original qualification unchanged. All results are development/temporal replication, not new OOS.", "", "## Full lifecycle, 100bps", "", "| Policy / chronology | fills | mean net/fill | return units/day | stop-gap units lost |", "|---|---:|---:|---:|---:|"]
    for key, cell in out["cells"].items():
        if cell["fills"]:
            lines.append("| {} | {} | {:.6f} | {:.6f} | {:.6f} |".format(key, cell["fills"], cell["mean_net_per_fill"], cell["net_return_units_per_day"], cell["stop_gap_return_units_loss"]))
    lines += ["", "## Interpretation and evidence boundaries", ""]+["- "+x for x in out["assumptions"]]
    lines += ["", "See summary JSON for the actual $250/$500/$1000 integer-order PnL frontier at 100/150/200bps, monthly zero-day-inclusive economics, occupancy, tails, missing windows and local NET whole-window source discrepancies.", "", "Paper journal alignment: served 085b200 on Sep15/16 is descriptive source provenance only. Journal write is not broker filled_at; this producer reads no broker records, sends no orders and changes no flags.", "", f"Resume signature: {signature}; daily artifacts: {daily}."]
    (a.output_root/f"{a.stage}_REPORT.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"summary": str(a.output_root/f"{a.stage}_summary.json"), "days": len(dates), "cells": {k:{x:v.get(x) for x in ("fills","mean_net_per_fill","net_return_units_per_day")} for k,v in out["cells"].items()}}, indent=2), flush=True)


if __name__ == "__main__":
    main()
