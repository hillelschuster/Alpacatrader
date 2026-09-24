#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import polars as pl

if __package__:
    from factory.scripts import basket_f1
    from factory.scripts import basket_sim as sim
else:
    import basket_f1
    import basket_sim as sim


ROOT = Path(__file__).resolve().parents[2]
F1_ROOT = ROOT / "factory/artifacts/basket/phase2/F1/F1"
NET_SIZE = ROOT / "factory/artifacts/basket/sip/net_size.json"
CONTRACT = ROOT / "factory/BASKET-SIM-CONTRACT.md"
FRICTION_BPS = (0, 50, 100, 150, 200)
BLOCKS = {
    "2021-02_2023-12": ("2021-02", "2023-12"),
    "2025-02_2026-05": ("2025-02", "2026-05"),
}
REQUIRED = ("config.json", "daily.parquet", "tickets.parquet", "metrics.json", "run_summary.json")


def strategy_constraints() -> dict:
    return {
        "strategy_semantics_changed": False,
        "account_size_selected": False,
        "deployment_claim": False,
        "profitability_claim": False,
    }


def frozen_cells() -> list:
    return [cell for cell in basket_f1.build_cells() if cell.bps == 100]


def comparison_registry(quote_files: int) -> list[dict]:
    return [
        {
            "comparison": "stored_open_friction_ladder",
            "status": "RUN",
            "friction_bps": list(FRICTION_BPS),
            "scope": "all frozen F1 cells; same stored entries/exits and C0=1",
        },
        {
            "comparison": "conservative_minute_execution",
            "status": "BLOCKED",
            "reason": (
                "PRE-REG-BASKET-02 §3.10 gives no exact minute fill rule; bars exist, "
                "but selecting a price rule would invent execution semantics."
            ),
        },
        {
            "comparison": "quote_aware_execution",
            "status": "BLOCKED" if not quote_files else "UNRUN",
            "reason": (
                "raw SIP quote parquet files are absent from data/sip/quotes; committed "
                "pilot sample manifest is metadata, not quote observations."
                if not quote_files
                else "quote input present but comparison not specified by this producer."
            ),
        },
        {
            "comparison": "capacity_curve",
            "status": "BLOCKED",
            "reason": (
                "canonical net_size.json is an eight-date universe-fetch-size measurement "
                "(eligibility/cutoff counts), not a position-volume participation surface; "
                "account size/order notional is intentionally unset."
            ),
        },
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_f1(run_dir: Path, run_id: str, bps: int, days: list[str]) -> None:
    if not all((run_dir / name).is_file() for name in REQUIRED):
        raise FileNotFoundError(f"incomplete canonical F1 run: {run_dir}")
    cfg = json.loads((run_dir / "config.json").read_text())
    summary = json.loads((run_dir / "run_summary.json").read_text())
    if (
        cfg.get("run_id") != run_id
        or cfg.get("bps_total") != float(bps)
        or cfg.get("n_days") != len(days)
        or summary.get("days_run") != len(days)
    ):
        raise ValueError(f"canonical F1 provenance mismatch: {run_dir}")
    got = pl.read_parquet(run_dir / "daily.parquet", columns=["date"])["date"].to_list()
    if got != days:
        raise ValueError(f"canonical F1 dates differ: {run_dir}")


def _copy_f1(src: Path, dst: Path) -> dict:
    dst.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED:
        if not (dst / name).is_file():
            shutil.copy2(src / name, dst / name)
    return {name: _sha(dst / name) for name in REQUIRED}


def _block_means(daily_path: Path) -> dict:
    df = pl.read_parquet(daily_path, columns=["date", "r_day"])
    out = {}
    for label, (start, end) in BLOCKS.items():
        rows = df.filter(
            (pl.col("date").str.slice(0, 7) >= start) & (pl.col("date").str.slice(0, 7) <= end)
        )
        out[label] = {"days_n": rows.height, "mean_basket_day": rows["r_day"].mean()}
    return out


def _minute_input_facts(cells: list, days: list[str]) -> dict:
    chosen = set()
    counts = {"blocked_slots": 0, "pending": 0, "carries": 0, "entries": 0}
    for cell in cells:
        run_dir = F1_ROOT / cell.run_id
        metrics = json.loads((run_dir / "metrics.json").read_text())
        counts["blocked_slots"] += metrics["n_blocked_slots"]
        counts["pending"] += metrics["n_pending"]
        counts["carries"] += metrics["n_carries"]
        counts["entries"] += metrics["n_entries"]
        tickets = pl.read_parquet(
            run_dir / "tickets.parquet",
            columns=["ticker", "sleeve_day", "entry_et"],
        )
        chosen.update(
            (r["sleeve_day"], r["ticker"], r["entry_et"]) for r in tickets.iter_rows(named=True)
        )
    by_day: dict[str, list[tuple[str, int]]] = {}
    for day, ticker, et in chosen:
        by_day.setdefault(day, []).append((ticker, et))
    dollars = []
    ranges = []
    matched = 0
    for day in days:
        wanted = by_day.get(day, [])
        if not wanted:
            continue
        tickers = {ticker for ticker, _ in wanted}
        ets = {et for _, et in wanted}
        frame = pl.read_parquet(
            sim.BARS_DIR / f"{day}.parquet",
            columns=["ticker", "et", "open", "high", "low", "volume"],
        ).filter(pl.col("ticker").is_in(tickers) & pl.col("et").is_in(ets))
        lookup = {(r["ticker"], r["et"]): r for r in frame.iter_rows(named=True)}
        for ticker, et in wanted:
            bar = lookup.get((ticker, et))
            if not bar or not bar["open"]:
                continue
            matched += 1
            dollars.append(float(bar["open"] * bar["volume"]))
            ranges.append(float((bar["high"] - bar["low"]) / bar["open"] * 10_000))

    def percentiles(values: list[float]) -> dict:
        return (
            {f"p{q}": float(pl.Series(values).quantile(q / 100)) for q in (10, 50, 90)}
            if values
            else {}
        )

    return {
        "scope": (
            "Unique selected entry bars across the 60 F1 strategy configurations; descriptive only"
        ),
        "unique_entry_bars": len(chosen),
        "entry_bars_matched": matched,
        "entry_bar_dollar_volume_usd_percentiles": percentiles(dollars),
        "entry_minute_high_low_range_bps_percentiles_not_slippage": percentiles(ranges),
        "f1_100bps_strategy_cell_counts": counts,
        "interpretation": (
            "Bar volume and high-low range are factual minute aggregates, not fills, "
            "bid-ask spread, market impact, or a capacity threshold. Carry/pending "
            "counts are simulator events, not verified exchange halt flags."
        ),
    }


def run(out_root: Path, workers: int = 4) -> dict:
    out_root = out_root.resolve()
    if out_root.exists() and not (out_root / "f11_manifest.json").is_file():
        raise FileExistsError(f"refusing non-unique F11 artifact root: {out_root}")
    days = sim.dev_days()
    if len(days) != 1066 or any(
        date.fromisoformat(d).year == 2024
        or d.startswith("2025-01")
        or d[:7] in sim.RESERVED_MONTHS
        for d in days
    ):
        raise ValueError("dev-day guard did not yield the frozen 1,066-day set")
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "f11_manifest.json"
    manifest = {
        "family_id": "F11",
        "friction_bps": list(FRICTION_BPS),
        "f1_cells_n": len(basket_f1.build_cells()),
        "days_n": len(days),
        "first_day": days[0],
        "last_day": days[-1],
        "pre_registration_sha256": _sha(ROOT / "researches/PRE-REG-BASKET-02.md"),
        "contract_sha256": _sha(CONTRACT),
    }
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("existing F11 root belongs to a different frozen run")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    cells = frozen_cells()
    checkpoint = out_root / "friction_surface.json"
    surfaces = json.loads(checkpoint.read_text()) if checkpoint.exists() else []
    completed = {row["run_id"] for row in surfaces}
    for cell in cells:
        for bps in FRICTION_BPS:
            run_id = f"{cell.run_id}__f11_bps{bps}"
            if run_id in completed:
                continue
            run_dir = out_root / "runs" / run_id
            if bps in (100, 150):
                src = F1_ROOT / cell.run_id.replace(f"bps{cell.bps}", f"bps{bps}")
                _validate_f1(src, src.name, bps, days)
                hashes = _copy_f1(src, run_dir)
                source = "canonical_F1_reuse"
                summary = json.loads((run_dir / "run_summary.json").read_text())
                metrics = json.loads((run_dir / "metrics.json").read_text())
            else:
                run_dir.parent.mkdir(parents=True, exist_ok=True)
                summary = sim.run(
                    sim.RunConfig(
                        family_id="F11",
                        run_id=run_id,
                        spec=cell.strategy(),
                        bps_total=float(bps),
                        out_root=out_root / "sim_runs",
                        days=days,
                        workers=workers,
                    ),
                    progress=False,
                )
                generated = Path(summary["run_dir"])
                run_dir.mkdir(parents=True, exist_ok=True)
                for name in REQUIRED:
                    shutil.copy2(generated / name, run_dir / name)
                hashes = {name: _sha(run_dir / name) for name in REQUIRED}
                metrics = summary["metrics"]
                source = "F11_full_dev_simulation"
            config = json.loads((run_dir / "config.json").read_text())
            daily = pl.read_parquet(run_dir / "daily.parquet")
            if daily.height != len(days) or daily["date"].to_list() != days:
                raise ValueError(f"F11 output does not cover exact dev dates: {run_id}")
            surfaces.append(
                {
                    "run_id": run_id,
                    "f1_run_id": cell.run_id,
                    "entry": cell.entry_id,
                    "N": cell.n,
                    "exit": cell.exit_id,
                    "friction_bps": bps,
                    "status": "RUN",
                    "source": source,
                    "days_n": len(days),
                    "mean_basket_day": metrics["mean_basket_day"],
                    "net_total_unit": float(daily["r_day"].sum()),
                    "dual_blocks": _block_means(run_dir / "daily.parquet"),
                    "metrics_path": str((run_dir / "metrics.json").relative_to(out_root)),
                    "config_frozen": {
                        k: config.get(k)
                        for k in (
                            "entry_pop",
                            "entry_T",
                            "top_n",
                            "n_slots",
                            "reserve_frac",
                            "release",
                            "scale_in",
                            "bps_total",
                            "n_days",
                            "sim_contract_hash",
                        )
                    },
                    "sha256": hashes,
                }
            )
            tmp = checkpoint.with_suffix(".tmp")
            tmp.write_text(json.dumps(surfaces, indent=2, sort_keys=True) + "\n")
            tmp.replace(checkpoint)
            if len(surfaces) % 50 == 0:
                total = len(cells) * len(FRICTION_BPS)
                print(f"F11 completed {len(surfaces)}/{total} friction cells", flush=True)

    quote_dir = ROOT / "data/sip/quotes"
    quote_files = len(list(quote_dir.glob("*.parquet"))) if quote_dir.exists() else 0
    net = json.loads(NET_SIZE.read_text())
    contract_sha = _sha(CONTRACT)
    prereg_sha = _sha(ROOT / "researches/PRE-REG-BASKET-02.md")
    doc = {
        "family_id": "F11",
        "evidence_label": "RUN",
        "artifact_kind": "complete_execution_realism_evidence",
        "out_root": str(out_root.relative_to(ROOT)),
        "created_utc_date": date.today().isoformat(),
        "frozen_comparisons": comparison_registry(quote_files),
        "friction_surface": surfaces,
        "friction_grid_bps_total_round_trip": list(FRICTION_BPS),
        "f1_grid_rows_n": len(basket_f1.build_cells()),
        "f1_strategy_configs_n": len(cells),
        "runs_n": len(surfaces),
        "days_n": len(days),
        "development_dates": {"first": days[0], "last": days[-1], "n": len(days)},
        "dual_block_months": {k: list(v) for k, v in BLOCKS.items()},
        "capacity_input": {
            "artifact": "factory/artifacts/basket/sip/net_size.json",
            "sha256": _sha(NET_SIZE),
            "sample_days": net["days"],
            "scope": "8 era-spread days; universe fetch sizing only; not position capacity",
        },
        "quote_input": {
            "raw_quote_parquet_files_found": quote_files,
            "pilot_sample_manifest_exists": (
                ROOT / "factory/artifacts/basket/sip" / "MANIFEST_SAMPLE_quotes_2021-02-01.json"
            ).exists(),
        },
        "minute_input_facts": _minute_input_facts(cells, days),
        "strategy_constraints": strategy_constraints(),
        "provenance": {
            "pre_registration": "researches/PRE-REG-BASKET-02.md §3.10",
            "pre_registration_sha256": prereg_sha,
            "simulator_contract": "factory/BASKET-SIM-CONTRACT.md",
            "simulator_contract_sha256": contract_sha,
            "canonical_f1_surface": "factory/artifacts/basket/phase2/F1/surface.json",
            "canonical_f1_readme": "factory/artifacts/basket/phase2/F1/README.md",
            "git_head": __import__("subprocess")
            .check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
            .strip(),
        },
        "interpretation_boundary": (
            "Development execution/friction evidence only. No selected strategy, "
            "profitability, deployment, or account-size conclusion."
        ),
    }
    final_path = out_root / "surface.json"
    tmp = final_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    tmp.replace(final_path)
    _write_readme(out_root, doc)
    return doc


def _write_readme(out_root: Path, doc: dict) -> None:
    rows = doc["friction_surface"]
    means = {
        bps: [r["mean_basket_day"] for r in rows if r["friction_bps"] == bps]
        for bps in FRICTION_BPS
    }
    ranges = {bps: (min(v), max(v)) for bps, v in means.items()}
    block_ranges = {
        (bps, block): (
            min(
                r["dual_blocks"][block]["mean_basket_day"] for r in rows if r["friction_bps"] == bps
            ),
            max(
                r["dual_blocks"][block]["mean_basket_day"] for r in rows if r["friction_bps"] == bps
            ),
        )
        for bps in FRICTION_BPS
        for block in BLOCKS
    }
    bl = doc["frozen_comparisons"]
    text = [
        "# F11 — execution realism and capacity",
        "",
        (
            f"**Evidence:** [RUN] complete stored-open friction ladder over "
            f"{doc['f1_strategy_configs_n']} unique frozen F1 strategy configurations "
            f"(F1 has {doc['f1_grid_rows_n']} rows including 100/150-bps duplicates) "
            f"× {len(FRICTION_BPS)} round-trip friction values = {doc['runs_n']} runs; "
            f"each covers the same {doc['days_n']} permitted development dates. Full "
            "row-level surface and run hashes are in `surface.json`; per-run artifacts "
            "are under `runs/`."
        ),
        "",
        "## Observed friction surface",
        "",
        "For each ladder value, the mean range across F1 cells "
        "(unit-normalized basket-day return):",
        "",
        "| Total round-trip friction | Mean range across all cells |",
        "|---:|---:|",
    ]
    text.extend(f"| {bps} bps | {lo:.6f} to {hi:.6f} |" for bps, (lo, hi) in ranges.items())
    all_negative = all(max(values) < 0 for values in means.values())
    text += [
        "",
        (
            "Every cell's full-development mean remains below zero at all five registered "
            "friction points; the stored-open baseline does not survive this descriptive "
            "unit-capital net-return screen. No single cell is selected."
            if all_negative
            else "At least one cell mean is non-negative at a registered friction point; see the "
            "complete surface. No cell is selected or promoted."
        ),
        "",
        "### Dual development blocks — mean ranges across the whole surface",
        "",
        "| Friction | 2021-02–2023-12 | 2025-02–2026-05 |",
        "|---:|---:|---:|",
    ]
    for bps in FRICTION_BPS:
        left = block_ranges[(bps, "2021-02_2023-12")]
        right = block_ranges[(bps, "2025-02_2026-05")]
        text.append(
            f"| {bps} bps | {left[0]:.6f} to {left[1]:.6f} | {right[0]:.6f} to {right[1]:.6f} |"
        )
    text += [
        "",
        (
            "This is a whole-surface descriptive range, not a selection among cells. "
            "Existing 100/150-bps F1 runs are reused after config/date validation; "
            "0/50/200 bps are rerun through the unchanged contract simulator. Dual "
            "development blocks are recorded per cell in `surface.json`."
        ),
        "",
        "## Execution/capacity scope and limitations",
        "",
    ]
    for row in bl:
        text.append(
            f"- **{row['comparison']} — {row['status']}:** "
            f"{row.get('reason', row.get('scope', ''))}"
        )
    minute = doc["minute_input_facts"]
    text += [
        "",
        f"Canonical minute bars matched {minute['entry_bars_matched']:,} of "
        f"{minute['unique_entry_bars']:,} unique selected entry bars. Entry-minute "
        "dollar-volume and high-low-range percentiles, plus frozen simulator blocked, "
        "pending, and carry counts, are in `surface.json` under `minute_input_facts`.",
    ]
    text += [
        "",
        (
            "Canonical `net_size.json` covers eight era-spread days and counts "
            "PIT-eligible universe names by gain floor / legacy top-10 cutoff margin. "
            "It does not contain trade-size participation, order notional, or realized "
            "impact, so no deployable-notional capacity curve is manufactured from it. "
            "Those eight days are input facts only, not full-dev evidence."
        ),
        "",
        "## Evidence boundary",
        "",
        (
            "Execution price rules, strategy semantics, C0 and the F1 unit capital "
            "convention remain unchanged. No account size is chosen. No quote/minute "
            "fill assumptions are imputed where the pre-registration and data do not "
            "support them. This development evidence is not a profitability, "
            "deployment, or live-readiness claim."
        ),
        "",
        "Pre-registration: `researches/PRE-REG-BASKET-02.md` §3.10. ",
        "Mechanics: `factory/BASKET-SIM-CONTRACT.md`. ",
        "Canonical baseline: `factory/artifacts/basket/phase2/F1/README.md`.",
        "",
    ]
    (out_root / "README.md").write_text("\n".join(text))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run frozen F11 execution/friction/capacity evidence."
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=ROOT / "factory/artifacts/basket/phase2/F11/run_20260923_full",
    )
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    doc = run(args.out_root, args.workers)
    print(
        json.dumps(
            {
                "out_root": doc["out_root"],
                "f1_cells": doc["f1_strategy_configs_n"],
                "runs": doc["runs_n"],
                "days": doc["days_n"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
