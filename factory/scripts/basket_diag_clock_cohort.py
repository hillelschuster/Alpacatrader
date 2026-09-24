"""Diagnostic producer (2026-09-25) — the coarse window prior.

For every A_pm top-3 fill: the mean/median forward return from each clock close to the session
close, split by the member's eventual session peak (an OUTCOME cohort — hindsight, used only to see
the shape the ATLAS minute panel must explain causally).  Also reports tail concentration, because
these means are dominated by a handful of giants.

    python factory/scripts/basket_diag_clock_cohort.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

WT = Path(__file__).resolve().parents[2]
if str(WT) not in sys.path:
    sys.path.insert(0, str(WT))

from factory.scripts import basket_sim as sim  # noqa: E402

OUT = WT / "factory/artifacts/basket/phase2/DIAGNOSTICS_20260925"
CLOCKS = (600, 660, 720, 780, 840, 900, 960)
COHORTS = (("A_peak_ge_100", 1.00), ("B_peak_30_100", 0.30), ("C_peak_0_30", 0.0))


def cohort_of(peak: float) -> str:
    if peak >= 1.0:
        return "A_peak_ge_100"
    if peak >= 0.30:
        return "B_peak_30_100"
    if peak >= 0.0:
        return "C_peak_0_30"
    return "D_never_above_entry"


def build() -> list[dict]:
    days = sim.dev_days()
    sem = sim.session_end_map()
    rows = []
    for day in days:
        bars = sim.load_bars(day, sem[day])
        snap = sim.snapshot_of(sim.load_anatomy(day), "A_pm", 570)
        if not snap:
            continue
        for nm in sorted(snap["names"], key=lambda x: (int(x["rank"]), x["ticker"]))[:3]:
            fl = nm.get("fill") or {}
            if fl.get("blocked"):
                continue
            tkb = bars.ticker(nm["ticker"])
            if tkb is None:
                continue
            ets, hi, cl = tkb["et"], tkb["high"], tkb["close"]
            i0 = int(np.searchsorted(ets, int(fl["et"])))
            if i0 >= len(ets) or int(ets[i0]) != int(fl["et"]):
                continue
            iend = max(i for i in range(len(ets)) if int(ets[i]) <= sem[day])
            if iend <= i0 + 5:
                continue
            entry = float(fl["px"])
            row = {"day": day, "ticker": nm["ticker"],
                   "peak": float(hi[i0:iend + 1].max()) / entry - 1.0,
                   "eod": float(cl[iend]) / entry - 1.0}
            for c in CLOCKS:
                j = next((i for i in range(i0, iend + 1) if int(ets[i]) >= c), None)
                row[c] = (float(cl[iend]) / float(cl[j]) - 1.0) if j is not None else None
            rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    out = {"n": len(rows), "by_cohort": {}}
    for name in ("A_peak_ge_100", "B_peak_30_100", "C_peak_0_30", "D_never_above_entry"):
        sub = [r for r in rows if cohort_of(r["peak"]) == name]
        if not sub:
            continue
        entry = {"n": len(sub), "share": len(sub) / len(rows),
                 "peak_mean": float(np.mean([r["peak"] for r in sub])),
                 "peak_median": float(np.median([r["peak"] for r in sub])),
                 "clocks": {}}
        for c in CLOCKS:
            vals = np.array([r[c] for r in sub if r[c] is not None])
            if not len(vals):
                continue
            order = np.sort(vals)[::-1]
            entry["clocks"][str(c)] = {
                "n": int(len(vals)),
                "mean": float(vals.mean()),
                "median": float(np.median(vals)),
                "positive_share": float((vals > 0).mean()),
                "top5_share_of_sum": (float(order[:5].sum() / vals.sum())
                                      if vals.sum() else None),
            }
        out["by_cohort"][name] = entry
    return out


def main() -> int:
    rows = build()
    result = summarize(rows)
    result["producer"] = "factory/scripts/basket_diag_clock_cohort.py"
    result["note"] = ("cohorts are OUTCOME cohorts (hindsight); forward return is measured from the "
                      "clock bar's close to the session close, no friction; this is a coarse prior "
                      "for the minute-grain ATLAS panel, not a policy")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "clock_cohort.json"
    path.write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({"out": str(path), "n": result["n"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
