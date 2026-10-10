#!/usr/bin/env python3
"""Exploratory sparse-model extension of the learned open-anchored selector.

The primary learned study (``alpha_open_learned.py``) froze h15/thr0.01 under a strict
power floor (>=100 known fills, >=50 traded days) and confirmed it NEGATIVE late. Its own
2023 validation surface, however, contains sparse sub-floor alternatives, and exactly one
of them is validation-positive while clearing a *secondary, exploratory* floor
(30 <= known fills < 100, traded_days >= 30): **h60 / thr0.03** — 61 known fills on 55
traded days, mean_daily_lower_bound +0.000907/day @100bps.

This producer is a **secondary discovery probe**, not a second pristine confirmation:

* it READS the original ``learned/surface_validation.json`` (no rescan, no refit, no HPO,
  no feature changes) and picks the eligible alternative with the best ORIGINAL
  mean_daily_lower_bound;
* it LOADS the stored 2021-2022 payoff model for the chosen horizon only and scores the
  liquidity-qualified panel with that single head (the other horizon heads are never
  computed);
* it REPRODUCES the 2023 validation cell at 100bps through the unchanged shared replay
  and aborts if any stored field of the original surface does not reproduce exactly;
* it FREEZES ``learned_sparse_extension/contract.json`` before any late-block outcome is
  computed, then runs the one frozen strategy on the 2025-02..2026-05 block at
  100/150/200bps, preserving every cash/UNKNOWN replay convention;
* the late block has already been explored by other studies, so the verdict is
  DISCOVERY-NOT-VALIDATED regardless of sign. No median or top-days skill gating is
  applied anywhere; the original h15 freeze, report, models and surface are never
  rewritten; no live flag is touched.

Uncertainty is reported as bootstrap day-level means (1000 draws, fixed seed) with the
fill count (n) and traded days printed beside it. If no alternative is eligible, an
explicit no-candidate artifact is produced and no late pipeline is fabricated.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from alpha_open_learned import (
    BOOT_N,
    BOOT_SEED,
    CONF_PERIOD,
    CONFIRM_COSTS,
    FEATURES_ALL,
    HORIZONS,
    PANEL_ROOT,
    SELECT_COST,
    THRESHOLDS,
    VAL_PERIOD,
    bootstrap_daily,
    cost_effect,
    feature_matrix,
    make_signals,
    metrics_view,
    monthly_accounting,
    prepare,
    sha256_file,
)
from alpha_open_sim import causal_liquidity, load_panel, replay

# ----- configuration (fixed; mirrors the primary study's replay engine) ----------
MIN_KNOWN_FILLS = 30  # secondary exploratory power floor (inclusive)
MAX_KNOWN_FILLS_EXCLUSIVE = 100  # sparse: strictly below the primary 100-fill floor
MIN_TRADED_DAYS = 30  # secondary exploratory traded-day floor
STATUS = "DISCOVERY-NOT-VALIDATED"  # late block was previously explored; never pristine
NO_CANDIDATE_STATUS = "NO-CANDIDATE"
EXPECTED_PANEL_DAYS = 1066  # full development corpus guard
FLOAT_TOL = 1e-12


# ----- selection from the ORIGINAL validation surface ---------------------------
def load_original_surface(path: Path) -> dict:
    """Stored 2023 validation surface of the primary learned study, keyed (h, thr)."""
    if not path.exists():
        raise SystemExit(
            f"[sparse-ext] original validation surface missing: {path} "
            "(run alpha_open_learned.py first)"
        )
    raw = json.loads(path.read_text())
    surface = {}
    for key, view in raw.items():
        h_s, thr_s = key.split("|")
        surface[(int(h_s), float(thr_s))] = view
    if not surface:
        raise SystemExit(f"[sparse-ext] original validation surface is empty: {path}")
    return surface


def eligibility_table(surface: dict) -> list[dict]:
    """Every original surface cell with the sparse-floor eligibility verdict and reasons."""
    rows = []
    for (h, thr), view in sorted(surface.items()):
        kf = int(view.get("known_fills") or 0)
        td = int(view.get("traded_days") or 0)
        mdlb = view.get("mean_daily_lower_bound")
        why = []
        if mdlb is None or not (mdlb > 0):
            why.append("not val-positive")
        if not (MIN_KNOWN_FILLS <= kf < MAX_KNOWN_FILLS_EXCLUSIVE):
            why.append(f"known_fills={kf} outside [30,100)")
        if td < MIN_TRADED_DAYS:
            why.append(f"traded_days={td} < 30")
        rows.append(
            {
                "cell": f"{h}|{thr:.2f}",
                "horizon": h,
                "threshold": thr,
                "mean_daily_lower_bound": mdlb,
                "known_fills": kf,
                "traded_days": td,
                "n_signals": view.get("n_signals"),
                "eligible": not why,
                "exclusion_reasons": why,
            }
        )
    return rows


def pick_chosen(rows: list[dict]) -> tuple[dict | None, list[dict]]:
    """Best eligible alternative by ORIGINAL mean_daily_lower_bound (deterministic order)."""
    eligible = [r for r in rows if r["eligible"]]
    if not eligible:
        return None, eligible
    best = max(
        eligible,
        key=lambda r: (
            r["mean_daily_lower_bound"],
            r["known_fills"],
            -r["horizon"],
            -r["threshold"],
        ),
    )
    return best, eligible


# ----- stored-model handling (no fit / no HPO / no feature changes) --------------
def load_chosen_model(model_dir: Path, horizon: int) -> tuple[dict, Path]:
    """Load ONLY the chosen horizon's stored 2021-2022 model bundle."""
    if horizon not in HORIZONS:
        raise SystemExit(f"[sparse-ext] horizon {horizon} has no stored model")
    path = model_dir / f"payoff_h{horizon}.joblib"
    if not path.exists():
        raise SystemExit(f"[sparse-ext] stored model missing: {path}")
    bundle = joblib.load(path)
    order = list(bundle.get("feature_order") or [])
    if order != list(FEATURES_ALL):
        raise SystemExit(
            f"[sparse-ext] stored feature_order != current FEATURES_ALL for h{horizon}"
        )
    return bundle, path


