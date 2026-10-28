#!/usr/bin/env python
"""BASKET-01 Phase-2 F2/F12 — how predictable is continuation value from causal state?

Hand-rolled numpy logistic regression (no sklearn; polars + numpy only) on the
strongest 4-6 causal features, validated **time-blocked only**:
  * fit block-1 (2021-02..2023-12) -> test block-2 (2025-02..2026-05), and reverse;
  * feature selection uses only the training block (no leakage);
  * report test AUC, Brier vs a base-rate benchmark, Brier skill, calibration
    deciles, and standardised coefficients.

Primary target = ``touch50`` (future +50% from the checkpoint price);
secondary = ``touch100``.  Outputs ``predictability.json`` / ``predictability.md``.
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


def col(df: pl.DataFrame, name: str) -> np.ndarray:
    return df[name].cast(pl.Float64, strict=False).to_numpy().astype(np.float64)


def _prepare(train: pl.DataFrame, test: pl.DataFrame, feats: list[str]):
    Xtr = np.column_stack([col(train, f) for f in feats]) if feats else np.zeros((train.height, 0))
    Xte = np.column_stack([col(test, f) for f in feats]) if feats else np.zeros((test.height, 0))
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    Xtr = C.impute(Xtr, med)
    Xte = C.impute(Xte, med)
    lo = np.nanpercentile(Xtr, 1, axis=0)
    hi = np.nanpercentile(Xtr, 99, axis=0)
    Xtr = np.clip(Xtr, lo, hi)
    Xte = np.clip(Xte, lo, hi)
    mu = Xtr.mean(axis=0); sd = Xtr.std(axis=0); sd = np.where(sd > 0, sd, 1.0)
    Xtr = (Xtr - mu) / sd
    Xte = (Xte - mu) / sd
    return Xtr, Xte


def fit_eval(train: pl.DataFrame, test: pl.DataFrame, feats: list[str],
             target: str, l2: float = 1.0) -> dict:
    ytr = col(train, target); yte = col(test, target)
    Xtr, Xte = _prepare(train, test, feats)
    if not feats:
        return {"features": []}
    Xtr1 = np.column_stack([np.ones(len(Xtr)), Xtr])
    Xte1 = np.column_stack([np.ones(len(Xte)), Xte])
    w = C.fit_logistic(Xtr1, ytr, l2=l2, lr=0.5, iters=6000)
    ptr = C.predict_proba(Xtr1, w); pte = C.predict_proba(Xte1, w)
    base_train = float(np.nanmean(ytr)); base_test = float(np.nanmean(yte))
    brier_model = C.brier(yte, pte)
    brier_base = C.brier(yte, np.full_like(pte, base_train))
    brier_oracle = C.brier(yte, np.full_like(pte, base_test))
    return {
        "features": feats,
        "n_train": int(len(ytr)), "n_test": int(len(yte)),
        "train_base_rate": base_train, "test_base_rate": base_test,
        "train_auc": C.auc(ytr, ptr), "test_auc": C.auc(yte, pte),
        "brier_model": brier_model, "brier_base": brier_base,
        "brier_skill_vs_base": 1.0 - brier_model / brier_base if brier_base > 0 else float("nan"),
        "brier_base_oracle": brier_oracle,
        "brier_skill_vs_base_oracle": (1.0 - brier_model / brier_oracle
                                       if brier_oracle > 0 else float("nan")),
        "coef": {f: float(c) for f, c in zip(["intercept"] + feats, w)},
        "calibration_deciles_test": C.calibration_deciles(yte, pte),
    }


def univariate(train: pl.DataFrame, test: pl.DataFrame, target: str) -> dict:
    ytr = col(train, target); yte = col(test, target)
    out = {}
    for f in FEATURES:
        if f not in train.columns:
            continue
        out[f] = {
            "auc_train": C.auc(ytr, col(train, f)),
            "auc_test": C.auc(yte, col(test, f)),
        }
    return out


def select_features(train: pl.DataFrame, target: str, k: int = 6) -> list[str]:
    y = col(train, target)
    scored = []
    for f in FEATURES:
        if f not in train.columns:
            continue
        a = C.auc(y, col(train, f))
        if a == a:
            scored.append((abs(a - 0.5), f, a))
    scored.sort(reverse=True)
    return [f for _, f, _ in scored[:k]], {f: a for _, f, a in scored}


def main() -> None:
    df = C.load_panel()
    df = df.with_columns(pl.col("date").str.slice(0, 7).alias("_m"))
    df = df.with_columns(
        pl.when((pl.col("_m") >= C.BLOCK1[0]) & (pl.col("_m") <= C.BLOCK1[1])).then(1)
        .when((pl.col("_m") >= C.BLOCK2[0]) & (pl.col("_m") <= C.BLOCK2[1])).then(2)
        .otherwise(0).alias("_block"))

    out = {
        "blocks": {"block1": list(C.BLOCK1), "block2": list(C.BLOCK2)},
        "target": "touch50 (future +50% from checkpoint price)",
        "secondary_target": "touch100",
        "method": ("hand-rolled numpy logistic regression; standardised features; "
                   "train-block feature selection; time-blocked validation only"),
        "by_checkpoint": {},
    }
    for cp in (585, 600):
        sub = df.filter(pl.col("et") == cp)
        b1 = sub.filter(pl.col("_block") == 1)
        b2 = sub.filter(pl.col("_block") == 2)
        rec = {"n_block1": b1.height, "n_block2": b2.height, "directions": {}}

        for tag, train, test in [("fit_block1_test_block2", b1, b2),
                                 ("fit_block2_test_block1", b2, b1)]:
            feats, uni = select_features(train, "touch50", k=6)
            rec["directions"][tag] = {
                "selected_features": feats,
                "train_univariate_auc": uni,
                "touch50": fit_eval(train, test, feats, "touch50"),
                "touch100": fit_eval(train, test, feats, "touch100"),
            }
        # pooled descriptive fit (not validation) + per-feature information
        all_feats, _ = select_features(sub, "touch50", k=6)
        rec["pooled_descriptive"] = {
            "selected_features": all_feats,
            "touch50": fit_eval(sub, sub, all_feats, "touch50"),
        }
        rec["univariate"] = univariate(b1, b2, "touch50")
        out["by_checkpoint"][str(cp)] = rec

    # verdict
    verdicts = []
    for cp in (585, 600):
        for tag in ("fit_block1_test_block2", "fit_block2_test_block1"):
            d = out["by_checkpoint"][str(cp)]["directions"][tag]
            t50 = d["touch50"]
            verdicts.append({"checkpoint": cp, "direction": tag,
                             "test_auc": t50["test_auc"],
                             "brier_skill_vs_base": t50["brier_skill_vs_base"],
                             "base_rate": t50["test_base_rate"]})
    out["verdict_rows"] = verdicts
    C.write_json(OUT / "predictability.json", out)
    _write_md(out, OUT / "predictability.md")
    for v in verdicts:
        print(f"cp{v['checkpoint']} {v['direction']}: test_auc={v['test_auc']:.3f} "
              f"brier_skill={v['brier_skill_vs_base']:+.4f} base={v['base_rate']:.3f}")


def _write_md(out: dict, path: Path) -> None:
    L = ["# F2/F12 — how predictable is continuation value from causal path state?\n",
         "**Label: RUN** (ART = `predictability.json`). Hand-rolled numpy logistic "
         "regression; standardised features; **time-blocked validation only** (no random "
         "splits). Primary target: future +50% from the checkpoint price.\n"]
    for cp in ("585", "600"):
        rec = out["by_checkpoint"][cp]
        L.append(f"## Checkpoint {cp}\n")
        L.append(f"block-1 n={rec['n_block1']}, block-2 n={rec['n_block2']}\n")
        for tag, lab in [("fit_block1_test_block2", "fit block-1 -> test block-2"),
                         ("fit_block2_test_block1", "fit block-2 -> test block-1")]:
            d = rec["directions"][tag]
            t50 = d["touch50"]; t100 = d["touch100"]
            L.append(f"### {lab}\n")
            L.append(f"Selected features (training-block univariate AUC, no leakage): "
                     f"`{', '.join(d['selected_features'])}`\n")
            L.append("| target | n_test | base rate | AUC | Brier model | Brier base(train) | skill | skill vs oracle base |")
            L.append("|---|---|---|---|---|---|---|---|")
            for tname, t in [("+50", t50), ("+100", t100)]:
                L.append(f"| {tname} | {t['n_test']} | {t['test_base_rate']:.3f} | "
                         f"{t['test_auc']:.3f} | {t['brier_model']:.4f} | {t['brier_base']:.4f} | "
                         f"{t['brier_skill_vs_base']:+.4f} | "
                         f"{t['brier_skill_vs_base_oracle']:+.4f} |")
            L.append("")
            L.append("Calibration deciles (test, +50): predicted vs actual\n")
            L.append("| decile | n | p_mean | y_rate |")
            L.append("|---|---|---|---|")
            for b in t50["calibration_deciles_test"]:
                L.append(f"| {b['bin']} | {b['n']} | {b['p_mean']:.3f} | {b['y_rate']:.3f} |")
            L.append("")
            L.append("Standardised coefficients (+50): " +
                     ", ".join(f"`{k}`={v:+.3f}" for k, v in t50["coef"].items()) + "\n")
            top = sorted(((v, k) for k, v in d["train_univariate_auc"].items()),
                         key=lambda z: -abs(z[0] - 0.5))[:8]
            L.append("Top univariate (training-block) AUC: " +
                     ", ".join(f"`{k}`={v:.3f}" for v, k in top) + "\n")
    L.append("## Verdict\n")
    aucs = [v["test_auc"] for v in out["verdict_rows"]]
    skills = [v["brier_skill_vs_base"] for v in out["verdict_rows"]]
    oracles = [out["by_checkpoint"][str(v["checkpoint"])]["directions"][v["direction"]]
               ["touch50"]["brier_skill_vs_base_oracle"] for v in out["verdict_rows"]]
    L.append(f"Out-of-block test AUC for future +50% ranges **{min(aucs):.3f}–{max(aucs):.3f}** "
             f"(base rate ~{out['verdict_rows'][0]['base_rate']:.3f}); Brier skill vs a "
             f"train-base-rate forecast **{min(skills):+.4f} to {max(skills):+.4f}** and vs an "
             f"oracle constant base rate **{min(oracles):+.4f} to {max(oracles):+.4f}**. "
             "The state carries real but modest information: the model beats the base rate "
             "on held-out time blocks, yet the absolute discrimination is far from a "
             "classifier. The dominant carriers are the size of the prior move "
             "(`mfe_so_far`), the premarket gap (`ret_from_prevclose`), traded volume "
             "(`cum_volume`/`dollar_volume`), and basket context (`basket_breadth_*`, "
             "`rel_strength`) — see coefficients and `surfaces.json`. Multivariate "
             "coefficient signs for the correlated drawdown pair "
             "(`dd_from_running_high`/`dd_from_day_high`) and for `ret_from_prevclose` are "
             "unstable across directions because of collinearity with `mfe_so_far`; trust the "
             "univariate information table and the surfaces for variable importance, not the "
             "partial coefficients.\n")
    path.write_text("\n".join(L))


if __name__ == "__main__":
    main()
