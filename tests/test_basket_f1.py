from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_frozen_grid_has_exactly_120_cells() -> None:
    from factory.scripts import basket_f1

    cells = basket_f1.build_cells()

    assert len(cells) == 120
    assert {(cell.entry_id, cell.entry_pop, cell.entry_T) for cell in cells} == {
        ("A_pm", "A_pm", 570),
        ("A_pm31", "A_pm31", 570),
        ("A_open", "A_open", 570),
        ("B585", "B", 585),
        ("B600", "B", 600),
    }
    assert {cell.n for cell in cells} == {2, 3, 4}
    assert {cell.exit_id for cell in cells} == {"R0", "R1m8", "R1m10", "R1m15"}
    assert {cell.bps for cell in cells} == {100, 150}
    assert all(cell.top_n == cell.n_slots == cell.n for cell in cells)
    assert all(cell.reserve_frac == 1.0 and not cell.scale_in for cell in cells)


def test_complete_cell_requires_all_engine_outputs(tmp_path: Path) -> None:
    from factory.scripts import basket_f1

    run_dir = tmp_path / "F1" / "A_pm_N2_R0_bps100"
    run_dir.mkdir(parents=True)
    for name in ("config.json", "daily.parquet", "tickets.parquet", "metrics.json"):
        (run_dir / name).write_text("x")

    assert not basket_f1.is_complete(run_dir)

    (run_dir / "run_summary.json").write_text("x")
    assert basket_f1.is_complete(run_dir)


def test_runner_script_exposes_cli() -> None:
    result = subprocess.run(
        [sys.executable, "factory/scripts/basket_f1.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--full" in result.stdout
