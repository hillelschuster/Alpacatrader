#!/usr/bin/env python3
"""Compare compact and monotone h60 selectors in the causal adaptive-depth execution."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_adaptive_depth_sizing as depth
from alpha_open_learned import CLIP, FEATURES_ALL, LGB_PARAMS, feature_matrix
from alpha_open_panel import allowed
from alpha_open_sim import causal_liquidity
from alpha_retained_book_shape import day_context

ROOT = Path.home() / "alpha-data/open-search-v1"
OUTPUT = ROOT / "selector_upgrade"
TRAIN_DAYS = ("2021-05-01", "2022-12-31")


def training_rows() -> pl.DataFrame:
    """Read only permitted fit years; use original filled-proxy h60 target contract."""
    parts = []
    for path in sorted((ROOT / "days").glob("202[12]-*.parquet")):
        day = path.stem
        if not TRAIN_DAYS[0] <= day <= TRAIN_DAYS[1]:
            continue
        frame = day_context(pl.read_parquet(path))
        cand = frame.filter(
            causal_liquidity(frame)
            & ((pl.col("t") % 15) == 0)
            & (pl.col("entry_status") == "filled_proxy")
            & pl.col("gross_60").is_not_null()
        )
        if cand.height:
            parts.append(cand.select(FEATURES_ALL + ["gross_60", "day"]))
    if not parts:
        raise RuntimeError("no in-window training rows found")
    return pl.concat(parts)


def fit_candidates(train: pl.DataFrame) -> dict:
    x = feature_matrix(train)
    y = np.clip(train["gross_60"].to_numpy().astype(float), *CLIP)
    models = {"current": depth.load_stored_model()[0][60]["lgbm"]}
    small = dict(LGB_PARAMS, num_leaves=7, min_data_in_leaf=600, n_estimators=150)
    mono = dict(LGB_PARAMS, num_leaves=7, min_data_in_leaf=600, n_estimators=150,
                monotone_constraints=[1] * len(FEATURES_ALL))
    for name, params in (("compact", small), ("monotone_compact", mono)):
        model = lgb.LGBMRegressor(**params)
        model.fit(x, y)
        models[name] = model
    return models


def deciles(frame: pl.DataFrame, model, label: str) -> list[dict]:
    # Proxy outcome calibration across all qualifying states; executed returns are separate.
    score = np.asarray(model.predict(feature_matrix(frame)), dtype=float)
    z = frame.with_columns(pl.Series("score", score)).with_columns(
        pl.col("score")
        .qcut(10, labels=[str(i) for i in range(10)], allow_duplicates=True)
        .alias("decile")
    )
    return [{"decile": r[0], "n": r[1], "mean_score": r[2], "mean_proxy_gross_60": r[3]}
            for r in z.group_by("decile").agg(pl.len().alias("n"), pl.col("score").mean(),
                                                pl.col("gross_60").mean()).sort("decile").iter_rows()]


def actual_trade_deciles(block: str) -> list[dict]:
    """Executed-return calibration of incumbent at the audited thr300/p50 arm."""
    root = ROOT / "adaptive_depth_sizing" / "replay" / block
    paths = sorted(root.glob("*.trades.parquet"))
    trades = pl.concat([pl.read_parquet(path) for path in paths]) if paths else pl.DataFrame()
    if not trades.height:
        return []
    trades = trades.filter((pl.col("arm_key") == "thr300bps|p50") & (pl.col("qty") > 0))
    trades = trades.with_columns(
        (pl.col("net_usd_25") / (pl.col("qty") * pl.col("entry_ask"))).alias("return_25")
    ).with_columns(
        pl.col("pred").qcut(4, labels=["Q1-low", "Q2", "Q3", "Q4-high"],
                            allow_duplicates=True).alias("pred_bucket")
    )
    return [
        {"bucket": row[0], "trades": row[1], "known_fills": row[2], "unknown": row[3],
         "mean_pred": row[4], "mean_return_25": row[5], "mean_net_usd_25": row[6]}
        for row in trades.group_by("pred_bucket").agg(
            pl.len().alias("trades"),
            (pl.col("fill_status") == "conditional_fill").sum().alias("known_fills"),
            pl.col("fill_status").str.contains("unknown").sum().alias("unknown"),
            pl.col("pred").mean().alias("mean_pred"),
            pl.col("return_25").filter(pl.col("fill_status") == "conditional_fill")
            .mean().alias("mean_return_25"),
            pl.col("net_usd_25").filter(pl.col("fill_status") == "conditional_fill")
            .mean().alias("mean_net_usd_25"),
        ).sort("pred_bucket").iter_rows()
    ]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=OUTPUT)
    p.add_argument("--days", nargs="*", help="restricted debug calendar only")
    p.add_argument("--calibration-only", action="store_true",
                   help="add executed incumbent trade-return buckets to existing results")
    args = p.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    if args.calibration_only:
        result_path = out / "results.json"
        report = json.loads(result_path.read_text())
        report["calibration_executed_incumbent"] = {
            "arm": "thr300bps|p50", "blocks": {
                block: actual_trade_deciles(block)
                for block in (depth.VAL_BLOCK, depth.LATE_BLOCK)
            }
        }
        result_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        return
    training = training_rows()
    models = fit_candidates(training)
    report = {"study": "selector_upgrade",
              "target": "h60 filled_proxy gross_60 clipped [-0.5,2.0] for fit",
              "fit_window": [training["day"].min(), training["day"].max()],
              "fit_rows": training.height, "models": {},
              "protected_unread": ["2024-*", "2025-01-*", "2026-06..2026-08"]}
    for name, model in models.items():
        path = out / f"{name}.joblib"
        import joblib
        joblib.dump({"lgbm": model, "horizon": 60, "feature_order": FEATURES_ALL}, path)
        report["models"][name] = {"path": str(path), "features": FEATURES_ALL,
                                  "params": model.get_params()}
    # Score + execute exactly the original depth-lane consumer for each candidate.
    # Selector threshold is selected on validation from the existing three fixed thresholds;
    # report all three so the neighboring cells expose plateaus, not a single spike.
    all_results = {}
    for name, model in models.items():
        bundle = {60: {"lgbm": model}}
        root = out / name
        root.mkdir(exist_ok=True)
        val = depth.block_days(args.days, depth.VAL_BLOCK)
        late = depth.block_days(args.days, depth.LATE_BLOCK)
        depth.score_block(root, bundle, val, True, depth.SUPPLEMENT_ROOTS)
        depth.replay_block(root, depth.VAL_BLOCK, val, True, depth.SUPPLEMENT_ROOTS)
        vs, _ = depth.aggregate_block(root, depth.VAL_BLOCK, val)
        chosen, rank = depth.choose_view(vs)
        # Same trained candidate and threshold apply to late; no late selection.
        if late:
            depth.score_block(root, bundle, late, True, depth.SUPPLEMENT_ROOTS)
            depth.replay_block(root, depth.LATE_BLOCK, late, True, depth.SUPPLEMENT_ROOTS)
            ls, _ = depth.aggregate_block(root, depth.LATE_BLOCK, late)
        else:
            ls = None
        all_results[name] = {
            "chosen_validation_arm": chosen.key, "validation_ranking": rank,
            "validation": {v.key: vs[v.key] for v in depth.VIEWS},
            "late": ({v.key: ls[v.key] for v in depth.VIEWS} if ls else None),
        }
    # Calibration on signal-candidate panel states, proxy 60-min label; actual executed returns
    # are separately present in the depth replay trade stats above.
    calibration = {}
    for block in (depth.VAL_BLOCK, depth.LATE_BLOCK):
        days = depth.block_days(args.days, block)
        frames = []
        for day in days:
            if not allowed(day):
                continue
            f = day_context(pl.read_parquet(ROOT / "days" / f"{day}.parquet"))
            f = f.filter(causal_liquidity(f) & pl.col("gross_60").is_not_null())
            if f.height:
                frames.append(f.select(FEATURES_ALL + ["gross_60"]))
        frame = pl.concat(frames) if frames else pl.DataFrame()
        calibration[block] = (
            {n: deciles(frame, m, n) for n, m in models.items()} if frame.height else {}
        )
    report["results"] = all_results
    report["calibration_proxy_deciles"] = calibration
    report["calibration_executed_incumbent"] = {
        "arm": "thr300bps|p50",
        "blocks": {block: actual_trade_deciles(block)
                   for block in (depth.VAL_BLOCK, depth.LATE_BLOCK)},
    }
    report["verdict"] = (
        "KEEP_CURRENT unless a candidate improves validation at 25bps and shows a "
        "neighboring-threshold plateau without late-only selection."
    )
    (out / "results.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(
        json.dumps(
            {"output": str(out), "models": list(models), "fit_rows": training.height},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
