#!/usr/bin/env python3
"""ATLAS E1a — the exhaustion score as a release / de-risk rule, priced by the dollar ledger.

The economic object is the *every-bar* exhaustion level:

    final_high_now(t) = 1 iff the running high at t is the session's final running high
                        (equivalently: no bar after t sets a new high), on a tape that is
                        complete to the session close, else 0.

That label is defined at every completed bar, so a release rule driven by its score is never an
out-of-support extrapolation (the event-hazard model of the fall analysis — P(this new high is the
last) fitted on new-high bars only — is carried as a clearly labelled ABLATION, because its training
distribution is not the distribution of the bars a release decision would be taken on).

Frozen protocol (nothing is tuned on an evaluation block):

  * panel        `ATLAS/panel.parquet` v2, sha-bound, loaded through `basket_atlas_fall.load_frame`.
  * training     complete (`path_complete_to_session_end`) AND uncensored bars, past the fill, where a
                 new high has already occurred (`running_high > running_high at the fill bar`) and the
                 label exists.  Censored members are excluded from training and from evaluation
                 (censoring is a terminal fact, never a future-known eligibility gate for a decision).
  * score        one ridge logistic per block, pooled over both families: 14 fall state features
                 (fall.design_matrix scaling) + exact clock-minute fixed effects
                 (fall.multi_effect_logit), the fall model machinery unchanged.
  * cross-fit    every scored bar comes from the model fitted on the OTHER block, and the threshold
                 pool is scored the same way, so neither the thresholds nor the evaluated rows are
                 ever in sample.
  * folds        `block_of` (frozen; the panel carries exactly two blocks, so the brief's
                 "blocks 0-1 train -> 2-3 eval" is fold A = block1 -> block2 and fold B = block2 ->
                 block1).
  * decisions    taken on every complete & uncensored bar where a new high has already occurred; a
                 release is a 100% exit executed at the next printed bar's open (the ledger's scan
                 semantics, forced-flat precedence included).
  * PRIMARY arm  a single global threshold learned on the TRAIN block from actual ledger dollars: a
                 bounded grid over the train score distribution (quantiles 0.50..0.99 of the pooled
                 train decision-population scores), objective = train-block net at 100 bps after
                 removing the five largest-|day net| days, ties broken toward fewer releases; the
                 winner is then frozen and applied to the evaluation block unchanged.
  * SECONDARY    clock-minute quantile rulers q70 / q80 / q90 of the TRAIN score distribution at that
                 bar's exact clock minute (< 30 train rows in a minute -> the pooled train quantile).
  * ABLATION     the event-hazard score (old target) with the same clock-minute rulers, decisions
                 restricted to new-high bars (its own support).
  * CONTROLS     `hold_flat` (the executable forced flat) and `giveback:10` (the panel's declared
                 continuation ruler).
  * frictions    100 and 150 bps round trip, the ledger's per-side convention (`led.fric_k`).
  * accounting   entirely imported from `basket_atlas_ledger.py`: `replay_member` ->
                 `aggregate_block` -> `accounting_views`; nothing is re-derived here.

Kill criterion (pre-registered): an arm is dead if, after removing the five days with the largest
|day net|, either (a) its net dollar ledger is <= the hold baseline, or (b) its destroyed giant tail
per avoided dollar (MFE from the release >= +300% AND the session close above the release price) is
not below the binding non-score control's (giveback:10), in EITHER fold.

Run:
    .venv/bin/python factory/scripts/basket_atlas_e1.py             # full run -> ATLAS/E1/E1.json
    .venv/bin/python factory/scripts/basket_atlas_e1.py --selftest  # unit / integration checks
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# The fits here are small Newton solves; with one BLAS thread per logical CPU the threading overhead
# dominates (same cap as basket_atlas_fall.py, which owns these fits).
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "4")

import polars as pl  # noqa: E402

WT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import basket_atlas_fall as fall  # noqa: E402  (frame, features, fits, AUC, bootstrap)
import basket_atlas_ledger as led  # noqa: E402  (rules, replay, dollar accounting, guard)

ATLAS = WT / "factory/artifacts/basket/phase2/ATLAS"
PANEL = ATLAS / "panel.parquet"
OUT_DIR = ATLAS / "E1"
OUT = OUT_DIR / "E1.json"
SELFTEST_OUT = OUT_DIR / "selftest.json"

PANEL_SHA256 = fall.PANEL_SHA256
PANEL_ROWS_EXPECTED = 1_900_432
PANEL_MEMBERS_EXPECTED = 6_160
STATE_ROWS_EXPECTED = 1_458_789
EVENT_ROWS_EXPECTED = 42_298

BLOCKS = fall.BLOCKS                       # ("block1", "block2") — the frozen block_of labels
FOLDS = (("A", "block1", "block2"), ("B", "block2", "block1"))
TARGETS = ("final_high_now", "event_hazard")
QUANTILES = (0.70, 0.80, 0.90)             # the secondary clock-minute rulers
GRID_QUANTILES = tuple(round(0.50 + 0.01 * i, 2) for i in range(50))   # 0.50 … 0.99
MIN_TRAIN_OBS_PER_MINUTE = 30
FRICTIONS = (100.0, 150.0)
GRID_OBJECTIVE_BPS = 100.0
# decision populations: the primary/secondary arms decide on state bars (a new high has already
# occurred — the state the model is trained on); the ablation decides on its own new-high support.
DECISION_STATE = "state"
DECISION_EVENT = "event"
CONTROLS = ("hold_flat", "giveback:10")
CONTROL_KEY = "giveback:10"                # the binding non-score control for the tail test
GIANT_MFE_LEVELS = (1.0, 3.0)
# ---- E1b: conditional (dispersion-niche) analysis ------------------------------------------
DISPERSION_K = 30
DISPERSION_TARGET = f"fwd_range_{DISPERSION_K}"   # the fall dispersion label (label-side)
DISPERSION_TERCILE_CUTS = (1.0 / 3.0, 2.0 / 3.0)
E1B_VERDICT_FRICTION = 100.0
BOOT_REPS = 1000
SEED = fall.SEED
ATTRIBUTION_GIANT_CAP = 300
PRODUCER = "factory/scripts/basket_atlas_e1.py"


# ======================================================================================
# small helpers
# ======================================================================================
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    """Atomic write (tmp + replace), the ledger's convention."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(fall.jsonable(payload), indent=1) + "\n")
    tmp.replace(path)


def r6(x) -> "float | None":
    return fall.r6(x)


def quantile_tag(q: float) -> str:
    return f"q{int(round(q * 100))}"


def module_provenance() -> dict:
    """The exact code that produced these numbers (sibling modules are imported, never copied)."""
    mods = {"basket_atlas_ledger.py": led, "basket_atlas_fall.py": fall}
    try:
        import basket_atlas_panel as panel_mod
        mods["basket_atlas_panel.py"] = panel_mod
    except Exception:                                     # pragma: no cover - reported, not hidden
        pass
    out = {}
    for name, mod in mods.items():
        p = Path(mod.__file__).resolve()
        out[name] = {"sha256": sha256_of(p), "path": p.name}
    return {"producer": {"path": PRODUCER, "sha256": sha256_of(Path(__file__).resolve())},
            "imported_modules": out, "panel_sha256": PANEL_SHA256}


def arm_id(role: str, q: float) -> str:
    return f"{'ablation_event_hazard_' if role == 'ablation' else ''}quantile_{quantile_tag(q)}"


def cell_id(arm: str, fold: str, bps: float) -> str:
    return f"{arm}|{fold}|{int(bps)}"


# ======================================================================================
# labels, masks and the score
# ======================================================================================
def final_high_now(mfe: np.ndarray, mid: np.ndarray, off: np.ndarray) -> np.ndarray:
    """final_high_now(t) = 1 iff the running high at t is the tape's final running high.

    `mfe` is the running high in return-from-entry units (monotone non-decreasing inside a member),
    so the identity is exact float comparison against the member's last stored value.
    """
    last = mfe[off[1:] - 1]
    return (mfe == last[mid]).astype(np.float64)


def build_masks(frame) -> dict:
    """Population masks.  None of them reads a label, and censoring never gates a decision."""
    complete = frame.path_complete & ~frame.censored
    mfe = frame.x["mfe_so_far"]
    mfe_entry = mfe[frame.member_rows][frame.mid]      # running high at the fill bar, per row
    new_high_seen = mfe > mfe_entry                    # a new high has already occurred
    is_new_high = frame.x["bars_since_new_high"] == 0
    label_now = final_high_now(mfe, frame.mid, frame.off)
    return {
        "complete": complete,
        "new_high_seen": new_high_seen,
        "is_new_high": is_new_high,
        # decision populations
        "state": complete & new_high_seen,
        "event": complete & is_new_high,
        # training populations (label defined)
        "train_now": complete & new_high_seen & frame.target_ok,
        "train_event": complete & is_new_high & frame.target_ok,
        "label_now": label_now,
    }


def fit_block_models(frame, masks: dict, target: str, member_keep: "np.ndarray | None" = None
                     ) -> dict:
    """One ridge logistic per block on the target's training population (fall machinery)."""
    y = masks["label_now"] if target == "final_high_now" else frame.final_high
    train = "train_now" if target == "final_high_now" else "train_event"
    models: dict = {}
    for blk in BLOCKS:
        sel = masks[train] & (frame.block == blk)
        if member_keep is not None:
            sel = sel & member_keep[frame.mid]
        idx = np.flatnonzero(sel)
        X, meta = fall.design_matrix(frame, idx, fall.MODEL_FEATURES, None)
        minute = fall.minute_codes(frame, idx)
        eff = ((minute, fall.n_minute_codes(frame)),)
        beta, it = fall.multi_effect_logit(X, y[idx], eff)
        p = fall.predict_multi(X, beta, eff)
        models[blk] = {
            "target": target, "model_block": blk, "n_train": int(idx.size),
            "base_rate_label": float(y[idx].mean()), "iterations": int(it),
            "n_features": int(X.shape[1]), "feature_names": list(meta["column_names"]),
            "beta": beta, "stats": meta["transform"], "n_minutes": fall.n_minute_codes(frame),
            "in_sample": fall.auc_multi_group(y[idx], p, {"clock": minute}),
        }
    for blk, m in models.items():
        other = "block2" if blk == "block1" else "block1"
        sel = masks[train] & (frame.block == other)
        if member_keep is not None:
            sel = sel & member_keep[frame.mid]
        idx = np.flatnonzero(sel)
        X, _ = fall.design_matrix(frame, idx, fall.MODEL_FEATURES, m["stats"])
        eff = ((fall.minute_codes(frame, idx), m["n_minutes"]),)
        p = fall.predict_multi(X, m["beta"], eff)
        minute = fall.minute_codes(frame, idx)
        fam = {}
        for famname in fall.FAMILIES:
            fm = frame.family[idx] == famname
            if fm.sum() >= 2:
                w = fall.auc_within_group(y[idx][fm], p[fm], minute[fm])
                fam[famname] = {"n": int(fm.sum()), "auc_pooled": r6(w["auc"]),
                                "auc_within_clock": r6(w["auc"]), "thin": bool(w["thin"])}
        m["oos"] = {"eval_block": other, "n": int(idx.size),
                    **{k: (r6(v) if isinstance(v, float) else v)
                       for k, v in fall.auc_multi_group(y[idx], p, {"clock": minute}).items()},
                    "by_family": fam}
    return models


def cross_fit_scores(frame, masks: dict, models: dict, target: str,
                     member_keep: "np.ndarray | None" = None) -> tuple:
    """Score the target's decision population with the model fitted on the OTHER block."""
    score = np.full(frame.n, np.nan, dtype=np.float64)
    population = masks["complete"] if target == "final_high_now" else masks["event"]
    prov: dict = {}
    for blk in BLOCKS:
        src = "block2" if blk == "block1" else "block1"
        m = models[src]
        sel = population & (frame.block == blk)
        if member_keep is not None:
            sel = sel & member_keep[frame.mid]
        idx = np.flatnonzero(sel)
        X, _ = fall.design_matrix(frame, idx, fall.MODEL_FEATURES, m["stats"])
        eff = ((fall.minute_codes(frame, idx), m["n_minutes"]),)
        score[idx] = fall.predict_multi(X, m["beta"], eff)
        prov[blk] = {"scored_rows": int(idx.size), "model_block": src,
                     "cross_fitted": bool(src != blk),
                     "population": ("every complete & uncensored bar" if target == "final_high_now"
                                    else "every new-high bar of a complete & uncensored tape")}
    return score, prov


def day_half_map(days) -> dict:
    """Deterministic day-level 2-fold split: sorted unique days alternate between the halves."""
    uniq = sorted({str(d) for d in days})
    return {d: (i % 2) for i, d in enumerate(uniq)}


def inner_cross_fit_scores(frame, masks: dict, target: str, block: str, member_day: np.ndarray,
                           member_keep: "np.ndarray | None" = None) -> tuple:
    """Honest TRAIN-side scores: fit on one day-half of `block`, score the other day-half.

    Threshold selection (the dollar grid and every clock-minute bank) must not be a function of the
    evaluation block.  The other-block model used for the *evaluation* rows is itself fitted on the
    whole train block, so scoring the train block with it would leak evaluation-block information
    into the thresholds.  This routine re-fits inside the train block, on a day-level split, and
    leaves the returned array NaN everywhere outside `block` — no selection routine can see an
    evaluation row, day or outcome through it.
    """
    half_of_day = day_half_map(member_day[frame.m_block == block])
    half = np.array([half_of_day.get(str(d), -1) for d in member_day], dtype=np.int64)
    row_half = half[frame.mid]
    train_name = "train_now" if target == "final_high_now" else "train_event"
    y = masks["label_now"] if target == "final_high_now" else frame.final_high
    pop = masks["complete"] if target == "final_high_now" else masks["event"]
    score = np.full(frame.n, np.nan, dtype=np.float64)
    info: dict = {"block": block, "target": target,
                  "split": "sorted unique days of the block, alternating between the two halves",
                  "n_days": int(len(half_of_day)), "halves": {}}
    for h in (0, 1):
        sel = masks[train_name] & (frame.block == block) & (row_half == h)
        if member_keep is not None:
            sel = sel & member_keep[frame.mid]
        idx = np.flatnonzero(sel)
        X, meta = fall.design_matrix(frame, idx, fall.MODEL_FEATURES, None)
        eff = ((fall.minute_codes(frame, idx), fall.n_minute_codes(frame)),)
        beta, it = fall.multi_effect_logit(X, y[idx], eff)
        sel_o = pop & (frame.block == block) & (row_half == 1 - h)
        if member_keep is not None:
            sel_o = sel_o & member_keep[frame.mid]
        ixo = np.flatnonzero(sel_o)
        Xo, _ = fall.design_matrix(frame, ixo, fall.MODEL_FEATURES, meta["transform"])
        effo = ((fall.minute_codes(frame, ixo), fall.n_minute_codes(frame)),)
        score[ixo] = fall.predict_multi(Xo, beta, effo)
        info["halves"][f"half{h}"] = {"n_train": int(idx.size), "iterations": int(it),
                                      "n_days": int(sum(1 for d, hh in half_of_day.items()
                                                        if hh == h)),
                                      "scored_other_half_rows": int(ixo.size)}
    info["n_scored"] = int(np.isfinite(score).sum())
    info["finite_outside_block"] = int(np.isfinite(score[frame.block != block]).sum())
    return score, info


