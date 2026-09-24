#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import median

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
F3 = ROOT / "factory/artifacts/basket/phase2/F3"
F8 = ROOT / "factory/artifacts/basket/phase2/F8"
BLOCKS = {"block1": ("2021-02", "2023-12"), "block2": ("2025-02", "2026-05")}
HORIZONS = (50, 100, 200)


def validate_daily_dates(expected: list[str], actual: list[str]) -> None:
    if len(actual) != len(set(actual)):
        raise ValueError("daily date grid must be unique")
    if expected != actual:
        raise ValueError("daily date grid differs across inputs")


def validate_ticket_dates(daily_dates: list[str], tickets: list[dict]) -> None:
    outside = {row["sleeve_day"] for row in tickets} - set(daily_dates)
    if outside:
        raise ValueError(f"ticket date outside daily date grid: {sorted(outside)}")


def daily_summary(rows: list[dict]) -> dict:
    returns = [float(row["r_day"]) for row in rows]
    return {"days_n": len(returns),
            "mean_c0_return": sum(returns) / len(returns) if returns else None,
            "c0_return_sum": sum(returns)}


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {"n": len(values), "median": median(values), "min": min(values), "max": max(values)}


def ticket_economics(tickets: list[dict]) -> dict:
    realized = [row for row in tickets if not row["open_end"]]
    marked = [row for row in tickets if row["open_end"]]
    failed = [row for row in tickets if float(row["net_return"]) < 0]
    release = [row for row in tickets if row.get("exit_reason") not in (None, "FORCED_FLAT")]
    forced = [row for row in tickets if row.get("exit_reason") == "FORCED_FLAT"]
    result = {
        "tickets_n": len(tickets),
        "realized_ticket_n": len(realized),
        "realized_net": sum(float(row["net"]) for row in realized),
        "marked_ticket_n": len(marked),
        "marked_net": sum(float(row["net"]) for row in marked),
        "failed_n": len(failed),
        "failed_net": sum(float(row["net"]) for row in failed),
        "mean_failed_ticket_net_return": (sum(float(row["net_return"]) for row in failed) / len(failed)
                                           if failed else None),
        "release_exit_count": len(release),
        "forced_flat_exit_count": len(forced),
        "release_execution_et": _distribution([int(row["exit_et"]) for row in release
                                                if row.get("exit_et") is not None]),
        "release_exit_reason_counts": dict(sorted(Counter(str(row["exit_reason"]) for row in release).items())),
        "forced_flat_execution_et": _distribution([int(row["exit_et"]) for row in forced
                                                    if row.get("exit_et") is not None]),
        "raw_mfe_is_executable_fill": False,
        "raw_mfe_contribution": {},
    }
    for horizon in HORIZONS:
        subset = [row for row in tickets if row.get("mfe_raw") is not None
                  and float(row["mfe_raw"]) >= horizon / 100]
        month_net: dict[str, float] = Counter()
        month_n: Counter = Counter()
        for row in subset:
            month = str(row["sleeve_day"])[:7]
            month_net[month] += float(row["net"])
            month_n[month] += 1
        total_tail_net = sum(float(row["net"]) for row in subset)
        result["raw_mfe_contribution"][str(horizon)] = {
            "n": len(subset),
            "ticket_net": sum(float(row["net"]) for row in subset),
            "realized_ticket_net": sum(float(row["net"]) for row in subset if not row["open_end"]),
            "marked_ticket_net": sum(float(row["net"]) for row in subset if row["open_end"]),
            "mean_ticket_net_return": (sum(float(row["net_return"]) for row in subset) / len(subset)
                                        if subset else None),
            "positive_ticket_n": sum(float(row["net"]) > 0 for row in subset),
            "positive_months_n": sum(value > 0 for value in month_net.values()),
            "months_n": len(month_net),
            "largest_month_net_share": (max(month_net.values()) / total_tail_net
                                         if month_net and total_tail_net > 0 else None),
            "monthly_ticket_net": dict(sorted(month_net.items())),
            "monthly_ticket_n": dict(sorted(month_n.items())),
        }
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _blocks(daily_rows: list[dict], tickets: list[dict]) -> dict:
    result = {}
    for name, (lo, hi) in BLOCKS.items():
        days = [row for row in daily_rows if lo <= row["date"][:7] <= hi]
        dates = {row["date"] for row in days}
        block_tickets = [row for row in tickets if row["sleeve_day"] in dates]
        result[name] = {**daily_summary(days), **ticket_economics(block_tickets),
                        "months_n": len({row["date"][:7] for row in days}),
                        "worst_day": min((float(row["r_day"]) for row in days), default=None)}
    return result