def score_chosen_horizon(cand: pl.DataFrame, bundle: dict, horizon: int) -> pl.DataFrame:
    """Score the liquidity-qualified panel on the chosen horizon head only."""
    x = feature_matrix(cand)
    pred = np.asarray(bundle["lgbm"].predict(x), dtype=float)
    col = f"pred_gross_{horizon}"
    if col in cand.columns:
        cand = cand.drop(col)
    return cand.with_columns(pl.Series(col, pred))


# ----- block views (n + traded days beside uncertainty; no median/top-days skill) --
def yearly_accounting(metrics: dict, trades: list[dict]) -> dict:
    """Per-year replayed-day / traded-day / fill / UNKNOWN accounting for one block."""
    years: dict[str, dict] = {}
    for r in metrics["daily"]:
        e = years.setdefault(
            r["day"][:4],
            {
                "days_replayed": 0,
                "traded_days": set(),
                "fills": 0,
                "unknown": 0,
                "known_fills": 0,
                "lower_bound_pnl": 0.0,
                "lower_bound_return_sum": 0.0,
            },
        )
        e["days_replayed"] += 1
        e["unknown"] += r["unknown"]
        e["lower_bound_pnl"] += r["lower_bound_pnl"]
        e["lower_bound_return_sum"] += r["lower_bound_return"]
    for tr in trades:
        e = years.setdefault(
            tr["day"][:4],
            {
                "days_replayed": 0,
                "traded_days": set(),
                "fills": 0,
                "unknown": 0,
                "known_fills": 0,
                "lower_bound_pnl": 0.0,
                "lower_bound_return_sum": 0.0,
            },
        )
        e["fills"] += 1
        e["traded_days"].add(tr["day"])
        if tr["net"] is not None:
            e["known_fills"] += 1
    out = {}
    for y in sorted(years):
        e = years[y]
        out[y] = {
            "days_replayed": e["days_replayed"],
            "traded_days": len(e["traded_days"]),
            "fills": e["fills"],
            "known_fills": e["known_fills"],
            "unknown_fills": e["unknown"],
            "lower_bound_pnl": round(e["lower_bound_pnl"], 2),
            "mean_daily_lower_bound": (
                e["lower_bound_return_sum"] / e["days_replayed"] if e["days_replayed"] else None
            ),
        }
    return out