def threshold_row(frame, thr) -> np.ndarray:
    """Per-row threshold: a scalar applies everywhere, an array is indexed by exact clock minute."""
    if np.ndim(thr) == 0:
        return np.full(frame.n, float(thr), dtype=np.float64)
    return np.take(np.asarray(thr, dtype=np.float64),
                   (frame.et - fall.FIRST_ET).astype(np.int64))


def per_minute_quantiles(values: np.ndarray, minute: np.ndarray, n_minutes: int,
                         quantiles: tuple, min_obs: int) -> tuple:
    """Quantiles of `values` per exact clock minute, with a pooled fallback for thin minutes."""
    order = np.argsort(minute, kind="mergesort")
    m_sorted, v_sorted = minute[order], values[order]
    starts = np.searchsorted(m_sorted, np.arange(n_minutes), side="left")
    ends = np.searchsorted(m_sorted, np.arange(n_minutes), side="right")
    pooled = np.quantile(values, list(quantiles))
    out = np.empty((len(quantiles), n_minutes), dtype=np.float64)
    fallback_minutes = []
    for m in range(n_minutes):
        a, b = int(starts[m]), int(ends[m])
        if b - a >= min_obs:
            out[:, m] = np.quantile(v_sorted[a:b], list(quantiles))
        else:
            out[:, m] = pooled
            fallback_minutes.append(int(m) + fall.FIRST_ET)
    return out, pooled, fallback_minutes


def first_crossing(mask: np.ndarray, off: np.ndarray) -> np.ndarray:
    """First True row index inside each member's row block (-1 when no row qualifies)."""
    out = np.full(off.size - 1, -1, dtype=np.int64)
    for i in range(out.size):
        a, b = int(off[i]), int(off[i + 1])
        if b > a:
            sub = mask[a:b]
            if sub.any():
                out[i] = int(np.argmax(sub))
    return out


def channel_release(frame, masks: dict, score: np.ndarray, thr, block: str, decision: str,
                    member_keep: "np.ndarray | None" = None) -> np.ndarray:
    """Trigger bar index per member (positional == bar_index), -1 for 'never released'."""
    mask = (masks[decision] & np.isfinite(score) & (score >= threshold_row(frame, thr))
            & (frame.block == block))
    if member_keep is not None:
        mask = mask & member_keep[frame.mid]
    return first_crossing(mask, frame.off)


# ======================================================================================
# the release rule: a pre-computed causal trigger, priced by the ledger untouched
# ======================================================================================
@dataclass
class ScoreRelease(led.Rule):
    """Release (100% exit) at the first bar whose causal score crosses the frozen threshold.

    Only the decision bar is computed here; everything downstream is the ledger's: `_resolve_scan`
    executes at the next printed bar's open, the forced flat keeps its precedence, and censored tapes
    stay unresolved (never zero-imputed).
    """

    triggers: dict = field(default_factory=dict)

    def trigger(self, cols) -> "int | None":
        key = (str(cols["sleeve_day"][0]), str(cols["ticker"][0]), str(cols["family"][0]),
               int(cols["entry_rank"][0]))
        bar = self.triggers.get(key)
        return None if bar is None or bar < 0 else int(bar)


def triggers_of(keys: list, bars: np.ndarray) -> dict:
    return {keys[i]: int(bars[i]) for i in range(len(keys)) if bars[i] >= 0}


# ======================================================================================
# ledger cells
# ======================================================================================
def replay_rows(df_eval, rule: led.Rule, bps: float) -> list:
    """One ledger row per member (`led.replay_member`; the session close is added so the contract's
    'destroyed giant' definition — which needs the close, not only the exit — can be evaluated)."""
    k = led.fric_k(bps)
    has_close = "session_close_ret_from_entry" in df_eval.columns
    rows = []
    for _key, sub in led.members_of(df_eval):
        r = led.replay_member(sub, rule, k)
        if has_close:
            scr = sub["session_close_ret_from_entry"][0]
            r["close_px"] = (float(sub["entry_px"][0]) * (1.0 + float(scr))
                             if scr is not None and math.isfinite(float(scr)) else None)
        rows.append(r)
    return rows


def judged_rows(rows: list) -> list:
    return [r for r in rows if r.get("delta") is not None]


def delta_stats(rows: list, reps: int = BOOT_REPS, seed: int = SEED) -> dict:
    """Net dollar ledger vs hold with a day-clustered 95% CI, and the top-5-day-removed basis."""
    judged = judged_rows(rows)
    if not judged:
        return {"n_members": 0}
    vals = np.array([r["delta"] for r in judged], dtype=np.float64)
    days = np.array([r["sleeve_day"] for r in judged])
    uniq, codes = np.unique(days, return_inverse=True)
    n_days = int(uniq.size)
    n = int(vals.size)
    mean = float(vals.mean())
    se_day = math.sqrt(max(fall.cluster_var(vals, codes, n_days), 0.0))
    boot = fall.cluster_bootstrap(vals, codes, n_days, stat=lambda v: float(v.mean()),
                                  reps=reps, seed=seed)
    dropped, dropped_sums, surviving = top5_drop(judged)
    kept_vals = np.array([r["delta"] for r in judged if r["sleeve_day"] in surviving],
                         dtype=np.float64)
    return {
        "n_members": n,
        "n_days": n_days,
        "sum": r6(float(vals.sum())),
        "mean": r6(mean),
        "se_day_cluster": r6(se_day),
        "ci95_day_cluster_sandwich_mean": [r6(mean - 1.96 * se_day), r6(mean + 1.96 * se_day)],
        "ci95_day_cluster_sandwich_sum": [r6((mean - 1.96 * se_day) * n),
                                          r6((mean + 1.96 * se_day) * n)],
        "ci95_day_cluster_bootstrap_mean": [r6(x) for x in boot["ci95_percentile"]],
        "ci95_day_cluster_bootstrap_sum": [r6(x * n) for x in boot["ci95_percentile"]],
        "boot_sd_mean": r6(boot["boot_sd"]),
        "bootstrap": {"reps": int(boot["reps"]), "seed": int(seed), "cluster": "sleeve_day"},
        "top5_days_by_abs_net": [{"day": d, "day_net": r6(dropped_sums[d])} for d in dropped],
        "n_days_after_removal": int(len(surviving)),
        "n_members_after_removal": int(kept_vals.size),
        "sum_after_top5_removal": r6(float(kept_vals.sum())) if kept_vals.size else None,
        "mean_after_top5_removal": r6(float(kept_vals.mean())) if kept_vals.size else None,
        "surviving_days": sorted(surviving),
    }


def tail_stats(rows: list) -> dict:
    """Avoided vs destroyed dollars, the giant tail in both readings, and the per-dollar ratios.

    Ledger reading (imported definitions): the `destroyed` dollars of members whose forward MFE from
    the exit reached +100% / +300%.  Contract reading: a destroyed giant is MFE from the release
    >= +300% AND the session close above the release price; the dollars are the same ledger
    `destroyed` dollars of those members (a member whose forced flat beat the release counts as cut
    but adds no destroyed dollars — reported separately, never silently dropped).
    """
    judged = judged_rows(rows)
    avoided = float(sum(r["avoided"] for r in judged))
    destroyed = float(sum(r["destroyed"] for r in judged))
    out: dict = {"avoided_dollars": r6(avoided), "destroyed_dollars": r6(destroyed),
                 "net_dollars": r6(avoided - destroyed)}
    for level in GIANT_MFE_LEVELS:
        tag = "giant300" if level >= 3.0 else "giant100"
        led_giants = [r for r in judged if r["destroyed"] > 0
                      and r.get("forward_mfe_from_exit") is not None
                      and r["forward_mfe_from_exit"] >= level]
        led_dollars = float(sum(r["destroyed"] for r in led_giants))
        con_giants = [r for r in judged if r.get("early_exit")
                      and r.get("forward_mfe_from_exit") is not None
                      and r["forward_mfe_from_exit"] >= level
                      and r.get("close_px") is not None and r.get("exit_px") is not None
                      and float(r["close_px"]) > float(r["exit_px"])]
        con_dollars = float(sum(r["destroyed"] for r in con_giants))
        out[tag] = {
            "ledger_view": {"n_cut": len(led_giants), "destroyed_dollars": r6(led_dollars),
                            "per_avoided_dollar": r6(led_dollars / avoided) if avoided > 0 else None,
                            "definition": "ledger: destroyed>0 and forward MFE from the exit >= "
                                          f"+{int(level * 100)}%"},
            "contract_view": {"n_cut": len(con_giants),
                              "n_with_destroyed_dollars": sum(1 for r in con_giants
                                                              if r["destroyed"] > 0),
                              "destroyed_dollars": r6(con_dollars),
                              "per_avoided_dollar": r6(con_dollars / avoided) if avoided > 0 else None,
                              "definition": "contract: early exit and forward MFE from the release "
                                            f">= +{int(level * 100)}% and session close > release "
                                            "price"},
        }
    return out


def day_sums(rows: list) -> dict:
    """Compact per-day ledger sums, used for the same-basis control comparison."""
    out: dict = {}
    for r in judged_rows(rows):
        d = r["sleeve_day"]
        e = out.setdefault(d, {"n": 0, "n_releases": 0, "delta": 0.0, "avoided": 0.0,
                               "destroyed": 0.0, "giant300_ledger_dollars": 0.0,
                               "giant300_contract_dollars": 0.0})
        e["n"] += 1
        e["n_releases"] += int(bool(r["early_exit"]))
        e["delta"] += r["delta"]
        e["avoided"] += r["avoided"]
        e["destroyed"] += r["destroyed"]
        mfe = r.get("forward_mfe_from_exit")
        if mfe is not None and mfe >= 3.0:
            if r["destroyed"] > 0:
                e["giant300_ledger_dollars"] += r["destroyed"]
            if (r["early_exit"] and r.get("close_px") is not None and r.get("exit_px") is not None
                    and float(r["close_px"]) > float(r["exit_px"])):
                e["giant300_contract_dollars"] += r["destroyed"]
    return out


def same_basis(day_sum: dict, days: list) -> dict:
    """Restrict a control's per-day sums to an arm's surviving days (both giant readings)."""
    avoided = sum(day_sum[d]["avoided"] for d in days if d in day_sum)
    g300_led = sum(day_sum[d]["giant300_ledger_dollars"] for d in days if d in day_sum)
    g300_con = sum(day_sum[d]["giant300_contract_dollars"] for d in days if d in day_sum)
    delta = sum(day_sum[d]["delta"] for d in days if d in day_sum)
    n = sum(day_sum[d]["n"] for d in days if d in day_sum)
    return {"n_members": int(n), "sum": r6(delta), "avoided_dollars": r6(avoided),
            "giant300_ledger_dollars": r6(g300_led),
            "giant300_contract_dollars": r6(g300_con),
            "per_avoided_dollar": r6(g300_con / avoided) if avoided > 0 else None,
            "per_avoided_dollar_ledger_view": r6(g300_led / avoided) if avoided > 0 else None}


def attribution(rows: list, limit: int = ATTRIBUTION_GIANT_CAP) -> dict:
    """Who was released: per day, per family, and the giants left behind."""
    judged = judged_rows(rows)
    released = [r for r in judged if r["early_exit"]]
    by_family: dict = {}
    for r in judged:
        e = by_family.setdefault(r["family"], {"n_members": 0, "n_releases": 0, "net_dollars": 0.0})
        e["n_members"] += 1
        e["n_releases"] += int(bool(r["early_exit"]))
        e["net_dollars"] += r["delta"]
    for e in by_family.values():
        e["net_dollars"] = r6(e["net_dollars"])
    days = day_sums(judged)
    day_table = [{"day": d, **{k: (r6(v) if isinstance(v, float) else v)
                               for k, v in days[d].items()}} for d in sorted(days)]
    giants = [r for r in released if r.get("forward_mfe_from_exit") is not None
              and r["forward_mfe_from_exit"] >= 1.0]

    def brief(r: dict) -> dict:
        return {"sleeve_day": r["sleeve_day"], "ticker": r["ticker"], "family": r["family"],
                "entry_rank": r["entry_rank"], "trigger_et": r.get("trigger_et"),
                "exit_et": r.get("exit_et"), "exit_px": r6(r.get("exit_px")),
                "forward_mfe_from_exit": r6(r.get("forward_mfe_from_exit")),
                "close_above_release": (None if r.get("close_px") is None or r.get("exit_px") is None
                                        else bool(float(r["close_px"]) > float(r["exit_px"]))),
                "delta": r6(r["delta"]), "destroyed_dollars": r6(r["destroyed"])}

    giants.sort(key=lambda r: (-r["destroyed"], r["sleeve_day"], r["ticker"]))
    destroyed_sorted = sorted(released, key=lambda r: (-r["destroyed"], r["sleeve_day"]))
    trig = [r.get("trigger_et") for r in released if r.get("trigger_et") is not None]
    return {
        "n_members": len(judged), "n_releases": len(released),
        "release_share": r6(len(released) / len(judged)) if judged else None,
        "n_triggered_without_execution": sum(1 for r in judged
                                             if r.get("trigger_bar_index") is not None
                                             and not r["early_exit"]),
        "trigger_et_median": r6(float(np.median(trig))) if trig else None,
        "trigger_et_p10": r6(float(np.percentile(trig, 10))) if trig else None,
        "trigger_et_p90": r6(float(np.percentile(trig, 90))) if trig else None,
        "by_family": by_family,
        "by_day": day_table,
        "released_giants_mfe_ge_100": {
            "n": len(giants), "n_listed": min(len(giants), limit),
            "members": [brief(r) for r in giants[:limit]]},
        "top_destroyed_releases": [brief(r) for r in destroyed_sorted[:limit]],
    }


