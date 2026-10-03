#!/usr/bin/env python3
"""LIFECYCLE-01: chronologically out-of-fold continuation estimates, never winner picking.

Input features explicitly past-only. All fitting, calibration and candidate selection
use the first 533 days. The validation command refuses to run without the hashed freeze.
Price-only vs full-state ablations show whether race/liquidity/tape earns its complexity.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import json
from pathlib import Path
for _var in ("POLARS_MAX_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "2")


import lightgbm as lgb
import numpy as np
import polars as pl
from lifecycle_features import ROUTE_FEATURES

PRICE = ["clock", "rank", "t", "tenure", "entry_px", "px", "px_age", "ret_fill",
         "peak_gain", "dd_from_high", "mae_sofar", "bars_since_high", "nh5", "nh15", "nh30",
         "streak_up", "ret1", "ret2", "ret3", "ret5", "ret10", "ret15", "accel5",
         "pm_ret5", "pm_ret15", "pm_range", "pm_gain_adj", "gain_adj"]
PRICE += ROUTE_FEATURES
FULL = PRICE + ["v5", "v15", "v30", "v60", "dvol5", "dvol15", "vol_med_ratio", "range5",
                "pm_n_bars", "pm_cum_vol", "pm_high", "pm_low", "pm_last_px", "pm_last_et",
                "race_gain", "race_rank", "race_rank_fresh", "race_n_known", "race_n_eligible",
                "race_age_min", "drank5", "drank15", "sib_ret_mean", "sib_above_fill",
                "basket_ret", "peer_ret_p25", "peer_ret_p50", "peer_ret_p75",
                "peer_dvol5_mean", "rel_dvol5", "peer_gain_gap", "leader_gap", "breadth5"]
TAPE = FULL + ["tr_n", "tr_dvol", "tr_avg_sz", "tr_n_5m", "tr_dvol_5m",
               "tr_n_15m", "tr_dvol_15m", "tr_odd_share", "tr_sweep_share",
               "tr_max_interarrival_s", "q_spread_bps_med", "q_spread_bps_last",
               "q_imb_med", "q_imb_last", "q_bid_shares_last", "q_ask_shares_last"]
TARGETS = ("V5", "V15", "V30")
PARAMS = {"objective": "regression", "metric": "l2", "num_leaves": 15,
          "max_depth": 5, "min_data_in_leaf": 600, "learning_rate": 0.05,
          "lambda_l2": 20.0, "feature_fraction": 1.0, "num_threads": 3,
          "verbosity": -1, "seed": 193, "deterministic": True, "force_col_wise": True}
ROUNDS = 100


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matrix(df: pl.DataFrame, features: list[str]) -> np.ndarray:
    x = df.select([pl.col(c).cast(pl.Float64) for c in features]).to_numpy().astype(np.float32)
    x[~np.isfinite(x)] = np.nan
    return x


def available(df: pl.DataFrame, names: list[str]) -> list[str]:
    return [c for c in names if c in df.columns and df[c].null_count() < df.height
            and (df.schema[c].is_numeric() or df.schema[c] == pl.Boolean)]


def weights(df: pl.DataFrame) -> np.ndarray:
    w = 1.0 / df.select(pl.len().over(["day", "clock", "ticker"]).alias("row_n"))["row_n"].to_numpy()
    return w * len(w) / w.sum()


def fit(df: pl.DataFrame, features: list[str], target: str) -> lgb.Booster:
    d = df.filter(pl.col(target).is_finite() & pl.col(target).is_not_null())
    # Preserve raw tails: the target is dollars, not a trimmed winner score.
    train = lgb.Dataset(matrix(d, features), label=d[target].to_numpy(),
                        weight=weights(d), feature_name=features)
    return lgb.train(PARAMS, train, num_boost_round=ROUNDS)

def forecast(model: lgb.Booster, df: pl.DataFrame, features: list[str]) -> np.ndarray:
    return model.predict(matrix(df, features), num_threads=3)


def calibration(oof: pl.DataFrame, prediction: str, target: str) -> list[dict]:
    d = oof.filter(pl.col(target).is_finite() & pl.col(prediction).is_finite())
    boundaries = sorted(set(float(d[prediction].quantile(q)) for q in (0.1, 0.3, 0.5, 0.7, 0.9)))
    out = []
    limits = [-float("inf")] + boundaries + [float("inf")]
    for lo, hi in zip(limits, limits[1:]):
        s = d.filter((pl.col(prediction) > lo) & (pl.col(prediction) <= hi))
        days = s.group_by("day").agg(pl.col(target).mean().alias("realized"))
        n = days.height
        mean = float(days["realized"].mean()) if n else 0.0
        se = float(days["realized"].std() or 0) / max(1, n) ** 0.5
        out.append({"lo": lo if np.isfinite(lo) else None,
                    "hi": hi if np.isfinite(hi) else None,
                    "ev": mean, "day_se": se, "days": n, "rows": s.height})
    return out


def calibrate(x: np.ndarray, bins: list[dict]) -> np.ndarray:
    out = np.zeros(len(x), dtype=np.float32)
    for b in bins:
        lo = -np.inf if b["lo"] is None else b["lo"]
        hi = np.inf if b["hi"] is None else b["hi"]
        # Require independent calendar support; don't manufacture certainty from minutes.
        out[(x > lo) & (x <= hi)] = b["ev"] if b["days"] >= 30 else 0.0
    return out


def discover(df: pl.DataFrame, split: dict, dest: Path) -> dict:
    days = split["discovery_days"]
    if set(df["day"].unique().to_list()) != set(days):
        raise ValueError("discovery frame must contain all and only 533 discovery days")
    dest.mkdir(parents=True, exist_ok=True)
    df = df.filter(pl.col("filled_asof") & pl.col("ret_fill").is_finite())
    supported_tape = pl.col("q_spread_bps_med").is_finite() & (pl.col("tr_n") > 0)
    all_oof = []
    models, feature_sets, calibrations = {}, {}, {}
    # Expanding chronological training, no same-day folds or name-row random split.
    for family, allowlist in (("price", PRICE), ("full", FULL), ("tape", TAPE)):
        base = df.filter(supported_tape) if family == "tape" else df
        features = available(base, allowlist)
        if len(features) < 10:
            raise ValueError(f"insufficient causal features for {family}: {features}")
        feature_sets[family] = features
        for start, stop in ((200, 311), (311, 422), (422, 533)):
            # Sampling training only does not coarsen scored paths: forecast EVERY minute.
            train = base.filter(pl.col("day").is_in(days[:start]) & (pl.col("t") % 3 == 0))
            test = base.filter(pl.col("day").is_in(days[start:stop]))
            keep = ["day", "clock", "rank", "ticker", "t"] + list(TARGETS)
            scored = test.select(keep)
            for target in TARGETS:
                model = fit(train, features, target)
                scored = scored.with_columns(pl.Series(f"pred_{target}", forecast(model, test, features)))
            scored = scored.with_columns(pl.lit(family).alias("family"), pl.lit(start).alias("fold_train_days"))
            all_oof.append(scored)
        oof = pl.concat([o for o in all_oof if o["family"][0] == family])
        for target in TARGETS:
            calibrations[f"{family}_{target}"] = calibration(oof, f"pred_{target}", target)
            final = fit(base.filter(pl.col("t") % 3 == 0), features, target)
            path = dest / f"{family}_{target}.txt"
            final.save_model(str(path))
            models[f"{family}_{target}"] = {"path": str(path), "sha256": hash_file(path)}
            importance = pl.DataFrame({"feature": features, "gain": final.feature_importance(importance_type="gain")})
            importance.sort("gain", descending=True).write_parquet(dest / f"importance_{family}_{target}.parquet")
    oof_all = pl.concat(all_oof)
    oof_all.write_parquet(dest / "oof.parquet")
    cfg = {"models": models, "features": feature_sets, "calibration": calibrations,
           "parameters": PARAMS, "rounds": ROUNDS, "training_days": days,
           "oof_scored_days": days[200:], "target_definition": "gross next-open hold/sell-1; fees handled by replay",
           "training_row_weight": "equal total weight per day-clock-name, untrimmed tails",
           "training_sampling": "t modulo3; every minute scored",
           "feature_gate": "explicit allowlists; future labels, full PM summaries and fills forbidden",
           "tape_support": "q_spread_bps_med finite and tr_n>0; matched-coverage crosscheck only; archive coverage is selected, not a universal policy signal"}
    (dest / "model_config.json").write_text(json.dumps(cfg, indent=2, allow_nan=False) + "\n")
    return cfg


def score(df: pl.DataFrame, config: dict, family: str) -> pl.DataFrame:
    features = config["features"][family]
    missing = set(features) - set(df.columns)
    if missing:
        raise ValueError(f"missing frozen columns: {sorted(missing)}")
    for target in TARGETS:
        record = config["models"][f"{family}_{target}"]
        path = Path(record["path"])
        if hash_file(path) != record["sha256"]:
            raise ValueError("frozen model hash mismatch")
        model = lgb.Booster(model_file=str(path))
        pred = forecast(model, df, features)
        if family == "tape":
            mask = df.select(supported_tape := (pl.col("q_spread_bps_med").is_finite()
                                               & (pl.col("tr_n") > 0))).to_series().fill_null(False).to_numpy()
            pred[~mask] = np.nan
        ev = calibrate(pred, config["calibration"][f"{family}_{target}"])
        df = df.with_columns(pl.Series(f"pred_{target}", pred), pl.Series(f"ev_{target}", ev))
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("discover", "score"), required=True)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--family", choices=("price", "full", "tape"), default="full")
    a = ap.parse_args()
    split_path = a.root / "split.json"
    split = json.loads(split_path.read_text())
    dest = a.root / "models"
    if a.stage == "discover":
        df = pl.read_parquet(a.input)
        discover(df, split, dest)
        print("discovery OOF and final models written; validation not inspected")
    else:
        # Lock first: never read the test-half input before the freeze verifies.
        from lifecycle_freeze import verify as verify_freeze
        freeze = verify_freeze(a.root)
        config_path = dest / "model_config.json"
        if freeze["model_config_sha256"] != hash_file(config_path):
            raise ValueError("model config changed since freeze")
        if freeze["split_sha256"] != hash_file(split_path):
            raise ValueError("chronology changed since freeze")
        df = pl.read_parquet(a.input)
        if set(df["day"].unique().to_list()) != set(split["validation_days"]):
            raise ValueError("validation frame must contain exactly the frozen second533 days")
        config = json.loads(config_path.read_text())
        scored = score(df, config, a.family)
        scored.write_parquet(a.root / f"validation_scores_{a.family}.parquet")
        print(f"frozen validation scored rows={scored.height}; no refitting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
