#!/usr/bin/env python3
"""Learned expected-realized-payoff entry selector over the open-anchored panel.

This is NOT a generic ML resurrection on stale close-close labels; it is a specific,
execution-honest supervised selector over the open-anchored top-gainer substrate:

* DYNAMIC watchlist — states are only ever drawn from names freshly admitted into the
  full-PIT SIP top-ten B watchlist at the open (>=5% completed-bar gain), followed every
  5 minutes. The candidate set is dynamic, recomputed each session from the tape's own
  admissions; there is no fixed universe and no previous-close dependence.
* EXECUTABLE payoff target — the label is the realized market-open payoff of a real minute-t
  entry: entry = the t-minute open (or a genuine no-fill if that minute has no bar), exit =
  the first observed open at/after entry+h (capped at the session's last minute), or an
  explicit UNKNOWN when that open is never printed. We train ONLY on filled, resolved labels,
  but we SCORE and may TRADE every liquidity-qualified observable state, including unfilled
  and unknown ones — those real outcomes are handled by the shared replay, never dropped.
* SPARSE fee-clearing admission — a state is only taken when the predicted payoff clears a
  fee-clearing bar (>=1%/3%/5%). With ~1% round-trip cost the 1% bar is near break-even and
  the 3%/5% bars demand conviction, so the model must beat cost, not just be positive.
* INDEPENDENT late confirmation — fitting uses 2021-2022 only; the (horizon, threshold) is
  chosen on 2023 validation under a power floor (>=100 known fills, >=50 traded days, not a
  median gate); the (horizon, threshold) is frozen to disk BEFORE the 2025-02..2026-05
  confirmation is inspected, and confirmation runs the one frozen strategy at 100/150/200 bps
  with no refit or adaptation.

No high-touch profit credit, no same-bar qu3 equal-weight convention; minute-open proxy only.
Model: three fixed LightGBM regressors (horizons 15/60/390) on 22 causal state features plus
4 past-only peer-context features, no ticker/date identifiers, no hyperparameter search.
Training target is clipped to [-0.5, 2] ONLY for fit stability; every reported PnL is the
unclipped realized value produced by the shared account replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
import polars as pl
from alpha_open_panel import FEATURES
from alpha_open_sim import CONTEXT_FEATURES, causal_liquidity, load_panel, period, replay
from lightgbm import LGBMRegressor

# ----- fixed configuration (no HPO). ---------------------------------------
HORIZONS = (15, 60, 390)
THRESHOLDS = (0.01, 0.03, 0.05)
FEATURES_ALL = list(FEATURES) + list(CONTEXT_FEATURES)  # 26, no ticker/date ids
CLIP = (-0.5, 2.0)  # target clip, FIT ONLY
LGB_PARAMS = {
    "objective": "regression",
    "num_leaves": 15,
    "min_data_in_leaf": 300,
    "learning_rate": 0.03,
    "n_estimators": 200,
    "num_threads": 2,
    "seed": 20261008,
    "verbosity": -1,
    "deterministic": True,
    "force_row_wise": True,
}
TRAIN_PERIOD = "train"
VAL_PERIOD = "validation"
CONF_PERIOD = "confirmation"
SELECT_COST = 100.0
CONFIRM_COSTS = (100.0, 150.0, 200.0)
MIN_KNOWN_FILLS = 100
MIN_TRADED_DAYS = 50
SEED = 20261008
BOOT_N = 1000
BOOT_SEED = 1000
PANEL_ROOT = Path("/home/hillel/alpha-data/open-search-v1")
OUTPUT = PANEL_ROOT / "learned"
MODEL_DIR = OUTPUT / "models"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ----- feature helpers -------------------------------------------------------
def feature_matrix(df: pl.DataFrame) -> np.ndarray:
    """Feature matrix in the frozen FEATURES_ALL order; all-Float64, null-filled."""
    return (
        df.select([pl.col(c).cast(pl.Float64, strict=False) for c in FEATURES_ALL])
        .fill_null(0.0)
        .to_numpy()
    )


def prepare(frame: pl.DataFrame) -> pl.DataFrame:
    """Attach a research-block period column via the shared period()."""
    pmap = {d: period(d) for d in frame["day"].unique().to_list()}
    if not set(pmap.values()) <= {TRAIN_PERIOD, VAL_PERIOD, CONF_PERIOD}:
        raise ValueError("unexpected period bucket in panel")
    return frame.with_columns(
        pl.col("day").replace_strict(pmap, default=None).alias("period")
    ).filter(pl.col("period").is_not_null())


def fit_models(frame: pl.DataFrame) -> tuple[dict, dict]:
    """Three fixed LightGBM payoff regressors on filled, known-label, liquidity-qualified,
    t%%15==0 rows of the 2021-2022 training block only."""
    liq = causal_liquidity(frame)
    cand = frame.filter(liq)
    models, report = {}, {}
    for h in HORIZONS:
        fit_df = cand.filter(
            (pl.col("period") == TRAIN_PERIOD)
            & ((pl.col("t") % 15) == 0)
            & (pl.col("entry_status") == "filled_proxy")
            & pl.col(f"gross_{h}").is_not_null()
        )
        raw = fit_df[f"gross_{h}"].to_numpy().astype(float)  # unclipped, for stats
        y = np.clip(raw, CLIP[0], CLIP[1])  # clip ONLY at fit
        x = feature_matrix(fit_df)
        model = LGBMRegressor(**LGB_PARAMS)
        model.fit(x, y)
        models[h] = model
        gain = np.asarray(model.booster_.feature_importance("gain"), dtype=float)
        gain_map = dict(zip(FEATURES_ALL, gain.tolist(), strict=True))
        report[h] = {
            "train_rows": int(fit_df.height),
            "train_day_min": fit_df["day"].min(),
            "train_day_max": fit_df["day"].max(),
            "label_unclipped": {
                "count": int(raw.size),
                "mean": float(raw.mean()),
                "std": float(raw.std()),
                "p05": float(np.percentile(raw, 5)),
                "p50": float(np.percentile(raw, 50)),
                "p95": float(np.percentile(raw, 95)),
                "min": float(raw.min()),
                "max": float(raw.max()),
            },
            "label_clip_for_fit": list(CLIP),
            "gain_importance": dict(sorted(gain_map.items(), key=lambda kv: -kv[1])),
        }
    return models, report


def score_frame(frame: pl.DataFrame, models: dict) -> tuple[pl.DataFrame, dict[str, list[str]]]:
    """Score every liquidity-qualified observable state on all three horizons."""
    cand = frame.filter(causal_liquidity(frame))
    x = feature_matrix(cand)
    for h, model in models.items():
        pred = model.predict(x)
        cand = cand.with_columns(pl.Series(f"pred_gross_{h}", pred))
    by_period = cand.group_by("period").agg(pl.col("day").unique().sort().alias("days"))
    days_by_period = {r["period"]: list(r["days"]) for r in by_period.iter_rows(named=True)}
    return cand, days_by_period


def make_signals(
    cand: pl.DataFrame, horizon: int, threshold: float, days: list[str]
) -> pl.DataFrame:
    """Sparse, causal, thresholded candidate signals for one horizon/threshold/period."""
    keep = [
        "day",
        "ticker",
        "t",
        "entry_et",
        "entry_open",
        "entry_status",
        "session_end",
        f"gross_{horizon}",
        f"exit_et_{horizon}",
        f"pred_gross_{horizon}",
        "score",
    ]
    return (
        cand.filter(pl.col("day").is_in(days) & (pl.col(f"pred_gross_{horizon}") >= threshold))
        .with_columns(pl.col(f"pred_gross_{horizon}").alias("score"))
        .select(keep)
    )


# ----- uncertainty + monthly accounting --------------------------------------
def bootstrap_daily(returns: list[float], n: int = BOOT_N, seed: int = BOOT_SEED) -> dict:
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return {"n_days": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, r.size, size=(n, r.size))
    means = r[idx].mean(axis=1)
    return {
        "n_days": int(r.size),
        "n_boot": n,
        "seed": seed,
        "mean": float(r.mean()),
        "ci95_lo": float(np.percentile(means, 2.5)),
        "ci95_hi": float(np.percentile(means, 97.5)),
        "p_gt_zero": float((means > 0).mean()),
    }


def metrics_view(metrics: dict) -> dict:
    """Stored metrics without the bulky per-day list; daily returns kept separately."""
    return {k: v for k, v in metrics.items() if k != "daily"}


def monthly_accounting(metrics: dict, trades: list[dict]) -> dict:
    """Per-month traded-day / fill / UNKNOWN accounting for a block (cost-invariant fill set)."""
    months = defaultdict(
        lambda: {
            "days_replayed": set(),
            "traded_days": set(),
            "fills": 0,
            "known_fills": 0,
            "unknown": 0,
            "lower_bound_pnl": 0.0,
        }
    )
    for r in metrics["daily"]:
        m = months[r["day"][:7]]
        m["days_replayed"].add(r["day"])
        m["unknown"] += r["unknown"]
        m["lower_bound_pnl"] += r["lower_bound_pnl"]
    for tr in trades:
        m = months[tr["day"][:7]]
        m["fills"] += 1
        m["traded_days"].add(tr["day"])
        if tr["net"] is not None:
            m["known_fills"] += 1
    out = {}
    for mm in sorted(months):
        v = months[mm]
        out[mm] = {
            "days_replayed": len(v["days_replayed"]),
            "traded_days": len(v["traded_days"]),
            "fills": v["fills"],
            "known_fills": v["known_fills"],
            "unknown_fills": v["unknown"],
            "lower_bound_pnl": round(v["lower_bound_pnl"], 2),
        }
    return out


def cost_effect(conf: dict) -> dict:
    """Slope of mean_daily_lower_bound per +50 bps across the confirmation cost ladder."""
    pts = [
        (c, conf[str(int(c))]["mean_daily_lower_bound"])
        for c in CONFIRM_COSTS
        if conf.get(str(int(c)), {}).get("mean_daily_lower_bound") is not None
    ]
    slope_per_50 = None
    if len(pts) >= 2:
        (c0, y0), (c1, y1) = pts[0], pts[-1]
        if c1 != c0:
            slope_per_50 = (y1 - y0) / ((c1 - c0) / 50.0)
    return {"points": {str(int(c)): y for c, y in pts}, "mean_daily_slope_per_50bps": slope_per_50}


# ----- persistence -----------------------------------------------------------
def save_models(models: dict) -> list[str]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for h, model in models.items():
        jl = MODEL_DIR / f"payoff_h{h}.joblib"
        txt = MODEL_DIR / f"payoff_h{h}.txt"
        joblib.dump(
            {
                "lgbm": model,
                "feature_order": FEATURES_ALL,
                "horizon": h,
                "clip_fit": list(CLIP),
                "params": LGB_PARAMS,
                "seed": SEED,
            },
            jl,
        )
        model.booster_.save_model(str(txt))
        saved += [str(jl), str(txt)]
    (MODEL_DIR / "feature_order.json").write_text(
        json.dumps(
            {
                "feature_order": FEATURES_ALL,
                "n_features": len(FEATURES_ALL),
                "excludes": ["day", "ticker", "t", "admit_t", "labels", "exit_*"],
            },
            indent=2,
        )
        + "\n"
    )
    return saved


def load_models(model_dir: Path) -> dict:
    return {h: joblib.load(model_dir / f"payoff_h{h}.joblib") for h in HORIZONS}


def predict_signal(
    state: dict, horizon: int, model_dir: Path = MODEL_DIR, model_dir_cache: dict | None = None
) -> float:
    """Reusable per-state predictor: dict{feature: value} -> expected realized gross payoff."""
    if model_dir_cache is None:
        model_dir_cache = load_models(model_dir)
    order = model_dir_cache[horizon]["feature_order"]
    x = np.array([[float(state[c]) for c in order]], dtype=float)
    return float(model_dir_cache[horizon]["lgbm"].predict(x)[0])


# ----- chosen-strategy artifacts ---------------------------------------------
def save_strategy_artifacts(
    tag: str, cand: pl.DataFrame, horizon: int, threshold: float, days: list[str], cost_bps: float
) -> dict:
    sig = make_signals(cand, horizon, threshold, days)
    metrics, trades = replay(sig, days, horizon, cost_bps)
    d = OUTPUT / f"strategy_{tag}"
    d.mkdir(parents=True, exist_ok=True)
    sig.write_parquet(d / f"signals_{tag}.parquet")
    tr = pl.DataFrame(trades)
    if tr.height:
        tr = tr.with_columns(
            (pl.col("entry_open") * (1.0 + pl.col("gross"))).alias("exit_open_proxy")
        )
    tr.write_parquet(d / f"trades_{tag}.parquet")
    summary = metrics_view(metrics)
    summary["bootstrap_daily"] = bootstrap_daily(
        [r["lower_bound_return"] for r in metrics["daily"]]
    )
    summary["monthly"] = monthly_accounting(metrics, trades)
    (d / f"metrics_{tag}.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    np.savez(
        d / f"daily_{tag}.npz",
        day=np.array([r["day"] for r in metrics["daily"]]),
        lower_bound_return=np.array([r["lower_bound_return"] for r in metrics["daily"]]),
    )
    return summary


# ----- main pipeline ---------------------------------------------------------
def write_freeze(chosen, surface: dict, model_report: dict, prov: dict, chosen_file: Path) -> None:
    frozen = {
        "frozen_before_confirmation": True,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "selection": {
            "objective": "validation mean_daily_lower_bound",
            "selection_cost_bps": SELECT_COST,
            "power_floor": {"min_known_fills": MIN_KNOWN_FILLS, "min_traded_days": MIN_TRADED_DAYS},
            "median_or_tail_gates": None,
        },
        "chosen": chosen,
        "validation_surface": {f"{h}|{thr:.2f}": v for (h, thr), v in surface.items()},
        "model_report": model_report,
        "provenance": prov,
        "feature_order": FEATURES_ALL,
    }
    chosen_file.write_text(json.dumps(frozen, indent=2, default=str) + "\n")


def cmd_run(args) -> None:
    t0 = time.time()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), OUTPUT / "producer_snapshot.py")

    frame, calendar = load_panel(PANEL_ROOT)
    print(f"[panel] {len(calendar)} days, {frame.height} rows", flush=True)
    frame = prepare(frame)
    prov = {
        "panel_root": str(PANEL_ROOT),
        "panel_contract_sha256": sha256_file(PANEL_ROOT / "contract.json"),
        "producer_sha256": sha256_file(Path(__file__)),
        "calendar_days": len(calendar),
        "rows_total": int(frame.height),
    }

    # 1) fit three fixed payoff models on 2021-2022 only.
    models, model_report = fit_models(frame)
    saved = save_models(models)
    print(f"[fit] models saved: {saved}", flush=True)
    for h in HORIZONS:
        r = model_report[h]
        print(
            f"  h={h}: train_rows={r['train_rows']} "
            f"label_mean={r['label_unclipped']['mean']:.4f} p50={r['label_unclipped']['p50']:.4f}",
            flush=True,
        )

    cand, days_by_period = score_frame(frame, models)
    liq = causal_liquidity(frame)
    prov["rows_liquidity_qualified"] = int(frame.filter(liq).height)
    prov["days_by_period"] = {k: len(v) for k, v in days_by_period.items()}

    val_days = days_by_period[VAL_PERIOD]
    # 2) full validation surface at the selection cost.
    surface = {}
    for h in HORIZONS:
        for thr in THRESHOLDS:
            sig = make_signals(cand, h, thr, val_days)
            metrics, trades = replay(sig, val_days, h, SELECT_COST)
            view = metrics_view(metrics)
            view["bootstrap_daily"] = bootstrap_daily(
                [r["lower_bound_return"] for r in metrics["daily"]]
            )
            view["n_signals"] = int(sig.height)
            surface[(h, thr)] = view
            print(
                f"[val] h={h} thr={thr:.2f} known_fills={view['known_fills']} "
                f"traded_days={view['traded_days']} mdlb={view['mean_daily_lower_bound']}",
                flush=True,
            )

    # 3) selection under the power floor (not median/tail).
    eligible = {
        k: v
        for k, v in surface.items()
        if v["known_fills"] >= MIN_KNOWN_FILLS and v["traded_days"] >= MIN_TRADED_DAYS
    }
    chosen = None
    if eligible:
        (ch, cthr) = max(
            eligible,
            key=lambda k: (
                eligible[k]["mean_daily_lower_bound"],
                eligible[k]["known_fills"],
                -k[0],
                -k[1],
            ),
        )
        chosen = {
            "horizon": ch,
            "threshold": cthr,
            "validation_mean_daily_lower_bound": eligible[(ch, cthr)]["mean_daily_lower_bound"],
            "validation_bootstrap": eligible[(ch, cthr)]["bootstrap_daily"],
            "selection_cost_bps": SELECT_COST,
            "rationale": (
                f"highest validation mean_daily_lower_bound among "
                f"{len(eligible)}/{len(surface)} alternatives clearing the power floor"
            ),
        }

    # 4) FREEZE before any confirmation inspection.
    freeze_path = OUTPUT / "frozen_contract.json"
    write_freeze(chosen, surface, model_report, prov, freeze_path)
    (OUTPUT / "surface_validation.json").write_text(
        json.dumps(
            {f"{h}|{thr:.2f}": metrics_view(v) for (h, thr), v in surface.items()},
            indent=2,
            default=str,
        )
        + "\n"
    )
    print(f"[freeze] chosen={chosen} -> {freeze_path}", flush=True)

    report = {
        "chosen": chosen,
        "validation_surface": {
            f"{h}|{thr:.2f}": metrics_view(v) for (h, thr), v in surface.items()
        },
        "provenance": prov,
        "model_report": model_report,
    }

    # 5) confirmation only for the one frozen strategy, at the cost ladder, no refit.
    if chosen is None:
        print("[confirm] no eligible candidate; skipping confirmation", flush=True)
        report["decision"] = "NO CANDIDATE (no alternative cleared the power floor)"
        report["confirmation"] = None
    else:
        h, thr = chosen["horizon"], chosen["threshold"]
        conf_days = days_by_period[CONF_PERIOD]
        conf = {}
        trades_by_cost = {}
        for cost in CONFIRM_COSTS:
            sig = make_signals(cand, h, thr, conf_days)
            metrics, trades = replay(sig, conf_days, h, cost)
            view = metrics_view(metrics)
            view["bootstrap_daily"] = bootstrap_daily(
                [r["lower_bound_return"] for r in metrics["daily"]]
            )
            conf[str(int(cost))] = view
            trades_by_cost[int(cost)] = (metrics, trades)
        m0, t0c = trades_by_cost[SELECT_COST]
        report["confirmation"] = conf
        report["confirmation_cost_effect"] = cost_effect(conf)
        report["confirmation_monthly"] = monthly_accounting(m0, t0c)
        report["decision"] = (
            f"frozen h={h} thr={thr:.2f}; confirmation mean_daily_lower_bound "
            f"{conf[str(int(SELECT_COST))]['mean_daily_lower_bound']:.5f} @100bps, "
            f"{conf['150']['mean_daily_lower_bound']:.5f} @150, "
            f"{conf['200']['mean_daily_lower_bound']:.5f} @200"
        )

        # chosen-strategy artifacts for parent quote-repricing (entry/exit times + prices).
        val_summary = save_strategy_artifacts("validation", cand, h, thr, val_days, SELECT_COST)
        conf_summary = save_strategy_artifacts("confirmation", cand, h, thr, conf_days, SELECT_COST)
        report["strategy_validation"] = val_summary
        report["strategy_confirmation"] = conf_summary
        print(f"[confirm] h={h} thr={thr:.2f} -> {report['decision']}", flush=True)

    report["runtime_s"] = round(time.time() - t0, 1)
    report["novelty"] = NOVELTY
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"[done] report -> {OUTPUT / 'report.json'} ({report['runtime_s']}s)", flush=True)


def cmd_replay(args) -> None:
    """Reproduce a runnable strategy score from saved models (no refit)."""
    bundle = load_models(MODEL_DIR)
    models = {h: bundle[h]["lgbm"] for h in HORIZONS}
    frame, _ = load_panel(PANEL_ROOT)
    frame = prepare(frame)
    cand, days_by_period = score_frame(frame, models)
    days = days_by_period[args.period]
    sig = make_signals(cand, args.horizon, args.threshold, days)
    metrics, trades = replay(sig, days, args.horizon, args.cost)
    tag = f"h{args.horizon}_thr{int(args.threshold * 100):02d}_{args.period}_{int(args.cost)}"
    d = OUTPUT / "replay"
    d.mkdir(parents=True, exist_ok=True)
    sig.write_parquet(d / f"signals_{tag}.parquet")
    tr = pl.DataFrame(trades)
    if tr.height:
        tr = tr.with_columns(
            (pl.col("entry_open") * (1.0 + pl.col("gross"))).alias("exit_open_proxy")
        )
    tr.write_parquet(d / f"trades_{tag}.parquet")
    view = metrics_view(metrics)
    view["bootstrap_daily"] = bootstrap_daily([r["lower_bound_return"] for r in metrics["daily"]])
    (d / f"metrics_{tag}.json").write_text(json.dumps(view, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {
                "tag": tag,
                "mean_daily_lower_bound": view["mean_daily_lower_bound"],
                "known_fills": view["known_fills"],
                "traded_days": view["traded_days"],
            },
            indent=2,
        ),
        flush=True,
    )


def cmd_predict(args) -> None:
    """Reusable per-state predictor demo."""
    state = json.loads(args.state) if args.state else None
    if state is None and args.file:
        state = pl.read_parquet(Path(args.file)).to_dicts()[0]
    if state is None:
        raise SystemExit("provide --state JSON or --file parquet")
    out = {h: predict_signal(state, h) for h in HORIZONS}
    print(
        json.dumps(
            {
                "predicted_gross": out,
                "clip_fit": list(CLIP),
                "clearance_vs_cost_bps": {h: out[h] * 10000 for h in HORIZONS},
            },
            indent=2,
        ),
        flush=True,
    )


NOVELTY = (
    "Genuinely new object vs the earlier close-close classifiers / previous-close baskets: "
    "(1) DYNAMIC open-anchored watchlist — candidates are only freshly admitted full-PIT SIP "
    "top-ten names >=5% at the open, re-derived each session (no fixed universe, no previous "
    "close fixpoint). (2) EXECUTABLE payoff target — the supervised target is the realized "
    "market-open payoff of an actual minute-t order (entry minute open, exit first open at/after "
    "entry+h, capped at session end), so training sees only filled+resolved outcomes while "
    "prediction and trading cover every liquidity-qualified observable state including genuine "
    "no-fill (cash) and UNKNOWN-exit states, which the shared replay prices explicitly at a -100% "
    "lower bound instead of dropping. (3) SPARSE fee-clearing admission — only states whose "
    "predicted payoff clears a fixed >=1%/3%/5% bar are traded, forcing the model to clear "
    "round-trip cost rather than merely tilt positive. (4) INDEPENDENT late confirmation — fit on "
    "2021-2022, select (horizon,threshold) on 2023 validation under a power floor, freeze to disk "
    "before ever looking at the 2025-02..2026-05 confirmation, which is then run once at "
    "100/150/200 bps with no refit. Novelties (1)+(2) are due to the independent open-anchored "
    "substrate; the mechanism novelty over a generic regressor is the fee-clearing "
    "sparse gate plus "
    "the freeze-before-confirmation protocol, not 'we ran LightGBM'."
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="full fit -> select -> freeze -> confirm pipeline")
    rp = sub.add_parser("replay", help="reproduce a runnable strategy from saved models")
    rp.add_argument("--horizon", type=int, choices=HORIZONS, required=True)
    rp.add_argument("--threshold", type=float, choices=THRESHOLDS, required=True)
    rp.add_argument("--period", choices=[TRAIN_PERIOD, VAL_PERIOD, CONF_PERIOD], required=True)
    rp.add_argument("--cost", type=float, default=SELECT_COST)
    pr = sub.add_parser("predict", help="predict_signal(state) for one observable state")
    pr.add_argument("--state", type=str, help="JSON dict of the 26 features")
    pr.add_argument("--file", type=str, help="parquet whose first row is the state")
    args = p.parse_args()
    {"run": cmd_run, "replay": cmd_replay, "predict": cmd_predict}[args.cmd](args)


if __name__ == "__main__":
    main()
