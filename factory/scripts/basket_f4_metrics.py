from __future__ import annotations

import math
from pathlib import Path

import polars as pl

try:
    from factory.scripts.basket_f4_blocks import block_report, mean
    from factory.scripts.basket_f4_rules import BLOCKS, Cell, F4ContractError
except ImportError:
    from basket_f4_blocks import block_report, mean
    from basket_f4_rules import BLOCKS, Cell, F4ContractError


def tail_metrics(records: list[dict]) -> dict:  # noqa: DICT_OK — JSON metrics schema
    result = {}
    for threshold in (30, 50, 100):
        hits = [record for record in records if record["mfe_raw"] >= threshold / 100]
        early = [record for record in hits
                 if record["reduced_before_touch"].get(str(threshold), False)]
        released = [record for record in hits
                    if record.get("released_before_touch", {}).get(str(threshold), False)]
        shares = [record["tail_fraction_at_touch"][str(threshold)] for record in hits
                  if str(threshold) in record["tail_fraction_at_touch"]]
        result[str(threshold)] = {
            "touch_n": len(hits), "reduced_early_n": len(early),
            "reduced_early_rate": len(early) / len(hits) if hits else None,
            "released_early_n": len(released),
            "released_early_rate": len(released) / len(hits) if hits else None,
            "mean_raw_shares_retained_at_touch": mean(shares),
        }
    return result


def _tranche_summary(records: list[dict], days_n: int) -> dict:  # noqa: DICT_OK — JSON metrics schema
    result: dict[str, dict] = {}
    for record in records:
        block = next(name for name, (lo, hi) in BLOCKS.items()
                     if lo <= record["sleeve_day"][:7] <= hi)
        for action in record["actions"]:
            if action["action"] != "REDUCE":
                continue
            stage = action["reason"].rsplit(":stage", 1)[-1]
            stage_row = result.setdefault(stage, {"n": 0, "net_pnl": 0.0, "blocks": {}})
            stage_row["n"] += 1
            stage_row["net_pnl"] += action["net_tranche_pnl"]
            for threshold in (50, 100):
                if record["mfe_raw"] >= threshold / 100:
                    key = f"net_pnl_raw_mfe_ge_{threshold}"
                    stage_row[key] = stage_row.get(key, 0.0) + action["net_tranche_pnl"]
            block_row = stage_row["blocks"].setdefault(block, {"n": 0, "net_pnl": 0.0})
            block_row["n"] += 1
            block_row["net_pnl"] += action["net_tranche_pnl"]
            for threshold in (50, 100):
                if record["mfe_raw"] >= threshold / 100:
                    key = f"net_pnl_raw_mfe_ge_{threshold}"
                    block_row[key] = block_row.get(key, 0.0) + action["net_tranche_pnl"]
    for stage_row in result.values():
        stage_row["mean_net_pnl_per_executed_tranche"] = stage_row["net_pnl"] / stage_row["n"]
        stage_row["net_pnl_c0_per_basket_day"] = stage_row["net_pnl"] / days_n
        for block, block_row in stage_row["blocks"].items():
            block_days = 734 if block == "block1" else 332
            block_row["mean_net_pnl_per_executed_tranche"] = block_row["net_pnl"] / block_row["n"]
            block_row["net_pnl_c0_per_basket_day"] = block_row["net_pnl"] / block_days
    return result


