#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["matplotlib", "numpy", "pandas", "pyarrow", "scikit-learn"]
# ///
# ─── How to run ───
# uv run factory/scripts/pattern_digest_events.py --extract --extract-limit 120
# uv run factory/scripts/pattern_digest_events.py --combine
# uv run factory/scripts/pattern_digest_events.py --self-test
# ──────────────────
# noqa: SIZE_OK — the frozen deliverable explicitly requires one producer script.
# pyright: basic, reportMissingImports=false, reportCallIssue=false, reportGeneralTypeIssues=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportIndexIssue=false
"""PRE-REG-PATTERN-02 event-anchored attention digest. Descriptive only: no
outcomes, no labels, no scoring pass (that pass stays user-gated)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import MiniBatchKMeans
from sklearn.metrics import silhouette_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pattern_digest import (  # noqa: E402
    ARTIFACTS,
    Candidate,
    ChannelScale,
    FIT_LIMIT,
    K_VALUES,
    LB_COLUMNS,
    LEADERBOARD,
    PATH_COLUMNS,
    PROFILE_STRIDE,
    ROOT,
    SEED,
    SILHOUETTE_LIMIT,
    V2_CHANNELS,
    V2_STOPPING_THRESHOLD,
    WindowBatch,
    cluster_digest,
    clustering_flat,
    dtw_medoids,
    month_stability,
    read_parquet_safe,
    shape_normalized,
    time_bucket,
)

EVENT_COLUMNS: Final = [*PATH_COLUMNS, "gain_c", "n_bars"]
EVENT_TYPES: Final = ("E1_flush_touch", "E2_thrust", "E3_volume_spike")
LENGTHS: Final = (15, 30, 60)
POWER_FLOOR: Final = 2_000
FLUSH_L: Final = 0.10
VOLUME_MULTIPLE: Final = 5.0
VOLUME_WINDOW: Final = 20
CACHE_DIR: Final = ROOT / "data" / "scratch_pattern_events"
JSON_OUT: Final = ARTIFACTS / "pattern_digest_events.json"
ASSIGNMENTS_OUT: Final = ARTIFACTS / "pattern_digest_events_assignments.parquet"
CARDS_TEMPLATE: Final = "pattern_digest_events_cards_{event_type}.png"
SELECTION_RULE: Final = (
    "Per event type, candidates are (L,k) for L in {15,30,60}, k in {8,16,24}; "
    "each is MiniBatchKMeans on per-window z-scored five-channel shapes; winner = "
    "max silhouette, ties prefer fewer clusters, then shorter L. If the winner's "
    "silhouette < 0.05 the type is EXHAUSTED (no further variants)."
)


@dataclass(frozen=True, slots=True)
class DayGroups:
    groups: dict[tuple[str, int], list[tuple[np.ndarray, str, int, int]]]
    raw_counts: dict[str, int]
    skipped: dict[tuple[str, int], int]


def flush_starts(closes: np.ndarray, lows: np.ndarray, new_bar: np.ndarray) -> np.ndarray:
    """New-bar flush starts: low <= 0.9 * running max close (lb18_episodes semantics)."""
    positions = np.flatnonzero(new_bar)
    if not len(positions):
        return np.empty(0, dtype=np.int64)
    running = np.maximum.accumulate(closes[positions])
    under = lows[positions] <= running * (1.0 - FLUSH_L)
    starts = under & ~np.r_[False, under[:-1]]
    return positions[starts]


def strict_minutes(gain_c: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """Strict-state minutes on the ffilled grid (gain_c >= 1, pullback >= -1%, r15 >= 3%)."""
    cummax = np.maximum.accumulate(closes)
    pullback = closes / cummax - 1.0
    r15 = np.full(len(closes), np.nan)
    if len(closes) > 15:
        r15[15:] = closes[15:] / closes[:-15] - 1.0
    strict = (gain_c >= 1.0) & (pullback >= -0.01) & (r15 >= 0.03)
    return np.flatnonzero(np.nan_to_num(strict, nan=False))


def volume_spike_minutes(
    volume: np.ndarray, new_bar: np.ndarray, rank_at: np.ndarray
) -> np.ndarray:
    """Rank-1 new-bar minutes with volume >= 5x the causal trailing-20 median."""
    trailing = pd.Series(volume).shift(1).rolling(VOLUME_WINDOW, min_periods=1).median()
    spike = new_bar & (rank_at == 1) & (trailing.to_numpy() > 0) & (
        volume >= VOLUME_MULTIPLE * trailing.to_numpy()
    )
    return np.flatnonzero(spike)


def dedupe_positions(positions: np.ndarray, min_gap: int) -> np.ndarray:
    """Keep the first event, drop later same-type events within min_gap minutes."""
    kept: list[int] = []
    for position in positions:
        if not kept or position - kept[-1] >= min_gap:
            kept.append(int(position))
    return np.asarray(kept, dtype=np.int64)


def pair_channels(group: pd.DataFrame) -> np.ndarray | None:
    """Six-channel matrix identical to the PATTERN-01 corpus (pattern_digest.py:133-165)."""
    prices = group[["o", "h", "l", "c"]].to_numpy(dtype=np.float64)
    closes = prices[:, 3]
    if not (np.isfinite(prices).all() and (closes > 0).all()):
        return None
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
    return np.nan_to_num(channels, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def day_event_groups(path_path: Path) -> DayGroups | None:
    frame = read_parquet_safe(path_path, EVENT_COLUMNS)
    lb_path = LEADERBOARD / path_path.name.replace("path_", "lb_", 1)
    ranks = read_parquet_safe(lb_path, LB_COLUMNS)
    if frame is None or ranks is None:
        return None
    frame = frame.drop_duplicates(["date", "ticker", "t"], keep="last").sort_values(
        ["date", "ticker", "t"]
    )
    ranks = ranks[ranks["rank"].between(1, 3)].drop_duplicates(
        ["date", "ticker", "t"], keep="last"
    )
    confirmed = {
        (str(day), str(ticker))
        for day, ticker in ranks[["date", "ticker"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    }
    groups: dict[tuple[str, int], list[tuple[np.ndarray, str, int, int]]] = defaultdict(list)
    raw_counts: dict[str, int] = {event_type: 0 for event_type in EVENT_TYPES}
    skipped: dict[tuple[str, int], int] = defaultdict(int)
    for (day, ticker), group in frame.groupby(["date", "ticker"], sort=False):
        if (str(day), str(ticker)) not in confirmed:
            continue
        group = group.sort_values("t").reset_index(drop=True)
        if len(group) < 2:
            continue
        channels = pair_channels(group)
        if channels is None:
            continue
        times = group["t"].to_numpy(dtype=np.int64)
        bars = group["n_bars"].to_numpy(dtype=np.int64)
        new_bar = np.r_[bars[0] > 0, bars[1:] > bars[:-1]]
        pair_rank = ranks[(ranks["date"] == day) & (ranks["ticker"] == ticker)]
        rank_map = dict(zip(pair_rank["t"].astype(int), pair_rank["rank"].astype(int), strict=True))
        rank_at = np.asarray([rank_map.get(int(minute), 0) for minute in times], dtype=np.int8)
        closes = group["c"].to_numpy(dtype=np.float64)
        events = {
            "E1_flush_touch": flush_starts(
                closes, group["l"].to_numpy(dtype=np.float64), new_bar
            ),
            "E2_thrust": strict_minutes(group["gain_c"].to_numpy(dtype=np.float64), closes),
            "E3_volume_spike": volume_spike_minutes(
                group["v"].to_numpy(dtype=np.float64), new_bar, rank_at
            ),
        }
        for event_type, positions in events.items():
            raw_counts[event_type] += int(len(positions))
            for length in LENGTHS:
                for position in dedupe_positions(positions, length):
                    if position - length + 1 < 0:
                        skipped[(event_type, length)] += 1
                        continue
                    window = channels[position - length + 1 : position + 1]
                    groups[(event_type, length)].append(
                        (window, str(ticker), int(times[position]), int(rank_at[position]))
                    )
    return DayGroups(dict(groups), raw_counts, dict(skipped))


def cache_day(path: Path) -> bool:
    destination = CACHE_DIR / f"events_{path.stem.removeprefix('path_')}.npz"
    if destination.exists():
        return False
    built = day_event_groups(path)
    if built is None:
        np.savez_compressed(destination, empty=np.asarray(1, dtype=np.int8))
        return True
    payload: dict[str, np.ndarray] = {}
    for (event_type, length), rows in built.groups.items():
        tag = f"{event_type}|{length}"
        payload[f"{tag}|paths"] = np.stack([row[0] for row in rows]).astype(np.float32)
        payload[f"{tag}|ticker"] = np.asarray([row[1] for row in rows], dtype="U16")
        payload[f"{tag}|end"] = np.asarray([row[2] for row in rows], dtype=np.int16)
        payload[f"{tag}|rank"] = np.asarray([row[3] for row in rows], dtype=np.int8)
    for event_type, count in built.raw_counts.items():
        payload[f"raw|{event_type}"] = np.asarray(count, dtype=np.int64)
    for (event_type, length), count in built.skipped.items():
        payload[f"skipped|{event_type}|{length}"] = np.asarray(count, dtype=np.int64)
    np.savez_compressed(destination, **payload)
    return True


def extract(paths: list[Path], limit: int) -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    for position, path in enumerate(paths):
        if done >= limit:
            break
        if cache_day(path):
            done += 1
            if done % 25 == 0:
                print(f"cached {done}/{limit} new days (scan position {position + 1})")
    remaining = sum(
        1 for path in paths if not (CACHE_DIR / f"events_{path.stem.removeprefix('path_')}.npz").exists()
    )
    print(f"extract: {done} new day caches written; {remaining} days still uncached")
    return remaining


def load_groups(
    month_prefixes: set[str],
) -> tuple[dict[tuple[str, int], WindowBatch], dict[str, int], dict[tuple[str, int], int]]:
    parts: dict[tuple[str, int], list[tuple[np.ndarray, pd.DataFrame]]] = defaultdict(list)
    raw_counts: dict[str, int] = defaultdict(int)
    skipped: dict[tuple[str, int], int] = defaultdict(int)
    for cache in sorted(CACHE_DIR.glob("events_*.npz")):
        day = cache.stem.removeprefix("events_")
        if day[:7] not in month_prefixes:
            continue
        with np.load(cache, allow_pickle=False) as saved:
            for key in saved.files:
                if key.startswith("raw|"):
                    raw_counts[key.split("|", 1)[1]] += int(saved[key])
                elif key.startswith("skipped|"):
                    _, event_type, length = key.split("|")
                    skipped[(event_type, int(length))] += int(saved[key])
                elif key.endswith("|paths"):
                    event_type, length = key.split("|")[:2]
                    tag = f"{event_type}|{length}"
                    frame = pd.DataFrame(
                        {
                            "date": day,
                            "ticker": saved[f"{tag}|ticker"].astype(str),
                            "window_end_min": saved[f"{tag}|end"],
                            "rank": saved[f"{tag}|rank"],
                        }
                    )
                    parts[(event_type, int(length))].append((saved[f"{tag}|paths"], frame))
    batches: dict[tuple[str, int], WindowBatch] = {}
    for key, chunks in parts.items():
        batches[key] = WindowBatch(
            np.concatenate([chunk[0] for chunk in chunks]),
            pd.concat([chunk[1] for chunk in chunks], ignore_index=True),
        )
    return batches, dict(raw_counts), dict(skipped)


def empty_scale() -> ChannelScale:
    return ChannelScale(
        np.zeros(len(V2_CHANNELS), dtype=np.float32),
        np.ones(len(V2_CHANNELS), dtype=np.float32),
    )


def evaluate_type(
    samples: dict[int, WindowBatch],
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for length, batch in samples.items():
        matrix = clustering_flat(batch.paths, empty_scale(), "v2")
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
            candidates.append(
                Candidate(
                    length,
                    clusters,
                    silhouette,
                    entropy,
                    float(shares.min()),
                    bool(shares.min() >= 0.25 / clusters),
                    empty_scale(),
                    model,
                )
            )
            print(
                f"  candidate L={length} k={clusters}: silhouette={silhouette:.4f} "
                f"entropy={entropy:.4f} min_share={shares.min():.4f}"
            )
    winner = max(candidates, key=lambda item: (item.silhouette, -item.clusters, -item.length))
    candidates.remove(winner)
    return [winner, *candidates]


def profiles_for(batch: WindowBatch, labels: np.ndarray) -> WindowBatch:
    take = np.arange(len(batch.paths)) % PROFILE_STRIDE == 0
    for cluster in np.unique(labels):
        take[int(np.flatnonzero(labels == cluster)[0])] = True
    rows = batch.rows.loc[take].copy()
    rows["cluster"] = labels[take]
    return WindowBatch(batch.paths[take], rows.reset_index(drop=True))


def plot_type_cards(
    event_type: str, clusters: list[dict], winner: Candidate, verdict: str
) -> None:
    columns = 4
    rows = int(np.ceil(winner.clusters / columns))
    figure, axes = plt.subplots(rows, columns * 2, figsize=(24, rows * 3.5), squeeze=False)
    x = np.arange(winner.length)
    colors = plt.cm.tab10(np.linspace(0, 1, len(V2_CHANNELS)))
    for cluster in range(winner.clusters):
        row, column = divmod(cluster, columns)
        shape_axis = axes[row, column * 2]
        gain_axis = axes[row, column * 2 + 1]
        item = clusters[cluster]
        if int(item["profile_sample_n"]) == 0:
            for axis in (shape_axis, gain_axis):
                axis.text(0.5, 0.5, "empty cluster", ha="center", va="center")
            shape_axis.set_title(f"C{cluster} shape  n=0")
            gain_axis.set_title("raw cumulative gain")
            continue
        for name, color in zip(V2_CHANNELS, colors, strict=True):
            values = item["shape_envelope_zscored"][name]
            shape_axis.fill_between(x, values["q25"], values["q75"], color=color, alpha=0.08)
            shape_axis.plot(x, values["median"], color=color, linewidth=1.0, label=name)
        raw_gain = item["path_envelope"]["cumulative_gain_session_open"]
        gain_axis.fill_between(x, raw_gain["q25"], raw_gain["q75"], color="black", alpha=0.12)
        gain_axis.plot(x, raw_gain["median"], color="black", linewidth=1.2)
        shape_axis.axhline(0, color="black", linewidth=0.4, alpha=0.5)
        shape_axis.set_title(f"C{cluster} shape  n={int(item['n_windows']):,} ({float(item['share']):.1%})")
        gain_axis.set_title("raw cumulative gain")
        shape_axis.set_xlim(0, winner.length - 1)
        gain_axis.set_xlim(0, winner.length - 1)
        shape_axis.grid(alpha=0.15)
        gain_axis.grid(alpha=0.15)
    handles, legend_labels = axes.flat[0].get_legend_handles_labels()
    figure.legend(handles, legend_labels, loc="lower center", ncol=3, fontsize=8)
    figure.suptitle(
        f"PRE-REG-PATTERN-02 {event_type} — L={winner.length}, k={winner.clusters}, "
        f"verdict {verdict}\nwithin-window z-scored five-channel envelopes | raw cumulative-gain descriptor",
        fontsize=14,
    )
    figure.tight_layout(rect=(0, 0.06, 1, 0.95))
    figure.savefig(
        ARTIFACTS / CARDS_TEMPLATE.format(event_type=event_type), dpi=160
    )
    plt.close(figure)


def combine(month_prefixes: set[str]) -> None:
    batches, raw_counts, skipped = load_groups(month_prefixes)
    census: dict[str, dict] = {}
    results: dict[str, dict] = {}
    assignment_parts: list[pd.DataFrame] = []
    for event_type in EVENT_TYPES:
        lengths = {
            length: batches.get((event_type, length))
            for length in LENGTHS
        }
        counts = {
            str(length): (0 if batch is None else int(len(batch.paths)))
            for length, batch in lengths.items()
        }
        available = [batch for batch in lengths.values() if batch is not None]
        months: dict[str, int] = defaultdict(int)
        symbol_days: set[tuple[str, str]] = set()
        for batch in available:
            for day, ticker in zip(batch.rows["date"], batch.rows["ticker"], strict=True):
                months[str(day)[:7]] += 1
                symbol_days.add((str(day), str(ticker)))
        census[event_type] = {
            "raw_events_detected": int(raw_counts.get(event_type, 0)),
            "windows_by_L": counts,
            "windows_per_month": dict(sorted(months.items())),
            "distinct_symbol_days": len(symbol_days),
            "skipped_insufficient_history_by_L": {
                str(length): int(skipped.get((event_type, length), 0)) for length in LENGTHS
            },
        }
        print(f"{event_type}: windows_by_L={counts} symbol_days={len(symbol_days)}")
        largest = max(counts.values())
        if largest < POWER_FLOOR or not available:
            results[event_type] = {
                "verdict": "INCONCLUSIVE_POWER",
                "reason": f"largest L window count {largest} < power floor {POWER_FLOOR}",
                "candidates": [],
            }
            continue
        candidates = evaluate_type(
            {length: batch for length, batch in lengths.items() if batch is not None}
        )
        winner, *others = candidates
        verdict = "EXHAUSTED" if winner.silhouette < V2_STOPPING_THRESHOLD else "ANALYZED"
        batch = lengths[winner.length]
        assert batch is not None
        matrix = clustering_flat(batch.paths, winner.scale, "v2")
        labels = winner.model.labels_.astype(np.int16)
        assignments = batch.rows.copy()
        assignments["L"] = np.int16(winner.length)
        assignments["event_type"] = event_type
        assignments["cluster"] = labels
        assignments = assignments[["date", "ticker", "window_end_min", "L", "cluster", "rank", "event_type"]]
        profiles = profiles_for(batch, labels)
        clusters = cluster_digest(assignments, profiles, winner, "v2")
        stability = month_stability(assignments, winner.clusters)
        dtw = dtw_medoids(profiles, winner, "v2")
        plot_type_cards(event_type, clusters, winner, verdict)
        assignment_parts.append(assignments)
        results[event_type] = {
            "verdict": verdict,
            "power_floor": POWER_FLOOR,
            "candidates": [
                {
                    "length": candidate.length,
                    "clusters": candidate.clusters,
                    "silhouette": round(candidate.silhouette, 6),
                    "entropy": round(candidate.entropy, 6),
                    "minimum_share": round(candidate.minimum_share, 6),
                }
                for candidate in [winner, *others]
            ],
            "winner": {
                "length": winner.length,
                "clusters": winner.clusters,
                "silhouette": round(winner.silhouette, 6),
                "stopping_threshold": V2_STOPPING_THRESHOLD,
                "order": "selection order: winner first, remaining candidates after",
            },
            "cluster_sizes": [
                {"cluster": int(cluster), "n_windows": int(cluster_item["n_windows"]), "share": float(cluster_item["share"])}
                for cluster, cluster_item in enumerate(clusters)
            ],
            "clusters": clusters,
            "month_stability": stability,
            "dtw_medoids": dtw,
            "interpretation_limit": "descriptive only; membership is not a signal and no outcome was used",
        }
    if assignment_parts:
        pd.concat(assignment_parts, ignore_index=True).sort_values(
            ["event_type", "date", "ticker", "window_end_min", "cluster"]
        ).to_parquet(ASSIGNMENTS_OUT, index=False)
    payload = {
        "study": "PRE-REG-PATTERN-02 event-anchored attention digest",
        "scope": {
            "descriptive_only": True,
            "outcomes_used": False,
            "labels_used": False,
            "scoring_pass_run": False,
            "interpretation_limit": "cluster membership is not a signal; the scoring pass is user-gated",
        },
        "frozen_config": {
            "event_types": list(EVENT_TYPES),
            "lengths": list(LENGTHS),
            "k_values": list(K_VALUES),
            "seed": SEED,
            "channels": list(V2_CHANNELS),
            "power_floor": POWER_FLOOR,
            "stopping_threshold": V2_STOPPING_THRESHOLD,
            "selection_rule": SELECTION_RULE,
            "month_prefixes": sorted(month_prefixes),
        },
        "census": census,
        "types": results,
    }
    JSON_OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"wrote {JSON_OUT} ({JSON_OUT.stat().st_size} bytes)")


def self_test() -> None:
    closes = np.asarray([1.0] * 15 + [1.05, 1.2, 1.5])
    lows = closes * 0.99
    lows[17] = 1.0
    new_bar = np.ones(len(closes), dtype=bool)
    starts = flush_starts(closes, lows, new_bar)
    assert starts.tolist() == [17], starts
    gain = np.zeros(len(closes))
    gain[17] = 1.6
    strict = strict_minutes(gain, closes)
    assert 17 in strict.tolist(), strict
    volume = np.ones(len(closes))
    volume[17] = 30.0
    ranks = np.ones(len(closes), dtype=np.int8)
    spikes = volume_spike_minutes(volume, new_bar, ranks)
    assert spikes.tolist() == [17], spikes
    assert dedupe_positions(np.asarray([10, 12, 25, 41]), 15).tolist() == [10, 25, 41]
    rng = np.random.default_rng(SEED)
    matrix = np.vstack(
        [rng.normal(0, 0.3, (60, 15 * len(V2_CHANNELS))), rng.normal(2, 0.3, (60, 15 * len(V2_CHANNELS)))]
    ).astype(np.float32)
    model = MiniBatchKMeans(n_clusters=2, n_init=5, random_state=SEED, batch_size=32).fit(matrix)
    recovered = int((model.labels_[:60] == np.bincount(model.labels_[:60]).argmax()).sum())
    assert recovered >= 55, recovered
    print("self-test passed: event detection + clustering smoke test")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--combine", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--extract-limit", type=int, default=120)
    parser.add_argument("--months", type=str, default="")
    arguments = parser.parse_args()
    if arguments.self_test:
        self_test()
        return
    path_files = sorted(LEADERBOARD.glob("path_*.parquet"))
    month_prefixes = {
        month for month in arguments.months.split(",") if month
    } or {path.stem.split("_")[1][:7] for path in path_files}
    if arguments.extract or not arguments.combine:
        remaining = extract(path_files, arguments.extract_limit)
        if remaining:
            print("extract incomplete: rerun --extract to continue")
            return
    if arguments.combine:
        combine(month_prefixes)


if __name__ == "__main__":
    main()
