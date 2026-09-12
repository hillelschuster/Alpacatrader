#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow"]
# ///
# ─── How to run ───
# uv run --quiet --python 3.11 --with pandas --with pyarrow --with alpaca-py \
#   --with python-dotenv python factory/scripts/lb18_iex_feed.py
# ──────────────────
"""Run frozen-engine parity and the pre-registered Study A IEX-feed replay."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Final
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPTS: Final = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import lb18_oos  # noqa: E402
from lb18_oos import load_months, run_engine, stats  # noqa: E402

IEX: Final = ROOT / "data" / "iex_tape"
ART: Final = ROOT / "factory" / "artifacts"
OOS_MONTHS: Final = tuple(f"2024-{month:02d}" for month in range(1, 13)) + (
    "2025-01",
    "2025-02",
)
DEV_MONTHS: Final = tuple(f"2025-{month:02d}" for month in range(3, 13)) + tuple(
    f"2026-{month:02d}" for month in range(1, 9)
)
ROW_COLUMNS: Final = ["side", "date", "ticker", "t0", "tf", "ret", "prior_flush",
                      "rank", "fc", "match_status", "dB_bps", "dtf"]


def sim_clock_tl30(fut, o, h, l, c, t, bid, c0):
    """Apply stop/target first, then stop at the first new bar at least 30 minutes after fill."""
    if len(fut) == 0:
        return None, None
    stop = bid * (1 - lb18_oos.STOP_L)
    fill_t = int(t[int(fut[0])])
    for j in fut:
        if l[j] <= stop:
            return min(float(o[j]), stop) / bid - 1, int(t[j])
        if h[j] >= c0:
            return c0 / bid - 1, int(t[j])
        if int(t[j]) >= fill_t + lb18_oos.TL:
            return float(c[j]) / bid - 1, int(t[j])
    j = int(fut[-1])
    return float(c[j]) / bid - 1, int(t[j])


def _with_bid(fills: pd.DataFrame, paths: pd.DataFrame) -> pd.DataFrame:
    anchors = (
        paths[["date", "ticker", "t", "c"]]
        .rename(columns={"t": "t0", "c": "bid"})
        .drop_duplicates(["date", "ticker", "t0"], keep="last")
    )
    anchors["bid"] *= 0.9
    result = fills.reset_index(drop=True).merge(
        anchors, on=["date", "ticker", "t0"], how="left", validate="many_to_one"
    )
    if result["bid"].isna().any():
        raise RuntimeError("fill anchor missing from its path tape")
    return result


def classify_fills(
    frozen: pd.DataFrame,
    iex: pd.DataFrame,
    frozen_paths: pd.DataFrame,
    iex_paths: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Classify fills one-to-one in chronological order within the frozen five-minute window."""
    left = _with_bid(frozen, frozen_paths)
    right = _with_bid(iex, iex_paths)
    left["_id"] = np.arange(len(left))
    right["_id"] = np.arange(len(right))
    left_status = {int(i): "missed" for i in left["_id"]}
    right_status = {int(i): "spurious" for i in right["_id"]}
    pair_rows = []

    keys = set(map(tuple, left[["date", "ticker"]].drop_duplicates().to_numpy()))
    keys.update(map(tuple, right[["date", "ticker"]].drop_duplicates().to_numpy()))
    for day, ticker in sorted(keys):
        li = left.index[(left["date"] == day) & (left["ticker"] == ticker)].tolist()
        ri = right.index[(right["date"] == day) & (right["ticker"] == ticker)].tolist()
        li.sort(key=lambda index: int(left.at[index, "tf"]))
        ri.sort(key=lambda index: int(right.at[index, "tf"]))
        a = b = 0
        while a < len(li) and b < len(ri):
            lf, rf = int(left.at[li[a], "tf"]), int(right.at[ri[b], "tf"])
            if lf < rf - 5:
                a += 1
                continue
            if rf < lf - 5:
                b += 1
                continue
            left_id, right_id = int(left.at[li[a], "_id"]), int(right.at[ri[b], "_id"])
            left_status[left_id] = right_status[right_id] = "matched"
            frozen_bid, iex_bid = float(left.at[li[a], "bid"]), float(right.at[ri[b], "bid"])
            pair_rows.append(
                {
                    "left_id": left_id,
                    "right_id": right_id,
                    "dB_bps": 10000 * (iex_bid - frozen_bid) / frozen_bid,
                    "dtf": rf - lf,
                    "dt0": int(right.at[ri[b], "t0"]) - int(left.at[li[a], "t0"]),
                }
            )
            a += 1
            b += 1

    pairs = pd.DataFrame(pair_rows, columns=["left_id", "right_id", "dB_bps", "dtf", "dt0"])
    left["match_status"] = left["_id"].map(left_status)
    right["match_status"] = right["_id"].map(right_status)
    for frame, side, id_column in ((left, "frozen", "left_id"), (right, "iex", "right_id")):
        frame["side"] = side
        frame["dB_bps"] = frame["_id"].map(pairs.set_index(id_column)["dB_bps"])
        frame["dtf"] = frame["_id"].map(pairs.set_index(id_column)["dtf"])
    rows = pd.concat([left[ROW_COLUMNS], right[ROW_COLUMNS]], ignore_index=True)
    return rows, pairs


