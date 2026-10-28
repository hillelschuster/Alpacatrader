#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from pathlib import Path


def fmt(value: float | None, digits: int = 5) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


def full_surface(lines: list[str], matrix: list[dict]) -> None:
    lines.extend([
        "## 1. Full 58-cell surface (net at 100 bps)",
        "",
        "| run | status | entry | base | pattern | N | K/x | net@100 | delta vs binary | block 1 | block 2 | failed ticket | raw-tail retained | worst day | DD | turn/day |",
        "|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in matrix:
        kx = "" if row["K"] is None else f"{row['K']}/{row['x']}"
        lines.append(
            f"| {row['run_id']} | {row['status']} | {row['entry']} | {row['base']} | {row['pattern']} | {row['N']} | {kx} | "
            f"{fmt(row['net100_mean'])} | {fmt(row['delta_net100_mean'])} | {fmt(row['block1_mean'])} | {fmt(row['block2_mean'])} | "
            f"{fmt(row['failed_ticket_cost'], 4)} | {fmt(row['tail_retained_mean_raw'], 3)} | {fmt(row['worst_day'], 4)} | "
            f"{fmt(row['compounded_max_dd'], 3)} | {fmt(row['turnover_per_day'], 3)} |"
        )
    lines.append("")


def tranche_surface(lines: list[str], tranches: list[dict]) -> None:
    lines.extend([
        "## 2. Tranche contribution from the action ledger",
        "",
        "`mean/entry` is each tranche's contribution spread across every entered ticket; `mean/triggered` conditions on tickets that executed the tranche. Exact 1/3 is restored from the frozen schedule, and terminal marks reconcile the still-open terminal rows.",
        "",
        "| run | tranche | P&L sum | triggered tickets | executions | mean/triggered | mean/entry | mean/day | removed notional |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in tranches:
        if row["scope"] != "all":
            continue
        lines.append(
            f"| {row['run_id']} | {row['label']} | {fmt(row['pnl_sum'])} | {row['triggered_tickets']} | {row['executions']} | "
            f"{fmt(row['mean_per_triggered_ticket'])} | {fmt(row['mean_per_entry'])} | {fmt(row['mean_per_day'])} | {fmt(row['notional_removed'])} |"
        )
    lines.append("")


def false_release_surface(lines: list[str], rows: list[dict]) -> None:
    lines.extend([
        "## 3. False release before raw +30/+50/+100 touch",
        "",
        "Denominators use the canonical anatomy's full post-fill path, including touches after a ticket was released. Deltas are versus the same entry/base binary EXIT cell.",
        "",
        "| run | H | touches | flat before | delta flat | >=50% sold before | delta half-sold | mean sold |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in rows:
        lines.append(
            f"| {row['run_id']} | +{row['H']} | {row['n_touch']} | {fmt(row['flat_before_rate'], 3)} | "
            f"{fmt(row['delta_flat_before'], 3)} | {fmt(row['half_sold_before_rate'], 3)} | "
            f"{fmt(row['delta_half_sold_before'], 3)} | {fmt(row['mean_sold_fraction'], 3)} |"
        )
    lines.append("")


def stability_surface(lines: list[str], matrix: list[dict], months: list[dict]) -> None:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in months:
        grouped[row["run_id"]].append(row)
    lines.extend([
        "## 4. Block and monthly stability",
        "",
        "Block 1 is 2021-02 through 2023-12; block 2 is 2025-02 through 2026-05. `monthly.csv` contains every cell-month. The table below covers all cells.",
        "",
        "| run | block 1 delta | block 2 delta | effect read | positive months | months beating binary | worst monthly mean | best monthly mean |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ])
    for row in matrix:
        cell_months = grouped[row["run_id"]]
        d1 = row["delta_block1_mean"]
        d2 = row["delta_block2_mean"]
        if row["pattern"] == "EXIT" and row["kind"] == "base" and row["N"] == 3:
            effect = "binary base"
        elif d1 > 0 and d2 > 0:
            effect = "improves both"
        elif d1 < 0 and d2 < 0:
            effect = "harms both"
        else:
            effect = "mixed/not stable"
        lines.append(
            f"| {row['run_id']} | {fmt(d1)} | {fmt(d2)} | {effect} | "
            f"{sum(month['mean'] > 0 for month in cell_months)}/{len(cell_months)} | "
            f"{sum(month['delta_mean'] > 0 for month in cell_months)}/{len(cell_months)} | "
            f"{fmt(min(month['mean'] for month in cell_months))} | {fmt(max(month['mean'] for month in cell_months))} |"
        )
    lines.append("")


def sensitivity_surfaces(lines: list[str], matrix: list[dict]) -> None:
    lines.extend([
        "## 5. N sensitivity: A_pm / R2_10_5",
        "",
        "| N | pattern | net@100 | block 1 | block 2 | failed ticket | raw-tail retained | turnover |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in sorted(matrix, key=lambda item: (item["pattern"], item["N"])):
        if row["entry"] == "A_pm" and row["base"] == "R2_10_5" and row["pattern"] in {"REDUCE_25", "25_25_rem"}:
            lines.append(
                f"| {row['N']} | {row['pattern']} | {fmt(row['net100_mean'])} | {fmt(row['block1_mean'])} | "
                f"{fmt(row['block2_mean'])} | {fmt(row['failed_ticket_cost'], 4)} | {fmt(row['tail_retained_mean_raw'], 3)} | {fmt(row['turnover_per_day'], 3)} |"
            )
    lines.extend([
        "",
        "## 6. De-risk overlays versus binary bases",
        "",
        "These rows are descriptive artifacts only: all 12 are policy-invalid because controller history leaks across sessions and sequential evaluation does not trim every still-open member.",
        "",
        "| run | net@100 | delta | block 1 delta | block 2 delta | worst-day delta | DD delta | turnover delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in matrix:
        if row["kind"] == "derisk":
            lines.append(
                f"| {row['run_id']} | {fmt(row['net100_mean'])} | {fmt(row['delta_net100_mean'])} | "
                f"{fmt(row['delta_block1_mean'])} | {fmt(row['delta_block2_mean'])} | {fmt(row['delta_worst_day'])} | "
                f"{fmt(row['delta_compounded_max_dd'])} | {fmt(row['delta_turnover_per_day'])} |"
            )
    lines.append("")


def mechanism_text(matrix: list[dict], tranches: list[dict], false_rows: list[dict]) -> list[str]:
    partial = [row for row in matrix if row["kind"] == "base" and row["N"] == 3 and row["pattern"] != "EXIT"]
    best = max(partial, key=lambda row: row["delta_net100_mean"])
    both = [row for row in partial if row["delta_block1_mean"] > 0 and row["delta_block2_mean"] > 0]
    positive = [row for row in matrix if row["net100_mean"] > 0 and row["status"] == "valid_as_run"]
    entry_best = {entry: max((row for row in partial if row["entry"] == entry), key=lambda row: row["delta_net100_mean"]) for entry in ("A_pm", "A_pm31", "B600")}
    best_tranches = {row["label"]: row for row in tranches if row["run_id"] == best["run_id"] and row["scope"] == "all"}
    best_false = {row["H"]: row for row in false_rows if row["run_id"] == best["run_id"]}
    lines = [
        "## 7. Mechanism read",
        "",
        f"This family answers an extraction question, not a generic thesis-falsification question. As run, {len(partial)} N=3 partial-release cells are compared with their primitive binary releases; {len(both)} improve net@100 in both blocks, while {len(positive)} mechanically valid cells have positive absolute net@100.",
        "",
        f"The largest incremental N=3 partial-release result is `{best['run_id']}`: delta net@100 {fmt(best['delta_net100_mean'])}, failed-ticket-cost delta {fmt(best['delta_failed_ticket_cost'], 4)}, raw-tail-retention delta {fmt(best['delta_tail_retained_mean_raw'], 3)}. This is the measured failed-ticket-cost versus survivor-tail trade, not evidence that the underlying top-gainer tail is absent.",
        "",
        f"Its tranche contribution is {fmt(best_tranches['t1_reduce']['mean_per_day'])}/day from the first 50% reduction, {fmt(best_tranches['t2_exit']['mean_per_day'])}/day from the remaining-share exit on the next distinct breach, and {fmt(best_tranches['hold_to_close']['mean_per_day'])}/day from tickets whose remainder survives to close. Full-flat-before-touch falls from binary by {fmt(best_false[30]['delta_flat_before'], 3)} at +30, {fmt(best_false[50]['delta_flat_before'], 3)} at +50, and {fmt(best_false[100]['delta_flat_before'], 3)} at +100.",
        "",
        f"Risk does not improve: worst day changes by {fmt(best['delta_worst_day'], 4)} to {fmt(best['worst_day'], 4)}, compounded DD remains {fmt(best['compounded_max_dd'], 3)}, and turnover changes by {fmt(best['delta_turnover_per_day'], 3)} per day.",
        "",
    ]
    for entry, row in entry_best.items():
        lines.append(
            f"- {entry}: best partial schedule `{row['base']}/{row['pattern']}` changes net@100 by {fmt(row['delta_net100_mean'])}, failed-ticket cost by {fmt(row['delta_failed_ticket_cost'], 4)}, and raw-tail retention by {fmt(row['delta_tail_retained_mean_raw'], 3)} versus binary."
        )
    lines.extend([
        "",
        "Adoption requires measurable incremental value over primitive EXIT after resolving the staged level-price ambiguity. The invalid de-risk overlays cannot be used for central integration.",
        "",
    ])
    return lines


def write_readme(path: Path, matrix: list[dict], tranches: list[dict], false_rows: list[dict], manifest: dict) -> None:
    valid = manifest["mechanically_valid_cells"]
    lines = [
        "# F4 scale-out mechanism read",
        "",
        f"Artifact audit: {valid}/58 mechanically valid; 58/58 planned run directories present; 12 de-risk cells policy-invalid and retained only as labeled evidence.",
        "",
        "Partial release is evaluated as an extraction mechanism: does reducing a deteriorating ticket lower failure cost without surrendering too much of the rare survivor tail? The decisive comparison is incremental net@100 versus the same entry/base binary EXIT, with both development blocks, raw post-fill touches, and action-ledger tranche P&L.",
        "",
        *mechanism_text(matrix, tranches, false_rows)[2:],
        "## Semantic findings",
        "",
        "- Stage semantics match the plan: one firing disarms the pattern; a strict completed-close reclaim re-arms it; the reclaim bar cannot fire; no repeated cascade occurs while the level remains breached.",
        "- Binary EXIT directly uses canonical R1/R2.",
        "- Staged PatternRule exits carry `level=None` while binary level exits use adverse `min(open, level)`. Results remain as-run and this comparability ambiguity requires an owner decision plus rerun before adoption.",
        "- De-risk cells are invalid for policy inference: state lacks a day key, earlier tickets miss a threshold reached by a later ticket, and the rolling-window definition differs from PRE-REG simultaneous satisfaction.",
        "",
        "Exact tables: `results.md`, `matrix.csv`, `tranches.csv`, `false_release.csv`, `monthly.csv`, and `validation_manifest.json`.",
        "",
    ]
    (path / "README.md").write_text("\n".join(lines))


def write_reports(path: Path, matrix: list[dict], tranches: list[dict], false_rows: list[dict], months: list[dict], manifest: dict) -> None:
    lines = [
        "# F4 dynamic scale-out results",
        "",
        "Evidence: ART/RUN from the 58 frozen F4 cells, 1,066 development days, 100 bps round trip, C0=1 per sleeve-day. No holdout or reserved month was read.",
        "",
        "## 0. Validation and implementation read",
        "",
        f"- 58/58 planned run directories and progress records are present; {manifest['mechanically_valid_cells']}/58 pass config/date/hash/action-ledger reconciliation.",
        f"- {len(manifest['policy_invalid_cells'])} de-risk cells are explicitly policy-invalid, not missing.",
        f"- Stage semantics: {manifest['stage_semantics']}",
        f"- Pricing ambiguity: {manifest['pricing_ambiguity']}",
        "- Corrected analysis uses canonical full-day anatomy MFE/touch paths; persisted engine ticket MFE stops when a ticket exits and is not used for false-release or raw-tail-retention conclusions.",
        "",
    ]
    full_surface(lines, matrix)
    tranche_surface(lines, tranches)
    false_release_surface(lines, false_rows)
    stability_surface(lines, matrix, months)
    sensitivity_surfaces(lines, matrix)
    lines.extend(mechanism_text(matrix, tranches, false_rows))
    (path / "results.md").write_text("\n".join(lines))
    write_readme(path, matrix, tranches, false_rows, manifest)
