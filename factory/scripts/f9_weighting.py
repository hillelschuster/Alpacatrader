#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
F1_ROOT = ROOT / "factory/artifacts/basket/phase2/F1/F1"
OUT_ROOT = ROOT / "factory/artifacts/basket/phase2/F9"
SCHEMES = ("equal", "rank_linear", "score_gap_mild", "golden_gate_1p5x", "survival_state_1p5x")
ENTRY = {"A_pm": ("A_pm", 570), "A_pm31": ("A_pm31", 570), "A_open": ("A_open", 570), "B585": ("B", 585), "B600": ("B", 600)}


def weights_for(
    scheme: str,
    ranks: list[int],
    scores: list[float],
    causal_flags: list[bool] | None = None,
) -> list[float]:
    if scheme not in SCHEMES or not ranks or sorted(ranks) != list(range(1, len(ranks) + 1)):
        raise ValueError("unknown scheme or ranks must be 1..N")
    if len(scores) != len(ranks):
        raise ValueError("scores and ranks must align")
    if scheme in ("golden_gate_1p5x", "survival_state_1p5x"):
        if causal_flags is None or len(causal_flags) != len(ranks):
            raise ValueError("causal flags are required and must align")
        raw = [1.5 if passed else 1.0 for passed in causal_flags]
    elif scheme == "rank_linear":
        n = len(ranks)
        raw = [n - rank + 1 for rank in ranks]
    elif scheme == "score_gap_mild":
        lo, hi = min(scores), max(scores)
        raw = [1.0 + (0.25 * (score - lo) / (hi - lo) if hi > lo else 0.0) for score in scores]
    else:
        raw = [1.0] * len(ranks)
    total = sum(raw)
    return [value / total for value in raw]


def validate_dev_days(days: list[str], expected_days: list[str] | None = None) -> None:
    if len(days) != 1066 or len(set(days)) != 1066:
        raise ValueError("F1 input must contain exactly 1,066 unique development days")
    if any(not ("2021-02-01" <= d <= "2023-12-29" or "2025-02-03" <= d <= "2026-05-29") for d in days):
        raise ValueError("F1 input contains a non-development day")
    if expected_days is not None and days != expected_days:
        raise ValueError("F1 input does not match the canonical dev calendar")


def _checkpoint_status(scheme: str) -> str:
    if scheme in ("golden_gate_1p5x", "survival_state_1p5x"):
        return "not_computable_preregistered_gate_or_timing_missing"
    return "computed_descriptive_reweighting"


def run(out_root: Path = OUT_ROOT) -> dict:
    surface: list[dict] = []
    cell_dirs = sorted(path for path in F1_ROOT.iterdir() if (path / "config.json").is_file())
    if not cell_dirs:
        raise ValueError(f"no F1 cells under {F1_ROOT}")
    first_daily = pl.read_parquet(cell_dirs[0] / "daily.parquet")
    days = first_daily.get_column("date").cast(pl.String).to_list()
    calendar = json.loads((ROOT / "factory/artifacts/basket/sip/phase2_session_calendar.json").read_text())
    validate_dev_days(days, sorted(calendar["evidence"]))
    anatomy_dir = ROOT / "factory/artifacts/basket/sip/anatomy"
    snapshot_cache: dict[str, dict[tuple[str, int], list[dict]]] = {}
    for day in days:
        record = json.loads((anatomy_dir / f"{day}.jsonl").read_text())
        snapshot_cache[day] = {
            (snapshot["pop"], int(snapshot["T"])): snapshot["names"]
            for snapshot in record["snapshots"]
            if (snapshot["pop"], int(snapshot["T"])) in set(ENTRY.values())
        }
    for cell_dir in cell_dirs:
        config_path = cell_dir / "config.json"
        if not config_path.is_file():
            continue
        config = json.loads(config_path.read_text())
        if config.get("family_id") != "F1" or config.get("n_days") != 1066:
            raise ValueError(f"non-final F1 input: {cell_dir}")
        daily = pl.read_parquet(cell_dir / "daily.parquet")
        tickets = pl.read_parquet(cell_dir / "tickets.parquet")
        if daily.get_column("date").cast(pl.String).to_list() != days:
            raise ValueError(f"F1 date mismatch in {cell_dir}")
        entry_pop, entry_t = ENTRY[config["run_id"].split("_N", 1)[0]]
        daily_returns = daily.select(pl.col("date").cast(pl.String).alias("date"), pl.col("r_day").alias("equal"))
        base_n = int(config["n_slots"])
        day_members: dict[str, list[dict]] = {}
        for day in daily_returns.get_column("date").to_list():
            day_members[day] = snapshot_cache[day][(entry_pop, entry_t)][:base_n]
        tickets_by_day: dict[str, dict[str, dict]] = {}
        for ticket in tickets.iter_rows(named=True):
            tickets_by_day.setdefault(str(ticket["sleeve_day"]), {})[ticket["ticker"]] = ticket
        for scheme in SCHEMES:
            if _checkpoint_status(scheme) != "computed_descriptive_reweighting":
                surface.append({"run_id": config["run_id"], "scheme": scheme, "status": _checkpoint_status(scheme), "days_n": 1066})
                continue
            weighted: dict[str, float] = {}
            for day, members in day_members.items():
                ranks = [int(name["rank"]) for name in members]
                scores = [float(name["sel"]) for name in members]
                allocations = weights_for(scheme, ranks, scores)
                weighted[day] = sum(
                    float(ticket["net_return"]) * weight
                    for name, weight in zip(members, allocations)
                    if (ticket := tickets_by_day.get(day, {}).get(name["ticker"])) is not None
                )
            rows = daily_returns.with_columns(
                pl.Series("weighted", [float(weighted.get(day, 0.0)) for day in daily_returns.get_column("date").to_list()])
            )
            mean = rows.get_column("weighted").mean()
            first = rows.filter(pl.col("date") <= "2023-12-29").get_column("weighted").mean()
            second = rows.filter(pl.col("date") >= "2025-02-03").get_column("weighted").mean()
            surface.append({
                "run_id": config["run_id"], "scheme": scheme, "status": "computed_descriptive_reweighting",
                "entry": config["run_id"].split("_N", 1)[0], "N": base_n,
                "exit": config["release"][0], "bps": config["bps_total"], "days_n": 1066,
                "mean_basket_day": mean, "block_1_mean": first, "block_2_mean": second,
                "block_1_days": rows.filter(pl.col("date") <= "2023-12-29").height,
                "block_2_days": rows.filter(pl.col("date") >= "2025-02-03").height,
                "gross_budget_c0": 1.0, "sizing_method": "initial ticket reweighting; blocked allocations stay cash",
            })
    if len(surface) != 120 * len(SCHEMES):
        raise ValueError(f"expected 600 full-grid rows, got {len(surface)}")
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "surface.json").write_text(json.dumps({"family_id": "F9", "source_family": "F1", "rows": surface}, indent=2, sort_keys=True) + "\n")
    return {"rows": len(surface), "computed": sum(row["status"] == "computed_descriptive_reweighting" for row in surface), "out_root": str(out_root)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", type=Path, default=OUT_ROOT)
    args = parser.parse_args()
    print(json.dumps(run(args.out_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
