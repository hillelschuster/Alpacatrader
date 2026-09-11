#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas", "pyarrow"]
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run --python 3.11 --with pandas --with pyarrow python factory/scripts/lb18_overlay.py
# ──────────────────
"""Measure production-overlay retention of the frozen flush rule; not an alpha claim."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final, TypedDict

import numpy as np
import pandas as pd  # noqa: PANDAS_OK

ROOT: Final = Path(__file__).resolve().parents[2]
ARTIFACT: Final = ROOT / "factory" / "artifacts" / "lb18_overlay.json"
OOS_MONTHS: Final = tuple(f"2024-{month:02d}" for month in range(1, 13)) + (
    "2025-01",
    "2025-02",
)
EXPECTED: Final = {"all": {"n": 541, "mean": 0.0091}, "pf2": {"n": 381, "mean": 0.0114}}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lb18_episodes import build  # noqa: E402
from lb18_oos import FR, STOP_L, TL, WIN, load_months, sim_tl30  # noqa: E402


class ExitResult(TypedDict):
    ret: float
    exit_t: int
    exit_reason: str
    original_exit_t: int


class CellStats(TypedDict):
    n: int
    mean_net_per_trade: float
    median: float
    months_positive: int
    n_months: int
    worst_month: float
    worst_month_name: str
    fills_per_month: float


def _exit_reason(fut, o, h, l, c, t, bid: float, target: float) -> ExitResult | None:
    frozen_ret, frozen_exit = sim_tl30(fut, o, h, l, c, t, bid, target)
    if frozen_ret is None or frozen_exit is None:
        return None
    stop = bid * (1 - STOP_L)
    bars = 0
    reason = "tl30"
    for j in fut:
        if l[j] <= stop:
            reason = "stop"
            break
        if h[j] >= target:
            reason = "target"
            break
        bars += 1
        if bars >= TL:
            break
    return {
        "ret": float(frozen_ret),
        "exit_t": int(frozen_exit),
        "exit_reason": reason,
        "original_exit_t": int(frozen_exit),
    }


def _candidates(paths: pd.DataFrame, lb: pd.DataFrame) -> pd.DataFrame:
    arrays = build(paths)
    grouped = paths.groupby(["date", "ticker"], sort=False)
    paths["pullback"] = paths["c"] / grouped["c"].transform("cummax") - 1
    paths["r15"] = paths["c"] / grouped["c"].shift(15) - 1
    merged = lb.merge(
        paths[["date", "ticker", "t", "c", "pullback", "r15", "prior_flush"]],
        on=["date", "ticker", "t"],
        how="left",
    )
    strict = (merged["gain"] >= 1.0) & (merged["pullback"] >= -0.01) & (merged["r15"] >= 0.03)
    states = merged[strict.fillna(False)].copy()
    state_groups = {
        key: group.sort_values("t").reset_index(drop=True)
        for key, group in states.groupby(["date", "ticker"], sort=False)
    }
    rows: list[dict[str, float | int | str]] = []
    for key, signals in state_groups.items():
        data = arrays.get(key)
        if data is None:
            continue
        t, o, h, low, close = data["t"], data["o"], data["h"], data["l"], data["c"]
        newpos = data["newpos"]
        state_times = signals["t"].to_numpy()
        signal_i = 0
        bid = target = row = bid_t = None
        flat = True
        exit_t = -(10**9)
        for new_i, raw_j in enumerate(newpos):
            j = int(raw_j)
            bar_t = int(t[j])
            if not flat and bar_t > exit_t:
                flat, bid, target = True, None, None
            while signal_i < len(signals) and int(state_times[signal_i]) < bar_t:
                if flat:
                    candidate = signals.iloc[signal_i]
                    pos = int(np.searchsorted(t, int(state_times[signal_i])))
                    anchor = float(close[pos])
                    if anchor > 0:
                        bid, target, row, bid_t = anchor * (1 - STOP_L), anchor, candidate, int(state_times[signal_i])
                signal_i += 1
            if not flat or bid is None or target is None or row is None:
                continue
            if bid_t is not None and bar_t - bid_t > WIN:
                bid = None
                continue
            if low[j] > bid:
                continue
            outcome = _exit_reason(newpos[new_i:], o, h, low, close, t, bid, target)
            if outcome is None:
                continue
            rows.append(
                {
                    "date": str(row["date"]),
                    "month": str(row["date"])[:7],
                    "ticker": str(row["ticker"]),
                    "t0": int(row["t"]),
                    "tf": bar_t,
                    "gain": float(row["gain"]),
                    "rank": int(row["rank"]),
                    "r15": float(row["r15"]),
                    "prior_flush": int(row["prior_flush"]),
                    "fc": float(close[j]) / bid - 1,
                    "ret": outcome["ret"] - FR,
                    "exit_t": outcome["exit_t"],
                    "exit_reason": outcome["exit_reason"],
                    "original_exit_t": outcome["original_exit_t"],
                    "bid": bid,
                }
            )
            flat, exit_t, bid, target = False, outcome["exit_t"], None, None
    return pd.DataFrame(rows)


def run_engine_overlay(
    paths: pd.DataFrame,
    lb: pd.DataFrame,
    entry_cutoff: int = 930,
    flatten_time: int = 955,
    pos_max: int = 3,
    apply_overlay: bool = True,
) -> pd.DataFrame:
    """Run the frozen engine, optionally applying cutoff, flatten, and POS_MAX."""
    candidates = _candidates(paths, lb)
    diagnostics = {"dropped_by_cutoff": 0, "exits_changed_to_flatten": 0, "pos_max_rejections": 0}
    if not apply_overlay or candidates.empty:
        candidates.attrs.update(diagnostics)
        return candidates.drop(columns=["bid"], errors="ignore")

    diagnostics["dropped_by_cutoff"] = int((candidates["tf"] >= entry_cutoff).sum())
    eligible = candidates[candidates["tf"] < entry_cutoff].copy()
    flatten_mask = eligible["exit_t"] > flatten_time
    for index in eligible.index[flatten_mask]:
        ticker_rows = paths[(paths["date"] == eligible.at[index, "date"]) & (paths["ticker"] == eligible.at[index, "ticker"])]
        flatten_rows = ticker_rows[ticker_rows["t"] == flatten_time]
        if flatten_rows.empty:
            continue
        eligible.at[index, "ret"] = float(flatten_rows.iloc[0]["c"]) / float(eligible.at[index, "bid"]) - 1 - FR
        eligible.at[index, "exit_t"] = flatten_time
        eligible.at[index, "exit_reason"] = "flatten"
    diagnostics["exits_changed_to_flatten"] = int((eligible["exit_reason"] == "flatten").sum())

    accepted: list[int] = []
    open_exits: list[int] = []
    current_date = ""
    ordered = eligible.sort_values(["date", "tf", "rank", "ticker", "t0"], kind="stable")
    for index, row in ordered.iterrows():
        date, fill_t = str(row["date"]), int(row["tf"])
        if date != current_date:
            current_date, open_exits = date, []
        open_exits = [value for value in open_exits if value >= fill_t]
        if len(open_exits) >= pos_max:
            diagnostics["pos_max_rejections"] += 1
            continue
        accepted.append(int(index))
        open_exits.append(int(row["exit_t"]))
    result = eligible.loc[accepted].sort_values(["date", "tf", "rank", "ticker"], kind="stable")
    result = result.drop(columns=["bid"], errors="ignore").reset_index(drop=True)
    result.attrs.update(diagnostics)
    return result


def _stats(frame: pd.DataFrame, n_months: int) -> CellStats:
    monthly = frame.groupby("month")["ret"].mean()
    worst_name = str(monthly.idxmin())
    return {
        "n": int(len(frame)),
        "mean_net_per_trade": round(float(frame["ret"].mean()), 4),
        "median": round(float(frame["ret"].median()), 4),
        "months_positive": int((monthly > 0).sum()),
        "n_months": n_months,
        "worst_month": round(float(monthly.min()), 4),
        "worst_month_name": worst_name,
        "fills_per_month": round(len(frame) / n_months, 2),
    }


def main() -> None:
    paths, lb = load_months(OOS_MONTHS)
    frozen = run_engine_overlay(paths.copy(), lb, apply_overlay=False)
    overlay = run_engine_overlay(paths.copy(), lb)
    frozen_cells = {"all": _stats(frozen, len(OOS_MONTHS)), "pf2": _stats(frozen[frozen["prior_flush"] >= 2], len(OOS_MONTHS))}
    overlay_cells = {"all": _stats(overlay, len(OOS_MONTHS)), "pf2": _stats(overlay[overlay["prior_flush"] >= 2], len(OOS_MONTHS))}
    observed = {name: {"n": cell["n"], "mean": cell["mean_net_per_trade"]} for name, cell in frozen_cells.items()}
    parity_pass = observed == EXPECTED
    assert parity_pass, f"frozen parity failed: observed={observed} expected={EXPECTED}"

    attrs = overlay.attrs
    base_n = len(frozen)
    post_cutoff_n = base_n - int(attrs["dropped_by_cutoff"])
    guards = {
        "dropped_by_cutoff": {"n": int(attrs["dropped_by_cutoff"]), "fraction_of_frozen_fills": round(int(attrs["dropped_by_cutoff"]) / base_n, 4)},
        "exits_changed_to_flatten": {"n": int(attrs["exits_changed_to_flatten"]), "fraction_of_overlay_fills": round(int(attrs["exits_changed_to_flatten"]) / len(overlay), 4)},
        "pos_max_rejections": {"n": int(attrs["pos_max_rejections"]), "binding_frequency": round(int(attrs["pos_max_rejections"]) / post_cutoff_n, 4)},
    }
    output = {
        "measurement": True,
        "seen_data": True,
        "not_an_alpha_claim": True,
        "months": list(OOS_MONTHS),
        "overlay": {"entry_cutoff": 930, "flatten_time": 955, "pos_max": 3, "same_minute_priority": "rank_then_ticker"},
        "exit_priority": "stop, then target, then tl30; flatten replaces only exits after 15:55 ET",
        "frozen": frozen_cells,
        "with_overlay": overlay_cells,
        **guards,
        "parity_check": {"pass": parity_pass, "expected": EXPECTED, "observed_overlay_off": observed},
    }
    ARTIFACT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")

    pf_frozen, pf_overlay = frozen_cells["pf2"], overlay_cells["pf2"]
    print(f"parity PASS: all n={frozen_cells['all']['n']} mean={frozen_cells['all']['mean_net_per_trade']:+.2%}; pf2 n={pf_frozen['n']} mean={pf_frozen['mean_net_per_trade']:+.2%}")
    print(f"overlay pf2: n={pf_overlay['n']} mean={pf_overlay['mean_net_per_trade']:+.2%} median={pf_overlay['median']:+.2%} months+={pf_overlay['months_positive']}/{pf_overlay['n_months']} worst={pf_overlay['worst_month']:+.2%} fills/month={pf_overlay['fills_per_month']:.2f}")
    print(f"retention: pf2 EV={pf_overlay['mean_net_per_trade'] / pf_frozen['mean_net_per_trade']:.1%}, fills/month={pf_overlay['fills_per_month'] / pf_frozen['fills_per_month']:.1%}; cutoff={guards['dropped_by_cutoff']['n']}, flatten={guards['exits_changed_to_flatten']['n']}, POS_MAX={guards['pos_max_rejections']['n']}")
    print(f"artifact -> {ARTIFACT}")


if __name__ == "__main__":
    main()