def _check_sources() -> tuple[dict, dict, dict]:
    surface = json.loads((F3 / "surface.json").read_text())
    cells = surface.get("cells", [])
    if surface.get("family_id") != "F3" or len(cells) != 20:
        raise ValueError("F3 surface must contain all 20 frozen cells")
    if len({cell["run_id"] for cell in cells}) != 20 or any(
            cell.get("status") != "complete" or cell.get("days_n") != 1066 for cell in cells):
        raise ValueError("F3 surface has duplicate or incomplete cells")
    structural = json.loads((F3 / "structural_map/provenance.json").read_text())
    if structural.get("days_n") != 1066 or structural.get("rows_n") != 673952:
        raise ValueError("F3 structural map coverage does not match validated artifact")
    map_config = json.loads((F3 / "structural_map/run_config.json").read_text())
    if map_config.get("selection_or_profitability") is not False:
        raise ValueError("structural map scope/claim mismatch")
    tables = json.loads((F3 / "structural_map/relationship_tables.json").read_text())
    f8 = json.loads((F8 / "joint_surface.json").read_text())
    f8_cells = f8.get("cells", [])
    if f8.get("family_id") != "F8" or len(f8_cells) != 120:
        raise ValueError("F8 must contain the complete 120-cell joint output")
    by_id = {cell["run_id"]: cell for cell in f8_cells}
    f8_ids = {cell["run_id"] for cell in f8_cells}
    if any(f"A_pm_N2_R0_bps{cell['bps']}" not in f8_ids for cell in cells):
        raise ValueError("F8 does not cover the matching A_pm/N2/R0 friction baselines")
    return surface, {"provenance": structural, "run_config": map_config, "tables": tables}, by_id