def _load_iex(months: tuple[str, ...]) -> pd.DataFrame:
    frames = [
        pd.read_parquet(path)
        for path in sorted(IEX.glob("path_*.parquet"))
        if path.name[5:12] in set(months)
    ]
    if not frames:
        raise RuntimeError(f"no IEX tapes for {months}")
    return pd.concat(frames, ignore_index=True).sort_values(
        ["date", "ticker", "t"]
    ).reset_index(drop=True)


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
        "worst5": fills.nsmallest(5, "ret")[["date", "ticker", "tf", "fc", "ret"]].to_dict("records"),
    }


def _distribution(values: pd.Series):
    finite = values.dropna().astype(float)
    if finite.empty:
        return {"n": 0}
    return {
        "n": int(len(finite)),
        "mean": round(float(finite.mean()), 4),
        "median": round(float(finite.median()), 4),
        "p05": round(float(finite.quantile(0.05)), 4),
        "p25": round(float(finite.quantile(0.25)), 4),
        "p75": round(float(finite.quantile(0.75)), 4),
        "p95": round(float(finite.quantile(0.95)), 4),
        "min": round(float(finite.min()), 4),
        "max": round(float(finite.max()), 4),
    }


def _parity(pool: str, months: tuple[str, ...]):
    paths, leaderboard = load_months(months)
    fresh = run_engine(paths, leaderboard)
    expected_frame = pd.read_parquet(ART / f"lb18_oos_{pool}.parquet")
    expected_json = json.loads((ART / f"lb18_oos_{pool}.json").read_text())
    try:
        pd.testing.assert_frame_equal(fresh.reset_index(drop=True), expected_frame.reset_index(drop=True), check_exact=True)
    except AssertionError as error:
        raise SystemExit(f"PARITY FAIL {pool}: fills differ: {error}") from error
    observed = _summary(fresh)
    for cell in ("all", "pf2", "rank1"):
        if observed[cell] != expected_json[cell]:
            raise SystemExit(f"PARITY FAIL {pool} {cell}: {observed[cell]} != {expected_json[cell]}")
    return fresh


def _clock_engine(paths: pd.DataFrame, leaderboard: pd.DataFrame) -> pd.DataFrame:
    with patch.object(lb18_oos, "sim_tl30", sim_clock_tl30):
        return run_engine(paths, leaderboard)


