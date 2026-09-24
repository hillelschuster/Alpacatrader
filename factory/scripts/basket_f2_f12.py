#!/usr/bin/env python
"""Build the frozen causal F2/F12 golden-window state panel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

try:
    from factory.scripts import basket_sim as sim
except ImportError:
    import basket_sim as sim

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "factory/artifacts/basket/phase2/F2_F12"
CHECKPOINTS = (580, 585, 590, 600, 615)
FAMILIES = {
    "A_pm": ("A_pm", 570),
    "A_pm31": ("A_pm31", 570),
    "A_open": ("A_open", 570),
    "B585": ("B", 585),
    "B600": ("B", 600),
}
BLOCKS = {"block1": ("2021-02", "2023-12"), "block2": ("2025-02", "2026-05")}
STATE_FEATURES = (
    "ret_from_fill", "ret_from_prevclose", "ret_from_open0930", "rank",
    "rank_change", "rank_std", "rank_mode_share", "mfe_so_far", "mae_so_far",
    "dd_from_running_high", "dd_from_day_high", "time_since_running_high",
    "time_since_day_high", "pos_in_range_fill",
    "velocity_1", "velocity_3", "velocity_5", "accel_proxy", "cum_volume",
    "dollar_volume", "vol_accel", "new_high_count", "new_high_freq", "time_below_recent_high",
    "bar_persistence", "recovery_5", "recovery_10", "spread_state",
    "rel_strength", "basket_score_disp", "basket_breadth_10", "basket_breadth_30",
)
TARGETS = ("touch30", "touch50", "touch100", "rem_mfe", "rem_mae", "nhba")


def _block(day: str) -> str:
    month = day[:7]
    for name, (lo, hi) in BLOCKS.items():
        if lo <= month <= hi:
            return name
    raise ValueError(f"day outside the frozen development blocks: {day}")


def month_complete(month_dir: Path) -> bool:
    return (month_dir / "panel.parquet").is_file() and (month_dir / "_done").is_file()


def checkpoint_row(day: str, family: str, name: dict, bars: dict, tp: int,
                   session_end: int, basket: list[dict] | None = None) -> dict | None:
    """Build one observation; state uses bars <= tp, target uses bars > tp."""
    et = np.asarray(bars["et"], dtype=np.int64)
    hi = np.asarray(bars["high"], dtype=float)
    lo = np.asarray(bars["low"], dtype=float)
    close = np.asarray(bars["close"], dtype=float)
    vol = np.asarray(bars["volume"], dtype=float)
    ti = int(np.searchsorted(et, tp))
    fill = name["fill"]
    fi = int(np.searchsorted(et, int(fill["et"])))
    if ti >= len(et) or et[ti] != tp or ti < fi:
        return None
    if et[fi] != int(fill["et"]):
        return None
    fill_px = float(fill["px"])
    ctp = float(close[ti])
    # Phase-2 semantics: canonical anatomy fill; verify it against canonical bar open.
    if abs(float(bars["open"][fi]) - fill_px) > 1e-9:
        raise AssertionError(f"anatomy fill/open mismatch {day} {name['ticker']}")
    state_hi, state_lo = hi[fi:ti + 1], lo[fi:ti + 1]
    state_cl, state_et, state_vol = close[fi:ti + 1], et[fi:ti + 1], vol[fi:ti + 1]
    peak = float(state_hi.max())
    peak_i = int(np.flatnonzero(state_hi == peak)[0])
    day_high = float(hi[:ti + 1].max())
    day_high_i = int(np.flatnonzero(hi[:ti + 1] == day_high)[0])
    prev = float(name.get("prev_close") or np.nan)
    open9 = float(name.get("open0930") or np.nan)
    def prior(lag: int) -> float:
        index = max(0, int(np.searchsorted(et, tp - lag, side="right")) - 1)
        return float(close[index])

    velocity = {k: (ctp / prior(k) - 1.0 if prior(k) > 0 else np.nan) for k in (1, 3, 5)}
    last5 = float(state_vol[state_et > tp - 5].sum())
    prior5 = float(state_vol[(state_et > tp - 10) & (state_et <= tp - 5)].sum())
    future = np.flatnonzero((et > tp) & (et <= session_end))
    if not len(future):
        return None
    fhi, flo = hi[future], lo[future]
    rem_mfe, rem_mae = float(fhi.max() / ctp - 1), float(flo.min() / ctp - 1)
    high_touch = float(fhi.max())
    event = 0.0
    for idx in future:
        if lo[idx] <= day_high * 0.90:
            event = -1.0
            break
        if hi[idx] > day_high:
            event = 1.0
            break
    own_open = ctp / open9 - 1 if np.isfinite(open9) and open9 > 0 else np.nan
    ret_fill = ctp / fill_px - 1
    member_returns = []
    if basket:
        for member in basket:
            mb = member.get("bars")
            if mb is None:
                continue
            mi = int(np.searchsorted(mb["et"], tp))
            o9 = member["name"].get("open0930")
            if mi < len(mb["et"]) and mb["et"][mi] == tp and o9:
                member_returns.append(float(mb["close"][mi]) / float(o9) - 1)
    median_member = float(np.median(member_returns)) if member_returns else np.nan
    score = [float(x["sel"]) for x in basket or [] if x.get("sel") is not None]
    pm_rank = name.get("premarket_rank")
    last_high_gap = tp - int(state_et[peak_i])
    day_high_gap = tp - int(et[day_high_i])
    breach5 = state_lo.min() <= fill_px * .95
    breach10 = state_lo.min() <= fill_px * .90
    breach5_at = int(np.flatnonzero(state_lo <= fill_px * .95)[0]) if breach5 else len(state_lo)
    breach10_at = int(np.flatnonzero(state_lo <= fill_px * .90)[0]) if breach10 else len(state_lo)
    recovery5 = bool(breach5 and np.any(state_cl[breach5_at + 1:] > fill_px * .95))
    recovery10 = bool(breach10 and np.any(state_cl[breach10_at + 1:] > fill_px * .90))
    rank_values = name.get("rank_history", [int(name["rank"])])
    _, rank_counts = np.unique(rank_values, return_counts=True)
    prior_session_high = np.maximum.accumulate(hi[:ti + 1])
    if fi == 0:
        new_high_count = int(1 + np.sum(hi[1:ti + 1] > prior_session_high[:ti]))
    else:
        new_high_count = int(np.sum(hi[fi:ti + 1] > prior_session_high[fi - 1:ti]))
    row = {
        "date": day, "block": _block(day), "family": family, "ticker": name["ticker"],
        "rank": int(name["rank"]), "fill_et": int(fill["et"]), "fill_px": fill_px,
        "et": tp, "session_end": session_end, "px_tp": ctp,
        "ret_from_fill": ret_fill,
        "ret_from_prevclose": ctp / prev - 1 if np.isfinite(prev) and prev > 0 else np.nan,
        "ret_from_open0930": own_open,
        "rank_change": int(name["rank"]) - int(pm_rank) if pm_rank is not None else np.nan,
        "rank_std": float(np.std(rank_values)),
        "rank_mode_share": float(rank_counts.max() / len(rank_values)),
        "mfe_so_far": float(state_hi.max() / fill_px - 1),
        "mae_so_far": float(state_lo.min() / fill_px - 1),
        "dd_from_running_high": ctp / peak - 1,
        "dd_from_day_high": ctp / day_high - 1, "time_since_running_high": last_high_gap,
        "time_since_day_high": day_high_gap,
        "pos_in_range_fill": (
            (ctp - float(state_lo.min())) / (float(state_hi.max()) - float(state_lo.min()))
            if state_hi.max() > state_lo.min() else .5
        ),
        "velocity_1": velocity[1], "velocity_3": velocity[3], "velocity_5": velocity[5],
        "accel_proxy": (
            velocity[1] - velocity[5] / 5
            if np.isfinite(velocity[1]) and np.isfinite(velocity[5]) else np.nan
        ),
        "cum_volume": float(state_vol.sum()), "dollar_volume": float(np.sum(state_cl * state_vol)),
        "vol_accel": last5 / prior5 - 1 if prior5 > 0 else np.nan,
        "new_high_count": new_high_count, "new_high_freq": new_high_count / len(state_hi),
        "time_below_recent_high": last_high_gap / max(1, tp - int(fill["et"])),
        "bar_persistence": float(np.mean(np.diff(state_cl) > 0)) if len(state_cl) > 1 else np.nan,
        "recovery_5": recovery5, "recovery_10": recovery10, "spread_state": np.nan,
        "rel_strength": (
            own_open - median_member
            if np.isfinite(own_open) and np.isfinite(median_member) else np.nan
        ),
        "basket_score_disp": float(np.std(score)) if len(score) > 1 else np.nan,
        "basket_breadth_10": int(sum(x >= .10 for x in member_returns)),
        "basket_breadth_30": int(sum(x >= .30 for x in member_returns)),
        "rem_mfe": rem_mfe, "rem_mae": rem_mae,
        "touch30": bool(high_touch >= ctp * 1.30), "touch50": bool(high_touch >= ctp * 1.50),
        "touch100": bool(high_touch >= ctp * 2.0), "nhba": event == 1,
        "adverse_first": event == -1, "no_event": event == 0,
        "forward_value": rem_mfe,
        "cohort": (
            "H100" if rem_mfe >= 1.0 else "H50_not_100" if rem_mfe >= .5 else
            "H30_not_50" if rem_mfe >= .3 else
            "clear_failure" if event == -1 else "ordinary"
        ),
    }
    return row


def build_day(rec: dict, bars: sim.Bars, session_end: int) -> tuple[list[dict], list[dict]]:
    snaps = {(s["pop"], int(s["T"])): s for s in rec["snapshots"]}
    pm = snaps.get(("A_pm", 570), {})
    pmranks = {n["ticker"]: int(n["rank"]) for n in pm.get("names", [])}
    rows, blocked = [], []
    for family, key in FAMILIES.items():
        snap = snaps.get(key)
        if not snap:
            raise RuntimeError(f"missing frozen snapshot {key} on {rec['date']}")
        members = []
        for raw in snap["names"]:
            mbar = bars.ticker(raw["ticker"])
            members.append({
                "name": {**raw, "premarket_rank": pmranks.get(raw["ticker"])},
                "bars": mbar,
            })
        for member in members:
            n, b = member["name"], member["bars"]
            fill = n.get("fill")
            if not fill or fill.get("blocked") or fill.get("et") is None or b is None:
                blocked.append({"date": rec["date"], "family": family, "ticker": n["ticker"],
                                "rank": int(n["rank"]),
                                "reason": "blocked" if fill and fill.get("blocked") else
                                "no_fill" if not fill or fill.get("et") is None else "no_bars"})
                continue
            fi = int(np.searchsorted(b["et"], int(fill["et"])))
            if fi >= len(b["et"]) or b["et"][fi] != int(fill["et"]):
                blocked.append({"date": rec["date"], "family": family, "ticker": n["ticker"],
                                "rank": int(n["rank"]), "reason": "fill_bar_missing"})
                continue
            # Freeze the rank trajectory using only snapshots observed by this checkpoint.
            for tp in CHECKPOINTS:
                history = [
                    int(x["rank"])
                    for ss in rec["snapshots"]
                    if ss["pop"] == "B" and int(ss["T"]) <= tp
                    for x in ss["names"] if x["ticker"] == n["ticker"]
                ]
                candidate = {**n, "rank": history[-1] if history else int(n["rank"]),
                             "rank_history": history or [int(n["rank"])]}
                row = checkpoint_row(rec["date"], family, candidate, b, tp, session_end, members)
                if row is not None:
                    rows.append(row)
    return rows, blocked


def _atomic(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_json(path: Path, obj) -> None:
    payload = json.dumps(obj, indent=1, sort_keys=True, allow_nan=False)
    _atomic(path, lambda tmp: tmp.write_text(payload))


def _file_manifest_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def validate_panel(df: pl.DataFrame, days: list[str]) -> None:
    expected_pairs = {
        (family, cp)
        for family, checkpoints in {
            "A_pm": CHECKPOINTS,
            "A_pm31": CHECKPOINTS,
            "A_open": CHECKPOINTS,
            "B585": (585, 590, 600, 615),
            "B600": (600, 615),
        }.items()
        for cp in checkpoints
    }
    got_pairs = set(df.select(["family", "et"]).unique().iter_rows())
    if got_pairs != expected_pairs:
        raise RuntimeError(f"family/checkpoint dimensions differ: {sorted(got_pairs)}")
    if df["date"].n_unique() != len(days) or set(df["date"].unique().to_list()) != set(days):
        raise RuntimeError("panel date coverage differs from permitted development days")
    if df.select(pl.struct(["date", "family", "ticker", "et"]).n_unique()).item() != df.height:
        raise RuntimeError("duplicate date/family/ticker/checkpoint observations")
    if not set(df["et"].unique().to_list()).issubset(CHECKPOINTS):
        raise RuntimeError("panel contains a non-frozen checkpoint")


def _surface(df: pl.DataFrame) -> dict:
    """Full univariate conditional surface; quantile bins are descriptive, not gates."""
    result = {"label": "RUN", "scope": "descriptive; no gate or strategy selected", "groups": {}}
    edges_by_state = {}
    for group_key, state_rows in df.partition_by(["family", "et"], as_dict=True).items():
        family, cp = group_key
        for feature in STATE_FEATURES:
            values = state_rows[feature].cast(pl.Float64).to_numpy()
            values = values[np.isfinite(values)]
            edges_by_state[(family, cp, feature)] = (
                np.unique(np.quantile(values, [0, .2, .4, .6, .8, 1]))
                if len(values) else np.array([])
            )
    for (family, cp, block), part in df.partition_by(
        ["family", "et", "block"], as_dict=True
    ).items():
        key = f"{family}|{cp}|{block}"
        group = {"n": part.height, "features": {}, "remaining_cohorts": {}}
        for cohort, cohort_rows in part.partition_by("cohort", as_dict=True).items():
            cohort_name = cohort[0] if isinstance(cohort, tuple) else cohort
            summary = {"n": cohort_rows.height}
            for target in TARGETS:
                values = cohort_rows[target].cast(pl.Float64).to_numpy()
                summary[target] = float(np.nanmean(values)) if np.isfinite(values).any() else None
            group["remaining_cohorts"][cohort_name] = summary
        for feature in STATE_FEATURES:
            values = part[feature].cast(pl.Float64).to_numpy()
            valid = values[np.isfinite(values)]
            if not len(valid):
                group["features"][feature] = []
                continue
            edges = edges_by_state[(family, cp, feature)]
            cells = []
            for i, low in enumerate(edges[:-1]):
                high = edges[i + 1]
                mask = (values >= low) & (
                    (values <= high) if i == len(edges) - 2 else (values < high)
                )
                if not mask.any():
                    continue
                cell = {"lo": float(low), "hi": float(high), "n": int(mask.sum())}
                for target in TARGETS:
                    arr = part[target].cast(pl.Float64).to_numpy()[mask]
                    cell[f"mean_{target}"] = (
                        float(np.nanmean(arr)) if np.isfinite(arr).any() else None
                    )
                cells.append(cell)
            group["features"][feature] = cells
        result["groups"][key] = group
    return result


def run(*, months: set[str] | None = None, max_days: int | None = None) -> None:
    out = DEFAULT_OUT
    days = sim.dev_days()
    expected = [d for d in days if "2021-02-01" <= d <= "2023-12-31"
                or "2025-02-01" <= d <= "2026-05-31"]
    if days != expected or len(days) != 1066:
        raise RuntimeError(f"canonical dev day coverage mismatch: {len(days)} days")
    if months is not None:
        days = [d for d in days if d[:7] in months]
    if max_days is not None:
        days = days[:max_days]
    calendar = sim.session_end_map()
    grouped: dict[str, list[str]] = defaultdict(list)
    for day in days:
        grouped[day[:7]].append(day)
    shard_root = out / "shards"
    blocked_all = []
    for month in sorted(grouped):
        month_dir = shard_root / f"month={month}"
        if month_complete(month_dir):
            marker = json.loads((month_dir / "_done").read_text())
            if marker.get("days") != grouped[month]:
                raise RuntimeError(f"stale month shard day coverage: {month}")
            blocked_all.extend(marker.get("blocked", []))
            continue
        month_rows = []
        month_blocked = []
        for day in grouped[month]:
            sim.guard_day(day)
            rec = sim.load_anatomy(day)
            bars = sim.load_bars(day, calendar[day])
            records, blocked = build_day(rec, bars, calendar[day])
            month_rows.extend(records)
            month_blocked.extend(blocked)
        df = pl.DataFrame(month_rows, infer_schema_length=None).sort(
            ["date", "family", "ticker", "et"]
        )
        month_dir.mkdir(parents=True, exist_ok=True)
        _atomic(
            month_dir / "panel.parquet",
            lambda tmp, frame=df: frame.write_parquet(tmp, compression="zstd"),
        )
        marker = {"days": grouped[month], "rows": df.height, "blocked": month_blocked}
        blocked_all.extend(month_blocked)
        _atomic(
            month_dir / "_done",
            lambda tmp, data=marker: tmp.write_text(json.dumps(data)),
        )
        print(f"[{month}] {len(grouped[month])} days {df.height} rows", flush=True)
    if months is not None or max_days is not None:
        return
    shards = [p / "panel.parquet" for p in sorted(shard_root.glob("month=*") ) if month_complete(p)]
    if len(shards) != 51:
        raise RuntimeError(f"incomplete month shards: {len(shards)} / 51")
    df = pl.concat([pl.read_parquet(p) for p in shards], how="diagonal_relaxed").sort(
        ["date", "family", "ticker", "et"]
    )
    validate_panel(df, sim.dev_days())
    _atomic(out / "state_panel.parquet", lambda tmp: df.write_parquet(tmp, compression="zstd"))
    excluded_text = "".join(json.dumps(x, sort_keys=True) + "\n" for x in blocked_all)
    _atomic(out / "excluded.jsonl", lambda tmp: tmp.write_text(excluded_text))
    coverage = {
        "days": len(days), "rows": df.height, "columns": df.width,
        "checkpoints": list(CHECKPOINTS), "families": list(FAMILIES),
        "pair_rows": {
            f"{family}|{cp}": int(part.height)
            for (family, cp), part in df.partition_by(["family", "et"], as_dict=True).items()
        },
        "month_rows": {p.parent.name: pl.read_parquet(p).height for p in shards},
        "block_days": {name: sum(_block(d) == name for d in days) for name in BLOCKS},
        "blocked_or_missing": len(blocked_all),
        "excluded_by_reason": dict(sorted((reason, sum(x["reason"] == reason for x in blocked_all))
                                           for reason in {x["reason"] for x in blocked_all})),
        "inputs": {"anatomy": "factory/artifacts/basket/sip/anatomy/YYYY-MM-DD.jsonl",
                   "bars": "factory/artifacts/basket/sip/bars/YYYY-MM-DD.parquet",
                   "calendar": "factory/artifacts/basket/sip/phase2_session_calendar.json",
                   "day_manifest_sha256": hashlib.sha256("\n".join(days).encode()).hexdigest(),
                   "anatomy_sha256": _file_manifest_hash(
                       [sim.ANAT_DIR / f"{day}.jsonl" for day in days]
                   ),
                   "bars_sha256": _file_manifest_hash(
                       [sim.BARS_DIR / f"{day}.parquet" for day in days]
                   ),
                   "calendar_sha256": hashlib.sha256(sim.CAL_PATH.read_bytes()).hexdigest(),
                   "contract_sha256": hashlib.sha256(sim.CONTRACT_PATH.read_bytes()).hexdigest()},
    }
    _write_json(out / "coverage.json", coverage)
    _write_json(out / "surface.json", _surface(df))
    print(f"complete: {coverage['days']} days, {df.height} rows, {df.width} columns", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", help="comma-separated YYYY-MM subset")
    ap.add_argument("--max-days", type=int)
    args = ap.parse_args()
    run(months=set(args.months.split(",")) if args.months else None, max_days=args.max_days)


if __name__ == "__main__":
    main()
