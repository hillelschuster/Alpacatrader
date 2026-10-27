from __future__ import annotations

import os
import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, "factory/scripts")

from depth_bot_features import (  # noqa: E402
    SIGNAL_THRESHOLD,
    IncrementalDepthFeatures,
    day_context,
    load_model,
)

ROOT = Path.home() / "alpha-data/open-search-v1"
BAR_ROOT = Path("/home/hillel/algo projects/Alpacatrader/data/sip/net/bars")
PANEL_ROOT = ROOT / "days"
STATE_ROOT = ROOT / "adaptive_depth_sizing/states"
DAYS = ("2023-01-19", "2023-05-15", "2025-02-04")
FEATURES = (
    "minute", "admission_age", "rank_snapshot", "admit_gain", "gain_open", "log_price",
    "ret1", "ret3", "ret5", "ret15", "dd_high15", "dd_day_high", "rebound_low5",
    "range5", "range15", "log_cum_dv", "log_dv5", "dv_accel", "efficiency15", "bars15",
    "since_high", "vwap_dist", "peer_positive3", "peer_gain_mean", "peer_breadth10",
    "peer_heat",
)


def _replay_day(day: str, model):
    panel_path, bars_path, states_path = (
        PANEL_ROOT / f"{day}.parquet",
        BAR_ROOT / f"{day}.parquet",
        STATE_ROOT / f"{day}.parquet",
    )
    if not all(path.exists() for path in (panel_path, bars_path, states_path)):
        pytest.skip(f"frozen parity inputs unavailable for {day}")
    panel = pl.read_parquet(panel_path)
    states = pl.read_parquet(states_path)
    raw = pl.read_parquet(bars_path).select(
        "ticker", "et", "open", "high", "low", "close", "volume"
    )
    session_end = int(panel["session_end"][0])
    engine = IncrementalDepthFeatures(day, session_end, model)
    by_clock = {}
    for minute in sorted(raw["et"].unique().to_list()):
        engine.add_bars(raw.filter(pl.col("et") == minute))
        t = int(minute) + 1
        if t >= 575 and t % 5 == 0 and t <= min(895, session_end - 1):
            by_clock[t] = (engine.feature_frame(t), engine.decision(t))

    feature_frames = [pair[0] for pair in by_clock.values() if pair[0].height]
    score_frames = [pair[1] for pair in by_clock.values() if pair[1].height]
    features = pl.concat(feature_frames, how="diagonal_relaxed").sort(["ticker", "t"])
    scored = pl.concat(score_frames, how="diagonal_relaxed").sort(["ticker", "t"])
    expected = day_context(panel).sort(["ticker", "t"])
    expected_admissions = {
        (row["ticker"], row["admit_t"])
        for row in expected.select("ticker", "admit_t").unique().iter_rows(named=True)
    }
    actual_admissions = {
        (ticker, values[0][0]) for ticker, values in engine.admissions.items()
    }
    admission_missing = expected_admissions - actual_admissions
    admission_extra = actual_admissions - expected_admissions
    assert not admission_missing and not admission_extra, (
        f"admission mismatch for {day}: missing={len(admission_missing)}, "
        f"extra={len(admission_extra)}"
    )

    # Rank snapshots are represented on the panel lattice as top-10 ranks or sentinel 11.
    rank_mismatches = 0
    for t, rank_map in engine.ranks.items():
        expected_rank = expected.filter(pl.col("t") == t).select("ticker", "rank_snapshot")
        for row in expected_rank.iter_rows(named=True):
            rank_mismatches += int(rank_map.get(row["ticker"], 11) != int(row["rank_snapshot"]))
    assert rank_mismatches == 0, f"rank snapshot mismatches for {day}: {rank_mismatches}"

    expected_features = expected.select("ticker", "t", *FEATURES)
    expected_feature_keys = set(expected.select("ticker", "t").iter_rows())
    actual_feature_keys = set(features.select("ticker", "t").iter_rows())
    missing_features = expected_feature_keys - actual_feature_keys
    extra_features = actual_feature_keys - expected_feature_keys
    assert not missing_features and not extra_features, (
        f"feature row mismatch for {day}: missing={len(missing_features)}, "
        f"extra={len(extra_features)}"
    )
    actual_features = features.select("ticker", "t", *FEATURES)
    feature_diff = expected_features.join(actual_features, on=["ticker", "t"], suffix="_actual")
    mismatch_counts = {}
    for name in FEATURES:
        mismatch_counts[name] = feature_diff.filter(
            (pl.col(name).is_null() != pl.col(f"{name}_actual").is_null())
            | ((pl.col(name) - pl.col(f"{name}_actual")).abs() > 1e-9)
        ).height
    assert not any(mismatch_counts.values()), (
        f"feature mismatch counts for {day}: {mismatch_counts}"
    )

    expected_scores = states.select("ticker", "t", "pred_60").sort(["ticker", "t"])
    expected_score_keys = set(states.select("ticker", "t").iter_rows())
    actual_score_keys = set(scored.select("ticker", "t").iter_rows())
    missing_scores = expected_score_keys - actual_score_keys
    extra_scores = actual_score_keys - expected_score_keys
    assert not missing_scores and not extra_scores, (
        f"scored-row mismatch for {day}: missing={len(missing_scores)}, "
        f"extra={len(extra_scores)}"
    )
    actual_scores = scored.select("ticker", "t", "pred_60").sort(["ticker", "t"])
    score_diff = expected_scores.join(actual_scores, on=["ticker", "t"], suffix="_actual")
    score_mismatches = score_diff.filter(
        (pl.col("pred_60") - pl.col("pred_60_actual")).abs() > 1e-9
    ).height
    assert score_mismatches == 0, f"prediction mismatches for {day}: {score_mismatches}"
    expected_signals = set(
        states.filter(pl.col("pred_60") >= SIGNAL_THRESHOLD).select("ticker", "t").iter_rows()
    )
    actual_signals = set(
        scored.filter(pl.col("pred_60") >= SIGNAL_THRESHOLD).select("ticker", "t").iter_rows()
    )
    assert actual_signals == expected_signals, (
        f"signal mismatch for {day}: missing={len(expected_signals - actual_signals)}, "
        f"extra={len(actual_signals - expected_signals)}"
    )
    return {
        "panel_rows": expected.height,
        "scored_rows": scored.height,
        "signal_rows": len(expected_signals),
        "rank_mismatches": rank_mismatches,
        "feature_mismatches": mismatch_counts,
        "prediction_mismatches": score_mismatches,
    }



@pytest.fixture(scope="module")
def h60_model():
    model_path = Path(
        os.path.expanduser("~/alpha-data/open-search-v1/learned/models/payoff_h60.joblib")
    )
    if not model_path.exists():
        pytest.skip("frozen payoff_h60 model unavailable")
    return load_model()


@pytest.mark.parametrize("day", DAYS)
def test_incremental_features_match_panel_and_depth_states(day, h60_model):
    report = _replay_day(day, h60_model)
    assert report["panel_rows"] > 0


def test_selected_arm_has_a_trade_on_parity_sample():
    trades = ROOT / "adaptive_depth_sizing/replay/validation/2023-01-19.trades.parquet"
    assert trades.exists(), "chosen-arm trade artifact missing for selected validation sample"
    frame = pl.read_parquet(trades)
    assert frame.filter(pl.col("arm_key") == "thr300bps|p50").height > 0
