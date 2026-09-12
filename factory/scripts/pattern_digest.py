#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["matplotlib", "numpy", "pandas", "pyarrow", "scikit-learn"]
# ///
# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run factory/scripts/pattern_digest.py [--self-test]
# ──────────────────
# noqa: SIZE_OK — the frozen deliverable explicitly requires one producer script.
# pyright: basic, reportMissingImports=false, reportCallIssue=false, reportGeneralTypeIssues=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportIndexIssue=false
"""Produce the frozen unsupervised top-3 minute-path digest."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd  # noqa: PANDAS_OK — required by the frozen dependency set.
from sklearn.cluster import KMeans, MiniBatchKMeans
from sklearn.metrics import silhouette_score

ROOT: Final = Path(__file__).resolve().parents[2]
LEADERBOARD: Final = ROOT / "data" / "leaderboard"
IEX_TAPE: Final = ROOT / "data" / "iex_tape"
SCRATCH: Final = ROOT / "data" / "scratch_pattern"
ARTIFACTS: Final = ROOT / "factory" / "artifacts"
JSON_OUT: Final = ARTIFACTS / "pattern_digest.json"
ASSIGNMENTS_OUT: Final = ARTIFACTS / "pattern_digest_assignments.parquet"
CARDS_OUT: Final = ARTIFACTS / "pattern_digest_cards.png"
SEED: Final = 20260912
LENGTHS: Final = (30, 60)
K_VALUES: Final = (8, 16, 24)
CHANNELS: Final = (
    "bar_return",
    "range_pct_close",
    "close_position",
    "volume_trailing20_median_ratio",
    "cumulative_gain_session_open",
    "minute_index",
)
PATH_COLUMNS: Final = ["date", "t", "ticker", "o", "h", "l", "c", "v"]
LB_COLUMNS: Final = ["date", "t", "rank", "ticker"]
FIT_LIMIT: Final = 80_000
SILHOUETTE_LIMIT: Final = 5_000
PROFILE_STRIDE: Final = 7
DTW_LIMIT: Final = 2_000
DTW_CANDIDATES: Final = 12
DTW_REFERENCES: Final = 48
SELECTION_RULE: Final = (
    "For each (L,k), fit on deterministic every-5th-minute windows (uniformly capped at 80,000). "
    "A configuration is balanced when its smallest cluster is at least 25% of "
    "equal occupancy (share >= 0.25/k). Among balanced configurations freeze the "
    "highest silhouette; ties prefer higher normalized occupancy entropy, then "
    "smaller L and k. If none balance, maximize normalized entropy, then silhouette."
)
CACHE_VERSION: Final = "v2-full-symbol-day"


@dataclass(frozen=True, slots=True)
class WindowBatch:
    paths: np.ndarray
    rows: pd.DataFrame


@dataclass(frozen=True, slots=True)
class ChannelScale:
    mean: np.ndarray
    scale: np.ndarray


@dataclass(frozen=True, slots=True)
class Candidate:
    length: int
    clusters: int
    silhouette: float
    entropy: float
    minimum_share: float
    balanced: bool
    scale: ChannelScale
    model: MiniBatchKMeans


def read_parquet_safe(path: Path, columns: list[str]) -> pd.DataFrame | None:
    """Read required columns, returning None for empty or schema-invalid files."""
    try:
        frame = pd.read_parquet(path, columns=columns)
    except (OSError, ValueError, KeyError):
        return None
    return frame if len(frame) else None


def day_windows(path_path: Path, length: int) -> WindowBatch | None:
    """Build six-channel right-aligned windows whose endpoint is causal top-3."""
    paths = read_parquet_safe(path_path, PATH_COLUMNS)
    lb_path = LEADERBOARD / path_path.name.replace("path_", "lb_", 1)
    ranks = read_parquet_safe(lb_path, LB_COLUMNS)
    if paths is None or ranks is None:
        return None
    paths = paths.drop_duplicates(["date", "ticker", "t"], keep="last").sort_values(
        ["date", "ticker", "t"]
    )
    ranks = ranks[ranks["rank"].between(1, 3)].drop_duplicates(
        ["date", "ticker", "t"], keep="last"
    )
    output_paths: list[np.ndarray] = []
    output_rows: list[pd.DataFrame] = []
    rank_index = ranks.set_index(["date", "ticker", "t"])["rank"]
    confirmed_pairs = set(
        (str(day), str(ticker))
        for day, ticker in ranks[["date", "ticker"]].drop_duplicates().itertuples(index=False, name=None)
    )
    for (day, ticker), group in paths.groupby(["date", "ticker"], sort=False):
        if (str(day), str(ticker)) not in confirmed_pairs:
            continue
        group = group.sort_values("t").reset_index(drop=True)
        if len(group) < length:
            continue
        prices = group[["o", "h", "l", "c"]].to_numpy(dtype=np.float64)
        closes = prices[:, 3]
        valid_prices = np.isfinite(prices).all(axis=1) & (closes > 0)
        if not valid_prices.all():
            continue
        first_close = closes[0]
        normalized = prices / first_close
        previous = np.r_[closes[0], closes[:-1]]
        bar_return = closes / previous - 1.0
        range_pct = (normalized[:, 1] - normalized[:, 2]) / normalized[:, 3]
        spread = normalized[:, 1] - normalized[:, 2]
        close_position = np.divide(
            normalized[:, 3] - normalized[:, 2],
            spread,
            out=np.full(len(group), 0.5),
            where=spread > 0,
        )
        volume = group["v"].to_numpy(dtype=np.float64)
        trailing_median = pd.Series(volume).rolling(20, min_periods=1).median().to_numpy()
        volume_ratio = np.divide(
            volume,
            trailing_median,
            out=np.ones(len(group)),
            where=trailing_median > 0,
        )
        cumulative_gain = closes / first_close - 1.0
        minute_index = group["t"].to_numpy(dtype=np.float64) - 570.0
        channels = np.column_stack(
            (bar_return, range_pct, close_position, volume_ratio, cumulative_gain, minute_index)
        )
        channels = np.nan_to_num(channels, nan=0.0, posinf=0.0, neginf=0.0).astype(
            np.float32
        )
        windows = np.lib.stride_tricks.sliding_window_view(
            channels, window_shape=length, axis=0
        ).transpose(0, 2, 1)
        end_positions = np.arange(length - 1, len(group))
        starts = end_positions - length + 1
        times = group["t"].to_numpy(dtype=np.int16)
        consecutive = times[end_positions] - times[starts] == length - 1
        endpoint_keys = pd.MultiIndex.from_arrays(
            (
                np.repeat(str(day), len(end_positions)),
                np.repeat(str(ticker), len(end_positions)),
                times[end_positions],
            ),
            names=["date", "ticker", "t"],
        )
        endpoint_ranks = rank_index.reindex(endpoint_keys).fillna(0).to_numpy(dtype=np.int8)
        keep = consecutive
        if not keep.any():
            continue
        output_paths.append(windows[keep])
        output_rows.append(
            pd.DataFrame(
                {
                    "date": str(day),
                    "ticker": str(ticker),
                    "window_end_min": times[end_positions][keep].astype(np.int16),
                    "rank": endpoint_ranks[keep],
                }
            )
        )
    if not output_paths:
        return None
    return WindowBatch(np.concatenate(output_paths), pd.concat(output_rows, ignore_index=True))


def fit_sample(paths: list[Path], length: int) -> WindowBatch:
    """Load or create the deterministic every-fifth-minute fit sample."""
    cache = SCRATCH / f"fit_sample_{CACHE_VERSION}_L{length}_n{FIT_LIMIT}.npz"
    if cache.exists():
        with np.load(cache, allow_pickle=False) as saved:
            cached_rows = pd.DataFrame(
                {
                    "date": saved["date"].astype(str),
                    "ticker": saved["ticker"].astype(str),
                    "window_end_min": saved["window_end_min"],
                    "rank": saved["rank"],
                }
            )
            return WindowBatch(saved["paths"], cached_rows)
    samples: list[np.ndarray] = []
    sampled_parts: list[pd.DataFrame] = []
    for path in paths:
        batch = day_windows(path, length)
        if batch is None:
            continue
        select = (batch.rows["window_end_min"].to_numpy() - 571) % 5 == 0
        samples.append(batch.paths[select])
        sampled_parts.append(batch.rows.loc[select])
    all_paths = np.concatenate(samples)
    all_rows = pd.concat(sampled_parts, ignore_index=True)
    if len(all_paths) > FIT_LIMIT:
        take = np.linspace(0, len(all_paths) - 1, FIT_LIMIT, dtype=np.int64)
        all_paths = all_paths[take]
        all_rows = all_rows.iloc[take].reset_index(drop=True)
    np.savez_compressed(
        cache,
        paths=all_paths,
        date=all_rows["date"].to_numpy(dtype="U10"),
        ticker=all_rows["ticker"].to_numpy(dtype="U16"),
        window_end_min=all_rows["window_end_min"].to_numpy(dtype=np.int16),
        rank=all_rows["rank"].to_numpy(dtype=np.int8),
    )
    return WindowBatch(all_paths, all_rows)


def channel_scale(paths: np.ndarray) -> ChannelScale:
    flat = paths.reshape(-1, len(CHANNELS)).astype(np.float64)
    mean = flat.mean(axis=0)
    scale = flat.std(axis=0)
    scale[scale < 1e-8] = 1.0
    return ChannelScale(mean.astype(np.float32), scale.astype(np.float32))


def standardized_flat(paths: np.ndarray, scale: ChannelScale) -> np.ndarray:
    normalized = (paths - scale.mean) / scale.scale
    return np.ascontiguousarray(normalized.reshape(len(paths), -1), dtype=np.float32)


def evaluate_candidates(samples: dict[int, WindowBatch]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for length in LENGTHS:
        sample = samples[length]
        scale = channel_scale(sample.paths)
        matrix = standardized_flat(sample.paths, scale)
        rng = np.random.default_rng(SEED + length)
        silhouette_indices = rng.choice(
            len(matrix), size=min(SILHOUETTE_LIMIT, len(matrix)), replace=False
        )
        for clusters in K_VALUES:
            model = MiniBatchKMeans(
                n_clusters=clusters,
                n_init=10,
                random_state=SEED,
                batch_size=4_096,
                max_iter=100,
            ).fit(matrix)
            labels = model.labels_
            shares = np.bincount(labels, minlength=clusters) / len(labels)
            entropy = float(-(shares * np.log(shares + 1e-12)).sum() / np.log(clusters))
            silhouette = float(
                silhouette_score(matrix[silhouette_indices], labels[silhouette_indices])
            )
            minimum_share = float(shares.min())
            candidates.append(
                Candidate(
                    length,
                    clusters,
                    silhouette,
                    entropy,
                    minimum_share,
                    minimum_share >= 0.25 / clusters,
                    scale,
                    model,
                )
            )
            print(
                f"candidate L={length} k={clusters}: silhouette={silhouette:.4f} "
                f"entropy={entropy:.4f} min_share={minimum_share:.4f}"
            )
    balanced = [candidate for candidate in candidates if candidate.balanced]
    if balanced:
        winner = max(
            balanced,
            key=lambda item: (item.silhouette, item.entropy, -item.length, -item.clusters),
        )
    else:
        winner = max(
            candidates,
            key=lambda item: (item.entropy, item.silhouette, -item.length, -item.clusters),
        )
    candidates.remove(winner)
    return [winner, *candidates]


def assign_full(paths: list[Path], winner: Candidate) -> tuple[pd.DataFrame, WindowBatch]:
    """Assign every eligible window and retain a deterministic profile sample."""
    assignment_parts: list[pd.DataFrame] = []
    profile_paths: list[np.ndarray] = []
    profile_rows: list[pd.DataFrame] = []
    for position, path in enumerate(paths):
        day = path.stem.removeprefix("path_")
        cache_tag = f"{CACHE_VERSION}_L{winner.length}_k{winner.clusters}_s{SEED}"
        assignment_cache = SCRATCH / f"assign_{cache_tag}_{day}.parquet"
        profile_cache = SCRATCH / f"profile_{cache_tag}_{day}.npz"
        if assignment_cache.exists() and profile_cache.exists():
            assignment = pd.read_parquet(assignment_cache)
            with np.load(profile_cache, allow_pickle=False) as saved:
                sampled_rows = pd.DataFrame(
                    {
                        "date": saved["date"].astype(str),
                        "ticker": saved["ticker"].astype(str),
                        "window_end_min": saved["window_end_min"],
                        "rank": saved["rank"],
                        "cluster": saved["cluster"],
                    }
                )
                profile_paths.append(saved["paths"])
                profile_rows.append(sampled_rows)
            assignment_parts.append(assignment)
            continue
        batch = day_windows(path, winner.length)
        if batch is None:
            continue
        labels = winner.model.predict(standardized_flat(batch.paths, winner.scale)).astype(np.int16)
        assignment = batch.rows.copy()
        assignment["L"] = np.int16(winner.length)
        assignment["cluster"] = labels
        assignment = assignment[["date", "ticker", "window_end_min", "L", "cluster", "rank"]]
        assignment.to_parquet(assignment_cache, index=False)
        take = np.arange(len(batch.paths)) % PROFILE_STRIDE == position % PROFILE_STRIDE
        sampled_rows = assignment.loc[take, ["date", "ticker", "window_end_min", "rank", "cluster"]]
        np.savez_compressed(
            profile_cache,
            paths=batch.paths[take],
            date=sampled_rows["date"].to_numpy(dtype="U10"),
            ticker=sampled_rows["ticker"].to_numpy(dtype="U16"),
            window_end_min=sampled_rows["window_end_min"].to_numpy(dtype=np.int16),
            rank=sampled_rows["rank"].to_numpy(dtype=np.int8),
            cluster=sampled_rows["cluster"].to_numpy(dtype=np.int16),
        )
        assignment_parts.append(assignment)
        profile_paths.append(batch.paths[take])
        profile_rows.append(sampled_rows)
        if (position + 1) % 100 == 0:
            print(f"assigned {position + 1}/{len(paths)} day files")
    assignments = pd.concat(assignment_parts, ignore_index=True).sort_values(
        ["date", "ticker", "window_end_min", "L", "cluster"]
    )
    profiles = WindowBatch(np.concatenate(profile_paths), pd.concat(profile_rows, ignore_index=True))
    return assignments.reset_index(drop=True), profiles


def time_bucket(minute: int) -> str:
    start = (minute // 30) * 30
    end = min(start + 29, 959)
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def summary_statistics(paths: np.ndarray) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for channel, name in enumerate(CHANNELS):
        values = paths[:, :, channel]
        statistics = np.column_stack(
            (values.mean(axis=1), values.std(axis=1), values.min(axis=1), values.max(axis=1), values[:, -1] - values[:, 0])
        )
        result[name] = {
            key: round(float(value), 6)
            for key, value in zip(
                ("median_mean", "median_std", "median_min", "median_max", "median_delta"),
                np.median(statistics, axis=0),
                strict=True,
            )
        }
    return result


def cluster_digest(
    assignments: pd.DataFrame, profiles: WindowBatch, winner: Candidate
) -> list[dict[str, int | float | str | list | dict]]:
    """Describe each cluster without linking membership to later observations."""
    rows: list[dict[str, int | float | str | list | dict]] = []
    profile_labels = profiles.rows["cluster"].to_numpy()
    profile_matrix = standardized_flat(profiles.paths, winner.scale)
    timed = assignments.assign(
        time_bucket=[time_bucket(int(value)) for value in assignments["window_end_min"]]
    )
    for cluster in range(winner.clusters):
        assigned = timed[timed["cluster"] == cluster]
        selected = profiles.paths[profile_labels == cluster]
        quantiles = np.quantile(selected, (0.25, 0.5, 0.75), axis=0)
        profile_indices = np.flatnonzero(profile_labels == cluster)
        distances = np.square(
            profile_matrix[profile_indices] - winner.model.cluster_centers_[cluster]
        ).sum(axis=1)
        examples: list[dict[str, str | int | float]] = []
        used_pairs: set[tuple[str, str]] = set()
        for local in np.argsort(distances):
            source = profiles.rows.iloc[int(profile_indices[local])]
            pair = (str(source["date"]), str(source["ticker"]))
            if pair in used_pairs:
                continue
            used_pairs.add(pair)
            examples.append(
                {
                    "date": pair[0],
                    "ticker": pair[1],
                    "window_end_min": int(source["window_end_min"]),
                    "distance_to_kmeans_center": round(float(np.sqrt(distances[local])), 6),
                }
            )
            if len(examples) == 5:
                break
        time_counts = assigned["time_bucket"].value_counts().sort_index()
        rank_counts = assigned["rank"].value_counts().sort_index()
        envelope = {
            name: {
                "q25": quantiles[0, :, channel].round(6).tolist(),
                "median": quantiles[1, :, channel].round(6).tolist(),
                "q75": quantiles[2, :, channel].round(6).tolist(),
            }
            for channel, name in enumerate(CHANNELS)
        }
        rows.append(
            {
                "cluster": cluster,
                "n_windows": int(len(assigned)),
                "share": round(float(len(assigned) / len(assignments)), 6),
                "profile_sample_n": int(len(selected)),
                "time_of_day": {
                    key: {"n": int(value), "cluster_share": round(float(value / len(assigned)), 6)}
                    for key, value in time_counts.items()
                },
                "rank": {
                    (str(int(key)) if int(key) else "outside_top3_at_window_end"): {
                        "n": int(value),
                        "cluster_share": round(float(value / len(assigned)), 6),
                    }
                    for key, value in rank_counts.items()
                },
                "examples": examples,
                "path_envelope": envelope,
                "volume_profile": envelope["volume_trailing20_median_ratio"],
                "summary_statistics_view": summary_statistics(selected),
            }
        )
    return rows


def month_stability(assignments: pd.DataFrame, clusters: int) -> dict[str, float | str | dict]:
    months = assignments["date"].str[:7]
    counts = pd.crosstab(months, assignments["cluster"]).reindex(columns=range(clusters), fill_value=0)
    shares = counts.div(counts.sum(axis=1), axis=0)
    global_shares = assignments["cluster"].value_counts(normalize=True).reindex(range(clusters), fill_value=0)
    total_variation = 0.5 * shares.sub(global_shares, axis=1).abs().sum(axis=1)
    verdict = "stable" if total_variation.mean() <= 0.10 and total_variation.max() <= 0.20 else "drifting"
    return {
        "verdict": verdict,
        "rule": "stable iff mean monthly total-variation <= 0.10 and maximum <= 0.20",
        "mean_total_variation": round(float(total_variation.mean()), 6),
        "max_total_variation": round(float(total_variation.max()), 6),
        "monthly_cluster_share": {
            month: {str(cluster): round(float(value), 6) for cluster, value in row.items()}
            for month, row in shares.iterrows()
        },
    }


def dtw_distance(left: np.ndarray, right: np.ndarray, band: int) -> float:
    """Compute multichannel Sakoe-Chiba-banded DTW distance."""
    size = len(left)
    previous = np.full(size + 1, np.inf, dtype=np.float64)
    previous[0] = 0.0
    for i in range(1, size + 1):
        current = np.full(size + 1, np.inf, dtype=np.float64)
        for j in range(max(1, i - band), min(size, i + band) + 1):
            cost = float(np.square(left[i - 1] - right[j - 1]).mean())
            current[j] = cost + min(current[j - 1], previous[j], previous[j - 1])
        previous = current
    return float(np.sqrt(previous[size] / size))


def dtw_medoids(profiles: WindowBatch, winner: Candidate) -> dict[str, int | str | list]:
    """Build illustration-only approximate DTW medoids on at most 2,000 windows."""
    take = np.linspace(0, len(profiles.paths) - 1, min(DTW_LIMIT, len(profiles.paths)), dtype=np.int64)
    raw = profiles.paths[take]
    metadata = profiles.rows.iloc[take].reset_index(drop=True)
    trajectories = (raw - winner.scale.mean) / winner.scale.scale
    flat = trajectories.reshape(len(trajectories), -1)
    medoids: list[int] = []
    for cluster in range(winner.clusters):
        members = np.flatnonzero(metadata["cluster"].to_numpy() == cluster)
        if not len(members):
            members = np.arange(len(raw))
        distance = np.square(flat[members] - winner.model.cluster_centers_[cluster]).sum(axis=1)
        medoids.append(int(members[np.argmin(distance)]))
    band = max(3, winner.length // 10)
    assignments = np.empty(len(raw), dtype=np.int16)
    distances = np.empty(len(raw), dtype=np.float32)
    for row in range(len(raw)):
        candidate_distances = [
            dtw_distance(trajectories[row], trajectories[medoid], band) for medoid in medoids
        ]
        assignments[row] = int(np.argmin(candidate_distances))
        distances[row] = candidate_distances[assignments[row]]
    refined: list[int] = []
    for cluster in range(winner.clusters):
        members = np.flatnonzero(assignments == cluster)
        if not len(members):
            refined.append(medoids[cluster])
            continue
        candidates = members[np.linspace(0, len(members) - 1, min(DTW_CANDIDATES, len(members)), dtype=np.int64)]
        references = members[np.linspace(0, len(members) - 1, min(DTW_REFERENCES, len(members)), dtype=np.int64)]
        costs = [
            sum(dtw_distance(trajectories[candidate], trajectories[reference], band) for reference in references)
            for candidate in candidates
        ]
        refined.append(int(candidates[int(np.argmin(costs))]))
    for row in range(len(raw)):
        candidate_distances = [
            dtw_distance(trajectories[row], trajectories[medoid], band) for medoid in refined
        ]
        assignments[row] = int(np.argmin(candidate_distances))
        distances[row] = candidate_distances[assignments[row]]
    medoid_rows = []
    for cluster, index in enumerate(refined):
        source = metadata.iloc[index]
        medoid_rows.append(
            {
                "dtw_cluster": cluster,
                "date": str(source["date"]),
                "ticker": str(source["ticker"]),
                "window_end_min": int(source["window_end_min"]),
                "kmeans_cluster": int(source["cluster"]),
            }
        )
    assignment_rows = []
    for index, source in metadata.iterrows():
        assignment_rows.append(
            {
                "date": str(source["date"]),
                "ticker": str(source["ticker"]),
                "window_end_min": int(source["window_end_min"]),
                "kmeans_cluster": int(source["cluster"]),
                "dtw_cluster": int(assignments[index]),
                "distance": round(float(distances[index]), 6),
            }
        )
    return {
        "purpose": "illustration only; not used for fitting or full-set assignment",
        "method": f"multichannel standardized DTW, Sakoe-Chiba band={band}, candidate-refined medoids",
        "sample_n": len(raw),
        "medoids": medoid_rows,
        "assignments": assignment_rows,
    }


def iex_coverage(path_files: list[Path]) -> dict[str, int | float | str | list]:
    """Measure IEX print coverage for the full top-3 symbol-day population."""
    top_pairs: set[tuple[str, str]] = set()
    skipped: list[str] = []
    for path in path_files:
        frame = read_parquet_safe(path, ["date", "ticker"])
        if frame is None:
            skipped.append(path.name)
            continue
        top_pairs.update((str(day), str(ticker)) for day, ticker in frame.drop_duplicates().itertuples(index=False, name=None))
    bar_counts: dict[tuple[str, str], int] = {}
    iex_files = sorted(IEX_TAPE.glob("path_*.parquet"))
    for path in iex_files:
        frame = read_parquet_safe(path, ["date", "ticker", "n_bars"])
        if frame is None:
            continue
        maximums = frame.groupby(["date", "ticker"], sort=False)["n_bars"].max()
        bar_counts.update(
            {(str(day), str(ticker)): int(value) for (day, ticker), value in maximums.items()}
        )
    present_counts = np.array([bar_counts[pair] for pair in top_pairs if pair in bar_counts], dtype=np.int32)
    zero_pairs = sorted(top_pairs.difference(bar_counts))
    logged_missing: set[tuple[str, str]] = set()
    missing_path = IEX_TAPE / "_missing.jsonl"
    if missing_path.exists():
        with missing_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                logged_missing.add((str(row["day"]), str(row["ticker"])))
    zero_set = set(zero_pairs)
    return {
        "top3_symbol_days": len(top_pairs),
        "leaderboard_day_files": len(path_files),
        "valid_leaderboard_days": len({day for day, _ticker in top_pairs}),
        "skipped_leaderboard_files": skipped,
        "iex_day_files": len(iex_files),
        "zero_iex_bars_all_day": len(zero_pairs),
        "present_in_both": len(present_counts),
        "median_iex_bar_count_present_pairs": round(float(np.median(present_counts)), 3),
        "pooled_session_minute_share_with_iex_bar_present_pairs": round(float(present_counts.sum() / (len(present_counts) * 390)), 6),
        "median_pair_session_share_with_iex_bar": round(float(np.median(present_counts / 390.0)), 6),
        "session_minutes_denominator": 390,
        "missing_log_entries": len(logged_missing),
        "missing_log_exact_match": logged_missing == zero_set,
        "zero_pairs_not_logged": [f"{day}:{ticker}" for day, ticker in sorted(zero_set - logged_missing)],
        "logged_pairs_not_zero": [f"{day}:{ticker}" for day, ticker in sorted(logged_missing - zero_set)],
    }


def plot_cards(clusters: list[dict[str, int | float | str | list | dict]], winner: Candidate) -> None:
    columns = 4
    rows = int(np.ceil(winner.clusters / columns))
    figure, axes = plt.subplots(rows, columns, figsize=(18, rows * 3.4), squeeze=False)
    x = np.arange(winner.length)
    colors = plt.cm.tab10(np.linspace(0, 1, len(CHANNELS)))
    for cluster, axis in enumerate(axes.flat):
        if cluster >= winner.clusters:
            axis.axis("off")
            continue
        item = clusters[cluster]
        envelope = item["path_envelope"]
        for channel, (name, color) in enumerate(zip(CHANNELS, colors, strict=True)):
            values = envelope[name]
            q25 = (np.asarray(values["q25"]) - winner.scale.mean[channel]) / winner.scale.scale[channel]
            median = (np.asarray(values["median"]) - winner.scale.mean[channel]) / winner.scale.scale[channel]
            q75 = (np.asarray(values["q75"]) - winner.scale.mean[channel]) / winner.scale.scale[channel]
            axis.fill_between(x, q25, q75, color=color, alpha=0.08)
            axis.plot(x, median, color=color, linewidth=1.0, label=name)
        axis.axhline(0, color="black", linewidth=0.4, alpha=0.5)
        axis.set_title(f"C{cluster}  n={int(item['n_windows']):,} ({float(item['share']):.1%})")
        axis.set_xlim(0, winner.length - 1)
        axis.grid(alpha=0.15)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=3, fontsize=8)
    figure.suptitle(
        f"Unsupervised top-3 path digest — L={winner.length}, k={winner.clusters}\n"
        "median and interquartile envelope; channels shown in corpus-standardized units",
        fontsize=14,
    )
    figure.tight_layout(rect=(0, 0.06, 1, 0.95))
    figure.savefig(CARDS_OUT, dpi=160)
    plt.close(figure)


def synthetic_paths(seed: int = SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    length = 30
    t = np.linspace(0, 1, length, dtype=np.float32)
    paths = np.empty((120, length, len(CHANNELS)), dtype=np.float32)
    for row in range(len(paths)):
        family = row // 60
        direction = 1.0 if family == 0 else -1.0
        cumulative = direction * 0.20 * t + rng.normal(0, 0.004, length)
        paths[row, :, 0] = np.r_[0.0, np.diff(cumulative)]
        paths[row, :, 1] = 0.015 + 0.004 * np.sin(t * np.pi) + rng.normal(0, 0.001, length)
        paths[row, :, 2] = 0.75 if family == 0 else 0.25
        paths[row, :, 3] = 1.0 + direction * 0.5 * t
        paths[row, :, 4] = cumulative
        paths[row, :, 5] = np.arange(length)
    return paths


def self_test() -> None:
    """Recover two deliberately distinct synthetic path families."""
    paths = synthetic_paths()
    scale = channel_scale(paths)
    labels = KMeans(n_clusters=2, n_init=10, random_state=SEED).fit_predict(
        standardized_flat(paths, scale)
    )
    first = labels[:60]
    second = labels[60:]
    recovered = max((first == 0).mean() + (second == 1).mean(), (first == 1).mean() + (second == 0).mean()) / 2
    assert recovered >= 0.95, f"synthetic family recovery={recovered:.3f}"
    print(f"self-test PASS: recovered two synthetic path families ({recovered:.1%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    SCRATCH.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path_files = sorted(LEADERBOARD.glob("path_*.parquet"))
    samples = {length: fit_sample(path_files, length) for length in LENGTHS}
    candidates = evaluate_candidates(samples)
    winner = candidates[0]
    print(f"FROZEN before illustration: L={winner.length}, k={winner.clusters}")
    assignments, profiles = assign_full(path_files, winner)
    assignments.drop(columns="rank").to_parquet(ASSIGNMENTS_OUT, index=False)
    clusters = cluster_digest(assignments, profiles, winner)
    stability = month_stability(assignments, winner.clusters)
    dtw = dtw_medoids(profiles, winner)
    coverage = iex_coverage(path_files)
    plot_cards(clusters, winner)
    artifact = {
        "study": "PRE-REG-PATTERN-01 FROZEN v1 phase 1",
        "scope": {
            "description": "Unsupervised description and illustration of causal top-3 minute-path shapes.",
            "interpretation_limit": "Shape structure only. No monetary or directional inference is made.",
            "scoring_pass_ran": False,
        },
        "frozen_config": {
            "L": winner.length,
            "k": winner.clusters,
            "seed": SEED,
            "channels": list(CHANNELS),
            "fit_sample_n": len(samples[winner.length].paths),
            "full_assignment_n": len(assignments),
            "profile_stride": PROFILE_STRIDE,
            "profile_sample_n": len(profiles.paths),
            "price_normalization": "Return/range/close-position are algebraically invariant to dividing OHLC by each window's first close; cumulative gain remains session-open anchored.",
            "algorithm": "scikit-learn MiniBatchKMeans (k-means objective), n_init=10, batch_size=4096, max_iter=100",
            "volume_normalization": "current minute volume / trailing 20-grid-minute median, retained as-is",
            "standardization_mean": winner.scale.mean.round(8).tolist(),
            "standardization_scale": winner.scale.scale.round(8).tolist(),
        },
        "configuration_selection": {
            "rule": SELECTION_RULE,
            "reason": (
                f"Selected among balanced candidates by maximum silhouette ({winner.silhouette:.6f}); "
                f"normalized occupancy entropy={winner.entropy:.6f}."
                if winner.balanced
                else f"No candidate met the occupancy-balance floor; the frozen fallback selected "
                f"maximum normalized occupancy entropy ({winner.entropy:.6f}), then silhouette "
                f"({winner.silhouette:.6f})."
            ),
            "candidates": [
                {
                    "L": candidate.length,
                    "k": candidate.clusters,
                    "silhouette": round(candidate.silhouette, 6),
                    "normalized_occupancy_entropy": round(candidate.entropy, 6),
                    "minimum_cluster_share": round(candidate.minimum_share, 6),
                    "balanced": candidate.balanced,
                    "selected": candidate is winner,
                }
                for candidate in candidates
            ],
        },
        "corpus": {
            "date_start": str(assignments["date"].min()),
            "date_end": str(assignments["date"].max()),
            "days": int(assignments["date"].nunique()),
            "source_symbol_days": int(coverage["top3_symbol_days"]),
            "assigned_symbol_days_with_at_least_L_minutes": int(assignments[["date", "ticker"]].drop_duplicates().shape[0]),
            "all_minutes_used": True,
            "membership": "Each symbol-day is confirmed to appear in lb rank 1..3 at least once. Full-session windows are retained; endpoint rank is 1..3 when currently present and outside_top3_at_window_end otherwise.",
        },
        "cluster_sizes": [
            {"cluster": int(item["cluster"]), "n_windows": int(item["n_windows"]), "share": float(item["share"])}
            for item in clusters
        ],
        "clusters": clusters,
        "month_stability": stability,
        "dtw_medoid_assignments": dtw,
        "iex_coverage": coverage,
    }
    JSON_OUT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"wrote {JSON_OUT}")
    print(f"wrote {ASSIGNMENTS_OUT} ({len(assignments):,} rows)")
    print(f"wrote {CARDS_OUT}")


if __name__ == "__main__":
    main()
