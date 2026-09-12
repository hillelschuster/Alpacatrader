#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow"]
# ///
# ─── How to run ───
# uv run --quiet --python 3.11 --with pandas --with pyarrow python factory/scripts/lb18_iex_hybrid.py
# uv run --quiet --python 3.11 --with pandas --with pyarrow python factory/scripts/lb18_iex_hybrid.py --self-test
# ──────────────────
"""Measure PRE-REG-MICRO-01 Amendment A1 hybrid feed variants."""

from __future__ import annotations

import sys
import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPTS: Final = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from lb18_episodes import build  # noqa: E402
from lb18_oos import FR, STOP_L, WIN, load_months, sim_tl30, stats  # noqa: E402

IEX: Final = ROOT / "data" / "iex_tape"
ART: Final = ROOT / "factory" / "artifacts"
OOS_MONTHS: Final = tuple(f"2024-{month:02d}" for month in range(1, 13)) + (
    "2025-01", "2025-02",
)
FILL_COLUMNS: Final = [
    "variant", "date", "ticker", "t0", "tf", "ret", "prior_flush", "rank", "B", "c0",
]


def _b_groups(paths: pd.DataFrame):
    return {
        key: (group["t"].to_numpy(), group["c"].to_numpy(float))
        for key, group in paths.groupby(["date", "ticker"], sort=False)
    }


def run_engine_hybrid(
    anchor_paths: pd.DataFrame,
    exec_paths: pd.DataFrame,
    b_paths: pd.DataFrame,
    lb: pd.DataFrame,
) -> pd.DataFrame:
    """Run the frozen lifecycle with independently selected state, execution, and B tapes."""
    anchor_arrays = build(anchor_paths)
    exec_arrays = build(exec_paths)
    bid_arrays = anchor_arrays if b_paths is anchor_paths else _b_groups(b_paths)
    gpb = anchor_paths.groupby(["date", "ticker"], sort=False)
    anchor_paths["pullback"] = anchor_paths["c"] / gpb["c"].transform("cummax") - 1
    anchor_paths["r15"] = anchor_paths["c"] / gpb["c"].shift(15) - 1
    merged = lb.merge(
        anchor_paths[["date", "ticker", "t", "c", "pullback", "r15", "prior_flush"]],
        on=["date", "ticker", "t"],
        how="left",
    )
    strict = (
        (merged["gain"] >= 1.0)
        & (merged["pullback"] >= -0.01)
        & (merged["r15"] >= 0.03)
    )
    strict_rows = merged[strict.fillna(False)].copy()
    strict_groups = {
        key: group.sort_values("t").reset_index(drop=True)
        for key, group in strict_rows.groupby(["date", "ticker"], sort=False)
    }

    rows = []
    for key, group in strict_groups.items():
        data = exec_arrays.get(key)
        bids = bid_arrays.get(key)
        if data is None or bids is None:
            continue
        t, o, h, low, close = data["t"], data["o"], data["h"], data["l"], data["c"]
        new_positions = data["newpos"]
        strict_times = group["t"].to_numpy()
        n = len(group)
        strict_index = 0
        bid = None
        c0 = None
        row = None
        bid_time = None
        flat = True
        exit_time = -(10**9)
        bid_times, bid_closes = bids if isinstance(bids, tuple) else (bids["t"], bids["c"])
        for new_index in range(len(new_positions)):
            position = int(new_positions[new_index])
            minute = int(t[position])
            if not flat and minute > exit_time:
                flat = True
                bid = None
                c0 = None
            while strict_index < n and int(strict_times[strict_index]) < minute:
                if flat:
                    candidate = group.iloc[strict_index]
                    anchor_minute = int(strict_times[strict_index])
                    bid_position = int(np.searchsorted(bid_times, anchor_minute))
                    if bid_position < len(bid_times) and int(bid_times[bid_position]) == anchor_minute:
                        next_c0 = float(bid_closes[bid_position])
                        if next_c0 > 0:
                            bid = next_c0 * (1 - STOP_L)
                            c0 = next_c0
                            row = candidate
                            bid_time = anchor_minute
                strict_index += 1
            if not flat or bid is None:
                continue
            if bid_time is not None and minute - bid_time > WIN:
                bid = None
                continue
            if low[position] <= bid:
                ret, exit_minute = sim_tl30(
                    new_positions[new_index:], o, h, low, close, t, bid, c0,
                )
                if ret is None:
                    continue
                fill_close = float(close[position]) / bid - 1
                rows.append(
                    {
                        "date": row["date"], "month": row["date"][:7],
                        "ticker": row["ticker"], "t0": int(row["t"]), "tf": minute,
                        "gain": float(row["gain"]), "rank": int(row["rank"]),
                        "r15": float(row["r15"]), "prior_flush": int(row["prior_flush"]),
                        "fc": fill_close, "ret": ret - FR, "exit_t": int(exit_minute),
                        "B": bid, "c0": c0,
                    }
                )
                flat = False
                exit_time = int(exit_minute)
                bid = None
                c0 = None
    return pd.DataFrame(rows)