def block_view(metrics: dict, trades: list[dict], n_signals: int) -> dict:
    """Stored metrics for one (horizon, threshold, cost, block) replay."""
    view = metrics_view(metrics)
    view["bootstrap_daily"] = bootstrap_daily([r["lower_bound_return"] for r in metrics["daily"]])
    view["n_signals"] = int(n_signals)
    view["sample"] = {
        "known_fills": view["known_fills"],
        "unknown_fills": view["unknown_fills"],
        "traded_days": view["traded_days"],
        "days_replayed": view["days"],
    }
    view["monthly"] = monthly_accounting(metrics, trades)
    view["yearly"] = yearly_accounting(metrics, trades)
    return view


def compare_to_original(original: dict, view: dict, n_signals: int) -> dict:
    """Field-by-field reproduction check of the stored original surface cell."""
    checks: dict[str, dict] = {}
    for f in (
        "days",
        "attempts",
        "fills",
        "known_fills",
        "unknown_fills",
        "cash_or_slot_skips",
        "traded_days",
        "positive_months_lower_bound",
        "months",
        "n_signals",
    ):
        o = original.get(f)
        m = n_signals if f == "n_signals" else view.get(f)
        checks[f] = {"original": o, "reproduced": m, "match": o == m}
    for f in (
        "mean_net_known_fill",
        "mean_daily_lower_bound",
        "daily_se",
        "known_win_rate",
        "known_profit_factor",
        "worst_known_fill",
    ):
        o, m = original.get(f), view.get(f)
        ok = (o is None and m is None) or (
            o is not None and m is not None and abs(float(o) - float(m)) <= FLOAT_TOL
        )
        checks[f] = {"original": o, "reproduced": m, "match": ok}
    om = original.get("monthly_mean_lower_bound") or {}
    mm = view.get("monthly_mean_lower_bound") or {}
    monthly_ok = set(om) == set(mm) and all(
        abs(float(om[k]) - float(mm[k])) <= FLOAT_TOL for k in om
    )
    checks["monthly_mean_lower_bound"] = {"match": monthly_ok}
    checks["all_match"] = all(v.get("match") for v in checks.values())
    return checks


# ----- persistence ----------------------------------------------------------------
def save_trades(trades: list[dict], path: Path) -> None:
    tr = pl.DataFrame(trades) if trades else pl.DataFrame()
    if tr.height:
        tr = tr.with_columns(
            (pl.col("entry_open") * (1.0 + pl.col("gross"))).alias("exit_open_proxy")
        )
    tr.write_parquet(path)


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def print_eligibility(rows: list[dict]) -> None:
    print("[elig] cell    known_fills traded_days  mdlb@100bps        verdict", flush=True)
    for r in rows:
        mdlb = r["mean_daily_lower_bound"]
        verdict = "ELIGIBLE" if r["eligible"] else "skip: " + "; ".join(r["exclusion_reasons"])
        print(
            f"[elig] {r['cell']:<8} {r['known_fills']:>11} {r['traded_days']:>11}  "
            f"{format(mdlb, '+.6f') if mdlb is not None else '     n/a':>13}  {verdict}",
            flush=True,
        )


def _fmt(value, spec="+.6f"):
    return "n/a" if value is None else format(value, spec)