def _analyze_pool(pool: str, months: tuple[str, ...], frozen: pd.DataFrame):
    frozen_paths, leaderboard = load_months(months)
    iex_paths = _load_iex(months)
    iex_fills = run_engine(iex_paths.copy(), leaderboard.copy())
    clock_fills = _clock_engine(iex_paths.copy(), leaderboard.copy())
    rows, pairs = classify_fills(frozen, iex_fills, frozen_paths, iex_paths)
    missed = rows[(rows["side"] == "frozen") & (rows["match_status"] == "missed")]
    spurious = rows[(rows["side"] == "iex") & (rows["match_status"] == "spurious")]
    missed_pf2 = missed[missed["prior_flush"] >= 2]
    frozen_pf2_n = int((frozen["prior_flush"] >= 2).sum())
    classification = {
        "matched": int(len(pairs)),
        "missed": int(len(missed)),
        "missed_pf2": int(len(missed_pf2)),
        "missed_pf2_rate": round(len(missed_pf2) / frozen_pf2_n, 4),
        "spurious": int(len(spurious)),
        "spurious_rate": round(len(spurious) / len(frozen), 4),
        "dB_bps": _distribution(pairs["dB_bps"]),
        "dtf_minutes": _distribution(pairs["dtf"]),
        "dt0_minutes": _distribution(pairs["dt0"]),
        "missed_frozen_ret": _distribution(missed["ret"]),
        "spurious_iex_ret": _distribution(spurious["ret"]),
    }
    result = {
        "months": list(months),
        "frozen": _summary(frozen),
        "iex": _summary(iex_fills),
        "clock_minute_tl30_sensitivity": _summary(clock_fills),
        "classification": classification,
    }
    if pool == "oos":
        iex_pf2 = result["iex"]["pf2"]
        checks = {
            "missed_pf2<=10% of pf2 fills": classification["missed_pf2_rate"] <= 0.10,
            "spurious<=10% of frozen fills": classification["spurious_rate"] <= 0.10,
            "pf2 EV within +-0.30pp": abs(iex_pf2["mean"] - result["frozen"]["pf2"]["mean"]) <= 0.003,
            "months+>=12/14": iex_pf2["months_pos"] >= 12 and iex_pf2["n_months"] == 14,
        }
        result["gate_a"] = {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    return result, rows


def main() -> None:
    frozen = {
        "oos": _parity("oos", OOS_MONTHS),
        "dev": _parity("dev", DEV_MONTHS),
    }
    print("parity  oos=PASS  dev=PASS")
    pools = {}
    row_frames = []
    for pool, months in (("oos", OOS_MONTHS), ("dev", DEV_MONTHS)):
        pools[pool], rows = _analyze_pool(pool, months, frozen[pool])
        row_frames.append(rows)

    output = {
        "study": "PRE-REG-MICRO-01 Study A — IEX feed fidelity",
        "parity": {"oos": "PASS", "dev": "PASS"},
        "design": "IEX paths substituted; frozen leaderboard and rolling-bid engine unchanged",
        "friction": 0.01,
        "match": "same date,ticker and abs(tf_iex-tf_frozen)<=5; chronological one-to-one",
        "production_overlay": json.loads((ART / "lb18_overlay.json").read_text()),
        "pools": pools,
    }
    (ART / "lb18_iex_feed.json").write_text(json.dumps(output, indent=1, default=str) + "\n")
    pd.concat(row_frames, ignore_index=True)[ROW_COLUMNS].to_parquet(
        ART / "lb18_iex_feed.parquet", index=False
    )
    print("pool side    all_n all_ev   pf2_n pf2_ev months+ worst")
    for pool in ("oos", "dev"):
        for side in ("frozen", "iex", "clock_minute_tl30_sensitivity"):
            summary = pools[pool][side]
            print(
                f"{pool:4s} {side[:7]:7s} {summary['all']['n']:5d} {summary['all']['mean']:+.4f} "
                f"{summary['pf2']['n']:5d} {summary['pf2']['mean']:+.4f} "
                f"{summary['pf2']['months_pos']:2d}/{summary['pf2']['n_months']:<2d} {summary['pf2']['worst_month']:+.4f}"
            )
        counts = pools[pool]["classification"]
        print(f"{pool:4s} match={counts['matched']} missed={counts['missed']} missed_pf2={counts['missed_pf2']} spurious={counts['spurious']}")
    print(f"Gate A: {pools['oos']['gate_a']['verdict']}")


if __name__ == "__main__":
    main()