def build_synthesis(f3_root: Path = F3, f8_root: Path = F8) -> dict:
    global F3, F8
    original_f3, original_f8 = F3, F8
    F3, F8 = f3_root, f8_root
    try:
        surface, structural, f8_by_id = _check_sources()
        root = F3 / "F3"
        baseline_days: list[str] | None = None
        cells = []
        source_hashes: dict[str, str] = {}
        for source in (F3 / "surface.json", F3 / "structural_map/provenance.json",
                       F3 / "structural_map/run_config.json", F3 / "structural_map/relationship_tables.json",
                       F8 / "joint_surface.json"):
            source_hashes[str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else source.name] = _hash(source)
        for surface_cell in sorted(surface["cells"], key=lambda row: row["run_id"]):
            run_id = surface_cell["run_id"]
            run_dir = root / run_id
            daily_file, tickets_file, metrics_file = (run_dir / "daily.parquet", run_dir / "tickets.parquet",
                                                       run_dir / "metrics.json")
            daily = pl.read_parquet(daily_file).sort("date")
            tickets = pl.read_parquet(tickets_file)
            metrics = json.loads(metrics_file.read_text())
            daily_rows = daily.to_dicts()
            dates = [row["date"] for row in daily_rows]
            if len(dates) != 1066:
                raise ValueError(f"{run_id}: expected 1,066 daily rows")
            if baseline_days is None:
                baseline_days = dates
            else:
                validate_daily_dates(baseline_days, dates)
            if any(not any(lo <= date[:7] <= hi for lo, hi in BLOCKS.values()) for date in dates):
                raise ValueError(f"{run_id}: date outside development blocks")
            rows = tickets.to_dicts()
            try:
                validate_ticket_dates(dates, rows)
            except ValueError as error:
                raise ValueError(f"{run_id}: {error}") from error
            pooled = {**daily_summary(daily_rows), **ticket_economics(rows),
                      "months_n": len({date[:7] for date in dates}),
                      "friction_cost_estimate": float(surface_cell["friction_cost"]),
                      "net_c0_ev": float(surface_cell["mean_basket_day"]),
                      "mean_basket_day_surface": float(surface_cell["mean_basket_day"]),
                      "delta_vs_same_friction_r0": float(surface_cell["delta_vs_hold_mean"]),
                      "compounded_max_dd": float(surface_cell["compounded_max_dd"]),
                      "worst_day": float(surface_cell["worst_day"]),
                      "worst_week": float(surface_cell["worst_week"]),
                      "worst_month": float(surface_cell["worst_month"]),
                      "positive_day_share": float(surface_cell["positive_day_share"]),
                      "avg_deployed_capital": float(surface_cell["avg_deployed_capital"]),
                      "turnover_per_day": float(surface_cell["turnover_per_day"]),
                      "turnover_annualized": float(surface_cell["turnover_annualized"]),
                      "action_counts": {"entries": int(metrics["n_entries"]), "exits": int(metrics["n_exits"]),
                                        "reduces": int(metrics["n_reduces"]), "adds": int(metrics["n_adds"]),
                                        "blocked_slots": int(metrics["n_blocked_slots"]),
                                        "pending_day_events": int(metrics["n_pending"]),
                                        "carry_day_events": int(metrics["n_carries"])},
                      "false_release_rate_by_H": surface_cell["false_release_rate"],
                      "false_release_rate_limit": "Pooled native metric only; first-touch/action chronology not retained for block reconstruction.",
                      "half_release_rate_by_H": surface_cell["half_release_rate"],
                      "tail_retained_net_over_raw_mfe": surface_cell["tail_retained"],
                      "blocks": _blocks(daily_rows, rows),
                      "month_surface": {block: surface_cell["blocks"][block]["monthly"] for block in BLOCKS},
                      "years": metrics["per_year"],
                      "quarters": metrics["per_quarter"],
                      "bootstrap_ci95_month_clustered": metrics["bootstrap_ci95"]}
            f8 = f8_by_id[f"A_pm_N2_R0_bps{surface_cell['bps']}"]
            cells.append({"run_id": run_id, "release_id": surface_cell["release_id"],
                          "bps": surface_cell["bps"], "pooled": pooled,
                          "joint_concurrent_baseline_context": {"source_run_id": f8["run_id"],
                                               "scope": "F8 frozen F1 R0 concurrent baseline; not measured under this F3 rule.",
                                               **{key: f8["pooled"][key] for key in
                                               ("mean_filled", "p_all_profitable", "p_ge2_profitable",
                                                "p_all_reach_5", "p_all_reach_10", "p_ge2_reach_20",
                                                "p_ge2_reach_30", "member_mfe_raw", "member_net_return",
                                                "pairwise_corr", "pair_observations")}},
                          "joint_baseline_blocks": {block: {key: f8[block][key] for key in
                                                   ("p_all_profitable", "p_ge2_profitable", "p_all_reach_5",
                                                    "p_all_reach_10", "p_ge2_reach_20", "p_ge2_reach_30",
                                                    "pairwise_corr", "pair_observations")}
                                           for block in BLOCKS},
                          "source_files_sha256": {name: _hash(run_dir / name) for name in
                                                   ("daily.parquet", "tickets.parquet", "metrics.json")}})
        release_cells = [cell for cell in cells if cell["release_id"] != "R0"]
        for cell in cells:
            base = next(row for row in cells if row["release_id"] == "R0" and row["bps"] == cell["bps"])
            own_months = cell["pooled"]["month_surface"]["block1"] | cell["pooled"]["month_surface"]["block2"]
            base_months = base["pooled"]["month_surface"]["block1"] | base["pooled"]["month_surface"]["block2"]
            month_deltas = {month: own_months[month]["mean"] - base_months[month]["mean"]
                            for month in own_months}
            cell["pooled"]["monthly_delta_vs_same_friction_r0"] = month_deltas
            cell["pooled"]["positive_month_delta_count"] = sum(value > 0 for value in month_deltas.values())
            cell["pooled"]["comparison_to_same_friction_r0"] = {
                "failed_ticket_net_sum_delta": cell["pooled"]["failed_net"] - base["pooled"]["failed_net"],
                "failed_ticket_mean_return_delta": (cell["pooled"]["mean_failed_ticket_net_return"] -
                                                     base["pooled"]["mean_failed_ticket_net_return"]),
                "raw_mfe_ticket_net_delta": {str(h): cell["pooled"]["raw_mfe_contribution"][str(h)]["ticket_net"] -
                                              base["pooled"]["raw_mfe_contribution"][str(h)]["ticket_net"]
                                              for h in HORIZONS},
                "interpretation": "Different rule outcomes on shared development inputs; descriptive contrast, not a paired causal counterfactual.",
            }
        deltas = [cell["pooled"]["delta_vs_same_friction_r0"] for cell in release_cells]
        block_positive = {block: sum(cell["pooled"]["blocks"][block]["mean_c0_return"] >
                                     next(base["pooled"]["blocks"][block]["mean_c0_return"]
                                          for base in cells if base["release_id"] == "R0"
                                          and base["bps"] == cell["bps"])
                                      for cell in release_cells) for block in BLOCKS}
        month_breadth = [cell["pooled"]["positive_month_delta_count"] for cell in release_cells]
        tail_month_shares = [cell["pooled"]["raw_mfe_contribution"]["50"]["largest_month_net_share"]
                             for cell in cells if cell["pooled"]["raw_mfe_contribution"]["50"]["largest_month_net_share"] is not None]
        block_delta_ranges = {}
        for block in BLOCKS:
            block_delta_ranges[block] = {}
            for bps in (100, 150):
                base = next(cell for cell in cells if cell["release_id"] == "R0" and cell["bps"] == bps)
                values = [cell["pooled"]["blocks"][block]["mean_c0_return"] -
                          base["pooled"]["blocks"][block]["mean_c0_return"]
                          for cell in release_cells if cell["bps"] == bps]
                block_delta_ranges[block][str(bps)] = [min(values), max(values)]
        return {
            "family_id": "F3_ECONOMIC_SYNTHESIS", "evidence": "RUN",
            "scope": {"development_days": 1066, "blocks": {"block1": {"days": 734, "months": 35},
                       "block2": {"days": 332, "months": 16}}, "structural_rows": 673952,
                       "cells": len(cells), "frictions_bps_round_trip": [100, 150],
                       "entry": "A_pm/T570 top-2, two equal slots, C0=1 per basket-day"},
            "accounting_contract": {
                "c0_ev": "Mean daily r_day, P&L/C0 across all 1,066 days; secondary compounded drawdown is separately reported.",
                "ticket_net": "Ticket net sums are unit-notional ticket outcomes; closed exits are realized and open_end tickets are marks.",
                "daily_vs_ticket": "Daily C0 mark-to-market returns are not a sum of ticket net values; do not equate their sums.",
                "friction": "Use the tested 100/150bps net runs. friction_cost_estimate sums explicit per-ticket charges and is not a separate gross sim.",
                "mfe": "Raw post-fill path high is an opportunity/touch label, not an executable fill or captured P&L.",
                "concurrent": "F8 joint metrics describe concurrent tickets on shared day grids; cells and members overlap and are not independent.",
                "release_time": "exit_et is next-available-bar execution time, not causal trigger time. Trigger time and first-H chronology are unavailable.",
            },
            "structural_map": {"provenance": structural["provenance"], "run_config": structural["run_config"],
                               "relationship_tables": structural["tables"]},
            "surface_read": {"release_cells_positive_mean_delta_pooled_n": sum(value > 0 for value in deltas),
                             "release_cells_n": len(deltas), "pooled_delta_range": [min(deltas), max(deltas)],
                             "positive_block_delta_cells_by_block": block_positive,
                             "block_delta_ranges_by_block_bps": block_delta_ranges,
                             "positive_month_delta_range_of_51": [min(month_breadth), max(month_breadth)],
                             "mfe50_largest_month_share_range": [min(tail_month_shares), max(tail_month_shares)],
                             "interpretation": "Relative loss improvement is shared across registered cells/blocks, but absolute C0 means remain negative. This does not nominate a setting."},
            "cells": cells,
            "source_hashes_sha256": source_hashes,
            "follow_up_handoff": [
                {"rank": 1, "family": "F4", "mechanism": "R2 entry-relative depth-after-grace exits are the clearest scaleout test: broadest ordinary failed-loss improvement, accompanied by a material raw-MFE tail ticket-net reduction.", "must_prove": "Compare partial retention versus the same frozen R2 state/action and R0; tranche-level net C0 EV, both frictions/blocks, and ordinary failure loss saved must beat the foregone tail contribution. Record trigger, next-open execution, first-touch and tranche cashflows.", "not_claimed": "This ranks an evidence question, not L/w settings; tail cohort difference is not observed early-exit loss."},
                {"rank": 2, "family": "F4", "mechanism": "R3 peak-relative giveback exits are the tail-preservation contrast: weaker failure-loss improvement but more ≥50 ticket-net tail contribution than R0 in these runs.", "must_prove": "Test whether a tranche retains the exceptional survivor without giving back the R3 loss benefit; same friction/block and chronology requirements as R2.", "not_claimed": "This ranks a comparison mechanism only; it does not establish captured executable returns or select g."},
                {"rank": 3, "family": "F6/F7", "mechanism": "Measure released-cash retention/redeployment separately for these already-frozen R2/R3 action families, tracking cash left idle versus allocated to existing concurrent survivors.", "must_prove": "No leverage/C0 breach; incremental redeployed capital earns positive net C0 contribution at both frictions and both blocks without concentrating loss or removing survivors' tail; use joint survivor and overlap-aware accounting.", "not_claimed": "F3/F8 do not show released notional is profitably reusable or that concurrent winners are independent."},
            ],
            "nonclaims": ["No strategy/cell is selected or promoted.", "No live or out-of-sample claim.",
                          "No inferred rule/threshold beyond frozen R0/R2/R3 surface.",
                          "False-release rates are pooled engine metrics; cannot allocate by block from native ticket files.",
                          "Exit execution time is observed; release trigger time and missed post-exit first touch are not."],
        }
    finally:
        F3, F8 = original_f3, original_f8


