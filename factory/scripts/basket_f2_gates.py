#!/usr/bin/env python
"""BASKET-01 Phase-2 F2/F12 — deliberately simple deterministic golden-window gates.

Each gate has <= 3 conjunctive conditions drawn from the §3.2 feature vocabulary,
with **round-number** thresholds (10%, 50%, -15%, 2 members, ...) — never a
quantile edge — and is checked on both frozen time blocks.  All gates built are
reported, no cherry-picking.

Outputs ``gates.json`` / ``gates.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_f2_common as C

OUT = C.OUT

# op is a python operator on the column array
GATES = [
    {
        "name": "G1_deep_run_liquid",
        "text": "mfe_so_far >= 0.10 AND ret_from_prevclose >= 0.50 AND cum_volume >= 1e7",
        "conds": [("mfe_so_far", "ge", 0.10),
                  ("ret_from_prevclose", "ge", 0.50),
                  ("cum_volume", "ge", 1e7)],
    },
    {
        "name": "G2_basket_breadth",
        "text": "basket_breadth_10 >= 2 AND mfe_so_far >= 0.10 AND dd_from_running_high >= -0.10",
        "conds": [("basket_breadth_10", "ge", 2),
                  ("mfe_so_far", "ge", 0.10),
                  ("dd_from_running_high", "ge", -0.10)],
    },
    {
        "name": "G3_strong_not_extended",
        "text": "ret_from_open0930 >= 0.10 AND dd_from_running_high >= -0.10 AND rel_strength >= 0.05",
        "conds": [("ret_from_open0930", "ge", 0.10),
                  ("dd_from_running_high", "ge", -0.10),
                  ("rel_strength", "ge", 0.05)],
    },
    {
        "name": "G4_deep_pullback_turning",
        "text": "dd_from_running_high <= -0.10 AND mfe_so_far >= 0.10 AND velocity_3 > 0",
        "conds": [("dd_from_running_high", "le", -0.10),
                  ("mfe_so_far", "ge", 0.10),
                  ("velocity_3", "gt", 0.0)],
    },
]


def _mask(df: pl.DataFrame, conds) -> np.ndarray:
    m = np.ones(df.height, dtype=bool)
    for feat, op, thr in conds:
        x = df[feat].to_numpy().astype(np.float64)
        if op == "ge":
            m &= (x >= thr)
        elif op == "gt":
            m &= (x > thr)
        elif op == "le":
            m &= (x <= thr)
        elif op == "lt":
            m &= (x < thr)
        else:
            raise ValueError(op)
    return m


def _eval(sub: pl.DataFrame, m: np.ndarray) -> dict:
    n = sub.height
    if n == 0:
        return {"n": 0}
    y50 = sub["touch50"].to_numpy().astype(np.float64)
    y100 = sub["touch100"].to_numpy().astype(np.float64)
    rem = sub["rem_mfe"].to_numpy().astype(np.float64)
    nhba = sub["nhba"].to_numpy().astype(np.float64)
    base50 = float(np.nanmean(y50))
    base100 = float(np.nanmean(y100))
    ng = int(m.sum())
    if ng == 0:
        return {"n": n, "n_gate": 0, "coverage": 0.0, "base_p_touch50": base50,
                "base_p_touch100": base100}
    p50 = float(np.nanmean(y50[m])); p100 = float(np.nanmean(y100[m]))
    return {
        "n": n, "n_gate": ng, "coverage": ng / n,
        "base_p_touch50": base50, "p_touch50": p50, "lift50": p50 - base50,
        "base_p_touch100": base100, "p_touch100": p100, "lift100": p100 - base100,
        "e_rem_mfe": float(np.nanmean(rem[m])),
        "base_e_rem_mfe": float(np.nanmean(rem)),
        "p_nhba": float(np.nanmean(nhba[m])),
        "base_p_nhba": float(np.nanmean(nhba)),
    }


def main() -> None:
    df = C.load_panel()
    df = df.with_columns(pl.col("date").str.slice(0, 7).alias("_m"))
    df = df.with_columns(
        pl.when((pl.col("_m") >= C.BLOCK1[0]) & (pl.col("_m") <= C.BLOCK1[1])).then(1)
        .when((pl.col("_m") >= C.BLOCK2[0]) & (pl.col("_m") <= C.BLOCK2[1])).then(2)
        .otherwise(0).alias("_block"))

    out = {"gates": [], "blocks": {"block1": list(C.BLOCK1), "block2": list(C.BLOCK2)},
           "checkpoints": list(C.CHECKPOINTS),
           "notes": ("<=3 conjunctive conditions, round thresholds only; every gate built "
                     "is reported; stability requires both blocks above base.")}
    for g in GATES:
        rec = {"name": g["name"], "equation": g["text"], "by_checkpoint": {}, "by_block": {}}
        for cp in C.CHECKPOINTS:
            sub = df.filter(pl.col("et") == cp)
            m = _mask(sub, g["conds"])
            rec["by_checkpoint"][str(cp)] = _eval(sub, m)
        for b, tag in [(1, "block1"), (2, "block2")]:
            sub = df.filter(pl.col("_block") == b)
            # pooled over all checkpoints for a ticket-level block view, plus 585/600
            rec["by_block"][tag] = {"pooled_all_cp": _eval(sub, _mask(sub, g["conds"]))}
            for cp in (585, 600):
                s2 = sub.filter(pl.col("et") == cp)
                rec["by_block"][tag][f"cp{cp}"] = _eval(s2, _mask(s2, g["conds"]))
        # stability verdict at cp 585 / 600: both blocks above base
        for cp in (585, 600):
            b1 = rec["by_block"]["block1"][f"cp{cp}"]
            b2 = rec["by_block"]["block2"][f"cp{cp}"]
            ok = (b1.get("n_gate", 0) > 0 and b2.get("n_gate", 0) > 0
                  and b1.get("p_touch50", 0) > b1.get("base_p_touch50", 1)
                  and b2.get("p_touch50", 0) > b2.get("base_p_touch50", 1))
            rec.setdefault("stable", {})[f"cp{cp}"] = bool(ok)
        out["gates"].append(rec)

    C.write_json(OUT / "gates.json", out)
    _write_md(out, OUT / "gates.md")
    for g in out["gates"]:
        r = g["by_checkpoint"]["585"]
        print(f"{g['name']}: cp585 cov={r.get('coverage',0):.3f} "
              f"P50={r.get('p_touch50',float('nan')):.3f} base={r.get('base_p_touch50',float('nan')):.3f} "
              f"lift={r.get('lift50',float('nan')):+.3f} stable585={g['stable'].get('cp585')} "
              f"stable600={g['stable'].get('cp600')}")


def _pct(v):
    if v is None or v != v:
        return "n/a"
    return f"{100*v:.1f}%"


def _write_md(out: dict, path: Path) -> None:
    L = ["# F2/F12 — deterministic golden-window gates\n",
         "**Label: REPORT** (ART = `gates.json`). Each gate is <=3 conjunctive conditions "
         "from the §3.2 vocabulary with round thresholds. Base rate = P(future +50 from the "
         "checkpoint price) over all tickets at that checkpoint. `stable` requires both frozen "
         "time blocks above their own base rate. All gates built are shown.\n"]
    for g in out["gates"]:
        L.append(f"## {g['name']}\n")
        L.append(f"`{g['equation']}`\n")
        L.append("| checkpoint | coverage | n_gate | P(+50) | base P(+50) | lift | P(+100) | base P(+100) | E[remMFE] | P(nhba) |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for cp in out["checkpoints"]:
            r = g["by_checkpoint"][str(cp)]
            if r.get("n_gate", 0) == 0:
                L.append(f"| {cp} | {r.get('coverage',0):.1%} | 0 | n/a | {_pct(r.get('base_p_touch50'))} | n/a | n/a | {_pct(r.get('base_p_touch100'))} | n/a | n/a |")
                continue
            L.append(f"| {cp} | {r['coverage']:.1%} | {r['n_gate']} | {_pct(r['p_touch50'])} | "
                     f"{_pct(r['base_p_touch50'])} | {r['lift50']:+.3f} | {_pct(r['p_touch100'])} | "
                     f"{_pct(r['base_p_touch100'])} | {r['e_rem_mfe']:.3f} | {_pct(r['p_nhba'])} |")
        L.append("")
        L.append("| block | window | coverage | P(+50) | base P(+50) | lift | P(+100) |")
        L.append("|---|---|---|---|---|---|---|")
        for tag, lab in [("block1", "2021-02..2023-12"), ("block2", "2025-02..2026-05")]:
            r = g["by_block"][tag]["pooled_all_cp"]
            L.append(f"| {tag} | {lab} | {r.get('coverage',0):.1%} | {_pct(r.get('p_touch50'))} | "
                     f"{_pct(r.get('base_p_touch50'))} | {r.get('lift50',float('nan')):+.3f} | "
                     f"{_pct(r.get('p_touch100'))} |")
        L.append("")
        L.append(f"**Stability:** cp585 {'STABLE' if g['stable'].get('cp585') else 'not stable'}; "
                 f"cp600 {'STABLE' if g['stable'].get('cp600') else 'not stable'}.\n")
    path.write_text("\n".join(L))


if __name__ == "__main__":
    main()