def cell_metrics(  # noqa: DICT_OK — simulator metrics are serialized as JSON
        cell: Cell, run_dir: Path, days: list[str], metrics: dict,
        chronology: list[dict], raw_paths: dict[tuple[str, str], dict]) -> dict:
    daily = pl.read_parquet(run_dir / "daily.parquet").sort("date")
    tickets_df = pl.read_parquet(run_dir / "tickets.parquet")
    if daily["date"].cast(pl.String).to_list() != days:
        raise F4ContractError(f"daily coverage mismatch for {cell.run_id}")
    tickets = list(tickets_df.iter_rows(named=True))
    raw_records = [{**raw_paths[(str(ticket["sleeve_day"]), ticket["ticker"])],
                    "sleeve_day": str(ticket["sleeve_day"]), "ticker": ticket["ticker"],
                    "net": ticket["net"], "net_return": ticket["net_return"]}
                   for ticket in tickets]
    action_cost = {(record["sleeve_day"], record["ticker"]): record["friction_cost"]
                   for record in chronology}
    side = cell.bps / 20_000
    friction_cost = 0.0
    for ticket in tickets:
        key = (str(ticket["sleeve_day"]), ticket["ticker"])
        if key in action_cost:
            friction_cost += action_cost[key]
        else:
            friction_cost += 0.5 - ticket["shares_entry"] * ticket["entry_px"]
            if not ticket["open_end"] and ticket["exit_px"] is not None:
                friction_cost += ticket["shares_entry"] * ticket["exit_px"] * side
    controls = {}
    for block, (lo, hi) in BLOCKS.items():
        block_days = [day for day in days if lo <= day[:7] <= hi]
        filtered_daily = daily.filter(pl.col("date").cast(pl.String).is_in(block_days))
        block_tickets = [ticket for ticket in tickets
                         if lo <= str(ticket["sleeve_day"])[:7] <= hi]
        controls[block] = block_report(filtered_daily, block_tickets)
        block_raw = [record for record in raw_records
                     if lo <= record["sleeve_day"][:7] <= hi]
        tail_values = [record["net_return"] / record["mfe_raw"] for record in block_raw
                       if record["mfe_raw"] >= .5 and record["mfe_raw"] > 0]
        controls[block]["raw_mfe_ge_50_n"] = len(tail_values)
        controls[block]["tail_retained_mean_mfe_ge_50"] = mean(tail_values)
        controls[block]["friction_cost"] = sum(
            action_cost.get((str(ticket["sleeve_day"]), ticket["ticker"]),
                            0.5 - ticket["shares_entry"] * ticket["entry_px"] +
                            (ticket["shares_entry"] * ticket["exit_px"] * side
                             if not ticket["open_end"] and ticket["exit_px"] is not None else 0.0))
            for ticket in block_tickets)
    values = daily["r_day"].to_list()
    touch_by_h = {str(h): [record for record in raw_records if record["mfe_raw"] >= h / 100]
                  for h in (30, 50, 100)}
    tail_stats = tail_metrics(chronology) if cell.kind in ("partial", "full_exit") else {
        str(h): {"touch_n": len(touch_by_h[str(h)]), "reduced_early_n": 0,
                 "reduced_early_rate": 0.0 if touch_by_h[str(h)] else None,
                 "released_early_n": 0, "released_early_rate": 0.0 if touch_by_h[str(h)] else None,
                 "mean_raw_shares_retained_at_touch": 1.0 if touch_by_h[str(h)] else None}
        for h in (30, 50, 100)}
    tranche_pnl = _tranche_summary(chronology, len(days))
    by_day: dict[str, list[dict]] = {}
    for ticket in raw_records:
        by_day.setdefault(ticket["sleeve_day"], []).append(ticket)
    joint = {
        "days_n": len(days),
        "days_ge2_mfe30": sum(sum(ticket["mfe_raw"] >= .30 for ticket in by_day.get(day, [])) >= 2
                               for day in days),
        "days_ge2_eod_net_positive": sum(sum(ticket["net"] > 0 for ticket in by_day.get(day, [])) >= 2
                                          for day in days),
        "all_filled_eod_net_positive_days": sum(
            bool(by_day.get(day)) and all(ticket["net"] > 0 for ticket in by_day[day])
            for day in days),
    }
    joint["p_days_ge2_mfe30"] = joint["days_ge2_mfe30"] / len(days)
    joint["p_days_ge2_eod_net_positive"] = joint["days_ge2_eod_net_positive"] / len(days)
    joint["p_all_filled_eod_net_positive"] = joint["all_filled_eod_net_positive_days"] / len(days)
    joint["blocks"] = {}
    for block, (lo, hi) in BLOCKS.items():
        block_days = [day for day in days if lo <= day[:7] <= hi]
        ge2_mfe = sum(sum(ticket["mfe_raw"] >= .30 for ticket in by_day.get(day, [])) >= 2
                      for day in block_days)
        ge2_positive = sum(sum(ticket["net"] > 0 for ticket in by_day.get(day, [])) >= 2
                           for day in block_days)
        joint["blocks"][block] = {
            "days_n": len(block_days), "days_ge2_mfe30": ge2_mfe,
            "p_days_ge2_mfe30": ge2_mfe / len(block_days),
            "days_ge2_eod_net_positive": ge2_positive,
            "p_days_ge2_eod_net_positive": ge2_positive / len(block_days),
        }
    return {
        "run_id": cell.run_id, "kind": cell.kind, "trigger_id": cell.trigger_id,
        "pattern_id": cell.pattern_id, "bps": cell.bps, "status": "complete",
        "days_n": len(days), "months_n": len({day[:7] for day in days}),
        "mean_basket_day": mean(values), "median_basket_day": float(daily["r_day"].median()),
        "std_basket_day": metrics["std_basket_day"], "bootstrap_ci95": metrics["bootstrap_ci95"],
        "worst_day": metrics["worst_day"], "worst_week": metrics["worst_week"],
        "worst_month": metrics["worst_month"],
        "positive_day_share": sum(value > 0 for value in values) / len(values),
        "compounded_growth": math.prod(1 + value for value in values),
        "compounded_max_dd": metrics["compounded_max_dd"],
        "avg_deployed_capital": metrics["avg_deployed_capital"],
        "turnover_per_day": metrics["turnover_per_day"],
        "turnover_annualized": metrics["turnover_annualized"],
        "n_entries": metrics["n_entries"], "n_reduces": metrics["n_reduces"],
        "n_exits": metrics["n_exits"], "n_blocked_slots": metrics["n_blocked_slots"],
        "n_pending": metrics["n_pending"], "n_carries": metrics["n_carries"],
        "avg_failed_ticket_cost": mean([ticket["net_return"] for ticket in tickets
                                         if ticket["net_return"] < 0]),
        "realized_ticket_net": sum(ticket["net"] for ticket in tickets if not ticket["open_end"]),
        "marked_ticket_net": sum(ticket["net"] for ticket in tickets if ticket["open_end"]),
        "friction_cost": friction_cost,
        "gross_net_before_friction": sum(ticket["net"] for ticket in tickets) + friction_cost,
        "raw_mfe_tail_n": len(touch_by_h["50"]),
        "tail_retained_mean": mean([record["net_return"] / record["mfe_raw"]
                                     for record in raw_records if record["mfe_raw"] >= .5]),
        "tail_contribution_after_each_reduction": tranche_pnl,
        "false_or_early_reduction_by_H": tail_stats,
        "raw_mfe_tail_retention": {h: stats["mean_raw_shares_retained_at_touch"]
                                    for h, stats in tail_stats.items()},
        "per_year": metrics["per_year"], "per_quarter": metrics["per_quarter"],
        "joint_sleeve_impact": joint, "blocks": controls,
    }


def attach_control_deltas(rows: list[dict]) -> list[dict]:
    by_key = {(row["kind"], row["bps"], row["trigger_id"], row["pattern_id"]): row
              for row in rows}
    for row in rows:
        hold = by_key.get(("hold", row["bps"], None, None))
        matched = (by_key.get(("full_exit", row["bps"], row["trigger_id"], None))
                   if row["kind"] == "partial" else row if row["kind"] == "full_exit" else None)
        for suffix, control in (("hold", hold), ("matched_full_exit", matched)):
            row[f"matched_{suffix}_run_id"] = control["run_id"] if control else None
            row[f"delta_net_c0_ev_vs_{suffix}"] = (
                row["mean_basket_day"] - control["mean_basket_day"] if control else None)
            row[f"failed_ticket_loss_delta_vs_{suffix}"] = (
                row["avg_failed_ticket_cost"] - control["avg_failed_ticket_cost"] if control else None)
            row[f"delta_net_c0_ev_by_block_vs_{suffix}"] = {
                block: (row["blocks"][block]["mean_basket_day"] -
                        control["blocks"][block]["mean_basket_day"] if control else None)
                for block in BLOCKS}
    return rows
