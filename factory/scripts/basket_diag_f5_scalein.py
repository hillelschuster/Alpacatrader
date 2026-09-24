"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Small F5 diagnostic: does later evidence deserve an additional dollar?

Subset: entry in {A_pm, B600}; N in {2,3}; release in {R0, R1m10}; add schedules
in {[], [50], [50,25]}; 100/150 bps = 48 cells instead of the registered 840.
Each add cell keeps its matched no-add control inside the subset, so the paired
basket-day delta and the executed-tranche table are both available.
"""
import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_f5_scalein as f5  # noqa: E402
from factory.scripts import basket_sim as sim  # noqa: E402

ENTRIES = ("A_pm", "B600")
NS = (2, 3)
EXITS = ("R0", "R1m10")
SCHEDULES = ((), (0.50,), (0.50, 0.25))
BPS = (100, 150)

_all_cells = f5.build_cells


def cells():
    return [c for c in _all_cells()
            if c.entry_id in ENTRIES and c.n in NS and c.exit_id in EXITS
            and tuple(c.add_sizes) in SCHEDULES and c.bps in BPS]


f5.build_cells = cells
days = sim.dev_days()
out_root = WT / "factory/artifacts/basket/phase2/F5_DIAG"
print(json.dumps({"cells": len(cells()), "days": len(days)}), flush=True)
rows = f5.run_cells(out_root, days=days, workers=2, cell_workers=3)
print(json.dumps({"rows": len(rows)}), flush=True)
