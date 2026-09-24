"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""Run one F1 entry family on the C1-corrected engine into a fresh root.

Usage: run_f1_c1.py <entry_id> <out_root>
`basket_f1.run_cells` iterates the module-level `build_cells()`, so the driver
patches that factory to the requested entry family (one process owns one
family's run dirs; nothing else may touch them).
"""
import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_f1 as f1  # noqa: E402
from factory.scripts import basket_sim as sim  # noqa: E402

entry = sys.argv[1]
out_root = Path(sys.argv[2])

_all_cells = f1.build_cells
f1.build_cells = lambda: [c for c in _all_cells() if c.entry_id == entry]


def write_surface(root: Path, rows: list) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"surface_{entry}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"family_id": "F1", "entry": entry, "cells": rows},
                              indent=1, sort_keys=True))
    tmp.replace(path)


f1._write_surface = write_surface
days = sim.dev_days()
cells = f1.build_cells()
assert cells and all(c.entry_id == entry for c in cells), "entry filter failed"
print(json.dumps({"entry": entry, "cells": len(cells), "days": len(days)}), flush=True)
rows = f1.run_cells(out_root, days=days, workers=4)
ran = sum(r["status"] == "ran" for r in rows)
print(json.dumps({"entry": entry, "rows": len(rows), "ran": ran}), flush=True)
