"""Diagnostic producer (worktree-local research tool, 2026-09-24 C1 cycle).

Generates the evidence under factory/artifacts/basket/phase2/DIAGNOSTICS_20260924/
and the run directories named in its docstring.  Not part of the frozen Phase-2
family registry: these are bounded diagnostics used to answer one economic
question each; see factory/STATE.md (2026-09-24 entries) for the readings.
"""
"""F6 mechanism decomposition (diagnostic, not the registered surface).

Separates the three economic questions for one entry family and breadth set:
  1. withholding only      : p=0.50 reserve held in CASH   vs p=1.00 full deployment
  2. indiscriminate redeploy: p=0.50, reserve split EQUALLY across eligible originals
  3. informed redeploy      : p=0.50, reserve deployed only to originals whose own
                              causal path at the checkpoint still shows a gain
                              (ret_from_fill > 0) within 5% of their running high
Entry A_pm; N in {2,3}; bps in {100,150}; checkpoint in {585,600}; 24 unique runs.
"""
import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from factory.scripts import basket_f6 as f6  # noqa: E402
from factory.scripts import basket_sim as sim  # noqa: E402

ENTRY_ID, ENTRY_POP, ENTRY_T = "A_pm", "A_pm", 570
NS = (2, 3)
P = 0.5
BPS = (100, 150)
CHECKPOINTS = (585, 600)

_all_cells = f6.build_cells


def cells():
    keep = []
    for c in _all_cells():
        if c.entry_id != ENTRY_ID or c.n not in NS or c.bps not in BPS:
            continue
        if c.p == 1.0:
            keep.append(c)
        elif c.p == P and c.reserve_policy == "cash":
            keep.append(c)
        elif c.p == P and c.reserve_policy == "equal" and c.checkpoint in CHECKPOINTS:
            keep.append(c)
    for variant in ("state67", "state33"):
        for n in NS:
            for ck in CHECKPOINTS:
                for bps in BPS:
                    keep.append(f6.Cell(ENTRY_ID, ENTRY_POP, ENTRY_T, n, P, variant, ck, bps))
    return keep


f6.build_cells = cells
days = sim.dev_days()
out_root = WT / "factory/artifacts/basket/phase2/F6_DIAG2"
runs = f6.unique_runs()
print(json.dumps({"unique_runs": len(runs), "logical": len(cells()), "days": len(days),
                  "runs": sorted(runs)}), flush=True)
rows = f6.run_cells(out_root, days=days, workers=2)
print(json.dumps({"rows": len(rows)}), flush=True)