# ======================================================================================
# the pre-registered kill criterion
# ======================================================================================
def tail_test(arm_g300_dollars, arm_avoided_dollars, control_ratio):
    """The tail clause of the kill criterion (contract giant: MFE >= +300% and close > release).

    Degenerate cases are named, never silently resolved: a rule that destroyed no +300% tail passes
    the clause (there is nothing it can be worse than the control at); a rule that avoided nothing
    fails it (the ratio has no denominator, and its delta clause has already failed); a control that
    avoided nothing on the same basis is no reference.
    """
    if arm_avoided_dollars is None or arm_avoided_dollars <= 0.0:
        return False, ("no avoided dollars after top-5-day removal: the tail ratio has no "
                       "denominator (and the delta clause has already failed)")
    if arm_g300_dollars is not None and arm_g300_dollars <= 0.0:
        return True, "no +300% giant tail destroyed after top-5-day removal"
    if control_ratio is None:
        return True, f"binding control {CONTROL_KEY} destroyed nothing to reference on the same basis"
    ratio = float(arm_g300_dollars) / float(arm_avoided_dollars)
    return bool(ratio < float(control_ratio)), (
        f"arm {ratio:.6f} vs {CONTROL_KEY} {float(control_ratio):.6f} giant300 dollars per avoided "
        f"dollar")


def grid_point_key(cand: dict):
    """Grid objective: train net after top-5-day removal; ties broken toward fewer releases."""
    return (cand["train_net_after_top5_removal"], -cand["n_releases"])


def choose_grid_point(curve: list) -> dict:
    return max(curve, key=grid_point_key)


# ======================================================================================
# cells
# ======================================================================================
def reconciliation(agg: dict) -> dict:
    """The ledger's own reconciliation counters, published per cell so a mismatch cannot hide."""
    obs = agg["unresolved_censored"]["observed_exits"]
    out = {
        "fwd_mfe_check_n": int(agg["fwd_mfe_check_n"]),
        "fwd_mfe_mismatch_n": int(agg["fwd_mfe_mismatch_n"]),
        "forced_flat_check_n": int(agg["forced_flat_check_n"]),
        "forced_flat_mismatch_n": int(agg["forced_flat_mismatch_n"]),
        "level_ret_check_n": int(agg["level_ret_check_n"]),
        "level_ret_mismatch_n": int(agg["level_ret_mismatch_n"]),
        "tail_class_100_check_n": int(agg["tail_class_100_check_n"]),
        "tail_class_100_mismatch_n": int(agg["tail_class_100_mismatch_n"]),
        "tail_class_300_mismatch_n": int(agg["tail_class_300_mismatch_n"]),
        "giveback_locator_check_n": int(agg["giveback_locator_check_n"]),
        "giveback_locator_mismatch_n": int(agg["giveback_locator_mismatch_n"]),
        "n_members_unscored_other": int(agg["n_members_unscored_other"]),
        "unresolved_censored": {
            "n_members": int(agg["unresolved_censored"]["n_members"]),
            "observed_rule_side_exits": {
                "n_exits": int(obs["n_exits"]),
                "n_members_without_executable_exit":
                    int(obs["n_members_without_executable_exit"]),
                "rule_side_net_sum": r6(obs["rule_side_net_sum"]),
                "mean_rule_side_net": r6(obs["mean_rule_side_net"]),
            },
        },
    }
    out["reconciliation_clean"] = bool(all(out[k] == 0 for k in out
                                           if k.endswith("mismatch_n")))
    return out


def dispersion_splitter(frame, masks: dict, label: np.ndarray, member_day: np.ndarray,
                        letter: str, train_block: str, eval_block: str,
                        member_keep: "np.ndarray | None" = None) -> dict:
    """The causal forward-dispersion splitter, frozen before any outcome is read.

    (a) TRAIN side — an inner day-level cross-fit inside the train block (the same split discipline
        as the threshold banks): the dispersion model fitted on one day-half predicts the other
        half, and the tercile cuts are the terciles of those honest train-side predictions.
    (b) EVAL side — the model fitted on the whole train block predicts the evaluation block's
        decision-population bars; the frozen cuts assign the tercile.
    (c) The splitter is a function of causal state only (14 fall state features + exact clock);
        the dispersion label (`fwd_range_30`, label-side) is never given to the model that scores
        the evaluation block, and the eval-side label correlation is measured AFTER the cuts exist.
    """
    disp_pop = masks["state"] & np.isfinite(label)
    n_min = fall.n_minute_codes(frame)
    keep = (lambda sel: sel if member_keep is None else sel & member_keep[frame.mid])

    def fit_predict(itr, ipr):
        Xtr, meta = fall.design_matrix(frame, itr, fall.MODEL_FEATURES, None)
        eff_tr = ((fall.minute_codes(frame, itr), n_min),)
        beta = fall.multi_effect_ridge_linear(Xtr, np.log1p(label[itr]), eff_tr)
        Xpr, _ = fall.design_matrix(frame, ipr, fall.MODEL_FEATURES, meta["transform"])
        eff_pr = ((fall.minute_codes(frame, ipr), n_min),)
        return fall.predict_multi(Xpr, beta, eff_pr)

    itr_eval = np.flatnonzero(keep(disp_pop & (frame.block == train_block)))
    iev = np.flatnonzero(keep(masks["state"] & (frame.block == eval_block)))
    pred_eval = fit_predict(itr_eval, iev)

    half_of_day = day_half_map(member_day[frame.m_block == train_block])
    half = np.array([half_of_day.get(str(d), -1) for d in member_day], dtype=np.int64)[frame.mid]
    pred_train = np.full(frame.n, np.nan, dtype=np.float64)
    n_train_half = {}
    for h in (0, 1):
        itr = np.flatnonzero(keep(disp_pop & (frame.block == train_block) & (half == h)))
        ipr = np.flatnonzero(keep(masks["state"] & (frame.block == train_block) & (half == 1 - h)))
        pred_train[ipr] = fit_predict(itr, ipr)
        n_train_half[f"half{h}"] = int(itr.size)
    pool = pred_train[keep(masks["state"] & (frame.block == train_block))]
    cuts = np.quantile(pool, list(DISPERSION_TERCILE_CUTS))
    tercile = np.full(frame.n, -1, dtype=np.int64)
    tercile[iev] = np.searchsorted(cuts, pred_eval, side="right")

    label_eval = label[iev]
    ok = np.isfinite(label_eval)
    terraces = {
        "letter": letter, "train_block": train_block, "eval_block": eval_block,
        "target": DISPERSION_TARGET, "fit_on": f"log1p({DISPERSION_TARGET}), label-side",
        "tercile_cuts_frozen_on": "inner day-split cross-fit predictions of the TRAIN block",
        "cuts": [r6(x) for x in cuts],
        "n_train_dispersion_rows": int(itr_eval.size), "n_train_halves": n_train_half,
        "n_train_pool_rows": int(pool.size),
        "n_eval_scored_rows": int(iev.size),
        "eval_pred_vs_label_spearman_POST_FREEZE": r6(
            fall.spearman_score(label_eval[ok], pred_eval[ok])) if ok.sum() > 2 else None,
        "eval_pred_r2_log1p_POST_FREEZE": r6(
            1.0 - float(np.var(np.log1p(label_eval[ok]) - pred_eval[ok]))
            / max(float(np.var(np.log1p(label_eval[ok]))), 1e-18)) if ok.sum() > 2 else None,
        "n_eval_rows_without_label": int((~ok).sum()),
        "tercile_row_counts": [int((tercile == t).sum()) for t in (0, 1, 2)],
        "no_outcome_used_for_freeze": True,
    }
    return {"tercile": tercile, "cuts": cuts, "eval_rows": iev, "pred_eval": pred_eval,
            "diagnostics": terraces}


def top5_drop(judged: list) -> tuple:
    """The pre-registered top-5-|day net| removal: (dropped days, dropped sums, surviving day set)."""
    day_sum: dict = {}
    for r in judged:
        day_sum[r["sleeve_day"]] = day_sum.get(r["sleeve_day"], 0.0) + r["delta"]
    dropped = sorted(day_sum, key=lambda d: (-abs(day_sum[d]), d))[:5]
    return dropped, {d: day_sum[d] for d in dropped}, set(day_sum) - set(dropped)