def print_block(tag: str, h: int, thr: float, view: dict, note: str = "") -> None:
    b = view["bootstrap_daily"]
    ci = f"[{_fmt(b.get('ci95_lo'))},{_fmt(b.get('ci95_hi'))}]" if "ci95_lo" in b else "n/a"
    print(
        f"[{tag}] h={h} thr={thr:.2f} mdlb={_fmt(view['mean_daily_lower_bound'])} "
        f"se={_fmt(view['daily_se'])} boot95={ci} p>0={b.get('p_gt_zero')} "
        f"n={view['known_fills']} traded_days={view['traded_days']} "
        f"signals={view['n_signals']} days={view['days']}{note}",
        flush=True,
    )


# ----- no-candidate path (explicit artifact, no fabricated pipeline) ---------------
def no_candidate(out: Path, rows: list[dict], surface_path: Path, t0: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    contract = {
        "study": "alpha_sparse_model_extension",
        "status": NO_CANDIDATE_STATUS,
        "exploratory": True,
        "frozen_before_late_inspection": True,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "chosen": None,
        "selection": {
            "basis": "original learned/surface_validation.json values (no rescan, no refit)",
            "eligibility": {
                "val_positive": "mean_daily_lower_bound > 0",
                "known_fills_min": MIN_KNOWN_FILLS,
                "known_fills_max_exclusive": MAX_KNOWN_FILLS_EXCLUSIVE,
                "traded_days_min": MIN_TRADED_DAYS,
            },
            "objective": "max original mean_daily_lower_bound among eligible",
            "median_or_tail_gates": None,
            "alternatives_total": len(rows),
            "alternatives_eligible": 0,
            "eligibility_table": rows,
        },
        "source": {
            "original_learned_surface": str(surface_path),
            "original_learned_surface_sha256": sha256_file(surface_path),
        },
        "producer_sha256": sha256_file(Path(__file__)),
    }
    write_json(out / "contract.json", contract)
    results = {
        "contract": contract,
        "validation": None,
        "confirmation_by_cost": None,
        "decision": (
            f"{NO_CANDIDATE_STATUS}: no val-positive original-surface alternative "
            f"with {MIN_KNOWN_FILLS}<=known_fills<"
            f"{MAX_KNOWN_FILLS_EXCLUSIVE} and traded_days>={MIN_TRADED_DAYS}; "
            "no late pipeline run"
        ),
        "status": NO_CANDIDATE_STATUS,
        "artifacts": {"contract": str(out / "contract.json")},
        "runtime_s": round(time.time() - t0, 1),
    }
    write_json(out / "results.json", results)
    print(f"[decision] {results['decision']}", flush=True)
    print(
        f"[done] no-candidate artifacts -> {out / 'contract.json'}, {out / 'results.json'}",
        flush=True,
    )


# ----- main probe ------------------------------------------------------------------
def run(args: argparse.Namespace) -> None:
    t0 = time.time()
    panel = args.panel
    out = args.out or panel / "learned_sparse_extension"
    surface_path = panel / "learned" / "surface_validation.json"
    model_dir = panel / "learned" / "models"
    report_path = panel / "learned" / "report.json"
    frozen_path = panel / "learned" / "frozen_contract.json"
    panel_contract_path = panel / "contract.json"

    # 1) selection reads the ORIGINAL surface only; no outcomes touched yet.
    surface = load_original_surface(surface_path)
    rows = eligibility_table(surface)
    print_eligibility(rows)
    chosen_row, eligible = pick_chosen(rows)
    if chosen_row is None:
        no_candidate(out, rows, surface_path, t0)
        return
    h, thr = chosen_row["horizon"], chosen_row["threshold"]
    if thr not in THRESHOLDS:
        raise SystemExit(
            f"[sparse-ext] chosen threshold {thr} outside the frozen grid {THRESHOLDS}"
        )
    print(
        f"[select] chosen h={h} thr={thr:.2f} by original mean_daily_lower_bound "
        f"({chosen_row['mean_daily_lower_bound']:+.6f}) among {len(eligible)}/"
        f"{len(rows)} eligible; STATUS={STATUS}",
        flush=True,
    )

    # 2) stored models (load ONLY the chosen head), full development corpus guard.
    bundle, model_path = load_chosen_model(model_dir, h)
    frame, calendar = load_panel(panel)
    if len(calendar) != EXPECTED_PANEL_DAYS:
        raise SystemExit(
            f"[sparse-ext] requires the full {EXPECTED_PANEL_DAYS}-day corpus, got {len(calendar)}"
        )
    frame = prepare(frame)
    by_period = frame.group_by("period").agg(pl.col("day").unique().sort().alias("days"))
    days_by_period = {r["period"]: list(r["days"]) for r in by_period.iter_rows(named=True)}
    val_days = days_by_period[VAL_PERIOD]
    conf_days = days_by_period[CONF_PERIOD]

    # 3) score the liquidity-qualified panel on the chosen horizon head only.
    cand = frame.filter(causal_liquidity(frame))
    cand = score_chosen_horizon(cand, bundle, h)

    # 4) reproduce the 2023 validation cell at the selection cost via unchanged replay.
    sig_val = make_signals(cand, h, thr, val_days)
    metrics_val, trades_val = replay(sig_val, val_days, h, SELECT_COST)
    val_view = block_view(metrics_val, trades_val, int(sig_val.height))
    original_cell = surface[(h, thr)]
    repro = compare_to_original(original_cell, val_view, int(sig_val.height))
    if not repro["all_match"]:
        bad = {k: v for k, v in repro.items() if k != "all_match" and not v.get("match")}
        raise SystemExit(f"[sparse-ext] validation reproduction mismatch vs stored surface: {bad}")
    val_view["reproduction"] = repro
    val_view["reproduced_from_stored_model"] = True
    print_block(
        f"val@{int(SELECT_COST)}", h, thr, val_view, " — reproduces original surface exactly"
    )

    # 5) FREEZE the contract before any late-block outcome is computed.
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), out / "producer_snapshot.py")
    contract = {
        "study": "alpha_sparse_model_extension",
        "label": "exploratory secondary analysis of the learned sparse tail",
        "status": STATUS,
        "exploratory": True,
        "pristine_collision": False,
        "frozen_before_late_inspection": True,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": {
            "original_learned_surface": str(surface_path),
            "original_learned_surface_sha256": sha256_file(surface_path),
            "original_learned_report": str(report_path),
            "original_learned_report_sha256": (
                sha256_file(report_path) if report_path.exists() else None
            ),
            "original_frozen_contract": str(frozen_path),
            "original_frozen_contract_sha256": (
                sha256_file(frozen_path) if frozen_path.exists() else None
            ),
            "panel_contract": str(panel_contract_path),
            "panel_contract_sha256": (
                sha256_file(panel_contract_path) if panel_contract_path.exists() else None
            ),
            "stored_model": str(model_path),
            "stored_model_sha256": sha256_file(model_path),
            "producer_sha256": sha256_file(Path(__file__)),
            "original_outputs_modified": False,
        },
        "selection": {
            "basis": "original learned/surface_validation.json values (no rescan, no refit)",
            "eligibility": {
                "val_positive": "mean_daily_lower_bound > 0",
                "known_fills_min": MIN_KNOWN_FILLS,
                "known_fills_max_exclusive": MAX_KNOWN_FILLS_EXCLUSIVE,
                "traded_days_min": MIN_TRADED_DAYS,
            },
            "objective": "max original mean_daily_lower_bound among eligible",
            "median_or_tail_gates": None,
            "alternatives_total": len(rows),
            "alternatives_eligible": len(eligible),
            "eligibility_table": rows,
        },
        "chosen": {
            "horizon": h,
            "threshold": thr,
            "original_validation_mean_daily_lower_bound": chosen_row["mean_daily_lower_bound"],
            "original_known_fills": chosen_row["known_fills"],
            "original_traded_days": chosen_row["traded_days"],
            "selection_cost_bps": SELECT_COST,
            "rationale": (
                "highest ORIGINAL mean_daily_lower_bound among the sparse "
                "val-positive alternatives; the primary study's h15 freeze is "
                "untouched and this probe is DISCOVERY-NOT-VALIDATED"
            ),
        },
        "validation_reproduction": {
            "reproduced_at_cost_bps": SELECT_COST,
            "matches_original_surface": True,
            "field_comparison": repro,
            "metrics": val_view,
        },
        "model": {
            "refit": False,
            "hpo": False,
            "feature_changes": False,
            "horizons_scored": [h],
            "feature_order": FEATURES_ALL,
            "stored_params": bundle.get("params"),
            "clip_fit": bundle.get("clip_fit"),
            "seed": bundle.get("seed"),
        },
        "replay": {
            "engine": "alpha_open_sim.replay (unchanged shared account replay)",
            "max_positions": 3,
            "order_budget": 1000.0,
            "selection_cost_bps": SELECT_COST,
            "late_costs_bps": [float(c) for c in CONFIRM_COSTS],
            "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED},
            "cash_and_unknown_policy": (
                "unchanged: unfilled minutes reserve and release "
                "budget, UNKNOWN exits are charged a -100% lower "
                "bound, never dropped"
            ),
            "skill_gates": None,
        },
        "provenance": {
            "panel_root": str(panel),
            "calendar_days": len(calendar),
            "rows_total": int(frame.height),
            "rows_liquidity_qualified": int(cand.height),
            "days_by_period": {k: len(v) for k, v in days_by_period.items()},
            "val_days": len(val_days),
            "late_days": len(conf_days),
        },
    }
    write_json(out / "contract.json", contract)
    print(f"[freeze] contract -> {out / 'contract.json'} (before any late inspection)", flush=True)

    # 6) late block: one frozen strategy, unchanged replay, full cost ladder.
    sig_conf = make_signals(cand, h, thr, conf_days)
    sig_val.write_parquet(out / "signals_validation.parquet")
    sig_conf.write_parquet(out / "signals_confirmation.parquet")
    save_trades(trades_val, out / "trades_validation.parquet")
    conf: dict[str, dict] = {}
    for cost in CONFIRM_COSTS:
        metrics, trades = replay(sig_conf, conf_days, h, cost)
        view = block_view(metrics, trades, int(sig_conf.height))
        conf[str(int(cost))] = view
        save_trades(trades, out / f"trades_confirmation_{int(cost)}.parquet")
        print_block(f"late@{int(cost)}", h, thr, view)
    effect = cost_effect(conf)

    ladder = ", ".join(
        f"{conf[str(int(c))]['mean_daily_lower_bound']:+.6f} @{int(c)}" for c in CONFIRM_COSTS
    )
    decision = (
        f"{STATUS}: exploratory sparse-tail probe h={h} thr={thr:.2f}; validation "
        f"{val_view['mean_daily_lower_bound']:+.6f}/day "
        f"({val_view['known_fills']} known fills, {val_view['traded_days']} traded days, "
        f"2023, @{int(SELECT_COST)}bps); late mean_daily_lower_bound {ladder} "
        f"on the previously explored 2025-02..2026-05 block — not a pristine "
        f"confirmation, no promotion"
    )
    print(f"[decision] {decision}", flush=True)

    results = {
        "contract": contract,
        "validation": val_view,
        "confirmation_by_cost": conf,
        "confirmation_cost_effect": effect,
        "decision": decision,
        "status": STATUS,
        "artifacts": {
            "contract": str(out / "contract.json"),
            "signals_validation": str(out / "signals_validation.parquet"),
            "trades_validation": str(out / "trades_validation.parquet"),
            "signals_confirmation": str(out / "signals_confirmation.parquet"),
            "trades_confirmation": {
                str(int(c)): str(out / f"trades_confirmation_{int(c)}.parquet")
                for c in CONFIRM_COSTS
            },
            "producer_snapshot": str(out / "producer_snapshot.py"),
        },
        "runtime_s": round(time.time() - t0, 1),
    }
    write_json(out / "results.json", results)
    print(f"[done] results -> {out / 'results.json'} ({results['runtime_s']}s)", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--panel",
        type=Path,
        default=PANEL_ROOT,
        help="panel root containing learned/ (default: %(default)s)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output root (default: <panel>/learned_sparse_extension)",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
