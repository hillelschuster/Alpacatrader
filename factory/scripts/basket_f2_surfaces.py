#!/usr/bin/env python
"""BASKET-01 Phase-2 F2/F12 — conditional state surfaces.

Reads ``state_panel.parquet`` and emits ``surfaces.json`` / ``surfaces.md``:
  * base rates P(future +30/+50/+100 | checkpoint) overall and per family;
  * per-checkpoint conditional surfaces for **every** feature in the vocabulary
    (P(touch30/50/100), E[rem_mfe], E[rem_mae], P(nhba));
  * cohort comparisons by **remaining** tail size (rem_mfe strata), always
    conditioning on remaining value;
  * univariate AUC of every feature for touch50 (feeds the predictability pass).

Quantile bucket edges are descriptive (full dev); no gate/regression threshold
is taken from here — those are round numbers checked dual-block in gates.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import basket_f2_common as C
import basket_f2_features as F

OUT = C.OUT
FEATURES = list(F.FEATURE_DEFS.keys())
KEY = ["touch30", "touch50", "touch100", "rem_mfe", "rem_mae", "nhba"]


def _arrays(sub: pl.DataFrame) -> dict[str, np.ndarray]:
    return {c: sub[c].to_numpy().astype(np.float64) for c in
            ("touch30", "touch50", "touch100", "rem_mfe", "rem_mae", "nhba")}


def _stats_for(mask: np.ndarray, a: dict[str, np.ndarray]) -> dict:
    return C.stats(a["touch50"][mask], a["touch30"][mask], a["touch100"][mask],
                   a["rem_mfe"][mask], a["rem_mae"][mask], a["nhba"][mask])


def base_rates(df: pl.DataFrame) -> dict:
    out = {}
    for fam in ["ALL", "A_pm", "A_open", "B600"]:
        d = df if fam == "ALL" else df.filter(pl.col("family") == fam)
        out[fam] = {}
        for cp in C.CHECKPOINTS:
            sub = d.filter(pl.col("et") == cp)
            a = _arrays(sub)
            out[fam][str(cp)] = _stats_for(np.ones(len(sub), bool), a)
    return out


def feature_surfaces(df: pl.DataFrame) -> dict:
    """Overall + by-family, every feature, every checkpoint."""
    out = {"overall": {}, "by_family": {}}
    for cp in C.CHECKPOINTS:
        sub = df.filter(pl.col("et") == cp)
        a = _arrays(sub)
        out["overall"][str(cp)] = _feature_block(sub, a)
        for fam in ["A_pm", "A_open", "B600"]:
            fs = sub.filter(pl.col("family") == fam)
            out["by_family"].setdefault(fam, {})[str(cp)] = _feature_block(fs, _arrays(fs))
    return out


def _feature_block(sub: pl.DataFrame, a: dict[str, np.ndarray]) -> dict:
    block = {}
    n = len(sub)
    for feat in FEATURES:
        if feat not in sub.columns:
            continue
        x = sub[feat].to_numpy().astype(np.float64)
        edges = C.bucketize(x)
        buckets = []
        for i, (lab, lo, hi) in enumerate(edges):
            last = i == len(edges) - 1
            mask = C.in_bucket(x, lo, hi, last)
            s = _stats_for(mask, a)
            s.update({"label": lab, "lo": lo, "hi": hi})
            buckets.append(s)
        y = a["touch50"]
        block[feat] = {
            "n": n,
            "auc_touch50": C.auc(y, x),
            "buckets": buckets,
        }
    return block


def cohort_surfaces(df: pl.DataFrame) -> dict:
    out = {"overall": {}, "by_family": {}}
    for cp in C.CHECKPOINTS:
        sub = df.filter(pl.col("et") == cp)
        out["overall"][str(cp)] = _cohort_block(sub)
        for fam in ["A_pm", "A_open", "B600"]:
            fs = sub.filter(pl.col("family") == fam)
            out["by_family"].setdefault(fam, {})[str(cp)] = _cohort_block(fs)
    return out


def _cohort_block(sub: pl.DataFrame) -> dict:
    a = _arrays(sub)
    rm = a["rem_mfe"]
    out = {}
    for name, lo, hi in C.COHORTS:
        mask = (rm >= lo) & (rm < hi) & np.isfinite(rm)
        out[name] = _stats_for(mask, a)
    return out


def cohort_feature_surfaces(df: pl.DataFrame, cps=(585, 600)) -> dict:
    """Feature surfaces *within* each remaining-tail cohort (the 'condition on
    remaining value' requirement), at the two decision checkpoints."""
    out = {}
    for cp in cps:
        sub = df.filter(pl.col("et") == cp)
        rm = sub["rem_mfe"].to_numpy().astype(np.float64)
        out[str(cp)] = {}
        for name, lo, hi in C.COHORTS:
            mask = (rm >= lo) & (rm < hi) & np.isfinite(rm)
            cs = sub.filter(pl.Series(mask))
            if cs.height == 0:
                out[str(cp)][name] = {}
                continue
            out[str(cp)][name] = _feature_block(cs, _arrays(cs))
    return out


def cohort_state_means(df: pl.DataFrame) -> dict:
    """Mean causal state of each remaining-tail cohort — the direct answer to
    'what did future monsters look like at the checkpoint?'."""
    out = {}
    for cp in C.CHECKPOINTS:
        sub = df.filter(pl.col("et") == cp)
        rm = sub["rem_mfe"].to_numpy().astype(np.float64)
        out[str(cp)] = {}
        for name, lo, hi in C.COHORTS:
            mask = (rm >= lo) & (rm < hi) & np.isfinite(rm)
            cs = sub.filter(pl.Series(mask))
            if cs.height == 0:
                out[str(cp)][name] = {"n": 0}
                continue
            means = {"n": cs.height}
            for f in FEATURES:
                if f in cs.columns:
                    x = cs[f].cast(pl.Float64, strict=False).to_numpy().astype(np.float64)
                    means[f] = float(np.nanmean(x))
            means["p_nhba"] = float(np.nanmean(cs["nhba"].to_numpy().astype(np.float64)))
            means["e_rem_mae"] = float(np.nanmean(cs["rem_mae"].to_numpy().astype(np.float64)))
            out[str(cp)][name] = means
    return out


def information_table(df: pl.DataFrame) -> dict:
    """Univariate AUC for touch50 of every feature, overall + per family, per cp."""
    out = {"overall": {}, "by_family": {}}
    for cp in C.CHECKPOINTS:
        sub = df.filter(pl.col("et") == cp)
        out["overall"][str(cp)] = _info_block(sub)
        for fam in ["A_pm", "A_open", "B600"]:
            fs = sub.filter(pl.col("family") == fam)
            out["by_family"].setdefault(fam, {})[str(cp)] = _info_block(fs)
    return out


def _info_block(sub: pl.DataFrame) -> dict:
    y50 = sub["touch50"].to_numpy().astype(np.float64)
    y100 = sub["touch100"].to_numpy().astype(np.float64)
    block = {}
    for feat in FEATURES:
        if feat not in sub.columns:
            continue
        x = sub[feat].to_numpy().astype(np.float64)
        block[feat] = {"auc_touch50": C.auc(y50, x), "auc_touch100": C.auc(y100, x)}
    return block


# --------------------------------------------------------------------------- #
# markdown
# --------------------------------------------------------------------------- #
def _pct(v) -> str:
    if v is None or v != v:
        return "n/a"
    return f"{100*v:.1f}%"


def _num(v, nd=3) -> str:
    if v is None or v != v:
        return "n/a"
    return f"{v:.{nd}f}"


def write_md(surfaces: dict, info: dict, path: Path) -> None:
    br = surfaces["base_rates"]
    L = []
    L.append("# F2/F12 — golden-window state surfaces\n")
    L.append("**Label: REPORT** (descriptive dev-set tables; ART = `surfaces.json`). "
             "Panel: `state_panel.parquet`. Features are causal by checkpoint "
             "(`close(tp)` and bars `et<=tp`); targets use bars `tp+1..session_end`.\n")
    L.append("## 1. Base rates by entry family (P(future touch | checkpoint))\n")
    L.append("| checkpoint | family | n | P(+30) | P(+50) | P(+100) | E[rem MFE] | P(nhba) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for cp in C.CHECKPOINTS:
        for fam in ["ALL", "A_pm", "A_open", "B600"]:
            s = br[fam][str(cp)]
            L.append(f"| {cp} | {fam} | {s.get('n',0)} | {_pct(s.get('p_touch30'))} | "
                     f"{_pct(s.get('p_touch50'))} | {_pct(s.get('p_touch100'))} | "
                     f"{_num(s.get('e_rem_mfe'))} | {_pct(s.get('p_nhba'))} |")
    L.append("")
    L.append("`nhba` = new session high before a 10% drawdown from the session running "
             "max (pessimistic same-bar).\n")

    L.append("## 2. Cohort by *remaining* tail (always conditioned on forward value)\n")
    L.append("Cohorts: remaining MFE from the checkpoint (`rem_mfe`). An already-+95% name "
             "with nothing left is not the same object as one with +150% still ahead.\n")
    L.append("| checkpoint | cohort | n | P(+50) | P(+100) | E[rem MFE] | E[rem MAE] | P(nhba) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for cp in C.CHECKPOINTS:
        for name, _, _ in C.COHORTS:
            s = surfaces["cohorts"]["overall"][str(cp)][name]
            L.append(f"| {cp} | {name} | {s.get('n',0)} | {_pct(s.get('p_touch50'))} | "
                     f"{_pct(s.get('p_touch100'))} | {_num(s.get('e_rem_mfe'))} | "
                     f"{_num(s.get('e_rem_mae'))} | {_pct(s.get('p_nhba'))} |")
    L.append("")

    L.append("## 2b. What did the future monsters actually look like? (mean causal state by cohort)\n")
    L.append("Mean feature values at checkpoints 585 / 600 for each remaining-tail cohort "
             "(ART: `surfaces.json` -> `cohort_state_means`).\n")
    show = ["n", "ret_from_fill", "ret_from_prevclose", "mfe_so_far", "dd_from_running_high",
            "cum_volume", "rel_strength", "basket_breadth_10", "rank", "p_nhba", "e_rem_mae"]
    for cp in (585, 600):
        L.append(f"### checkpoint {cp}\n")
        L.append("| cohort | " + " | ".join(show) + " |")
        L.append("|" + "---|" * (len(show) + 1))
        for name, _, _ in C.COHORTS:
            m = surfaces["cohort_state_means"][str(cp)][name]
            vals = []
            for k in show:
                v = m.get(k)
                vals.append(f"{v:.3f}" if isinstance(v, float) else str(v))
            L.append(f"| {name} | " + " | ".join(vals) + " |")
        L.append("")

    L.append("## 3. Which state features separate continuation? (overall, per checkpoint)\n")
    L.append("Ranked by spread in P(future +50) between the best and worst bucket "
             "(buckets with n>=50). Full tables for every feature/checkpoint/family are in "
             "`surfaces.json`.\n")
    for cp in C.CHECKPOINTS:
        L.append(f"### checkpoint {cp}\n")
        block = surfaces["features"]["overall"][str(cp)]
        rows = []
        for feat, d in block.items():
            bs = [b for b in d["buckets"] if b.get("n", 0) >= 50]
            if len(bs) < 2:
                continue
            p = [b["p_touch50"] for b in bs]
            rows.append((max(p) - min(p), feat, bs, d.get("auc_touch50", float("nan"))))
        rows.sort(reverse=True)
        L.append("| feature | AUC(+50) | best bucket | P(+50) | worst bucket | P(+50) | spread |")
        L.append("|---|---|---|---|---|---|---|")
        for spread, feat, bs, a in rows[:8]:
            best = max(bs, key=lambda b: b["p_touch50"])
            worst = min(bs, key=lambda b: b["p_touch50"])
            L.append(f"| `{feat}` | {a:.3f} | {best['label']} | {_pct(best['p_touch50'])} | "
                     f"{worst['label']} | {_pct(worst['p_touch50'])} | {_pct(spread)} |")
        L.append("")
    L.append("## 4. Family differences at the decision checkpoints\n")
    L.append("P(+50) by family and checkpoint (from base rates):\n")
    L.append("| checkpoint | A_pm | A_open | B600 |")
    L.append("|---|---|---|---|")
    for cp in C.CHECKPOINTS:
        vals = [br[f][str(cp)].get("p_touch50", float("nan")) for f in ["A_pm", "A_open", "B600"]]
        L.append(f"| {cp} | " + " | ".join(_pct(v) for v in vals) + " |")
    L.append("")
    path.write_text("\n".join(L))


def main() -> None:
    df = C.load_panel()
    print("panel", df.shape)
    surfaces = {
        "base_rates": base_rates(df),
        "features": feature_surfaces(df),
        "cohorts": cohort_surfaces(df),
        "cohort_features": cohort_feature_surfaces(df),
        "cohort_state_means": cohort_state_means(df),
        "feature_defs": F.FEATURE_DEFS,
        "target_defs": F.TARGET_DEFS,
        "cohort_defs": {n: [lo, hi] for n, lo, hi in C.COHORTS},
        "notes": ("Causal by checkpoint; targets from tp+1. Cohort = forward rem_mfe. "
                  "Quantile edges descriptive; gates use round dual-block thresholds."),
    }
    info = information_table(df)
    surfaces["information"] = info
    C.write_json(OUT / "surfaces.json", surfaces)
    write_md(surfaces, info, OUT / "surfaces.md")
    print("wrote surfaces.json / surfaces.md")
    # console headline
    for cp in (585, 600):
        b = surfaces["base_rates"]["ALL"][str(cp)]
        print(f"cp{cp}: n={b['n']} P(+30)={b['p_touch30']:.3f} "
              f"P(+50)={b['p_touch50']:.3f} P(+100)={b['p_touch100']:.3f} "
              f"E[remMFE]={b['e_rem_mfe']:.3f} P(nhba)={b['p_nhba']:.3f}")


if __name__ == "__main__":
    main()