def _load_iex(months: tuple[str, ...]) -> pd.DataFrame:
    month_set = set(months)
    frames = [
        pd.read_parquet(path)
        for path in sorted(IEX.glob("path_*.parquet"))
        if path.name[5:12] in month_set
    ]
    if not frames:
        raise RuntimeError(f"no IEX tapes for {sorted(month_set)}")
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["date", "ticker", "t"])
        .reset_index(drop=True)
    )


def _quiet_stats(frame: pd.DataFrame, tag: str):
    with redirect_stdout(io.StringIO()):
        return stats(frame, tag)


def _summary(fills: pd.DataFrame):
    pf2 = fills[fills["prior_flush"] >= 2]
    monthly = pf2.groupby("month")["ret"].agg(["count", "mean"]).round(4)
    return {
        "all": _quiet_stats(fills, "all"),
        "pf2": _quiet_stats(pf2, "pf2"),
        "rank1": _quiet_stats(fills[fills["rank"] == 1], "rank1"),
        "monthly_pf2": {
            month: {"n": int(row["count"]), "mean": float(row["mean"])}
            for month, row in monthly.iterrows()
        },
        "worst5": fills.nsmallest(5, "ret")[
            ["date", "ticker", "t0", "tf", "ret", "B", "c0"]
        ].to_dict("records"),
    }


def _match_ids(frozen: pd.DataFrame, variant: pd.DataFrame):
    matched_frozen: set[int] = set()
    matched_variant: set[int] = set()
    pairs = []
    keys = set(map(tuple, frozen[["date", "ticker"]].drop_duplicates().to_numpy()))
    keys.update(map(tuple, variant[["date", "ticker"]].drop_duplicates().to_numpy()))
    for day, ticker in sorted(keys):
        left = frozen.index[(frozen["date"] == day) & (frozen["ticker"] == ticker)].tolist()
        right = variant.index[(variant["date"] == day) & (variant["ticker"] == ticker)].tolist()
        left.sort(key=lambda index: int(frozen.at[index, "tf"]))
        right.sort(key=lambda index: int(variant.at[index, "tf"]))
        left_index = right_index = 0
        while left_index < len(left) and right_index < len(right):
            frozen_tf = int(frozen.at[left[left_index], "tf"])
            variant_tf = int(variant.at[right[right_index], "tf"])
            if frozen_tf < variant_tf - 5:
                left_index += 1
            elif variant_tf < frozen_tf - 5:
                right_index += 1
            else:
                frozen_id, variant_id = left[left_index], right[right_index]
                matched_frozen.add(frozen_id)
                matched_variant.add(variant_id)
                pairs.append((frozen_id, variant_id))
                left_index += 1
                right_index += 1
    return matched_frozen, matched_variant, pairs


def _strict_keys(anchor_paths: pd.DataFrame, lb: pd.DataFrame) -> set[tuple[str, str, int]]:
    grouped = anchor_paths.groupby(["date", "ticker"], sort=False)
    anchor_paths["pullback"] = anchor_paths["c"] / grouped["c"].transform("cummax") - 1
    anchor_paths["r15"] = anchor_paths["c"] / grouped["c"].shift(15) - 1
    merged = lb.merge(
        anchor_paths[["date", "ticker", "t", "pullback", "r15"]],
        on=["date", "ticker", "t"], how="left",
    )
    strict = (
        (merged["gain"] >= 1.0)
        & (merged["pullback"] >= -0.01)
        & (merged["r15"] >= 0.03)
    )
    return set(map(tuple, merged.loc[strict.fillna(False), ["date", "ticker", "t"]].to_numpy()))