def render_markdown(report: dict) -> str:
    cells = report["cells"]
    lines = ["# F3 economic mechanism synthesis", "", "**Evidence: [RUN]. Development-only, full frozen surface; no cell selected.**", "",
             "## Direct answer", "",
             "Release rules reduce losses relative to same-friction R0 broadly across the tested surface and both blocks, but they do not make the tested mechanism profitable: all absolute mean daily C0 returns remain negative. The economic trade is real, not settled in favor of release: F3 must be judged by ordinary failed-ticket loss removed versus positive raw-MFE tail ticket contribution foregone, not classifier accuracy. Raw MFE is a path touch, not an executable fill.", "",
             "## Measurement boundaries", "",
             "- One C0 sleeve per basket-day; daily mean is C0 EV. Ticket-net sums are not daily return sums. Closed-ticket P&L is realized; open-end ticket net is marked.",
             "- Friction is tested at 100/150bps round trip. `friction_cost_estimate` is the explicit ticket charge estimate, not a separate gross simulation.",
              "- Exit ET is the next-available-bar execution time. False-release values are pooled engine metrics. Trigger ET and per-block false-release/first-touch chronology are absent; do not reinterpret execution timing as signal timing. Half-release is 0 for every cell (no REDUCE actions).",
             "- F8 concurrent survivor metrics share dates and ticket members across cells; correlation/overlap are descriptive, not independent observations.", "",
             "## Complete 20-cell economic surface", "",
             "All quantities in returns use fractions (e.g., -0.04 = -4%). Tail columns show ticket-net P&L summed for tickets whose raw post-fill MFE touched the stated level; these are not executable proceeds.", "",
              "| Rule | bps | C0 EV | Δ vs R0 | Failed mean / n | Failed net Δ vs R0 | Release / forced-flat exits | Median release exec ET | False release 30/50/100 | Realized ticket net | Marked ticket net | MFE≥50 net (Δ vs R0) | ≥100 net (Δ) | ≥200 net (Δ) | Capture ratio ≥50 | DD | Friction |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for cell in cells:
        p = cell["pooled"]
        tail = p["raw_mfe_contribution"]
        delta = p["comparison_to_same_friction_r0"]["raw_mfe_ticket_net_delta"]
        false = p["false_release_rate_by_H"]
        forced_n = p["action_counts"]["exits"] - p["release_exit_count"]
        lines.append(f"| {cell['release_id']} | {cell['bps']} | {p['net_c0_ev']:.4f} | {p['delta_vs_same_friction_r0']:+.4f} | {p['mean_failed_ticket_net_return']:.4f} / {p['failed_n']} | {p['comparison_to_same_friction_r0']['failed_ticket_net_sum_delta']:+.3f} | {p['release_exit_count']} / {forced_n} | {p['release_execution_et']['median']} | {false['30']:.3f}/{false['50']:.3f}/{false['100']:.3f} | {p['realized_net']:.3f} | {p['marked_net']:.3f} | {tail['50']['ticket_net']:.3f} ({delta['50']:+.3f}) | {tail['100']['ticket_net']:.3f} ({delta['100']:+.3f}) | {tail['200']['ticket_net']:.3f} ({delta['200']:+.3f}) | {p['tail_retained_net_over_raw_mfe']['mean']:.3f} ({p['tail_retained_net_over_raw_mfe']['n']}) | {p['compounded_max_dd']:.3f} | {p['friction_cost_estimate']:.3f} |")
    lines += ["", "## Block, month, year and joint evidence", "",
              f"Tail deltas in the table are ticket-net outcome differences versus same-friction R0 among all tickets that ultimately touched each raw-MFE threshold, not cash captured at release. R2 cells reduce mean failed-ticket return by about 2.0–4.5 percentage points and lower the failed-ticket net-loss sum, while their MFE≥50 ticket-net sum is 4.5–9.2 ticket-net units lower than R0 (all tickets remain in the full-ticket run; these tails are cohort contrasts, not observed premature-exit losses). R3 provides the contrasting state/action shape: ≥50 ticket-net sum is about +0.8 to +2.3 versus R0, but mean failed loss improves by only about 0.1–0.8 points. Both changes persist at 150bps with near-identical rankings/magnitudes. Mean C0 improvement is not confined to one event: positive release-vs-R0 months range {report['surface_read']['positive_month_delta_range_of_51'][0]}–{report['surface_read']['positive_month_delta_range_of_51'][1]} of 51 across tested release cells; pooled MFE≥50 ticket-net's largest single-month share spans {report['surface_read']['mfe50_largest_month_share_range'][0]:.1%}–{report['surface_read']['mfe50_largest_month_share_range'][1]:.1%} across cells. Per-threshold month detail is retained in JSON.",
              f"Mean daily C0 Δ ranges by block (fraction; 18 release cells / friction): block1 100bps {report['surface_read']['block_delta_ranges_by_block_bps']['block1']['100'][0]:+.4f} to {report['surface_read']['block_delta_ranges_by_block_bps']['block1']['100'][1]:+.4f}, 150bps {report['surface_read']['block_delta_ranges_by_block_bps']['block1']['150'][0]:+.4f} to {report['surface_read']['block_delta_ranges_by_block_bps']['block1']['150'][1]:+.4f}; block2 100bps {report['surface_read']['block_delta_ranges_by_block_bps']['block2']['100'][0]:+.4f} to {report['surface_read']['block_delta_ranges_by_block_bps']['block2']['100'][1]:+.4f}, 150bps {report['surface_read']['block_delta_ranges_by_block_bps']['block2']['150'][0]:+.4f} to {report['surface_read']['block_delta_ranges_by_block_bps']['block2']['150'][1]:+.4f}. Absolute annual/quarterly per-cell results and the full 51-month table are in JSON.",
              "Per-cell JSON retains pooled and both block failed-ticket, release, realized/marked, MFE contribution, capture, C0 EV, drawdown, friction, plus month-by-month C0 and ticket/action metrics; annual and quarterly metric tables are copied from each native metrics artifact. Each block is compared with same-friction R0; complete values are in `economic_synthesis.json`.", "",
             "F8 does not contain F3 release-rule runs. For context only, each F3 cell carries the matching A_pm/N=2 R0 F8 concurrent baseline (100/150bps): all/≥2 profitable and raw-MFE threshold frequencies, member net/MFE distributions, pairwise correlation and pair counts, pooled and by block. This does not estimate joint behavior under R2/R3. Same date/member overlap remains, and raw-MFE survivor counts do not establish executable capture.", "",
              "## Mechanism read and bounded handoff", "",
              "**Established:** relative C0 mean improvement is broad over the frozen release surface and appears in both development blocks; the absolute C0 mean is negative, so loss reduction alone is insufficient. Release exit ET/count and survivor tail ticket-net contribution are reported together in every cell.",
              "**Unresolved:** native outputs cannot locate causal trigger ET or determine whether the path reaches H after release before any hypothetical executable reacquisition. Pooled false-release rates exist; block-level rates do not. Therefore early exits cannot be declared avoidable or beneficial from raw-path hindsight.",
              "**Ranked F4 (bounded, not selected):** first test R2 entry-relative depth-after-grace scaleout because it removes the most failed-ticket loss but also has the largest tail cohort cost; contrast with R3 peak-relative giveback scaleout, which preserves tail ticket net better but improves failed losses less. Keep each tested rule/parameter frozen and report all declared tranches. Require positive incremental net C0 EV at 100/150bps and both blocks/months, while separating realized/marked and raw-MFE opportunity from actual tranche executions.",
              "**F6/F7 (bounded next):** compare idle released cash and controlled redeployment for R2 and R3 actions into already-existing concurrent survivors, preserving C0 and no leverage. Require positive incremental redeployment contribution net of friction in both blocks, without tail concentration or overlap accounting errors. F3 supplies no evidence that released capital is profitably reusable.",
              "**Falsified here:** the proposition that tested release makes this A_pm/N2 implementation profitable, and any claim that raw-MFE ≥50/100/200 is captured executable profit. No release threshold is selected or recommended.", "",
              "## Provenance and limits", "",
              "Inputs: F3 `surface.json`, structural map (`provenance.json`, `run_config.json`, `relationship_tables.json`), every cell's `daily.parquet`, `tickets.parquet`, `metrics.json`, and F8 `joint_surface.json`. Source SHA-256 values and the entire map are in the JSON artifact. Sealed and reserved periods remain excluded. No F3 runs/artifacts were changed.", "",
              "The raw structural map records causal completed-bar state and strictly later path outcomes; those conditional future high/low/close paths are descriptive, not fills or a simulated counterfactual release. Cell comparisons share data and are not independent evidence.", ""]
    return "\n".join(lines)


def run() -> dict:
    report = build_synthesis()
    (F3 / "economic_synthesis.json").write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (F3 / "ECONOMIC_SYNTHESIS.md").write_text(render_markdown(report))
    return {"cells": len(report["cells"]), "days": report["scope"]["development_days"],
            "outputs": ["economic_synthesis.json", "ECONOMIC_SYNTHESIS.md"]}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
