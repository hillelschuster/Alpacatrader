#!/usr/bin/env python3
"""HARVEST01 — assemble the morning report from the generated readouts.

Concatenates the generated markdown readouts (unmanaged map, containment, management,
policy grid, basket capital, golden-window anatomy, execution probe) with an intro
header, artifact counts, and the ledger, into one REPORT.md. No numbers are retyped.

Usage: .venv/bin/python factory/scripts/basket_harvest_assemble.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_pm_snapshots as bps  # noqa: E402

SECTIONS = [
    ("Unmanaged basket money map (all clocks x N x exits)", "readout.md"),
    ("Containment / capture funnel / joint tail", "containment.md"),
    ("Winner/failure personality anatomy", "anatomy.md"),
    ("Member management rules (12)", "mgmt.md"),
    ("Policy grid (36+ handling policies)", "policies.md"),
    ("Basket capital: release -> cash / survivors / leader / reserve / dip", "basket.md"),
    ("Birds-eye golden window (marks): peak time and capture", "window.md"),
    ("Sub-minute execution probe (sampled, local print tape)", "subminute_probe.md"),
    ("Trades coverage of selected names (local archive)", "trades_coverage.md"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args()
    data_root = bps.resolve_data_root(args.data_root)
    rep = data_root / "harvest01" / "report"
    counts = {}
    for lane in ("base/selected", "base/champs", "base/bars", "base/leaders",
                 "sim/cells", "sim/fills", "mgmt", "policies", "basket", "window"):
        counts[lane] = len(list((data_root / "harvest01" / lane).glob("*.parquet")))

    out = ["# HARVEST01 — top-gainer basket harvest: night report\n",
           "Dev-only discovery run. Conventions and authorization: "
           "`factory/artifacts/basket/phase2/HARVEST01/contract.json`; validations in the "
           "ledger. Reserved months (2026-06..08) untouched; 2024/2025-01 sealed.\n",
           "## Artifact counts (days with parquet)\n",
           "| lane | days |", "|---|---|"]
    for k, v in counts.items():
        out.append(f"| {k} | {v} |")
    out.append("")
    for title, fname in SECTIONS:
        p = rep / fname
        if not p.exists():
            out.append(f"\n## {title}\n\n_missing: {fname} not generated_\n")
            continue
        out.append(f"\n---\n\n# {title}\n")
        out.append(p.read_text())
    ledger = ROOT / "factory" / "artifacts" / "basket" / "phase2" / "HARVEST01" / "LEDGER.md"
    if ledger.exists():
        out.append("\n---\n\n# Running ledger (may predate the sections above)\n")
        out.append(ledger.read_text())
    (rep / "REPORT.md").write_text("\n".join(out))
    print(f"REPORT.md written ({len(out)} blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