def _classify(
    frozen: pd.DataFrame,
    variant: pd.DataFrame,
    anchor_paths: pd.DataFrame,
    exec_paths: pd.DataFrame,
    b_paths: pd.DataFrame,
    lb: pd.DataFrame,
):
    matched_frozen, matched_variant, pairs = _match_ids(frozen, variant)
    strict = _strict_keys(anchor_paths, lb)
    bid_lookup = {
        (row.date, row.ticker, int(row.t)): float(row.c) * (1 - STOP_L)
        for row in b_paths.itertuples()
    }
    exec_groups = {}
    for key, group in exec_paths.groupby(["date", "ticker"], sort=False):
        bars = group[["t", "l", "n_bars"]].copy()
        increments = bars["n_bars"].diff().gt(0)
        increments.iloc[0] = bool(bars["n_bars"].iloc[0] > 0)
        exec_groups[key] = bars[increments]

    classes = {}
    for frozen_id, fill in frozen.iterrows():
        key = (fill["date"], fill["ticker"], int(fill["t0"]))
        if frozen_id in matched_frozen:
            label = "matched"
        elif key not in strict:
            label = "anchor_lost"
        else:
            bid = bid_lookup.get(key)
            bars = exec_groups.get((fill["date"], fill["ticker"]))
            touched = False
            if bid is not None and bars is not None:
                window = bars[(bars["t"] > fill["t0"]) & (bars["t"] <= fill["t0"] + WIN)]
                touched = bool((window["l"] <= bid).any())
            label = "lifecycle_lost" if touched else "fill_lost"
        classes[int(frozen_id)] = label

    counts = {label: sum(value == label for value in classes.values()) for label in (
        "matched", "anchor_lost", "fill_lost", "lifecycle_lost",
    )}
    pf2_ids = set(frozen.index[frozen["prior_flush"] >= 2])
    counts.update({
        f"{label}_pf2": sum(classes[index] == label for index in pf2_ids)
        for label in ("matched", "anchor_lost", "fill_lost", "lifecycle_lost")
    })
    counts["missed"] = len(frozen) - counts["matched"]
    counts["missed_pf2"] = len(pf2_ids) - counts["matched_pf2"]
    counts["spurious"] = len(variant) - len(matched_variant)
    counts["missed_rate"] = round(counts["missed"] / len(frozen), 4)
    counts["missed_pf2_rate"] = round(counts["missed_pf2"] / len(pf2_ids), 4)
    counts["definitions"] = {
        "matched": "same date,ticker and abs(tf_variant-tf_frozen)<=5; chronological one-to-one",
        "anchor_lost": "unmatched frozen fill with no strict anchor at its frozen t0",
        "fill_lost": "unmatched frozen fill with anchor present but no execution-tape B touch before expiry",
        "lifecycle_lost": "unmatched despite anchor and raw B touch because rolling lifecycle timing differed",
    }
    return counts, pairs


def _distribution(values: pd.Series):
    return {
        "n": int(len(values)), "mean": round(float(values.mean()), 4),
        "median": round(float(values.median()), 4),
        "p05": round(float(values.quantile(0.05)), 4),
        "p95": round(float(values.quantile(0.95)), 4),
        "min": round(float(values.min()), 4), "max": round(float(values.max()), 4),
    }


def _parity(frozen_paths: pd.DataFrame, lb: pd.DataFrame) -> pd.DataFrame:
    fresh = run_engine_hybrid(frozen_paths.copy(), frozen_paths.copy(), frozen_paths.copy(), lb.copy())
    expected = pd.read_parquet(ART / "lb18_oos_oos.parquet").reset_index(drop=True)
    pd.testing.assert_frame_equal(
        fresh[expected.columns].reset_index(drop=True), expected, check_exact=True,
    )
    expected_json = json.loads((ART / "lb18_oos_oos.json").read_text())
    observed = _summary(fresh)
    for cell in ("all", "pf2", "rank1"):
        if observed[cell] != expected_json[cell]:
            raise SystemExit(f"PARITY FAIL {cell}: {observed[cell]} != {expected_json[cell]}")
    return fresh


def _a2_matched_delta():
    rows = pd.read_parquet(ART / "lb18_iex_feed.parquet")
    rows = rows[rows["date"].str[:7].isin(OOS_MONTHS)]
    frozen = rows[(rows["side"] == "frozen") & (rows["match_status"] == "matched")].reset_index(drop=True)
    iex = rows[(rows["side"] == "iex") & (rows["match_status"] == "matched")].reset_index(drop=True)
    _, _, pairs = _match_ids(frozen, iex)
    deltas = pd.Series([float(frozen.at[left, "ret"] - iex.at[right, "ret"]) for left, right in pairs])
    if len(deltas) != 367:
        raise SystemExit(f"A2 matched-pair count changed: {len(deltas)} != 367")
    return _distribution(deltas)