def member_niche(frame, masks: dict, tercile: np.ndarray) -> np.ndarray:
    """Each member's dispersion REGIME: the median tercile over its state bars (-1 when it has none).

    A member's first state bar is systematically a high-predicted-dispersion moment (it is the first
    new high past the fill), so a first-bar niche collapses ~97% of members into the top tercile and
    carries no conditioning.  The median over the member's releasable bars is arm-independent,
    causal, invariant to the monotone score transform, and spreads members across the terciles; the
    tercile cuts themselves stay the frozen train-side ones.
    """
    sel = masks["state"] & (tercile >= 0)
    idx = np.flatnonzero(sel)
    niche = np.full(frame.n_members, -1, dtype=np.int64)
    if idx.size == 0:
        return niche
    order = np.argsort(frame.mid[idx], kind="mergesort")
    m_sorted, v_sorted = frame.mid[idx][order], tercile[idx][order]
    bounds = np.flatnonzero(np.r_[True, m_sorted[1:] != m_sorted[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        niche[int(m_sorted[a])] = int(np.median(v_sorted[a:b]))
    return niche


def member_key(day: str, ticker: str, family: str, rank) -> str:
    """The one member-key form used by the ledger rows and the niche map."""
    return f"{day}|{ticker}|{family}|{int(rank)}"


def niche_increments(arm_rows: list, control_rows: list, niche_by_key: dict, survivors) -> list:
    """Per-tercile `arm - control` increments on identical surviving days (sleeve and dedup views).

    The control is the same member set (the tercile is a member property), restricted to the arm's
    own top-5-day-removed day set, so the terciles decompose the unconditional increment.
    """
    ctrl = {member_key(r["sleeve_day"], r["ticker"], r["family"], r["entry_rank"]): r
            for r in judged_rows(control_rows)}
    keep_days = set(survivors)
    tables = []
    for t in (0, 1, 2):
        sel = []
        for r in judged_rows(arm_rows):
            if r["sleeve_day"] not in keep_days:
                continue
            key = member_key(r["sleeve_day"], r["ticker"], r["family"], r["entry_rank"])
            if niche_by_key.get(key, -1) != t:
                continue
            c = ctrl.get(key)
            if c is None:
                continue
            sel.append({
                "sleeve_day": r["sleeve_day"], "ticker": r["ticker"], "family": r["family"],
                "entry_rank": r["entry_rank"], "n": r.get("n"),
                "delta": r["delta"], "inc": r["delta"] - c["delta"], "early_exit": r["early_exit"],
                "avoided": r["avoided"], "destroyed": r["destroyed"],
                "mfe": r.get("forward_mfe_from_exit"), "exit_px": r.get("exit_px"),
                "close_px": r.get("close_px"),
                "c_avoided": c["avoided"], "c_destroyed": c["destroyed"],
                "c_mfe": c.get("forward_mfe_from_exit"), "c_exit_px": c.get("exit_px"),
                "c_close_px": c.get("close_px"),
            })
        kept: dict = {}
        for x in sel:
            k = (x["sleeve_day"], x["ticker"])
            if k not in kept or led._dedup_key(x, True) < led._dedup_key(kept[k], True):
                kept[k] = x

        def giant300(rows_):
            return [x for x in rows_ if x["early_exit"] and x.get("mfe") is not None
                    and x["mfe"] >= 3.0 and x.get("close_px") is not None
                    and x.get("exit_px") is not None and float(x["close_px"]) > float(x["exit_px"])]

        def c_giant300(rows_):
            return [x for x in rows_ if x.get("c_mfe") is not None and x["c_mfe"] >= 3.0
                    and x.get("c_close_px") is not None and x.get("c_exit_px") is not None
                    and float(x["c_close_px"]) > float(x["c_exit_px"])]

        arm_av = float(sum(x["avoided"] for x in sel))
        ctrl_av = float(sum(x["c_avoided"] for x in sel))
        arm_g = float(sum(x["destroyed"] for x in giant300(sel)))
        ctrl_g = float(sum(x["c_destroyed"] for x in c_giant300(sel)))
        tables.append({
            "tercile": t,
            "n_members": len(sel),
            "n_releases": sum(1 for x in sel if x["early_exit"]),
            "arm_net": r6(float(sum(x["delta"] for x in sel))),
            "control_net": r6(float(sum(x["delta"] - x["inc"] for x in sel))),
            "increment_sleeve": r6(float(sum(x["inc"] for x in sel))),
            "increment_dedup": r6(float(sum(x["inc"] for x in kept.values()))),
            "n_paths": len(kept),
            "n_members_dropped_by_dedup": len(sel) - len(kept),
            "arm": {"avoided_dollars": r6(arm_av), "destroyed_dollars": r6(
                float(sum(x["destroyed"] for x in sel))),
                "giant300_dollars": r6(arm_g), "n_giant300": len(giant300(sel))},
            "control": {"avoided_dollars": r6(ctrl_av),
                        "destroyed_dollars": r6(float(sum(x["c_destroyed"] for x in sel))),
                        "giant300_dollars": r6(ctrl_g), "n_giant300": len(c_giant300(sel))},
            "giant300_per_avoided_dollar_arm": r6(arm_g / arm_av) if arm_av > 0 else None,
            "giant300_per_avoided_dollar_control": r6(ctrl_g / ctrl_av) if ctrl_av > 0 else None,
        })
    return tables


def e1b_verdict(by_cell: dict, friction: float) -> dict:
    """The pre-registered closure rule: no tercile positive (dedup) for any score arm in both folds."""
    arms = sorted({v["arm"] for v in by_cell.values()})
    pairs = []
    per_arm = {}
    for arm in arms:
        cells = {v["fold"]: v for v in by_cell.values()
                 if v["arm"] == arm and v["friction_bps"] == friction}
        pos = {f: sorted({t["tercile"] for t in c["terciles"] if (t["increment_dedup"] or 0) > 0})
               for f, c in cells.items()}
        both = sorted(set(pos.get("A", [])) & set(pos.get("B", [])))
        per_arm[arm] = {"positive_terciles_dedup": pos, "positive_in_both_folds": both}
        for t in both:
            pairs.append({"arm": arm, "tercile": t})
    every_bar = [a for a in arms if not a.startswith("ablation_")]
    return {
        "friction_bps": friction,
        "rule": ("E1 (score-based release) is economically closed unless some tercile shows a "
                 "positive dedup increment for a score arm in BOTH folds"),
        "per_arm": per_arm,
        "terciles_positive_in_both_folds": pairs,
        "arms_with_a_positive_tercile_in_both_folds": sorted({p["arm"] for p in pairs}),
        "every_bar_arms_with_a_positive_tercile_in_both_folds":
            sorted({p["arm"] for p in pairs if p["arm"] in every_bar}),
        "every_bar_arms": every_bar,
        "closed": bool(not pairs),
        "closed_for_every_bar_arms": bool(not [p for p in pairs if p["arm"] in every_bar]),
    }


def build_cell(arm: str, role: str, variant: str, fold: str, bps: float, train_block: str,
               eval_block: str, rows: list, agg: dict, control_day_sums: dict,
               control_cells: dict, extra: "dict | None" = None) -> dict:
    """One (arm, fold, friction) cell: ledger numbers, CIs, control comparison, kill verdict."""
    dst = delta_stats(rows)
    tail = tail_stats(rows)
    survivors = dst.get("surviving_days", [])
    kept = [r for r in judged_rows(rows) if r["sleeve_day"] in set(survivors)]
    tail_removed = tail_stats(kept)
    control_basis = {name: same_basis(control_day_sums[name], survivors) for name in CONTROLS}
    binding = control_basis[CONTROL_KEY]
    con_ratio = binding["per_avoided_dollar"]
    arm_ratio = tail_removed["giant300"]["contract_view"]["per_avoided_dollar"]
    arm_g300 = tail_removed["giant300"]["contract_view"]["destroyed_dollars"]
    tail_ok, tail_reason = tail_test(arm_g300, tail_removed["avoided_dollars"], con_ratio)
    delta_ok = (dst.get("sum_after_top5_removal") is not None
                and dst["sum_after_top5_removal"] > 0.0)
    kill = bool(not delta_ok or not tail_ok)
    return {
        "arm": arm, "role": role, "variant": variant, "fold": fold, "friction_bps": bps,
        "train_block": train_block, "eval_block": eval_block,
        "friction_k": r6(led.fric_k(bps)),
        "n_members_total": int(agg["n_members_total"]),
        "n_members_judged": int(agg["n_members"]),
        "n_members_unresolved_censored": int(agg["n_members_unresolved_censored"]),
        "n_releases": int(agg["n_early_exits"]),
        "release_share": r6(agg["n_early_exits"] / agg["n_members"]) if agg["n_members"] else None,
        "n_triggered_without_execution": int(sum(
            1 for r in judged_rows(rows) if r.get("trigger_bar_index") is not None
            and not r["early_exit"])),
        "ledger": {
            "failure_tax_avoided": r6(agg["failure_tax_avoided"]),
            "dollars_destroyed": r6(agg["dollars_destroyed"]),
            "net_dollar_ledger": r6(agg["net_dollar_ledger"]),
            "mean_delta": r6(agg["mean_delta"]),
            "n_giants_cut": int(agg["n_giants_cut"]),
            "n_giants_cut_300": int(agg["n_giants_cut_300"]),
            "giant_dollars_destroyed_100": r6(agg["giant_dollars_destroyed_100"]),
            "giant_dollars_destroyed_300": r6(agg["giant_dollars_destroyed_300"]),
            "n_reentry_candidates": int(agg["n_reentry_candidates"]),
            "n_hold_like": int(agg["n_hold_like"]),
            "n_triggered_after_forced_flat": int(agg["n_triggered_after_forced_flat"]),
            "by_family": agg["by_family"],
            "accounting_views": agg["accounting_views"],
            "close_reference_baseline_net": agg["close_reference_baseline"]["net_dollar_ledger"],
            "checks": agg["checks"],
            "reconciliation": reconciliation(agg),
        },
        "delta_vs_hold": {k: v for k, v in dst.items() if k != "surviving_days"},
        "tail": tail,
        "tail_after_top5_removal": tail_removed,
        "control_same_basis": control_basis,
        "control_cells": {name: {
            "n_releases": cc["n_releases"],
            "failure_tax_avoided": cc["ledger"]["failure_tax_avoided"],
            "dollars_destroyed": cc["ledger"]["dollars_destroyed"],
            "net_dollar_ledger": cc["ledger"]["net_dollar_ledger"],
            "giant_dollars_destroyed_300": cc["ledger"]["giant_dollars_destroyed_300"],
            "n_giants_cut_300": cc["ledger"]["n_giants_cut_300"],
            "own_top5_removed": cc["own_top5_removed"]} for name, cc in control_cells.items()},
        "config": extra or {},
        "verdict": {
            "delta_net_after_top5_removal": dst.get("sum_after_top5_removal"),
            "delta_ok": bool(delta_ok),
            "arm_giant300_contract_dollars_after_top5_removal": arm_g300,
            "arm_avoided_dollars_after_top5_removal": tail_removed["avoided_dollars"],
            "giant300_per_avoided_dollar_after_top5_removal": arm_ratio,
            "giant300_per_avoided_dollar_after_top5_removal_ledger_view":
                tail_removed["giant300"]["ledger_view"]["per_avoided_dollar"],
            "binding_control": CONTROL_KEY,
            "binding_control_per_avoided_dollar": con_ratio,
            "binding_control_per_avoided_dollar_ledger_view":
                binding["per_avoided_dollar_ledger_view"],
            "binding_control_own_top5_removed_per_avoided_dollar":
                control_cells[CONTROL_KEY]["own_top5_removed"]["giant300_per_avoided_dollar"],
            "tail_ok": bool(tail_ok),
            "tail_reason": tail_reason,
            "kill": kill,
            "killed_by": ([] if not kill else
                          [k for k, ok in (("delta_net<=hold", delta_ok),
                                           ("tail_not_below_control", tail_ok)) if not ok]),
        },
    }


def control_cell(name: str, fold: str, bps: float, train_block: str, eval_block: str,
                 rows: list, agg: dict) -> dict:
    """A fixed ruler's own cell (no kill verdict: it is the reference, not a candidate)."""
    dst = delta_stats(rows)
    survivors = set(dst.get("surviving_days", []))
    tail_removed = tail_stats([r for r in judged_rows(rows) if r["sleeve_day"] in survivors])
    return {
        "arm": name, "role": "control", "variant": "control", "fold": fold, "friction_bps": bps,
        "train_block": train_block, "eval_block": eval_block, "friction_k": r6(led.fric_k(bps)),
        "n_members_total": int(agg["n_members_total"]),
        "n_members_judged": int(agg["n_members"]),
        "n_members_unresolved_censored": int(agg["n_members_unresolved_censored"]),
        "n_releases": int(agg["n_early_exits"]),
        "release_share": r6(agg["n_early_exits"] / agg["n_members"]) if agg["n_members"] else None,
        "n_triggered_without_execution": int(sum(
            1 for r in judged_rows(rows) if r.get("trigger_bar_index") is not None
            and not r["early_exit"])),
        "ledger": {
            "failure_tax_avoided": r6(agg["failure_tax_avoided"]),
            "dollars_destroyed": r6(agg["dollars_destroyed"]),
            "net_dollar_ledger": r6(agg["net_dollar_ledger"]),
            "mean_delta": r6(agg["mean_delta"]),
            "n_giants_cut": int(agg["n_giants_cut"]),
            "n_giants_cut_300": int(agg["n_giants_cut_300"]),
            "giant_dollars_destroyed_100": r6(agg["giant_dollars_destroyed_100"]),
            "giant_dollars_destroyed_300": r6(agg["giant_dollars_destroyed_300"]),
            "n_reentry_candidates": int(agg["n_reentry_candidates"]),
            "n_hold_like": int(agg["n_hold_like"]),
            "n_triggered_after_forced_flat": int(agg["n_triggered_after_forced_flat"]),
            "by_family": agg["by_family"],
            "accounting_views": agg["accounting_views"],
            "close_reference_baseline_net": agg["close_reference_baseline"]["net_dollar_ledger"],
            "checks": agg["checks"],
            "reconciliation": reconciliation(agg),
        },
        "delta_vs_hold": {k: v for k, v in dst.items() if k != "surviving_days"},
        "tail": tail_stats(rows),
        "tail_after_top5_removal": tail_removed,
        "own_top5_removed": {
            "net_after_top5_removal": dst.get("sum_after_top5_removal"),
            "avoided_dollars": tail_removed["avoided_dollars"],
            "giant300_contract_dollars":
                tail_removed["giant300"]["contract_view"]["destroyed_dollars"],
            "giant300_per_avoided_dollar":
                tail_removed["giant300"]["contract_view"]["per_avoided_dollar"],
        },
        "control_same_basis": None,
        "config": {},
        "verdict": None,
        "note": ("control: a fixed ruler, not a score arm — no kill verdict is applied; these "
                 "numbers are the reference the score arms are judged against"),
    }


def summarise_arm(arm: str, role: str, variant: str, quantile, cells: dict,
                  attribution_by_fold: dict) -> dict:
    ids = sorted(cells)
    killed = [c for c in ids if cells[c]["verdict"]["kill"]]
    killed_folds = sorted({cells[c]["fold"] for c in killed})
    return {
        "arm": arm, "role": role, "variant": variant, "quantile": quantile,
        "cells": ids,
        "verdict": "KILLED" if killed else "SURVIVES",
        "killed_in_cells": killed,
        "killed_in_folds": killed_folds,
        "cell_headline": {c: {"fold": cells[c]["fold"], "friction_bps": cells[c]["friction_bps"],
                              "n_releases": cells[c]["n_releases"],
                              "net_dollar_ledger": cells[c]["ledger"]["net_dollar_ledger"],
                              "net_after_top5_removal":
                                  cells[c]["verdict"]["delta_net_after_top5_removal"],
                              "giant300_per_avoided_dollar_after_top5_removal":
                                  cells[c]["verdict"][
                                      "giant300_per_avoided_dollar_after_top5_removal"],
                              "binding_control_per_avoided_dollar":
                                  cells[c]["verdict"]["binding_control_per_avoided_dollar"],
                              "kill": cells[c]["verdict"]["kill"]}
                          for c in ids},
        "attribution": attribution_by_fold,
    }


# ======================================================================================
# selftest
# ======================================================================================
def _case(name: str, description: str, expected: dict, actual: dict) -> dict:
    def close(a, b):
        if isinstance(b, (list, tuple)):
            return (isinstance(a, (list, tuple)) and len(a) == len(b)
                    and all(close(x, y) for x, y in zip(a, b)))
        if a is None or b is None:
            return a is b or (a is None and b is None)
        if isinstance(b, str) or isinstance(a, str):
            return a == b
        return abs(float(a) - float(b)) <= 1e-9

    ok = True
    detail = {}
    for k, v in expected.items():
        got = actual.get(k)
        good = (got == v) if isinstance(v, (bool, int, str)) or v is None else close(got, v)
        detail[k] = {"expected": v, "actual": got, "ok": bool(good)}
        ok = ok and good
    return {"name": name, "description": description, "passed": bool(ok), "detail": detail}


def run_selftest() -> int:
    """Unit + integration checks with hand-computed expectations (no panel needed)."""
    results = []

    # -- (1) the every-bar label ----------------------------------------------------------
    mfe = np.array([0.0, 1.0, 1.0, 3.0, 3.0, 2.0])
    mid = np.array([0, 0, 0, 1, 1, 1])
    off = np.array([0, 3, 6])
    lab = final_high_now(mfe, mid, off)
    results.append(_case(
        "final_high_now/definition",
        "1 exactly where the running high equals the tape's final running high (ties included)",
        {"labels": [0.0, 1.0, 1.0, 0.0, 0.0, 1.0]},
        {"labels": [float(x) for x in lab]}))

    # -- (1b) the inner day-level split ---------------------------------------------------
    days = ["2021-02-03", "2021-02-01", "2021-02-02", "2021-02-04", "2021-02-05"]
    m = day_half_map(days)
    results.append(_case(
        "day_half_map/deterministic_day_split",
        "sorted unique days alternate between the halves; every day lands in exactly one half and "
        "the map is order-independent",
        {"sorted_half": [0, 1, 0, 1, 0], "reversed_half": [0, 1, 0, 1, 0],
         "n_days": 5, "n_half0": 3, "n_half1": 2},
        {"sorted_half": [m[d] for d in sorted(days)],
         "reversed_half": [day_half_map(list(reversed(days)))[d] for d in sorted(days)],
         "n_days": len(m), "n_half0": sum(1 for v in m.values() if v == 0),
         "n_half1": sum(1 for v in m.values() if v == 1)}))

    # -- (2) per-minute quantiles with the thin-minute fallback ---------------------------
    vals = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 100.0, 200.0])
    minute = np.array([0, 0, 0, 0, 0, 1, 1], dtype=np.int64)
    got, pooled, thin = per_minute_quantiles(vals, minute, 3, (0.5,), min_obs=3)
    results.append(_case(
        "per_minute_quantiles/thin_fallback",
        "a minute below min_obs falls back to the pooled quantile, a thick minute does not",
        {"thick_minute": 3.0, "thin_minute_fallback": 4.0, "pooled": 4.0,
         "fallback_minutes": [1, 2], "n_fallback": 2},
        {"thick_minute": float(got[0, 0]), "thin_minute_fallback": float(got[0, 1]),
         "pooled": float(pooled[0]), "fallback_minutes": [m - fall.FIRST_ET for m in thin],
         "n_fallback": len(thin)}))
    got2, _, thin2 = per_minute_quantiles(vals, minute, 3, (0.5,), min_obs=2)
    results.append(_case(
        "per_minute_quantiles/no_fallback_when_thick",
        "with min_obs=2 the two-observation minute uses its own quantile",
        {"minute_1": 150.0, "n_fallback": 1},
        {"minute_1": float(got2[0, 1]), "n_fallback": len(thin2)}))

    # -- (3) first crossing --------------------------------------------------------------
    mask = np.array([False, False, True, False, True, False, False], dtype=bool)
    fc = first_crossing(mask, np.array([0, 3, 7], dtype=np.int64))
    results.append(_case("first_crossing", "first qualifying index per member, -1 when none",
                         {"member0": 2, "member1": 1},
                         {"member0": int(fc[0]), "member1": int(fc[1])}))

    # -- (4) top-5-day removal and the delta CI ------------------------------------------
    rows = []
    for d, v in (("d1", 10.0), ("d2", -9.0), ("d3", 1.0), ("d4", 1.0), ("d5", 1.0), ("d6", 1.0),
                 ("d7", 1.0)):
        rows.append({"sleeve_day": d, "delta": v, "avoided": max(v, 0.0),
                     "destroyed": max(-v, 0.0), "early_exit": True, "forward_mfe_from_exit": None,
                     "close_px": None, "exit_px": None})
    dst = delta_stats(rows, reps=50)
    results.append(_case(
        "delta_stats/top5_removal",
        "the five days with the largest |day net| are removed and the remainder is summed",
        {"n_members": 7, "sum": 6.0, "top5": ["d1", "d2", "d3", "d4", "d5"],
         "sum_after_removal": 2.0, "n_after_removal": 2, "n_days_after_removal": 2},
        {"n_members": dst["n_members"], "sum": dst["sum"],
         "top5": [d["day"] for d in dst["top5_days_by_abs_net"]],
         "sum_after_removal": dst["sum_after_top5_removal"],
         "n_after_removal": dst["n_members_after_removal"],
         "n_days_after_removal": dst["n_days_after_removal"]}))

    # -- (5) the contract's destroyed-giant predicate ------------------------------------
    def row(delta, mfe_v, exit_px, close_px):
        return {"sleeve_day": "d1", "ticker": "T", "family": "A_pm", "entry_rank": 1,
                "delta": delta, "avoided": max(delta, 0.0), "destroyed": max(-delta, 0.0),
                "early_exit": True, "forward_mfe_from_exit": mfe_v, "exit_px": exit_px,
                "close_px": close_px, "trigger_et": 600, "exit_et": 601}
    ts = tail_stats([row(-0.5, 4.0, 10.0, 12.0), row(-0.2, 3.5, 10.0, 9.0),
                     row(-0.1, 1.5, 10.0, 11.0), row(0.3, 5.0, 10.0, 20.0)])
    results.append(_case(
        "tail_stats/giant_definitions",
        "ledger view (destroyed>0 and MFE>=level) beside the contract view (early exit, MFE>=level "
        "and close>release; a member whose flat beat the release counts as cut with no destroyed "
        "dollars)",
        {"giant100_ledger_n": 3, "giant100_contract_n": 3, "giant300_ledger_n": 2,
         "giant300_contract_n": 2, "giant300_ledger_dollars": 0.7,
         "giant300_contract_dollars": 0.5, "giant300_ledger_per_avoided": 2.333333,
         "giant300_contract_per_avoided": 1.666667, "avoided": 0.3},
        {"giant100_ledger_n": ts["giant100"]["ledger_view"]["n_cut"],
         "giant100_contract_n": ts["giant100"]["contract_view"]["n_cut"],
         "giant300_ledger_n": ts["giant300"]["ledger_view"]["n_cut"],
         "giant300_contract_n": ts["giant300"]["contract_view"]["n_cut"],
         "giant300_ledger_dollars": ts["giant300"]["ledger_view"]["destroyed_dollars"],
         "giant300_ledger_per_avoided": ts["giant300"]["ledger_view"]["per_avoided_dollar"],
         "giant300_contract_dollars": ts["giant300"]["contract_view"]["destroyed_dollars"],
         "giant300_contract_per_avoided": ts["giant300"]["contract_view"]["per_avoided_dollar"],
         "avoided": ts["avoided_dollars"]}))
    results.append(_case(
        "tail_stats/no_avoided_dollars",
        "a rule that avoids nothing has no tail ratio (never a fabricated zero)",
        {"ratio": None, "avoided": 0.0},
        {"ratio": tail_stats([row(-0.5, 4.0, 10.0, 20.0)])["giant300"]["contract_view"][
            "per_avoided_dollar"],
         "avoided": tail_stats([row(-0.5, 4.0, 10.0, 20.0)])["avoided_dollars"]}))

    # -- (6) the tail clause of the kill criterion ---------------------------------------
    cases = {
        "no_giants_passes": tail_test(0.0, 10.0, 0.05),
        "arm_worse_fails": tail_test(1.0, 10.0, 0.05),
        "arm_better_passes": tail_test(0.2, 10.0, 0.05),
        "no_avoided_fails": tail_test(0.0, 0.0, 0.05),
        "zero_control_ratio_blocks_a_destroyer": tail_test(0.5, 10.0, 0.0),
        "no_control_reference_passes": tail_test(0.5, 10.0, None),
    }
    results.append(_case(
        "tail_test/pre_registered_clause",
        "nothing destroyed passes; a worse per-avoided-dollar tail fails; no avoided dollars fails; "
        "a control with no reference cannot kill",
        {"no_giants_passes": True, "arm_worse_fails": False, "arm_better_passes": True,
         "no_avoided_fails": False, "zero_control_ratio_blocks_a_destroyer": False,
         "no_control_reference_passes": True},
        {k: bool(v[0]) for k, v in cases.items()}))

    # -- (7) the dollar grid: objective and tie-break ------------------------------------
    curve = [{"quantile": 0.70, "train_net_after_top5_removal": 4.0, "n_releases": 90},
             {"quantile": 0.80, "train_net_after_top5_removal": 7.0, "n_releases": 70},
             {"quantile": 0.90, "train_net_after_top5_removal": 7.0, "n_releases": 40},
             {"quantile": 0.95, "train_net_after_top5_removal": 7.0, "n_releases": 40},
             {"quantile": 0.99, "train_net_after_top5_removal": 1.0, "n_releases": 5}]
    chosen = choose_grid_point(curve)
    results.append(_case(
        "choose_grid_point/objective_and_tiebreak",
        "the grid picks the largest train net and, on a tie, the fewest releases (then the lowest "
        "quantile, deterministically)",
        {"quantile": 0.90, "net": 7.0, "n_releases": 40},
        {"quantile": chosen["quantile"], "net": chosen["train_net_after_top5_removal"],
         "n_releases": chosen["n_releases"]}))

    # -- (8) end-to-end: the release rule priced by the ledger ---------------------------
    bars = [(570, 10.0, 10.2, 9.9, 10.1),
            (571, 10.1, 10.3, 10.0, 10.2),
            (572, 10.5, 10.6, 10.4, 10.5),
            (573, 10.6, 10.8, 10.5, 10.7),
            (574, 10.7, 11.0, 10.6, 10.9)]
    members = [{"sleeve_day": "2021-02-01", "ticker": "AAA", "family": "A_pm", "entry_rank": 1,
                "entry_px": 10.0, "bars": bars},
               {"sleeve_day": "2021-02-01", "ticker": "BBB", "family": "A_pm", "entry_rank": 2,
                "entry_px": 10.0, "bars": bars},
               {"sleeve_day": "2021-02-01", "ticker": "CCC", "family": "A_pm", "entry_rank": 3,
                "entry_px": 10.0, "bars": bars},
               {"sleeve_day": "2021-02-01", "ticker": "CEN", "family": "A_pm", "entry_rank": 4,
                "entry_px": 10.0, "bars": bars[:4], "censored": True, "session_end": 574}]
    df = led.synth_frame(members)
    k = led.fric_k(100.0)
    rule = ScoreRelease(name="score:selftest", kind="scan", triggers={
        ("2021-02-01", "AAA", "A_pm", 1): 1,   # exits at bar 2's open (10.5)
        ("2021-02-01", "BBB", "A_pm", 2): 3,   # et 573 == session_end-1 -> preempted, delta 0
        ("2021-02-01", "CEN", "A_pm", 4): 1,   # censored tape -> observation only, no delta
    })
    replayed = {}
    for key, sub in led.members_of(df):
        replayed[key[3]] = led.replay_member(sub, rule, k)
    a, b, c = replayed["AAA"], replayed["BBB"], replayed["CEN"]
    gross_rule, gross_hold = 10.5 / 10.0 - 1.0, 10.7 / 10.0 - 1.0
    results.append(_case(
        "replay/score_release_priced_by_the_ledger",
        "a release at bar 1 executes at bar 2's open; delta is the ledger's k*(rule-hold)",
        {"status": "early_exit", "exit_et": 572, "exit_px": 10.5, "gross_rule": gross_rule,
         "gross_hold": gross_hold, "delta": k * (gross_rule - gross_hold), "early_exit": True},
        {"status": a["status"], "exit_et": a["exit_et"], "exit_px": a["exit_px"],
         "gross_rule": a["gross_rule"], "gross_hold": a["gross_hold"], "delta": a["delta"],
         "early_exit": a["early_exit"]}))
    results.append(_case(
        "replay/forced_flat_precedence",
        "a trigger on the last completed bar is preempted by the forced flat (delta 0, no release)",
        {"status": "hold_equivalent", "delta": 0.0, "early_exit": False,
         "triggered_after_forced_flat": True},
        {"status": b["status"], "delta": b["delta"], "early_exit": b["early_exit"],
         "triggered_after_forced_flat": b.get("triggered_after_forced_flat")}))
    results.append(_case(
        "replay/censored_tape_unresolved",
        "a release on a censored tape is an observation: the delta stays unresolved",
        {"status": "unresolved_terminal_censored", "delta": None, "early_exit": True,
         "observed_rule_side_net": (1.0 + (10.5 / 10.0 - 1.0)) * k - 1.0},
        {"status": c["status"], "delta": c["delta"], "early_exit": c["early_exit"],
         "observed_rule_side_net": c.get("observed_rule_side_net")}))
    hold = led.parse_rule("hold_flat", led.load_registry())
    agg = led.aggregate_block([led.replay_member(sub, hold, k) for _, sub in led.members_of(df)],
                              "selftest")
    results.append(_case(
        "aggregate/hold_is_the_zero_baseline",
        "hold_flat nets exactly zero and leaves every member judged",
        {"net": 0.0, "n_judged": 3, "n_censored": 1},
        {"net": agg["net_dollar_ledger"], "n_judged": agg["n_members"],
         "n_censored": agg["n_members_unresolved_censored"]}))

    # -- (9) threshold -> trigger integration --------------------------------------------
    class _MiniFrame:
        pass
    mf = _MiniFrame()
    mf.et = np.array([570, 571, 572] * 3, dtype=np.int64)
    mf.bar_index = np.array([0, 1, 2] * 3, dtype=np.int64)
    mf.off = np.array([0, 3, 6, 9], dtype=np.int64)
    mf.mid = np.repeat(np.array([0, 1, 2], dtype=np.int64), 3)
    mf.block = np.array(["block1"] * 9, dtype=object)
    mf.x = {"bars_since_new_high": np.array([0.0, 1.0, 0.0, 5.0, 0.0, 2.0, 1.0, 0.0, 0.0])}
    mf.n = 9
    masks = {"complete": np.ones(9, dtype=bool),
             "state": np.array([False, True, True, False, True, True, False, True, True]),
             "event": mf.x["bars_since_new_high"] == 0}
    score = np.array([0.1, 0.9, 0.2, 0.3, 0.95, 0.1, 0.7, 0.1, 0.8])
    trig = channel_release(mf, masks, score, np.array([0.5, 0.5, 0.5]), "block1", DECISION_STATE)
    trig_event = channel_release(mf, masks, score, np.array([0.5, 0.5, 0.5]), "block1",
                                 DECISION_EVENT)
    trig_scalar = channel_release(mf, masks, score, 0.85, "block1", DECISION_STATE)
    trig_none = channel_release(mf, masks, score, 0.99, "block1", DECISION_STATE)
    results.append(_case(
        "channel_release/first_crossing_of_the_threshold",
        "state decisions skip bars before the first new high; a scalar threshold applies at every "
        "clock minute; no crossing is -1",
        {"state_member0": 1, "state_member1": 1, "state_member2": 2,
         "event_member0": -1, "event_member1": 1, "event_member2": 2,
         "scalar_threshold_member1": 1, "no_crossing": -1},
        {"state_member0": int(trig[0]), "state_member1": int(trig[1]),
         "state_member2": int(trig[2]), "event_member0": int(trig_event[0]),
         "event_member1": int(trig_event[1]), "event_member2": int(trig_event[2]),
         "scalar_threshold_member1": int(trig_scalar[1]), "no_crossing": int(trig_none[0])}))

    # -- (10) E1b: the niche decomposition ------------------------------------------------
    def lrow(day, ticker, family, rank, delta, mfe=None, exit_px=None, close_px=None,
             early=True, avoided=None, destroyed=None):
        return {"sleeve_day": day, "ticker": ticker, "family": family, "entry_rank": rank,
                "delta": delta, "avoided": avoided if avoided is not None else max(delta, 0.0),
                "destroyed": destroyed if destroyed is not None else max(-delta, 0.0),
                "early_exit": early, "forward_mfe_from_exit": mfe, "exit_px": exit_px,
                "close_px": close_px}
    arm_rows = [
        lrow("d1", "AAA", "A_pm", 1, 1.0, mfe=4.0, exit_px=10.0, close_px=20.0),   # niche 0
        lrow("d1", "BBB", "A_pm", 2, 2.0, mfe=0.5, exit_px=10.0, close_px=9.0),    # niche 1
        lrow("d2", "CCC", "B600", 1, -1.0, mfe=None, exit_px=None, close_px=None),  # niche 0
        lrow("d3", "DDD", "A_pm", 1, 5.0, mfe=3.5, exit_px=10.0, close_px=12.0),   # dropped day
        lrow("d4", "EEE", "A_pm", 1, 0.5, mfe=None, exit_px=None, close_px=None),  # niche 2
    ]
    ctrl_rows = [
        lrow("d1", "AAA", "A_pm", 1, 0.5, early=False),
        lrow("d1", "BBB", "A_pm", 2, 3.0, early=False),
        lrow("d2", "CCC", "B600", 1, -2.0, early=False),
        lrow("d3", "DDD", "A_pm", 1, 0.0, early=False),
        lrow("d4", "EEE", "A_pm", 1, 0.25, early=False),
    ]
    # a duplicate path (same day, ticker, two sleeves) to exercise the dedup view
    arm_rows.append(lrow("d5", "FFF", "B600", 1, 1.0, mfe=None, exit_px=None, close_px=None))
    ctrl_rows.append(lrow("d5", "FFF", "B600", 1, -1.0, early=False))
    arm_rows.append({"sleeve_day": "d5", "ticker": "FFF", "family": "A_pm", "entry_rank": 2,
                     "delta": 3.0, "avoided": 3.0, "destroyed": 0.0, "early_exit": True,
                     "forward_mfe_from_exit": None, "exit_px": None, "close_px": None})
    ctrl_rows.append(lrow("d5", "FFF", "A_pm", 2, -1.0, early=False))
    niche = {"d1|AAA|A_pm|1": 0, "d1|BBB|A_pm|2": 1, "d2|CCC|B600|1": 0, "d3|DDD|A_pm|1": 0,
             "d4|EEE|A_pm|1": 2, "d5|FFF|A_pm|2": 1, "d5|FFF|B600|1": 1}
    tabs = {t["tercile"]: t for t in niche_increments(arm_rows, ctrl_rows, niche, ["d1", "d2", "d4",
                                                                                  "d5"])}
    results.append(_case(
        "e1b/niche_increments",
        "per-tercile arm-minus-control increments on the arm's surviving days, sleeve and dedup "
        "views, with the contract giant set",
        {"t0_n": 2, "t0_sleeve": 1.5, "t0_control_net": -1.5, "t0_arm_net": 0.0,
         "t1_n": 3, "t1_sleeve": 5.0, "t1_dedup": 3.0, "t1_dropped": 1,
         "t2_n": 1, "t2_sleeve": 0.25,
         "t0_giant300_arm_n": 1, "t0_giant300_arm_dollars": 0.0},
        {"t0_n": tabs[0]["n_members"], "t0_sleeve": tabs[0]["increment_sleeve"],
         "t0_control_net": tabs[0]["control_net"], "t0_arm_net": tabs[0]["arm_net"],
         "t1_n": tabs[1]["n_members"], "t1_sleeve": tabs[1]["increment_sleeve"],
         "t1_dedup": tabs[1]["increment_dedup"],
         "t1_dropped": tabs[1]["n_members_dropped_by_dedup"], "t2_n": tabs[2]["n_members"],
         "t2_sleeve": tabs[2]["increment_sleeve"],
         "t0_giant300_arm_n": tabs[0]["arm"]["n_giant300"],
         "t0_giant300_arm_dollars": tabs[0]["arm"]["giant300_dollars"]}))

    # -- (11) E1b: the closure rule --------------------------------------------------------
    def fake_cell(arm, fold, incs, bps=100.0):
        return {"arm": arm, "fold": fold, "friction_bps": bps,
                "terciles": [{"tercile": i, "increment_dedup": v} for i, v in enumerate(incs)]}
    mixed = {"x|A|100": fake_cell("quantile_q70", "A", [1.0, -1.0, -1.0]),
             "x|B|100": fake_cell("quantile_q70", "B", [0.5, -1.0, -1.0]),
             "y|A|100": fake_cell("ablation_event_hazard_quantile_q70", "A", [-1.0, 2.0, -1.0]),
             "y|B|100": fake_cell("ablation_event_hazard_quantile_q70", "B", [-1.0, -2.0, -1.0])}
    closed = {"x|A|100": fake_cell("quantile_q70", "A", [1.0, -1.0, -1.0]),
              "x|B|100": fake_cell("quantile_q70", "B", [-1.0, -1.0, -1.0])}
    v_mixed, v_closed = e1b_verdict(mixed, 100.0), e1b_verdict(closed, 100.0)
    results.append(_case(
        "e1b/closure_rule",
        "a tercile must be positive in BOTH folds for the arm; the closure statement covers all "
        "score arms and is reported separately for the every-bar arms",
        {"mixed_pairs": ["quantile_q70|0"], "mixed_closed": False,
         "mixed_every_bar_closed": False, "closed_pairs": [], "closed": True,
         "closed_every_bar": True},
        {"mixed_pairs": [f"{p['arm']}|{p['tercile']}"
                         for p in v_mixed["terciles_positive_in_both_folds"]],
         "mixed_closed": v_mixed["closed"],
         "mixed_every_bar_closed": v_mixed["closed_for_every_bar_arms"],
         "closed_pairs": v_closed["terciles_positive_in_both_folds"], "closed": v_closed["closed"],
         "closed_every_bar": v_closed["closed_for_every_bar_arms"]}))

    n_fail = sum(1 for r in results if not r["passed"])
    doc = {"producer": PRODUCER, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "n_cases": len(results), "n_failed": n_fail,
           "status": "PASS" if n_fail == 0 else "FAIL", "cases": results}
    write_json(SELFTEST_OUT, doc)
    for r in results:
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['name']}")
        if not r["passed"]:
            print(f"        {json.dumps(r['detail'])}")
    print(f"selftest {doc['status']}: {len(results) - n_fail}/{len(results)} cases "
          f"-> {SELFTEST_OUT}")
    return 0 if n_fail == 0 else 1


