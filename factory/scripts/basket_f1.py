#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from factory.scripts import basket_sim as sim
else:
    import basket_sim as sim


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "F1"
ENTRY_SPECS = (
    ("A_pm", "A_pm", 570),
    ("A_pm31", "A_pm31", 570),
    ("A_open", "A_open", 570),
    ("B585", "B", 585),
    ("B600", "B", 600),
)
EXIT_SPECS = (("R0", None), ("R1m8", -8), ("R1m10", -10), ("R1m15", -15))
REQUIRED_OUTPUTS = ("config.json", "daily.parquet", "tickets.parquet", "metrics.json", "run_summary.json")


@dataclass(frozen=True)
class Cell:
    entry_id: str
    entry_pop: str
    entry_T: int
    n: int
    exit_id: str
    loss_pct: int | None
    bps: int
    reserve_frac: float = 1.0
    scale_in: tuple[object, ...] = ()

    @property
    def top_n(self) -> int:
        return self.n

    @property
    def n_slots(self) -> int:
        return self.n

    @property
    def run_id(self) -> str:
        return f"{self.entry_id}_N{self.n}_{self.exit_id}_bps{self.bps}"

    def strategy(self) -> sim.StrategySpec:
        release = [sim.R0()] if self.loss_pct is None else [sim.R1(self.loss_pct)]
        return sim.StrategySpec(
            family_id="F1",
            entry_pop=self.entry_pop,
            entry_T=self.entry_T,
            top_n=self.top_n,
            n_slots=self.n_slots,
            reserve_frac=self.reserve_frac,
            release=release,
            scale_in=list(self.scale_in),
            name=self.run_id,
        )


def build_cells() -> list[Cell]:
    return [
        Cell(entry_id, entry_pop, entry_T, n, exit_id, loss_pct, bps)
        for entry_id, entry_pop, entry_T in ENTRY_SPECS
        for n in (2, 3, 4)
        for exit_id, loss_pct in EXIT_SPECS
        for bps in (100, 150)
    ]


def is_complete(run_dir: Path) -> bool:
    return all((run_dir / name).is_file() for name in REQUIRED_OUTPUTS)


def _write_surface(out_root: Path, rows: list[dict]) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / "surface.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"family_id": "F1", "cells": rows}, indent=1, sort_keys=True))
    tmp.replace(path)


def run_cells(out_root: Path, *, days: list[str], workers: int) -> list[dict]:
    rows: list[dict] = []
    for cell in build_cells():
        run_dir = out_root / "F1" / cell.run_id
        if not is_complete(run_dir) and run_dir.exists():
            shutil.rmtree(run_dir)
        if is_complete(run_dir):
            metrics = json.loads((run_dir / "metrics.json").read_text())
            status = "skipped_complete"
        else:
            summary = sim.run(
                sim.RunConfig(
                    family_id="F1",
                    run_id=cell.run_id,
                    spec=cell.strategy(),
                    bps_total=float(cell.bps),
                    out_root=out_root,
                    days=days,
                    workers=workers,
                )
            )
            metrics = summary["metrics"]
            status = "ran"
        rows.append(
            {
                "run_id": cell.run_id,
                "entry": cell.entry_id,
                "N": cell.n,
                "exit": cell.exit_id,
                "bps": cell.bps,
                "status": status,
                "mean_basket_day": metrics["mean_basket_day"],
                "days_n": metrics["days_n"],
            }
        )
        _write_surface(out_root, rows)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="run all 1,066 development days")
    parser.add_argument("--max-days", type=int, default=None, help="smoke-test prefix only")
    parser.add_argument("--out-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if args.full == (args.max_days is not None):
        parser.error("provide exactly one of --full or --max-days")

    days = sim.dev_days()
    if args.max_days is not None:
        if args.max_days < 1:
            parser.error("--max-days must be positive")
        days = days[: args.max_days]
    out_root = args.out_root or (OUT_ROOT if args.full else OUT_ROOT / "smoke")
    rows = run_cells(out_root, days=days, workers=args.workers)
    print(json.dumps({"cells": len(rows), "ran": sum(r["status"] == "ran" for r in rows), "out_root": str(out_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