def _gate_a3(summary, classification):
    checks = {
        "pf2 EV within +-0.30pp of frozen +1.14%": abs(summary["pf2"]["mean"] - 0.0114) <= 0.003,
        "months+>=12/14": summary["pf2"]["months_pos"] >= 12 and summary["pf2"]["n_months"] == 14,
        "frozen pf2 fills lost to IEX anchor/B drift<=10%": classification["missed_pf2_rate"] <= 0.10,
    }
    return {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def _gate_a4(summary, classification):
    checks = {
        "pf2 EV within +-0.30pp of frozen +1.14%": abs(summary["pf2"]["mean"] - 0.0114) <= 0.003,
        "missed<=10%": classification["missed_rate"] <= 0.10,
    }
    return {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def _run() -> None:
    frozen_paths, leaderboard = load_months(OOS_MONTHS)
    parity = _parity(frozen_paths, leaderboard)
    print("PARITY PASS: all n=541 mean=+0.0091; pf2 n=381 mean=+0.0114")
    if "--parity-only" in sys.argv:
        return

    iex_paths = _load_iex(OOS_MONTHS)
    variants = {}
    fill_frames = []
    definitions = (
        ("A3b", iex_paths, frozen_paths, iex_paths),
        ("A3a", iex_paths, frozen_paths, frozen_paths),
        ("A4", frozen_paths, iex_paths, frozen_paths),
        ("parity", frozen_paths, frozen_paths, frozen_paths),
    )
    parity_classification, _ = _classify(
        parity, parity, frozen_paths, frozen_paths, frozen_paths, leaderboard,
    )
    for name, anchor, execution, bid_source in definitions:
        fills = parity if name == "parity" else run_engine_hybrid(
            anchor, execution, bid_source, leaderboard,
        )
        classification, _ = (
            (parity_classification, []) if name == "parity" else
            _classify(parity, fills, anchor, execution, bid_source, leaderboard)
        )
        summary = _summary(fills)
        result = {"stats": summary, "classification": classification}
        if name == "A3b":
            result["gate"] = _gate_a3(summary, classification)
        elif name == "A4":
            result["gate"] = _gate_a4(summary, classification)
        elif name == "A3a":
            result["gate"] = {"verdict": "DESCRIPTIVE", "checks": {}}
        else:
            result["gate"] = {"verdict": "PASS", "checks": {"exact frozen parity": True}}
        variants[name] = result
        output_rows = fills.copy()
        output_rows.insert(0, "variant", name)
        fill_frames.append(output_rows[FILL_COLUMNS])

    output = {
        "study": "PRE-REG-MICRO-01 Amendment A1 — Study A decomposition",
        "months": list(OOS_MONTHS),
        "design": "frozen lb gate; independently sourced anchor/state, execution, and B/c0 tapes",
        "friction": FR,
        "flags": {"measurement": True, "seen_data": True, "not_an_alpha_claim": True},
        "a2_matched_frozen_minus_iex_ret": _a2_matched_delta(),
        "variants": variants,
    }
    (ART / "lb18_iex_hybrid.json").write_text(json.dumps(output, indent=1, default=str) + "\n")
    pd.concat(fill_frames, ignore_index=True).to_parquet(
        ART / "lb18_iex_hybrid.parquet", index=False,
    )

    print("variant all_n  all_ev pf2_n  pf2_ev months+   worst missed pf2lost gate")
    for name in ("A3b", "A3a", "A4", "parity"):
        result = variants[name]
        summary, classification = result["stats"], result["classification"]
        print(
            f"{name:7s} {summary['all']['n']:5d} {summary['all']['mean']:+.4f} "
            f"{summary['pf2']['n']:5d} {summary['pf2']['mean']:+.4f} "
            f"{summary['pf2']['months_pos']:2d}/{summary['pf2']['n_months']:<2d} "
            f"{summary['pf2']['worst_month']:+.4f} {classification['missed']:6d} "
            f"{classification['missed_pf2']:7d} {result['gate']['verdict']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--parity-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        _self_test()
        print("self-test PASS")
        return
    _run()


def _self_test() -> None:
    anchor = pd.DataFrame([
        {"date": "2099-01-02", "ticker": "TEST", "t": t, "o": 96.0 if t < 572 else 100.0,
         "h": 96.0 if t < 572 else 100.0, "l": 96.0 if t < 572 else 100.0,
         "c": 96.0 if t < 572 else 100.0, "v": 1.0, "n_bars": t - 569}
        for t in range(570, 591)
    ])
    exec_paths = anchor.copy()
    exec_paths.loc[exec_paths["t"] == 587, ["o", "h", "l", "c"]] = [91.0, 92.0, 89.0, 91.0]
    exec_paths.loc[exec_paths["t"] == 588, ["o", "h", "l", "c"]] = [99.0, 101.0, 98.0, 100.0]
    leaderboard = pd.DataFrame(
        [{"date": "2099-01-02", "ticker": "TEST", "t": 586, "gain": 1.0, "rank": 1}]
    )

    assert ("2099-01-02", "TEST", 586) in _strict_keys(anchor.copy(), leaderboard)
    fills = run_engine_hybrid(anchor, exec_paths, anchor.copy(), leaderboard)

    assert len(fills) == 1
    fill = fills.iloc[0]
    assert int(fill["t0"]) == 586
    assert int(fill["tf"]) == 587
    assert int(fill["exit_t"]) == 588
    assert abs(float(fill["B"]) - 90.0) < 1e-12
    assert abs(float(fill["ret"]) - (100.0 / 90.0 - 1.0 - 0.01)) < 1e-12


if __name__ == "__main__":
    main()