# ======================================================================================
# main
# ======================================================================================
def member_subsample(frame, frac: float) -> np.ndarray:
    """Deterministic per-block member subsample (smoke mode only; the deliverable run is 1.0)."""
    keep = np.ones(frame.n_members, dtype=bool)
    if frac >= 1.0:
        return keep
    keep[:] = False
    step = max(1, int(round(1.0 / frac)))
    for blk in BLOCKS:
        ids = np.flatnonzero(frame.m_block == blk)
        keep[ids[::step]] = True
    return keep


def panel_block_map(panel: Path) -> tuple:
    """Member keys (in frame order) + the frozen block_of verification + day census."""
    from basket_atlas_panel import block_of  # the producer that froze the boundaries
    tbl = (pl.scan_parquet(panel).filter(pl.col("bar_index") == 0)
           .select("sleeve_day", "family", "ticker", "entry_rank", "month", "block", "et")
           .collect())
    days = tbl["sleeve_day"].to_numpy()
    blocks = tbl["block"].to_numpy()
    expected = np.array([block_of(str(d)) for d in days], dtype=object)
    mismatch = int((blocks != expected).sum())
    day_block: dict = {}
    for d, b in zip(days.tolist(), blocks.tolist()):
        day_block[d] = b
    return tbl, {"panel_blocks_equal_block_of": bool(mismatch == 0),
                 "block_of_mismatch_members": mismatch,
                 "days_per_block": {b: int(sum(1 for x in day_block.values() if x == b))
                                    for b in dict.fromkeys(blocks.tolist())},
                 "block_boundaries": {"block1": "2021-02..2023-12", "block2": "2025-02..2026-05"}}


def learn_dollar_threshold(frame, masks: dict, score: np.ndarray, df_train, keys: list,
                           train_block: str, grid: tuple, bps: float,
                           member_keep: "np.ndarray | None") -> dict:
    """The primary arm's threshold: a grid over the train score distribution, scored in dollars.

    Objective = train-block net (delta vs hold) at `bps` after removing the five largest-|day net|
    days; ties are broken toward fewer releases, then toward the lower quantile (deterministic).
    """
    pool_mask = masks["state"] & (frame.block == train_block)
    if member_keep is not None:
        pool_mask = pool_mask & member_keep[frame.mid]
    pool = score[pool_mask]
    curve = []
    for q in grid:
        thr = float(np.quantile(pool, q))
        bars = channel_release(frame, masks, score, thr, train_block, DECISION_STATE, member_keep)
        triggers = triggers_of(keys, bars)
        rule = ScoreRelease(name=f"score:grid:{quantile_tag(q)}", kind="scan", triggers=triggers)
        rows = replay_rows(df_train, rule, bps)
        dst = delta_stats(rows)
        curve.append({"quantile": q, "threshold": r6(thr), "n_releases": len(triggers),
                      "train_net": r6(dst.get("sum")),
                      "train_net_after_top5_removal": dst.get("sum_after_top5_removal")})
    chosen = choose_grid_point(curve)
    return {"objective_bps": bps, "pool": "train decision population (complete & uncensored bars "
                                          "with a new high already seen), cross-fitted scores",
            "grid_quantiles": list(grid), "n_grid_points": len(curve),
            "train_block": train_block, "n_pool": int(pool.size),
            "curve": curve, "chosen": chosen,
            "optimum_at_grid_boundary": {
                "lower": bool(chosen["quantile"] == grid[0]),
                "upper": bool(chosen["quantile"] == grid[-1]),
                "note": ("a binding boundary means the train objective is still improving at the "
                         "edge of the declared grid; the grid itself is part of the frozen arm "
                         "definition and is not extended here")}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ATLAS E1a exhaustion-score release test")
    ap.add_argument("--panel", type=Path, default=PANEL)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--smoke-frac", type=float, default=1.0,
                    help="deterministic member subsample for a pipeline smoke run (default: full)")
    ap.add_argument("--bps", type=float, nargs="+", default=list(FRICTIONS))
    ap.add_argument("--grid-step", type=float, default=0.01)
    ap.add_argument("--allow-panel-sha-mismatch", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()

    t0 = time.time()
    grid = tuple(round(0.50 + args.grid_step * i, 4) for i in range(
        int(round((0.99 - 0.50) / args.grid_step)) + 1))
    panel = Path(args.panel).resolve()
    failures: list[str] = []
    if not panel.exists():
        print(f"panel absent: {panel}", file=sys.stderr)
        return 3
    panel_sha = sha256_of(panel)
    if panel_sha != PANEL_SHA256 and not args.allow_panel_sha_mismatch:
        print(f"panel sha mismatch: {panel_sha} != {PANEL_SHA256}", file=sys.stderr)
        return 4
    schema_cols = list(pl.read_parquet_schema(panel))

    print(f"[e1] panel {panel.name} sha {panel_sha[:12]}… loading", flush=True)
    frame, checks = fall.load_frame(panel)
    if frame.n != PANEL_ROWS_EXPECTED:
        failures.append(f"panel rows {frame.n} != {PANEL_ROWS_EXPECTED}")
    if frame.n_members != PANEL_MEMBERS_EXPECTED:
        failures.append(f"panel members {frame.n_members} != {PANEL_MEMBERS_EXPECTED}")
    member_keep = member_subsample(frame, args.smoke_frac)
    row_keep = member_keep[frame.mid]

    guard = fall.registry_guard(fall.MODEL_FEATURES, schema_cols)
    if not guard["guard_passes"]:
        failures.append(f"causal guard failed: {guard['violations']}")

    masks = build_masks(frame)
    # the every-bar label must be the panel's own final_high_flag wherever the panel defines it
    defined = masks["complete"] & frame.target_ok
    label_mismatch = int((masks["label_now"][defined] != frame.final_high[defined]).sum())
    if label_mismatch != 0:
        failures.append(f"final_high_now disagrees with the panel's final_high_flag on "
                        f"{label_mismatch} rows")
    state_rows = int(masks["state"].sum())
    event_rows = int(masks["event"].sum())
    if args.smoke_frac >= 1.0:
        if state_rows != STATE_ROWS_EXPECTED:
            failures.append(f"state rows {state_rows} != {STATE_ROWS_EXPECTED}")
        if event_rows != EVENT_ROWS_EXPECTED:
            failures.append(f"event rows {event_rows} != {EVENT_ROWS_EXPECTED}")

    tbl, block_map = panel_block_map(panel)
    if not block_map["panel_blocks_equal_block_of"]:
        failures.append("panel block column disagrees with basket_atlas_panel.block_of")
    order_ok = (tbl.height == frame.n_members
                and np.array_equal(tbl["family"].to_numpy().astype(object), frame.m_family)
                and np.array_equal(tbl["et"].to_numpy().astype(np.int64), frame.m_entry_et)
                and np.array_equal(tbl["block"].to_numpy().astype(object), frame.m_block))
    if not order_ok:
        failures.append("member key table is not in frame member order (trigger keys unsafe)")
    contig = bool(np.all(frame.bar_index[frame.off[:-1]] == 0)
                  and np.all(frame.bar_index[frame.off[1:] - 1] == frame.sizes - 1))
    if not contig:
        failures.append("bar_index is not contiguous from 0 within members")
    keys = [(str(d), str(t), str(f), int(rk)) for d, f, t, rk in
            zip(tbl["sleeve_day"].to_list(), tbl["family"].to_list(), tbl["ticker"].to_list(),
                tbl["entry_rank"].to_list())]

    print(f"[e1] members {frame.n_members} rows {frame.n} complete {int(masks['complete'].sum())} "
          f"state {state_rows} event {event_rows} ({time.time() - t0:.0f}s)", flush=True)

    # ---- models and cross-fitted scores -------------------------------------------------
    models_now = fit_block_models(frame, masks, "final_high_now", member_keep)
    models_event = fit_block_models(frame, masks, "event_hazard", member_keep)
    score_now, prov_now = cross_fit_scores(frame, masks, models_now, "final_high_now", member_keep)
    score_event, prov_event = cross_fit_scores(frame, masks, models_event, "event_hazard",
                                               member_keep)
    for tag, models in (("final_high_now", models_now), ("event_hazard", models_event)):
        for blk, m in models.items():
            print(f"[e1] {tag} model {blk}: n_train {m['n_train']} base {m['base_rate_label']:.4f} "
                  f"oos->{m['oos']['eval_block']} pooled {m['oos']['auc_pooled']:.4f} "
                  f"within-clock {m['oos']['auc_within_clock']:.4f}", flush=True)
    for prov in (prov_now, prov_event):
        if any(not v["cross_fitted"] for v in prov.values()):
            failures.append("a block was scored by its own model (cross-fit broken)")

    # ---- inner (train-side) cross-fit: thresholds may not read the evaluation block ---------
    member_day = np.array(tbl["sleeve_day"].to_list(), dtype=object)
    inner: dict = {}
    for letter, train_block, _eval_block in FOLDS:
        for target in TARGETS:
            score_inner, info = inner_cross_fit_scores(frame, masks, target, train_block,
                                                      member_day, member_keep)
            inner[(letter, target)] = {"score": score_inner, "info": info}
            if info["finite_outside_block"] != 0:
                failures.append(f"fold {letter} inner cross-fit ({target}) scored "
                                f"{info['finite_outside_block']} rows outside {train_block}")
        print(f"[e1] fold {letter}: inner train-side cross-fit built "
              f"({inner[(letter, 'final_high_now')]['info']['n_scored']} + "
              f"{inner[(letter, 'event_hazard')]['info']['n_scored']} rows, "
              f"0 outside {train_block}) ({time.time() - t0:.0f}s)", flush=True)

    # ---- threshold banks (all built from the INNER cross-fit) ---------------------------
    thr_bank: dict = {}
    thr_arrays: dict = {}
    for letter, train_block, eval_block in FOLDS:
        variants = (
            ("secondary_ruler", "final_high_now", DECISION_STATE,
             masks["state"] & (frame.block == train_block)),
            ("ablation", "event_hazard", DECISION_EVENT,
             masks["event"] & (frame.block == train_block)),
        )
        for tag, target, decision, pool in variants:
            if args.smoke_frac < 1.0:
                pool = pool & row_keep
            score = inner[(letter, target)]["score"]
            minute = (frame.et[pool] - fall.FIRST_ET).astype(np.int64)
            values = score[pool]
            finite = np.isfinite(values)
            if not finite.all():
                failures.append(f"{letter}/{tag}: non-finite train-side scores in the pool")
            thr, pooled, fallback = per_minute_quantiles(
                values[finite], minute[finite], fall.n_minute_codes(frame), QUANTILES,
                MIN_TRAIN_OBS_PER_MINUTE)
            thr_arrays[(letter, tag)] = thr
            thr_bank[(letter, tag)] = {
                "target": target, "decision_population": decision, "train_block": train_block,
                "n_pool": int(finite.sum()),
                "scored_by": ("inner day-level cross-fit inside the train block (fit on one day "
                              "half, score the other) — no evaluation-block row is read"),
                "eval_block": eval_block,
                "n_eval_rows_in_pool": 0,
                "pooled_quantiles": {quantile_tag(q): r6(pooled[i])
                                     for i, q in enumerate(QUANTILES)},
                "n_minutes_with_fallback": len(fallback),
                "fallback_minutes_et": fallback,
                "threshold_probe": {str(et): {quantile_tag(q): r6(thr[i, et - fall.FIRST_ET])
                                              for i, q in enumerate(QUANTILES)}
                                    for et in (570, 600, 660, 720, 780, 840, 900, 959)},
            }
        print(f"[e1] fold {letter}: threshold banks built from inner cross-fit "
              f"({time.time() - t0:.0f}s)", flush=True)

    # ---- ledger cells -------------------------------------------------------------------
    control_rule = {name: led.parse_rule(name, led.load_registry()) for name in CONTROLS}
    df_all = led.load_panel(panel, control_rule["hold_flat"])
    cells: dict = {}
    cells_by_arm: dict = {}
    attribution_by_arm: dict = {}
    grid_docs: dict = {}
    control_days: dict = {}
    e1b_cells: dict = {}
    e1b_splitter: dict = {}
    disp_label = fall.forward_vol_labels(frame, ks=(DISPERSION_K,))[DISPERSION_TARGET]
    print(f"[e1] dispersion label {DISPERSION_TARGET} ready "
          f"({int(np.isfinite(disp_label).sum())} finite rows) ({time.time() - t0:.0f}s)", flush=True)

    for letter, train_block, eval_block in FOLDS:
        def block_frame(blk: str):
            df = df_all.filter(pl.col("block") == blk)
            if args.smoke_frac < 1.0:
                keep_keys = [keys[i] for i in range(frame.n_members)
                             if frame.m_block[i] == blk and member_keep[i]]
                keep_df = pl.DataFrame(keep_keys,
                                       schema={"sleeve_day": pl.String, "ticker": pl.String,
                                               "family": pl.String, "entry_rank": pl.Int64},
                                       orient="row")
                df = df.join(keep_df, on=["sleeve_day", "ticker", "family", "entry_rank"],
                             how="semi")
            return df

        df_train = block_frame(train_block)
        df_eval = block_frame(eval_block)

        # ---- E1b: the dispersion splitter, frozen before any outcome is read ---------------
        split = dispersion_splitter(frame, masks, disp_label, member_day, letter, train_block,
                                    eval_block, member_keep if args.smoke_frac < 1.0 else None)
        niche = member_niche(frame, masks, split["tercile"])
        niche_by_key = {member_key(*keys[i]): int(niche[i]) for i in range(frame.n_members)}
        in_eval = frame.m_block == eval_block
        e1b_splitter[letter] = {
            **split["diagnostics"],
            "eval_members": int(in_eval.sum()),
            "eval_members_without_a_state_bar": int(((niche < 0) & in_eval).sum()),
            "eval_members_per_tercile": [int(((niche == t) & in_eval).sum()) for t in (0, 1, 2)],
            "train_members_are_never_niche_assigned": True,
        }
        print(f"[e1] fold {letter} dispersion splitter: cuts "
              f"{[round(float(c), 4) for c in split['cuts']]} tercile rows "
              f"{split['diagnostics']['tercile_row_counts']} eval members/tercile "
              f"{e1b_splitter[letter]['eval_members_per_tercile']} "
              f"({e1b_splitter[letter]['eval_members_without_a_state_bar']} without a state bar) "
              f"({time.time() - t0:.0f}s)", flush=True)

        # controls first: their per-day sums are the same-basis reference
        control_cells: dict = {bps: {} for bps in args.bps}
        control_rows: dict = {}
        for bps in args.bps:
            for name, rule in control_rule.items():
                rows = replay_rows(df_eval, rule, bps)
                agg = led.aggregate_block(rows, f"control {name} (ledger machinery)")
                if not agg["checks"]["net_identity"]:
                    failures.append(f"control {name} {letter} {bps}: net identity violated")
                cell = control_cell(name, letter, bps, train_block, eval_block, rows, agg)
                control_cells[bps][name] = cell
                if name == CONTROL_KEY:
                    control_rows[bps] = rows
                control_days[(letter, bps, name)] = day_sums(rows)
                cid = cell_id(name.replace(":", "_"), letter, bps)
                cells[cid] = cell
                cells_by_arm.setdefault(name.replace(":", "_"), []).append(cid)
                if name == CONTROL_KEY:
                    attribution_by_arm.setdefault("giveback_10", {})[letter] = attribution(rows)
            print(f"[e1] fold {letter} controls @{int(bps)}bps ({time.time() - t0:.0f}s)", flush=True)

        def run_arm(arm: str, role: str, variant: str, quantile, score, decision, thr, extra=None):
            bars = channel_release(frame, masks, score, thr, eval_block, decision,
                                   member_keep if args.smoke_frac < 1.0 else None)
            triggers = triggers_of(keys, bars)
            rule = ScoreRelease(name=f"score:{arm}:fold{letter}", kind="scan", triggers=triggers,
                                params={"arm": arm, "role": role, "variant": variant,
                                        "quantile": quantile, "fold": letter,
                                        "train_block": train_block,
                                        "n_trigger_members": len(triggers)})
            first_cid = None
            for bps in args.bps:
                rows = replay_rows(df_eval, rule, bps)
                agg = led.aggregate_block(rows, f"{arm} fold {letter} (ledger machinery)")
                if not agg["checks"]["net_identity"]:
                    failures.append(f"{arm} {letter} {bps}: net identity violated")
                if agg["n_members_unresolved_censored"] != \
                        control_cells[bps]["hold_flat"]["n_members_unresolved_censored"]:
                    failures.append(f"{arm} {letter} {bps}: censored census differs from hold")
                cid = cell_id(arm, letter, bps)
                cell = build_cell(
                    arm, role, variant, letter, bps, train_block, eval_block, rows, agg,
                    {name: control_days[(letter, bps, name)] for name in CONTROLS},
                    control_cells[bps], extra)
                cells[cid] = cell
                cells_by_arm.setdefault(arm, []).append(cid)
                e1b_cells[cid] = {
                    "arm": arm, "role": role, "fold": letter, "friction_bps": bps,
                    "terciles": niche_increments(rows, control_rows[bps], niche_by_key,
                                                 top5_drop(judged_rows(rows))[2]),
                }
                first_cid = first_cid or cid
                if bps == args.bps[0] and role in ("primary", "secondary_ruler"):
                    attribution_by_arm.setdefault(arm, {})[letter] = attribution(rows)
            print(f"[e1] fold {letter} {arm}: releases {cells[first_cid]['n_releases']}"
                  f"/{cells[first_cid]['n_members_judged']} net {cells[first_cid]['ledger']['net_dollar_ledger']}"
                  f" ({time.time() - t0:.0f}s)", flush=True)

        # primary: the dollar-learned global threshold, frozen on the train block
        # (threshold pool AND the train-side triggers both come from the inner day-level cross-fit,
        #  so the evaluation block's rows/days/outcomes are never read by the selection)
        df_train_blocks = set(df_train["block"].unique().to_list())
        if df_train_blocks != {train_block}:
            failures.append(f"fold {letter}: the grid replay frame is not {train_block}-only "
                            f"({sorted(df_train_blocks)})")
        grid_doc = learn_dollar_threshold(
            frame, masks, inner[(letter, "final_high_now")]["score"], df_train, keys, train_block,
            grid, GRID_OBJECTIVE_BPS, member_keep if args.smoke_frac < 1.0 else None)
        grid_doc["scored_by"] = inner[(letter, "final_high_now")]["info"]
        grid_doc["eval_block"] = eval_block
        grid_doc["n_eval_rows_read_by_selection"] = 0
        grid_docs[letter] = grid_doc
        chosen = grid_doc["chosen"]
        print(f"[e1] fold {letter} grid: q{chosen['quantile']:.2f} thr {chosen['threshold']} "
              f"train net {chosen['train_net_after_top5_removal']} "
              f"releases {chosen['n_releases']} ({time.time() - t0:.0f}s)", flush=True)
        run_arm("dollar_threshold", "primary", "dollar_grid", chosen["quantile"], score_now,
                DECISION_STATE, chosen["threshold"],
                {"train_grid_quantile": chosen["quantile"], "threshold": chosen["threshold"],
                 "objective_bps": GRID_OBJECTIVE_BPS,
                 "train_net_after_top5_removal": chosen["train_net_after_top5_removal"],
                 "train_n_releases": chosen["n_releases"]})

        # secondary rulers: clock-minute quantiles of the same score
        for q_idx, q in enumerate(QUANTILES):
            run_arm(arm_id("secondary", q), "secondary_ruler", "clock_quantile", q, score_now,
                    DECISION_STATE, thr_arrays[(letter, "secondary_ruler")][q_idx],
                    {"threshold": "per clock minute", "quantile": q,
                     "min_train_obs_per_minute": MIN_TRAIN_OBS_PER_MINUTE})

        # ablation: the event-hazard score on its own new-high support
        for q_idx, q in enumerate(QUANTILES):
            run_arm(arm_id("ablation", q), "ablation", "event_hazard_clock_quantile", q,
                    score_event, DECISION_EVENT, thr_arrays[(letter, "ablation")][q_idx],
                    {"threshold": "per clock minute", "quantile": q,
                     "min_train_obs_per_minute": MIN_TRAIN_OBS_PER_MINUTE})

    # ---- verdicts ----------------------------------------------------------------------
    arms: dict = {}
    for q in QUANTILES:
        arm = arm_id("secondary", q)
        arms[arm] = summarise_arm(arm, "secondary_ruler", "clock_quantile", q,
                                  {c: cells[c] for c in cells_by_arm.get(arm, [])},
                                  attribution_by_arm.get(arm, {}))
        arm = arm_id("ablation", q)
        arms[arm] = summarise_arm(arm, "ablation", "event_hazard_clock_quantile", q,
                                  {c: cells[c] for c in cells_by_arm.get(arm, [])}, {})
    arms["dollar_threshold"] = summarise_arm(
        "dollar_threshold", "primary", "dollar_grid", None,
        {c: cells[c] for c in cells_by_arm.get("dollar_threshold", [])},
        attribution_by_arm.get("dollar_threshold", {}))
    for name, key in (("hold_flat", "hold_flat"), ("giveback:10", "giveback_10")):
        ids = cells_by_arm.get(key, [])
        arms[key] = {
            "arm": name, "role": "control", "variant": "control", "quantile": None,
            "cells": ids, "verdict": "CONTROL", "killed_in_cells": [], "killed_in_folds": [],
            "cell_headline": {c: {"fold": cells[c]["fold"], "friction_bps": cells[c]["friction_bps"],
                                  "n_releases": cells[c]["n_releases"],
                                  "net_dollar_ledger": cells[c]["ledger"]["net_dollar_ledger"],
                                  "own_top5_removed": cells[c]["own_top5_removed"]}
                              for c in ids},
            "attribution": (attribution_by_arm.get("giveback_10", {}) if key == "giveback_10"
                            else {"note": "hold_flat never releases; it is the zero baseline"})}

    n_arms = 1 + 2 * len(QUANTILES) + len(CONTROLS)
    expected_cells = n_arms * len(FOLDS) * len(args.bps)
    if len(cells) != expected_cells:
        failures.append(f"cell count {len(cells)} != {expected_cells}")

    # ---- artifact ----------------------------------------------------------------------
    doc = {
        "producer": PRODUCER,
        "provenance": module_provenance(),
        "reproduce": ("git -C <worktree> rev-parse HEAD && "
                      ".venv/bin/python factory/scripts/basket_atlas_e1.py"),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runtime_s": round(time.time() - t0, 1),
        "mode": {"smoke_frac": args.smoke_frac, "smoke": bool(args.smoke_frac < 1.0),
                 "frictions_bps": list(args.bps), "grid_step": args.grid_step},
        "run": {
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "checks": {
                "panel_sha_match": bool(panel_sha == PANEL_SHA256),
                "panel_rows_match": bool(frame.n == PANEL_ROWS_EXPECTED),
                "panel_members_match": bool(frame.n_members == PANEL_MEMBERS_EXPECTED),
                "state_rows_match": bool(state_rows == STATE_ROWS_EXPECTED),
                "event_rows_match": bool(event_rows == EVENT_ROWS_EXPECTED),
                "final_high_now_equals_panel_column": bool(label_mismatch == 0),
                "final_high_now_label_mismatch_rows": label_mismatch,
                "causal_guard_passes": bool(guard["guard_passes"]),
                "panel_blocks_equal_block_of": block_map["panel_blocks_equal_block_of"],
                "member_key_table_in_frame_order": bool(order_ok),
                "bar_index_contiguous_from_zero": contig,
                "score_cross_fitted": all(v["cross_fitted"] for v in
                                          list(prov_now.values()) + list(prov_event.values())),
                "selection_never_reads_the_eval_block": {
                    "inner_cross_fit_rows_outside_train_block": {
                        f"{letter}|{target}":
                            inner[(letter, target)]["info"]["finite_outside_block"]
                        for letter in ("A", "B") for target in TARGETS},
                    "grid_replay_frames_are_train_block_only": all(
                        g["train_block"] == f[1] for g, f in
                        zip([grid_docs[k] for k in sorted(grid_docs)], list(FOLDS))),
                    "note": ("thresholds (dollar grid + clock-minute banks) come from the inner "
                             "day-level cross-fit inside the train block; the grid objective is "
                             "evaluated by replaying the ledger on the train block only"),
                },
                "reconciliation_clean_every_cell": all(
                    cells[c]["ledger"]["reconciliation"]["reconciliation_clean"] for c in cells),
                "e1b_dispersion_splitter_frozen_before_outcomes": {
                    "cuts_by_fold": {letter: v["cuts"] for letter, v in e1b_splitter.items()},
                    "inner_train_halves_only": True,
                    "eval_prediction_source": "the train-block dispersion model",
                    "note": ("the splitter's cuts come from the inner day-split cross-fit of the "
                             "train block; the eval-side prediction-vs-label correlation is "
                             "computed after the cuts exist and is reported as POST_FREEZE"),
                },
                "cells_complete": bool(len(cells) == expected_cells),
                "ledger_net_identities_hold": all(
                    cells[c]["ledger"]["checks"]["net_identity"] for c in cells),
                "censoring_never_gates_a_decision": (
                    "the score is emitted for every complete bar and decisions are taken on the "
                    "state mask (complete & uncensored & a new high already seen); censored members "
                    "are excluded from training and never enter an executable aggregate — the "
                    "ledger partitions them as unresolved"),
                "blocked_rows": {
                    "note": ("the panel population is already filled & not blocked; the loader is "
                             "fall.load_frame with no extra filter (no blocked column exists)"),
                    "blocked_column_present": bool("blocked" in schema_cols)},
            },
        },
        "panel": {
            "path": str(panel.relative_to(WT)) if panel.is_relative_to(WT) else str(panel),
            "sha256": panel_sha, "expected_sha256": PANEL_SHA256,
            "rows": int(frame.n), "members": int(frame.n_members),
            "complete_rows": int(masks["complete"].sum()),
            "complete_members": int(masks["complete"][frame.member_rows].sum()),
            "censored_members": int(frame.censored[frame.member_rows].sum()),
            "state_rows": state_rows, "event_rows": event_rows,
            "state_members": int((np.bincount(frame.mid, weights=masks["state"].astype(np.float64),
                                              minlength=frame.n_members) > 0).sum()),
            "by_block": {b: {"rows": int((frame.block == b).sum()),
                             "members": int((frame.m_block == b).sum()),
                             "state_rows": int((masks["state"] & (frame.block == b)).sum()),
                             "train_now_rows": int((masks["train_now"] & (frame.block == b)).sum()),
                             "train_event_rows": int((masks["train_event"]
                                                      & (frame.block == b)).sum()),
                             "days": block_map["days_per_block"].get(b)}
                         for b in BLOCKS},
            "load_checks": checks,
        },
        "protocol": {
            "question": ("does the cross-sectionally fitted exhaustion LEVEL make a better release "
                         "rule than the fixed rulers, on dollars, out of block?"),
            "targets": {
                "final_high_now": {
                    "role": "PRIMARY",
                    "definition": ("1 iff the running high at t equals the session's final running "
                                   "high (no bar after t sets a new high) on a tape complete to "
                                   "the close, else 0 — defined at every completed bar"),
                    "verified_against": ("the panel's own final_high_flag: identical on all "
                                         f"{int(defined.sum())} rows where the panel defines it "
                                         f"({label_mismatch} mismatches)"),
                    "training_population": ("complete & uncensored bars with a new high already "
                                            "seen (running high > running high at the fill bar) "
                                            "and the label defined"),
                },
                "event_hazard": {
                    "role": "ABLATION only",
                    "definition": ("P(this new high is the last of the session) — the fall "
                                   "analysis' discrete hazard, labelled final_high_flag on "
                                   "new-high bars"),
                    "why_ablation": ("its training support is new-high bars but a release decision "
                                     "is taken on any bar, and the two state distributions differ "
                                     "(the exact out-of-support extrapolation this run removes "
                                     "from the primary arm); decisions are therefore restricted "
                                     "to new-high bars"),
                },
            },
            "state_mask": ("complete & uncensored & (running high > running high at the fill bar): "
                           "the post-fill state in which a release decision is taken"),
            "score": ("ridge logistic, 14 fall state features (fall.design_matrix scaling) + exact "
                      "clock-minute fixed effects (fall.multi_effect_logit), fitted pooled over "
                      "both families on the train block; score emitted for every complete bar"),
            "cross_fit": ("TWO layers, both day/block-level. (1) Evaluation: every evaluated bar is "
                          "scored by the model fitted on the OTHER block. (2) Selection: every "
                          "threshold (the primary dollar grid and every clock-minute bank) is built "
                          "from an INNER day-level cross-fit inside the train block (the block's "
                          "sorted days alternate between two halves; each half is scored by the "
                          "model fitted on the other half), and the grid's train-side replay runs "
                          "on the train block only — so no threshold, and no selection objective, "
                          "is a function of the evaluation block. Verified: the inner score arrays "
                          "are finite on 0 rows outside their train block, and the grid replay "
                          "frame contains only the train block."),
            "folds": {letter: {"train_block": tr, "eval_block": ev,
                               "note": ("block_of (frozen) carries exactly two blocks, so the "
                                        "brief's 'blocks 0-1 train -> 2-3 eval' is block1 -> "
                                        "block2 and its mirror is block2 -> block1")}
                      for letter, tr, ev in FOLDS},
            "arms": {
                "primary": {"arm": "dollar_threshold",
                            "definition": ("one global threshold learned on the TRAIN block from "
                                           "ledger dollars: a bounded grid over the train score "
                                           "distribution (quantiles "
                                           f"{grid[0]:.2f}..{grid[-1]:.2f}, step "
                                           f"{args.grid_step:.2f}), objective = train-block net at "
                                           f"{int(GRID_OBJECTIVE_BPS)} bps after removing the five "
                                           "largest-|day net| days, ties broken toward fewer "
                                           "releases; frozen and applied to the evaluation block "
                                           "unchanged")},
                "secondary_rulers": {arm_id("secondary", q):
                                     {"quantile": q, "definition": ("release at the first state "
                                                                    "bar with score >= the train "
                                                                    "quantile of the score at that "
                                                                    "bar's exact clock minute")}
                                     for q in QUANTILES},
                "ablation": {arm_id("ablation", q):
                             {"quantile": q, "definition": ("the event-hazard score with the same "
                                                            "clock-minute quantile ruler, "
                                                            "decisions on new-high bars")}
                             for q in QUANTILES},
                "controls": {"hold_flat": "the executable forced flat (delta = 0 by construction)",
                             "giveback:10": ("the panel's declared giveback continuation (fixed "
                                             "ruler)"),
                             "binding_tail_control": CONTROL_KEY},
            },
            "frictions_bps": list(args.bps),
            "friction_convention": ("ledger: side = bps/2/10000, k = (1-side)/(1+side), "
                                    "delta = k*(gross_rule - gross_hold) — identical on both legs"),
            "kill_criterion": {
                "pre_registered": ("an arm is dead if, after removing the five days with the "
                                   "largest |day net|, its net dollar ledger is <= hold OR its "
                                   "destroyed giant tail (MFE from the release >= +300% AND the "
                                   "session close above the release price) per avoided dollar is "
                                   "not below its non-score control's, in EITHER fold"),
                "delta_test": "sum of delta after top-5-day removal > 0 (hold nets exactly 0)",
                "tail_test": ("giant300 dollars per avoided dollar after top-5-day removal < the "
                              f"{CONTROL_KEY} control's ratio on the same surviving days"),
                "fold_aggregation": "arm KILLED if any (fold, friction) cell kills",
            },
            "top5_day_removal": ("per cell: the five days with the largest |sum of member deltas| "
                                 "are dropped from the arm AND from the control on the same basis"),
            "ambiguity_resolutions": [
                {"issue": "the brief names folds as 'blocks 0-1 train -> blocks 2-3 eval'",
                 "resolution": ("block_of (basket_atlas_panel, frozen) yields exactly two blocks; "
                                "fold A = block1 train -> block2 eval, fold B = block2 train -> "
                                "block1 eval. Verified: the panel's block column equals block_of "
                                "on every member.")},
                {"issue": "which bars take a release decision",
                 "resolution": ("the state mask (complete & uncensored, a new high already seen) — "
                                "the same population the primary target is trained on, so the "
                                "score is never applied out of support")},
                {"issue": "which population defines the per-minute threshold quantile",
                 "resolution": ("the decision population itself, cross-fitted, on the train block; "
                                f"a minute with fewer than {MIN_TRAIN_OBS_PER_MINUTE} train rows "
                                "uses the pooled train quantile, and the fallback is counted and "
                                "listed per fold")},
                {"issue": "censoring as an eligibility gate",
                 "resolution": ("censoring never gates a decision: the score is emitted for every "
                                "complete bar and the rule is applied wherever the state mask "
                                "holds. Censored members cannot carry a label, so they are "
                                "excluded from training; the ledger partitions them as unresolved "
                                "and they never enter an executable aggregate (asserted equal "
                                "across arms and controls).")},
                {"issue": "'avoided' / 'destroyed' wording in the brief",
                 "resolution": ("the ledger's definitions are imported verbatim "
                                "(failure_tax_avoided = sum of positive deltas, dollars_destroyed "
                                "= -sum of negative deltas); the brief's giant definition "
                                "(MFE >= +300% and close above the release) is reported beside the "
                                "ledger's own giant sets and is the one used for the tail ratio")},
                {"issue": "hold_flat has 0 avoided and 0 destroyed dollars: its tail ratio is 0/0",
                 "resolution": (f"reported as null (never a fabricated 0); the binding non-score "
                                f"control for the tail test is {CONTROL_KEY}, which does cut "
                                "positions. The delta clause already uses hold as the baseline.")},
                {"issue": "score pooling across families",
                 "resolution": ("'cross-sectionally fitted' is read as one pooled model over the "
                                "panel cross-section (both families); per-family out-of-block AUC "
                                "is reported as a diagnostic")},
                {"issue": "the brief's 'blocked non-causal rows excluded same as fall.py'",
                 "resolution": ("the frozen panel contains only filled, non-blocked members and "
                                "carries no blocked column; the loader is fall.load_frame with no "
                                "extra filter")},
            ],
        },
        "score": {
            "features": list(fall.MODEL_FEATURES),
            "n_features": len(fall.MODEL_FEATURES),
            "clock_effects": {"coordinate": "exact clock minute (et - 570)",
                              "n_codes": fall.n_minute_codes(frame)},
            "models": {
                "final_high_now": {blk: {k: v for k, v in m.items() if k not in ("beta", "stats")}
                                   for blk, m in models_now.items()},
                "event_hazard": {blk: {k: v for k, v in m.items() if k not in ("beta", "stats")}
                                 for blk, m in models_event.items()},
            },
            "score_provenance": {"final_high_now": prov_now, "event_hazard": prov_event},
            "inner_selection_cross_fit": {
                f"{letter}|{target}": inner[(letter, target)]["info"]
                for letter in ("A", "B") for target in TARGETS},
            "threshold_banks": {f"{letter}|{tag}": v for (letter, tag), v in thr_bank.items()},
        },
        "grid_search": grid_docs,
        "e1b_dispersion_niche": {
            "question": ("does any every-bar score arm beat giveback:10 inside a niche defined by "
                         "CAUSALLY PREDICTED forward dispersion, even though it loses "
                         "unconditionally?"),
            "splitter": {
                "target": DISPERSION_TARGET,
                "model": ("ridge least squares (fall.multi_effect_ridge_linear) on "
                          f"log1p({DISPERSION_TARGET}) with the 14 fall state features + exact "
                          "clock-minute fixed effects, fitted pooled over both families"),
                "causal_inputs_only": ("the splitter is a function of state at t only; the "
                                       "dispersion label is label-side and is never given to the "
                                       "model that scores the evaluation block"),
                "train_side": ("inner day-level cross-fit inside the train block (the B1 split "
                               "discipline): each day half is scored by the model fitted on the "
                               "other half, so the tercile cuts see no evaluation row"),
                "eval_side": "predicted by the model fitted on the whole train block",
                "niche_moment": ("a member's tercile at its FIRST state bar (the first completed "
                                 "bar where a new high has already occurred) — arm-independent, so "
                                 "the terciles partition the same judged members for every arm and "
                                 "decompose the unconditional increment"),
                "cuts_frozen_before_outcomes": True,
                "by_fold": e1b_splitter,
            },
            "increment_definition": {
                "sleeve": "sum over the arm's judged members in the tercile of (arm delta - giveback:10 delta)",
                "dedup": ("the same increment after collapsing shared (day, ticker) paths with the "
                          "ledger's declared duplicate rule (A_pm first, then the lower entry_rank)"),
                "identical_surviving_days": ("the arm's own top-5-day-removed day set from the E1a "
                                             "cell, applied to the control as well"),
                "ratio": ("giant300 dollars (MFE from the release >= +300% and close > release) "
                          "per avoided dollar for the arm and for the control on those days"),
            },
            "by_cell": e1b_cells,
            "verdict": e1b_verdict(e1b_cells, E1B_VERDICT_FRICTION),
            "verdict_150bps": e1b_verdict(e1b_cells, 150.0),
        },
        "cells": cells,
        "arms": arms,
        "headline": {
            role: {arm: {"verdict": a["verdict"], "killed_in_folds": a["killed_in_folds"],
                         "cells": a["cell_headline"]}
                   for arm, a in arms.items() if a["role"] == role}
            for role in ("primary", "secondary_ruler", "ablation", "control")},
    }
    write_json(Path(args.out), doc)
    print(f"[e1] {doc['run']['status']} in {doc['runtime_s']}s -> {args.out}", flush=True)
    for arm, a in arms.items():
        print(f"  {arm:>28} [{a['role']}]: {a['verdict']}"
              + (f" (folds {','.join(a['killed_in_folds'])})" if a["killed_in_folds"] else ""))
    if failures:
        for f in failures:
            print(f"  FAILURE: {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
